# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Operations a stage plans, and the one applier that does and undoes them, one journal line per operation.

A stage never changes a file itself. It returns a list of operation dicts, `ops_action` writes them to a file under
the plugin's state folder, and the engine runs `apply_ops` on `homelab apply`. Each finished operation is appended
to a journal together with what is needed to reverse it, so `homelab undo` replays the journal backwards, even
after a run that stopped half way.

Operation kinds (a stage can add its own with `@register`):

  {"op": "move", "src": ..., "dst": ...}                    rename; undo moves it back
  {"op": "tags", "path": ..., "before": {k: [v]}, "after": {k: [v] | None}}
                                                            tag write; undo writes `before` back
  {"op": "create", "path": ..., "text": ... | "from": ...}  a new file; undo moves it to the quarantine folder
  {"op": "replace", "path": ..., "text": ...}              rewrite a text file; the old copy is saved first

Every path must sit under the music library, the staging area or the plugin's state folder.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from homelab_plugin_api import Action

from . import tagio


class OpError(Exception):
    pass


@dataclass
class Handler:
    paths: Callable[[dict], list[str]]  # every path the op touches
    check: Callable[[dict], None]  # raise OpError when the world changed since planning
    do: Callable[[dict, Run], dict]  # perform it, return the journal record
    undo: Callable[[dict, Run], None]  # reverse a journal record


HANDLERS: dict[str, Handler] = {}


def register(name: str, paths, check, do, undo) -> None:
    HANDLERS[name] = Handler(paths, check, do, undo)


@dataclass
class Run:
    roots: list[Path]  # the only places an op may touch
    keep: Path  # where saved copies and quarantined files go


def _under(path: str, roots: list[Path]) -> bool:
    resolved = Path(path).expanduser().resolve()
    return any(resolved == r or r in resolved.parents for r in roots)


def read_ops(path: str | Path) -> Iterator[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def ops_action(ctx, ops: list[dict], describe: str) -> Action:
    """One plugin action that carries every op; the file is named after its content, so planning twice agrees."""
    folder = ctx.path("state") / "ops"
    folder.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(op, ensure_ascii=False, sort_keys=True) + "\n" for op in ops)
    target = folder / f"{hashlib.sha256(text.encode()).hexdigest()[:20]}.jsonl"
    target.write_text(text, encoding="utf-8")
    return Action(
        kind="plugin", describe=f"{describe} ({len(ops)} operations)", args={"ops": str(target), "count": len(ops)}
    )


def run_for(ctx) -> Run:
    roots = [ctx.path(k).resolve() for k in ("music", "tmp", "state") if k in ctx.paths]
    return Run(roots=roots, keep=ctx.path("state") / "kept")


def validate(ops_file: str, run: Run) -> int:
    """Check every op before the first one runs, so a stale plan changes nothing."""
    count = 0
    claimed: set[str] = set()
    for op in read_ops(ops_file):
        handler = HANDLERS.get(op.get("op", ""))
        if handler is None:
            raise OpError(f"unknown operation {op.get('op')!r}")
        for path in handler.paths(op):
            if not _under(path, run.roots):
                raise OpError(f"{op['op']} touches {path}, which is outside the library, staging and state folders")
        handler.check(op)
        for path in handler.paths(op)[-1:]:  # the destination must not be claimed twice
            if op["op"] in ("move", "create") and path.lower() in claimed:
                raise OpError(f"two operations write {path}")
            claimed.add(path.lower())
        count += 1
    return count


def apply_ops(ctx, options, action: Action) -> dict:
    run = run_for(ctx)
    ops_file = action.args["ops"]
    validate(ops_file, run)
    journal = Path(ops_file).with_suffix(".done.jsonl")
    with journal.open("a", encoding="utf-8") as done:
        for op in read_ops(ops_file):
            record = HANDLERS[op["op"]].do(op, run)
            done.write(json.dumps({"op": op["op"], **record}, ensure_ascii=False) + "\n")
            done.flush()
    return {"journal": str(journal)}


def undo_ops(ctx, options, data: dict) -> None:
    run = run_for(ctx)
    journal = Path(data["journal"])
    if not journal.exists():
        return
    for record in reversed(list(read_ops(journal))):
        HANDLERS[record["op"]].undo(record, run)


# --- move -----------------------------------------------------------------------------------------------------


def _move_check(op):
    if not Path(op["src"]).exists():
        raise OpError(f"missing source {op['src']}")
    dst = Path(op["dst"])
    if dst.exists() and not (dst.name.lower() == Path(op["src"]).name.lower() and dst.parent == Path(op["src"]).parent):
        raise OpError(f"destination exists {op['dst']}")


def _move_do(op, run):
    Path(op["dst"]).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(op["src"], op["dst"])
    return {"src": op["src"], "dst": op["dst"]}


def _move_undo(record, run):
    if not Path(record["dst"]).exists():
        raise OpError(f"cannot undo, {record['dst']} is gone")
    Path(record["src"]).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(record["dst"], record["src"])


register("move", lambda op: [op["src"], op["dst"]], _move_check, _move_do, _move_undo)


# --- tags -----------------------------------------------------------------------------------------------------


def _tags_check(op):
    now = tagio.read(op["path"], sorted(set(op["before"]) | set(op["after"])))
    if now != {k: v for k, v in op["before"].items() if v}:
        raise OpError(f"tags of {op['path']} changed since the plan")


def _tags_do(op, run):
    tagio.write(op["path"], op["after"])
    return {"path": op["path"], "before": op["before"], "after": op["after"]}


def _tags_undo(record, run):
    now = tagio.read(record["path"], sorted(set(record["before"]) | set(record["after"])))
    if now != {k: v for k, v in record["after"].items() if v}:
        raise OpError(f"tags of {record['path']} were edited after the run, not restoring")
    tagio.write(
        record["path"], {k: (record["before"].get(k) or None) for k in set(record["before"]) | set(record["after"])}
    )


register("tags", lambda op: [op["path"]], _tags_check, _tags_do, _tags_undo)


# --- create / replace -------------------------------------------------------------------------------------------


def _create_check(op):
    if Path(op["path"]).exists():
        raise OpError(f"already exists {op['path']}")
    if "from" in op and not Path(op["from"]).exists():
        raise OpError(f"missing source {op['from']}")


def _create_do(op, run):
    target = Path(op["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    if "from" in op:
        shutil.copy2(op["from"], target)
    else:
        target.write_text(op["text"], encoding="utf-8")
    return {"path": op["path"]}


def _create_undo(record, run):
    target = Path(record["path"])
    if target.exists():
        kept = run.keep / "created" / hashlib.sha256(str(target).encode()).hexdigest()[:12]
        kept.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), kept / target.name)


register("create", lambda op: [op["path"]], _create_check, _create_do, _create_undo)


def _replace_check(op):
    if not Path(op["path"]).exists():
        raise OpError(f"missing {op['path']}")


def _replace_do(op, run):
    target = Path(op["path"])
    saved = run.keep / "replaced" / (hashlib.sha256(str(target).encode()).hexdigest()[:12] + "-" + target.name)
    saved.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, saved)
    target.write_text(op["text"], encoding="utf-8")
    return {"path": op["path"], "saved": str(saved)}


def _replace_undo(record, run):
    shutil.copy2(record["saved"], record["path"])


register("replace", lambda op: [op["path"]], _replace_check, _replace_do, _replace_undo)

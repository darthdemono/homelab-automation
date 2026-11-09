# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Plan, apply and undo. Every run is a JSON file in the ledger; nothing is ever deleted."""

from __future__ import annotations

import contextlib
import difflib
import getpass
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from homelab_plugin_api import Action, Context, Manifest, Plan, Plugin
from pydantic import ValidationError

from .config import Config
from .errors import Ambiguous, Conflict, Denied, Invalid, NotFound, RunError, Unavailable
from .registry import Found
from .secrets import scrub

ZERO = "0" * 64


def _chain(previous: str, line: dict) -> str:
    body = json.dumps({k: v for k, v in line.items() if k != "hash"}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((previous + body).encode()).hexdigest()


SECRET_REF = re.compile(r"\{\{secret:([A-Za-z0-9_]+)\}\}")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _under(path: Path, prefixes: list[Path]) -> bool:
    return any(path == p or p in path.parents for p in prefixes)


def check_permissions(manifest: Manifest, ctx: Context, plan: Plan) -> list[str]:
    """Return one message per action that leaves the plugin's declared permissions."""
    prefixes = []
    for item in manifest.permissions.writes:
        if item.startswith("{") and item.endswith("}"):
            if item[1:-1] in ctx.paths:
                prefixes.append(ctx.paths[item[1:-1]].resolve())
        else:
            prefixes.append(Path(item).expanduser().resolve())
    problems = []
    for action in plan.actions:
        targets = []
        if action.kind == "move":
            targets = [action.args["src"], action.args["dst"]]
        elif action.kind in ("write", "quarantine"):
            targets = [action.args["path"]]
        elif action.kind == "exec" and action.args["argv"][0] not in manifest.permissions.exec:
            problems.append(f"exec {action.args['argv'][0]!r} is not declared")
        for target in targets:
            if not _under(Path(target).expanduser().resolve(), prefixes):
                problems.append(f"{action.kind} {target!r} is outside the declared writes")
    return problems


class Runner:
    def __init__(self, config: Config, found: Found):
        self.config = config
        self.found = found
        self.runs_dir = config.state_dir / "runs"

    def context(self, name: str) -> Context:
        """`ctx.path("state")` is this plugin's own folder under state/plugins/, for journals and saved copies."""
        state = self.config.state_dir / "plugins" / name
        return Context(plugin=name, paths={**self.config.paths, "state": state}, inventory=self.config.inventory)

    @property
    def paused(self) -> bool:
        return (self.config.state_dir / "PAUSED").exists()

    def set_paused(self, on: bool) -> None:
        flag = self.config.state_dir / "PAUSED"
        if on:
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.write_text("paused\n")
        else:
            flag.unlink(missing_ok=True)
        self.audit("pause" if on else "resume")

    def is_script(self, name: str) -> bool:
        return name in self.found.plugins and self.found.plugins[name].manifest.category == "script"

    def enabled(self, name: str) -> Plugin:
        block = self.config.plugins.get(name)
        if self.is_script(name) and block is None:
            return self.found.plugins[name]  # scripts are on by default; a block can set options or switch one off
        if block is None or not block.enabled:
            raise Unavailable(f"plugin {name!r} has no enabled block in plugins.yaml")
        if name not in self.found.plugins:
            raise Unavailable(f"plugin {name!r} did not load: {self.found.errors.get(name, 'not found')}")
        return self.found.plugins[name]

    def options(self, name: str, overrides: dict | None = None):
        block = self.config.plugins.get(name)
        given = {**(block.options if block else {}), **(overrides or {})}
        try:
            return self.enabled(name).config_model(**given)
        except ValidationError as err:
            problems = err.errors(include_url=False, include_context=False, include_input=False)
            raise Invalid(
                f"options of plugin {name!r} are invalid: {len(problems)} problem(s)", {"errors": problems}
            ) from err

    def _save(self, record: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        text = scrub(json.dumps(record, indent=2), self._secret_values(record["ref"].partition(".")[0]))
        target = self.runs_dir / f"{record['id']}.json"
        part = target.with_name(target.name + ".part")
        part.write_text(text)
        part.replace(target)
        record.clear()
        record.update(json.loads(text))  # callers see the scrubbed record, never the secret

    def _secret_values(self, name: str) -> dict[str, str]:
        plugin = self.found.plugins.get(name)
        names = plugin.manifest.secrets if plugin else []
        return {n: os.environ[n] for n in names if len(os.environ.get(n, "")) >= 8}

    def _ids(self) -> list[str]:
        if not self.runs_dir.is_dir():
            return []
        return [p.stem for p in sorted(self.runs_dir.glob("*.json"))]

    def resolve(self, ref: str) -> str:
        """A run id, a unique prefix of one, or `latest`. Never guesses between several."""
        ids = self._ids()
        if ref == "latest":
            if ids:
                return ids[-1]
            raise NotFound("no runs yet")
        if ref in ids:
            return ref
        hits = [i for i in ids if i.startswith(ref)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Ambiguous(f"{ref!r} matches {len(hits)} runs", {"candidates": hits})
        close = difflib.get_close_matches(ref, ids, 3)
        raise NotFound(f"no run {ref!r}", {"suggestions": close} if close else None)

    def get(self, ref: str) -> dict:
        return json.loads((self.runs_dir / f"{self.resolve(ref)}.json").read_text())

    def runs(self) -> list[dict]:
        return [json.loads((self.runs_dir / f"{i}.json").read_text()) for i in self._ids()]

    def audit(self, event: str, **fields) -> None:
        """Append one line to state/audit.jsonl. Each line carries the hash of the one before it, so an edited,
        removed or reordered line breaks the chain and `homelab audit --verify` says where."""
        path = self.config.state_dir / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = ZERO
        if path.exists():
            lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
            if lines:
                previous = json.loads(lines[-1]).get("hash", ZERO)
        line = {
            "ts": datetime.now(UTC).isoformat(),
            "user": getpass.getuser(),
            "event": event,
            **fields,
            "prev": previous,
        }
        line["hash"] = _chain(previous, line)
        with path.open("a") as handle:
            handle.write(json.dumps(line) + "\n")

    def audit_events(self) -> list[dict]:
        path = self.config.state_dir / "audit.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]

    def verify_audit(self) -> dict:
        """Recompute the chain. Lines written before chaining existed (no hash) are allowed only at the start."""
        previous, legacy, chained = ZERO, 0, 0
        for number, line in enumerate(self.audit_events(), 1):
            if "hash" not in line:
                if chained:
                    return {
                        "ok": False,
                        "events": number,
                        "legacy": legacy,
                        "broken_at": number,
                        "why": "an unchained line after the chain began",
                    }
                legacy += 1
                continue
            if chained == 0 and line.get("prev") not in (ZERO, None) and legacy == 0:
                return {
                    "ok": False,
                    "events": number,
                    "legacy": legacy,
                    "broken_at": number,
                    "why": "the chain does not start at the beginning",
                }
            if chained and line.get("prev") != previous:
                return {
                    "ok": False,
                    "events": number,
                    "legacy": legacy,
                    "broken_at": number,
                    "why": "previous hash does not match",
                }
            if _chain(line.get("prev", ZERO), {k: v for k, v in line.items() if k != "hash"}) != line["hash"]:
                return {
                    "ok": False,
                    "events": number,
                    "legacy": legacy,
                    "broken_at": number,
                    "why": "the line was edited",
                }
            previous, chained = line["hash"], chained + 1
        return {"ok": True, "events": legacy + chained, "legacy": legacy, "broken_at": None, "why": ""}

    def resolve_job(self, ref: str) -> str:
        """`plugin.job`, or a bare job name when exactly one enabled plugin has it."""
        if "." in ref:
            return ref
        if ref in self.found.plugins and not self._on(ref):
            raise Unavailable(f"{ref!r} is switched off or has no enabled block in plugins.yaml")
        if ref in self.found.plugins and self._on(ref) and len(self.found.plugins[ref].jobs) == 1:
            return f"{ref}.{next(iter(self.found.plugins[ref].jobs))}"
        hits = [f"{n}.{j}" for n, p in self.found.plugins.items() if self._on(n) for j in p.jobs if j == ref]
        if len(hits) == 1:
            return hits[0]
        if hits:
            raise Ambiguous(f"job {ref!r} exists in several plugins", {"candidates": hits})
        every = [f"{n}.{j}" for n, p in self.found.plugins.items() if self._on(n) for j in p.jobs]
        close = difflib.get_close_matches(ref, every, 3)
        raise NotFound(f"no job {ref!r}", {"suggestions": close} if close else None)

    def _on(self, name: str) -> bool:
        block = self.config.plugins.get(name)
        if block is None:
            return self.is_script(name)
        return block.enabled

    def make_plan(self, ref: str, overrides: dict | None = None) -> dict:
        ref = self.resolve_job(ref)
        name, _, job_name = ref.partition(".")
        level, rule = self.config.level_for(ref)
        if level == "deny":
            raise Denied(
                f"policy: {ref} is denied (rule {rule!r}); even planning it is refused", {"level": level, "rule": rule}
            )
        plugin = self.enabled(name)
        if job_name not in plugin.jobs:
            close = [f"{name}.{j}" for j in difflib.get_close_matches(job_name, list(plugin.jobs), 3)]
            raise NotFound(f"plugin {name!r} has no job {job_name!r}", {"suggestions": close} if close else None)
        ctx = self.context(name)
        try:
            plan = plugin.jobs[job_name].plan(ctx, self.options(name, overrides))
        except ValueError as err:  # a plugin saying "these options make no sense" is the caller's mistake
            raise Invalid(str(err)) from err
        plan.job = ref
        problems = check_permissions(plugin.manifest, ctx, plan)
        if problems:
            raise Denied("plan refused:\n  " + "\n  ".join(problems), {"problems": problems})
        record = {
            "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"),
            "ref": ref,
            "status": "planned",
            "plan": plan.model_dump(),
            "options": overrides or {},
            "undo": [],
            "error": None,
        }
        self._save(record)
        self.audit("plan", run=record["id"], ref=ref, actions=len(plan.actions))
        return record

    def check_apply(self, ref: str) -> tuple[dict, Plugin, Context, object, Plan]:
        """Everything `apply` checks before it changes anything, so `--dry-run` can run it alone."""
        record = self.get(ref)
        if record["status"] != "planned":
            raise Conflict(f"run {record['id']} is {record['status']}, only a planned run can be applied")
        level, rule = self.config.level_for(record["ref"])
        if level in ("deny", "plan"):
            raise Denied(
                f"policy: {record['ref']} is {level}-only (rule {rule!r}), it cannot be applied",
                {"level": level, "rule": rule},
            )
        if self.paused:
            raise Unavailable("paused: changes are switched off (`homelab resume` turns them back on)")
        name = record["ref"].partition(".")[0]
        plugin = self.enabled(name)
        ctx = self.context(name)
        options = self.options(name, record.get("options"))
        plan = Plan(**record["plan"])
        problems = check_permissions(plugin.manifest, ctx, plan)
        if problems:
            raise Denied("plan refused:\n  " + "\n  ".join(problems), {"problems": problems})
        for action in plan.actions:
            if action.kind == "move":
                src, dst = Path(action.args["src"]), Path(action.args["dst"])
                if not src.exists() or dst.exists():
                    raise Conflict(f"changed since the plan, nothing applied: {action.describe}")
            elif action.kind == "quarantine" and not Path(action.args["path"]).exists():
                raise Conflict(f"changed since the plan, nothing applied: {action.describe}")
        return record, plugin, ctx, options, plan

    def alive(self, record: dict) -> bool:
        """A run marked running whose process is gone was interrupted: mark it failed so it can be undone."""
        if record.get("status") != "running":
            return True
        try:
            os.kill(record["pid"], 0)
            return True
        except (ProcessLookupError, KeyError, TypeError):
            record["status"], record["error"] = "failed", "interrupted: the process ended before the run finished"
            self._save(record)
            return False
        except PermissionError:
            return True

    def cancel(self, ref: str) -> dict:
        import signal

        record = self.get(ref)
        if record["status"] != "running":
            raise Conflict(f"run {record['id']} is {record['status']}, only a running run can be cancelled")
        try:
            os.killpg(record["pid"], signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            if record.get("pid"):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(record["pid"], signal.SIGTERM)
        record["status"], record["error"] = "failed", "cancelled: undo it to restore what was already done"
        self._save(record)
        self.audit("cancel", run=record["id"], ref=record["ref"])
        return record

    def apply(self, ref: str) -> dict:
        record, plugin, ctx, options, plan = self.check_apply(ref)
        run_id = record["id"]
        record.update(status="running", pid=os.getpid(), started=datetime.now(UTC).isoformat())
        self._save(record)
        try:
            for number, action in enumerate(plan.actions):
                record["undo"].append(self._do(record["id"], number, action, plugin, ctx, options))
                self._save(record)
        except Exception as err:
            record["status"], record["error"] = "failed", f"{type(err).__name__}: {err}"
            self._save(record)
            self.audit("apply", run=run_id, ref=record["ref"], result="failed", error=record["error"])
            raise RunError(f"run {run_id} failed, undo it to restore: {record['error']}") from err
        record["status"] = "applied"
        self._save(record)
        self.audit("apply", run=run_id, ref=record["ref"], result="applied", actions=len(plan.actions))
        return record

    def _expand(self, value):
        """Fill `{{secret:NAME}}` placeholders from the environment, at apply time only."""
        if isinstance(value, str):

            def fill(match):
                name = match.group(1)
                if name not in os.environ:
                    raise Unavailable(f"secret {name} is not available; see `homelab secrets`")
                return os.environ[name]

            return SECRET_REF.sub(fill, value)
        if isinstance(value, list):
            return [self._expand(v) for v in value]
        if isinstance(value, dict):
            return {k: self._expand(v) for k, v in value.items()}
        return value

    def _do(self, run_id: str, number: int, action: Action, plugin: Plugin, ctx: Context, options) -> dict:
        action = action.model_copy(update={"args": self._expand(action.args)})
        a = action.args
        if action.kind == "move":
            Path(a["dst"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(a["src"], a["dst"])
            return {"kind": "move", "src": a["dst"], "dst": a["src"]}
        if action.kind == "quarantine":
            source = Path(a["path"])
            target = self.config.state_dir / "quarantine" / run_id / f"{number}-{source.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            return {"kind": "move", "src": str(target), "dst": str(source)}
        if action.kind == "write":
            path = Path(a["path"])
            previous = path.read_text() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(a["text"])
            return {"kind": "restore", "path": str(path), "previous": previous, "wrote": _hash(a["text"])}
        if action.kind == "exec":
            subprocess.run(a["argv"], check=True)
            if a.get("undo_argv"):
                return {"kind": "exec", "argv": a["undo_argv"]}
            return {"kind": "irreversible", "describe": action.describe}
        if plugin.apply_action is None:
            raise Invalid(f"plugin {plugin.manifest.name!r} has no apply_action")
        return {"kind": "plugin", "data": plugin.apply_action(ctx, options, action)}

    def check_undo(self, ref: str) -> tuple[dict, Plugin, list[dict]]:
        record = self.get(ref)
        if record["status"] not in ("applied", "failed"):
            raise Conflict(f"run {record['id']} is {record['status']}, nothing to undo")
        plugin = self.enabled(record["ref"].partition(".")[0])
        steps = list(reversed(record["undo"]))
        for step in steps:
            if step["kind"] == "move" and (not Path(step["src"]).exists() or Path(step["dst"]).exists()):
                raise Conflict(f"changed since the run, nothing undone: {step['src']} -> {step['dst']}")
            if step["kind"] == "restore":
                path = Path(step["path"])
                if not path.exists() or _hash(path.read_text()) != step["wrote"]:
                    raise Conflict(f"{path} was edited after the run, nothing undone")
        return record, plugin, steps

    def undo(self, ref: str) -> dict:
        record, plugin, steps = self.check_undo(ref)
        run_id = record["id"]
        name = plugin.manifest.name
        skipped = []
        for step in steps:
            if step["kind"] == "move":
                shutil.move(step["src"], step["dst"])
            elif step["kind"] == "restore":
                path = Path(step["path"])
                if step["previous"] is None:
                    target = self.config.state_dir / "quarantine" / run_id / f"undo-{path.name}"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(target))
                else:
                    path.write_text(step["previous"])
            elif step["kind"] == "exec":
                subprocess.run(step["argv"], check=True)
            elif step["kind"] == "plugin":
                if plugin.undo_action is None:
                    raise Invalid(f"plugin {name!r} has no undo_action")
                plugin.undo_action(self.context(name), self.options(name, record.get("options")), step["data"])
            else:
                skipped.append(step["describe"])
        record["status"] = "undone"
        record["skipped_irreversible"] = skipped
        self._save(record)
        self.audit("undo", run=run_id, ref=record["ref"], skipped_irreversible=len(skipped))
        return record

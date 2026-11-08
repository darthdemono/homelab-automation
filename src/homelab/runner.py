# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Plan, apply and undo. Every run is a JSON file in the ledger; nothing is ever deleted."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from homelab_plugin_api import Action, Context, Manifest, Plan, Plugin

from .config import Config
from .registry import Found


class RunError(Exception):
    pass


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
        return Context(plugin=name, paths=self.config.paths)

    def enabled(self, name: str) -> Plugin:
        block = self.config.plugins.get(name)
        if block is None or not block.enabled:
            raise RunError(f"plugin {name!r} has no enabled block in plugins.yaml")
        if name not in self.found.plugins:
            raise RunError(f"plugin {name!r} did not load: {self.found.errors.get(name, 'not found')}")
        return self.found.plugins[name]

    def options(self, name: str):
        return self.enabled(name).config_model(**self.config.plugins[name].options)

    def _save(self, record: dict) -> None:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        (self.runs_dir / f"{record['id']}.json").write_text(json.dumps(record, indent=2))

    def get(self, run_id: str) -> dict:
        path = self.runs_dir / f"{run_id}.json"
        if not path.exists():
            raise RunError(f"no run {run_id!r}")
        return json.loads(path.read_text())

    def runs(self) -> list[dict]:
        if not self.runs_dir.is_dir():
            return []
        return [json.loads(p.read_text()) for p in sorted(self.runs_dir.glob("*.json"))]

    def make_plan(self, ref: str) -> dict:
        name, _, job_name = ref.partition(".")
        plugin = self.enabled(name)
        if job_name not in plugin.jobs:
            raise RunError(f"plugin {name!r} has no job {job_name!r}")
        ctx = self.context(name)
        plan = plugin.jobs[job_name].plan(ctx, self.options(name))
        plan.job = ref
        problems = check_permissions(plugin.manifest, ctx, plan)
        if problems:
            raise RunError("plan refused:\n  " + "\n  ".join(problems))
        record = {
            "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"),
            "ref": ref,
            "status": "planned",
            "plan": plan.model_dump(),
            "undo": [],
            "error": None,
        }
        self._save(record)
        return record

    def apply(self, run_id: str) -> dict:
        record = self.get(run_id)
        if record["status"] != "planned":
            raise RunError(f"run {run_id} is {record['status']}, only a planned run can be applied")
        name = record["ref"].partition(".")[0]
        plugin = self.enabled(name)
        ctx = self.context(name)
        options = self.options(name)
        plan = Plan(**record["plan"])
        problems = check_permissions(plugin.manifest, ctx, plan)
        if problems:
            raise RunError("plan refused:\n  " + "\n  ".join(problems))
        for action in plan.actions:
            if action.kind == "move":
                src, dst = Path(action.args["src"]), Path(action.args["dst"])
                if not src.exists() or dst.exists():
                    raise RunError(f"changed since the plan, nothing applied: {action.describe}")
            elif action.kind == "quarantine" and not Path(action.args["path"]).exists():
                raise RunError(f"changed since the plan, nothing applied: {action.describe}")
        try:
            for number, action in enumerate(plan.actions):
                record["undo"].append(self._do(record["id"], number, action, plugin, ctx, options))
                self._save(record)
        except Exception as err:
            record["status"], record["error"] = "failed", f"{type(err).__name__}: {err}"
            self._save(record)
            raise RunError(f"run {run_id} failed, undo it to restore: {record['error']}") from err
        record["status"] = "applied"
        self._save(record)
        return record

    def _do(self, run_id: str, number: int, action: Action, plugin: Plugin, ctx: Context, options) -> dict:
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
            raise RunError(f"plugin {plugin.manifest.name!r} has no apply_action")
        return {"kind": "plugin", "data": plugin.apply_action(ctx, options, action)}

    def undo(self, run_id: str) -> dict:
        record = self.get(run_id)
        if record["status"] not in ("applied", "failed"):
            raise RunError(f"run {run_id} is {record['status']}, nothing to undo")
        name = record["ref"].partition(".")[0]
        plugin = self.enabled(name)
        steps = list(reversed(record["undo"]))
        for step in steps:
            if step["kind"] == "move" and (not Path(step["src"]).exists() or Path(step["dst"]).exists()):
                raise RunError(f"changed since the run, nothing undone: {step['src']} -> {step['dst']}")
            if step["kind"] == "restore":
                path = Path(step["path"])
                if not path.exists() or _hash(path.read_text()) != step["wrote"]:
                    raise RunError(f"{path} was edited after the run, nothing undone")
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
                    raise RunError(f"plugin {name!r} has no undo_action")
                plugin.undo_action(self.context(name), self.options(name), step["data"])
            else:
                skipped.append(step["describe"])
        record["status"] = "undone"
        record["skipped_irreversible"] = skipped
        self._save(record)
        return record

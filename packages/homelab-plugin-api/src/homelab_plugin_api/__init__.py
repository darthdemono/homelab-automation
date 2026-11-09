# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Public, versioned contract for homelab-automation plugins."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

API_VERSION = "1"

ActionKind = Literal["move", "write", "quarantine", "exec", "journal", "plugin"]


class Permissions(BaseModel):
    """What a plugin may touch. The engine refuses any action outside these."""

    model_config = ConfigDict(extra="forbid")
    reads: list[str] = []
    writes: list[str] = []
    network: list[str] = []
    exec: list[str] = []


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    api_version: str = API_VERSION
    category: str
    summary: str
    permissions: Permissions = Permissions()
    secrets: list[str] = []


class Action(BaseModel):
    """One intended change.

    move        args: src, dst
    write       args: path, text
    quarantine  args: path (moved into the run's quarantine folder, never deleted)
    exec        args: argv, optional undo_argv
    journal     args: path (a JSON-lines file of {"src", "dst"} moves), count. For thousands of moves: the engine
                streams the file, appends each finished move to its own journal, and undo replays that journal
                backwards. Build it with `journal_action`.
    plugin      args: anything; the plugin's apply_action and undo_action handle it
    """

    kind: ActionKind
    describe: str
    args: dict[str, Any] = {}


class Plan(BaseModel):
    job: str = ""
    actions: list[Action] = []
    notes: list[str] = []


@dataclass
class Context:
    plugin: str
    paths: Mapping[str, Path] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)
    inventory: Mapping[str, Mapping[str, str]] = field(default_factory=dict)  # kind -> name -> value, from homelab.yaml

    def named(self, kind: str, name: str) -> str | None:
        """Look a target up by name (for example kind "stacks"), so a caller never passes a raw path."""
        return self.inventory.get(kind, {}).get(name)

    def path(self, key: str) -> Path:
        return self.paths[key]

    def secret(self, env_name: str) -> str | None:
        return self.env.get(env_name) or None

    def secret_ref(self, env_name: str) -> str:
        """Put this in an action's args instead of the value: the engine fills it in at apply time,
        so the plan, the ledger and every printout never hold the secret."""
        return "{{secret:" + env_name + "}}"


@dataclass
class Job:
    summary: str
    plan: Callable[[Context, Any], Plan]


@dataclass
class Nudge:
    """Four short hints for the model that will run a script. The engine never calls a model:
    it only shows these at the moment they are useful (search, describe, plan, apply)."""

    when: str = ""  # when to reach for this script
    before: str = ""  # what to check before applying
    after: str = ""  # how to verify afterwards
    avoid: str = ""  # when not to use it, and what to use instead


SCRIPT_STATUSES = ("one-off", "stable", "retired")


@dataclass
class Plugin:
    manifest: Manifest
    config_model: type[BaseModel]
    jobs: dict[str, Job]
    example: dict[str, Any] = field(default_factory=dict)
    apply_action: Callable[[Context, Any, Action], dict[str, Any]] | None = None
    undo_action: Callable[[Context, Any, dict[str, Any]], None] | None = None
    # set for scripts (a one-job plugin made with @script or script_exec)
    tags: list[str] = field(default_factory=list)
    status: str = ""
    nudge: Nudge | None = None


class NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


def script(
    *,
    summary: str,
    tags: list[str] | None = None,
    status: str = "one-off",
    reads: list[str] | None = None,
    writes: list[str] | None = None,
    network: list[str] | None = None,
    exec: list[str] | None = None,  # noqa: A002 - mirrors Permissions.exec
    secrets: list[str] | None = None,
    nudge: Nudge | None = None,
    options: type[BaseModel] = NoOptions,
    example: dict[str, Any] | None = None,
    apply_action: Callable[[Context, Any, Action], dict[str, Any]] | None = None,
    undo_action: Callable[[Context, Any, dict[str, Any]], None] | None = None,
) -> Callable[[Callable[[Context, Any], Plan]], Plugin]:
    """Turn one function, `plan(ctx, options) -> Plan`, into a script: a one-job plugin that gets
    plan, apply, undo, audit, secrets and --dry-run from the engine. The function name is the script name."""

    def wrap(function: Callable[[Context, Any], Plan]) -> Plugin:
        return Plugin(
            manifest=Manifest(
                name=function.__name__,
                version="1",
                category="script",
                summary=summary,
                permissions=Permissions(reads=reads or [], writes=writes or [], network=network or [], exec=exec or []),
                secrets=secrets or [],
            ),
            config_model=options,
            jobs={"run": Job(summary=summary, plan=function)},
            example=example or {},
            apply_action=apply_action,
            undo_action=undo_action,
            tags=list(tags or []),
            status=status,
            nudge=nudge,
        )

    return wrap


class ExecOptions(BaseModel):
    """Options of a wrapped command: extra arguments appended after the fixed ones."""

    model_config = ConfigDict(extra="forbid")
    args: list[str] = []


def script_exec(
    name: str,
    argv: list[str],
    *,
    summary: str,
    tags: list[str] | None = None,
    status: str = "one-off",
    secrets: list[str] | None = None,
    nudge: Nudge | None = None,
) -> Plugin:
    """Wrap an existing command as a script so it is audited and declared on day one. It is one
    irreversible `exec` action: port it to real move, write and quarantine actions to make it undoable."""

    def plan(ctx: Context, options: ExecOptions) -> Plan:
        full = [*argv, *options.args]
        return Plan(
            actions=[Action(kind="exec", describe=" ".join(full), args={"argv": full})],
            notes=["wrapped command: cannot be undone"],
        )

    return Plugin(
        manifest=Manifest(
            name=name,
            version="1",
            category="script",
            summary=summary,
            permissions=Permissions(exec=[argv[0]]),
            secrets=secrets or [],
        ),
        config_model=ExecOptions,
        jobs={"run": Job(summary=summary, plan=plan)},
        tags=list(tags or []),
        status=status,
        nudge=nudge,
    )


def journal_action(ctx: Context, moves: list[tuple[str, str]], describe: str) -> Action:
    """One action that carries many moves, written to a file under the plugin's state folder."""
    folder = ctx.path("state") / "journal-plans"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        for src, dst in moves:
            handle.write(json.dumps({"src": src, "dst": dst}, ensure_ascii=False) + "\n")
    return Action(
        kind="journal", describe=f"{describe} ({len(moves)} moves)", args={"path": str(target), "count": len(moves)}
    )

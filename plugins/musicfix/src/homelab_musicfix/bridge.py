# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Turn a stage's own command line into an engine job.

A stage keeps its logic and its dry-run default. Planning runs it without its commit flag and shows the output as
notes; applying runs it with the commit flag (one `exec` action); undoing runs the stage's own restore command,
which reads the journal the stage wrote. So every stage is reviewed first and reversible where the stage is.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field

from homelab_plugin_api import Action, Job, Plan

from . import settings


@dataclass
class Stage:
    module: str
    summary: str
    commit: list[str] = field(default_factory=list)  # flags that make the stage change things ([] = read only)
    undo: list[str] = field(default_factory=list)  # flags that reverse its last run ([] = none)
    undo_module: str = ""  # a different module does the undo (merge and apply use `undo`)
    dry: list[str] = field(default_factory=list)  # extra flags for the dry run
    needs: str = ""  # a note shown first, for example the stage that must run before


def _argv(ctx, options, module: str, flags: list[str]) -> list[str]:
    reports = ctx.path("state")
    argv = [
        sys.executable,
        "-m",
        "homelab_musicfix.run",
        "--music",
        str(ctx.path("music")),
        "--tmp",
        str(ctx.path("tmp")),
        "--reports",
        str(reports),
    ]
    if getattr(options, "leftovers", None):
        argv += ["--leftovers", json.dumps(options.leftovers)]
    return [*argv, module, *[f.replace("{reports}", str(reports)) for f in flags]]


def _extra(options) -> list[str]:
    args = getattr(options, "args", None) or []
    return [str(a) for a in args]


def job(stage: Stage) -> Job:
    def plan(ctx, options) -> Plan:
        settings.prepare(ctx, options)
        dry = _argv(ctx, options, stage.module, [*stage.dry, *_extra(options)])
        done = subprocess.run(dry, capture_output=True, text=True, timeout=7200, check=False)  # noqa: S603
        out = (done.stdout + done.stderr).strip().splitlines()
        lines = options.lines or 60
        shown = out[:lines] + ([f"... {len(out) - lines} more lines"] if len(out) > lines else [])
        notes = ([stage.needs] if stage.needs else []) + [f"dry run exit {done.returncode}", *shown]
        if not stage.commit or done.returncode != 0:
            return Plan(notes=notes)
        real = _argv(ctx, options, stage.module, [*stage.commit, *_extra(options)])
        args = {"argv": real}
        if stage.undo:
            args["undo_argv"] = _argv(ctx, options, stage.undo_module or stage.module, stage.undo)
        else:
            notes.append("this stage has no undo of its own: review the dry run before applying")
        return Plan(actions=[Action(kind="exec", describe=" ".join(real[3:]), args=args)], notes=notes)

    return Job(stage.summary, plan)


def jobs(stages: dict[str, Stage]) -> dict[str, Job]:
    return {name: job(stage) for name, stage in stages.items()}

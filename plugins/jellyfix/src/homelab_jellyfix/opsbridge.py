# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Turn the maintenance scripts of a plugin into engine jobs.

Each script keeps its logic and its journal. Its flags decide the job: `--apply` means the script is a dry run
by default; `--dry-run` means it applies by default; with neither it can only be run, so the plan says so. Planning
shows the dry run as notes, applying runs it for real, and `--undo` scripts are undone by the engine.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from homelab_plugin_api import Action, Job, Plan

SETTING_KEYS = (
    "root",
    "quarantine",
    "state",
    "plans",
    "docker_dir",
    "urls",
    "qbittorrent_container",
    "qbittorrent_internal_url",
    "container_paths",
    "config_files",
    "downloads",
    "pairs",
    "manga_root",
    "import_archive",
)


def settings_from(ctx, options) -> dict:
    """The stack settings for one run: the plugin options plus the engine's media and state folders."""
    data = options.model_dump() if hasattr(options, "model_dump") else dict(options)
    out = {k: data[k] for k in SETTING_KEYS if data.get(k) not in (None, "", {}, [])}
    libs = data.get("libraries") or []
    out["libraries"] = {lib["name"]: lib["path"] for lib in libs}
    out["library_specs"] = libs
    out.setdefault("state", str(ctx.path("state")))
    if "media" in ctx.paths:
        out.setdefault("root", str(ctx.path("media")))
    return out


def describe(path: Path) -> tuple[str, set[str]]:
    source = path.read_text()
    doc = ast.get_docstring(ast.parse(source)) or path.stem
    first = " ".join(doc.split("\n\n")[0].split())
    return first, set(re.findall(r'add_argument\(\s*"(--[a-z-]+)"', source))


def job_for(package: str, path: Path) -> Job:
    summary, flags = describe(path)
    module = f"{package}.ops.{path.stem}"

    def argv(ctx, options, extra):
        base = [
            sys.executable,
            "-m",
            "homelab_jellyfix.runop",
            "--stack",
            json.dumps(settings_from(ctx, options)),
            module,
        ]
        return [*base, *extra, *[str(a) for a in (getattr(options, "args", None) or [])]]

    def plan(ctx, options) -> Plan:
        notes: list[str] = []
        if "--apply" in flags:
            dry, real = [], ["--apply"]
        elif "--dry-run" in flags:
            dry, real = ["--dry-run"], []
        else:
            dry, real = None, []
        if dry is not None:
            done = subprocess.run(argv(ctx, options, dry), capture_output=True, text=True, timeout=7200, check=False)  # noqa: S603
            out = (done.stdout + done.stderr).strip().splitlines()
            lines = getattr(options, "lines", 60) or 60
            notes = [f"dry run exit {done.returncode}", *out[:lines]] + (
                [f"... {len(out) - lines} more lines"] if len(out) > lines else []
            )
            if done.returncode != 0:
                return Plan(notes=notes)
        else:
            notes = ["this script has no dry run: read its description before applying"]
        args = {"argv": argv(ctx, options, real)}
        if "--undo" in flags:
            args["undo_argv"] = argv(ctx, options, ["--undo", "@latest"])
        else:
            notes.append("no undo of its own")
        return Plan(actions=[Action(kind="exec", describe=" ".join(args["argv"][5:]), args=args)], notes=notes)

    return Job(summary, plan)


def ops_jobs(package: str, ops_dir: Path) -> dict[str, Job]:
    return {p.stem: job_for(package, p) for p in sorted(ops_dir.glob("*.py")) if not p.stem.startswith("_")}

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""List containers with their state, status and image. Read only."""

import json
import subprocess

from homelab_plugin_api import Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: bool = True
    name_contains: str = ""
    limit: int = 40


@script(
    summary="List Docker containers with state, status and image (read only, changes nothing)",
    tags=["docker", "status"],
    status="stable",
    exec=["docker"],
    options=Options,
    nudge=Nudge(
        when="you need to know what is running, what restarted, or whether a container is unhealthy",
        before="nothing to check: it only reads",
        after="for one container use --set name_contains=NAME; restart a stack with compose_restart",
        avoid="use compose_update, not this, to change anything",
    ),
)
def docker_ps(ctx, options: Options) -> Plan:
    argv = ["docker", "ps", *(["-a"] if options.all else []), "--format", "{{json .}}"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        return Plan(notes=["docker was not found on PATH"])
    except subprocess.CalledProcessError as err:
        return Plan(notes=[f"docker ps failed: {err.stderr.strip()[:200]}"])
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    wanted = options.name_contains.lower()
    rows = [r for r in rows if wanted in r.get("Names", "").lower()]
    states: dict[str, int] = {}
    for row in rows:
        states[row.get("State", "?")] = states.get(row.get("State", "?"), 0) + 1
    notes = [f"{len(rows)} containers: " + ", ".join(f"{n} {s}" for s, n in sorted(states.items()))]
    notes += [
        f"{r.get('Names', '?')} | {r.get('State', '?')} | {r.get('Status', '?')} | {r.get('Image', '?')}"
        for r in rows[: options.limit]
    ]
    if options.limit and len(rows) > options.limit:
        notes.append(f"... {len(rows) - options.limit} more, narrow it with --set name_contains=NAME")
    return Plan(notes=notes)

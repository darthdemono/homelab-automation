# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Restart some or all services of a Docker Compose file."""

from pathlib import Path

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = ""
    stack: str = ""  # a name from `inventory: stacks:` in homelab.yaml, instead of a path
    services: list[str] = []


@script(
    summary="Restart services of a Docker Compose project (all services when none are named)",
    tags=["docker", "compose", "restart"],
    status="stable",
    exec=["docker"],
    options=Options,
    example={"file": "docker-compose.yml"},
    nudge=Nudge(
        when="a service is stuck, or a changed config or mounted file must be picked up",
        before="name the services with --set services='[\"app\"]'; restarting everything drops every connection",
        after="run docker_ps with --set name_contains=NAME and check the status is Up and healthy",
        avoid="use compose_update when the image itself must change; restart does not pull",
    ),
)
def compose_restart(ctx, options: Options) -> Plan:
    file = (ctx.named("stacks", options.stack) if options.stack else options.file) or ""
    if not file:
        return Plan(notes=["set file=PATH or stack=NAME (a name from inventory: stacks:)"])
    if not Path(file).expanduser().is_file():
        return Plan(notes=[f"compose file not found: {file}"])
    argv = ["docker", "compose", "-f", file, "restart", *options.services]
    return Plan(
        actions=[Action(kind="exec", describe=" ".join(argv), args={"argv": argv})],
        notes=["a restart cannot be undone"],
    )

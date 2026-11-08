# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Pull newer images for a Docker Compose project and recreate what changed."""

from pathlib import Path

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str
    services: list[str] = []


@script(
    summary="Pull newer images and recreate the services that changed (docker compose pull, then up -d)",
    tags=["docker", "compose", "update"],
    status="stable",
    exec=["docker"],
    options=Options,
    example={"file": "docker-compose.yml"},
    nudge=Nudge(
        when="you want the latest image of a tag such as latest, or a pinned tag you bumped by hand",
        before="check the image tags: pinned digests will not change, and a major bump may need a migration",
        after="run docker_ps and look for Restarting or unhealthy; note the old image id in case of a rollback",
        avoid="never update the database containers of a service in the same step as the service itself",
    ),
)
def compose_update(ctx, options: Options) -> Plan:
    if not Path(options.file).expanduser().is_file():
        return Plan(notes=[f"compose file not found: {options.file}"])
    base = ["docker", "compose", "-f", options.file]
    steps = [[*base, "pull", *options.services], [*base, "up", "-d", *options.services]]
    return Plan(
        actions=[Action(kind="exec", describe=" ".join(argv), args={"argv": argv}) for argv in steps],
        notes=["pulling and recreating cannot be undone by the engine; the old image stays on disk until pruned"],
    )

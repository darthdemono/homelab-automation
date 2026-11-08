# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Pull newer images for a Docker Compose project and recreate what changed, with an undo."""

import subprocess
from pathlib import Path

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str = ""
    stack: str = ""  # a name from `inventory: stacks:` in homelab.yaml, instead of a path
    services: list[str] = []


def compose_file(ctx, options: Options) -> str:
    return (ctx.named("stacks", options.stack) if options.stack else options.file) or ""


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if result.returncode:
        raise RuntimeError(f"docker {' '.join(args[:3])} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def image_ids(file: str) -> dict[str, str]:
    ids = {}
    for image in docker("compose", "-f", file, "config", "--images").split():
        try:
            ids[image] = docker("image", "inspect", "--format", "{{.Id}}", image).strip()
        except RuntimeError:
            ids[image] = ""  # not pulled yet: nothing to return to
    return ids


def update(ctx, options: Options, action: Action) -> dict:
    file, services = action.args["file"], action.args["services"]
    before = image_ids(file)
    docker("compose", "-f", file, "pull", *services)
    docker("compose", "-f", file, "up", "-d", *services)
    return {"file": file, "services": services, "before": before, "after": image_ids(file)}


def rollback(ctx, options: Options, data: dict) -> None:
    """Point each changed image tag back at the image id it had before, then recreate the services."""
    for image, old in data["before"].items():
        if old and data["after"].get(image) != old:
            docker("tag", old, image)
    docker("compose", "-f", data["file"], "up", "-d", *data["services"])


@script(
    summary="Pull newer images and recreate the services that changed; undo returns to the previous images",
    tags=["docker", "compose", "update"],
    status="stable",
    exec=["docker"],
    options=Options,
    example={"file": "docker-compose.yml"},
    apply_action=update,
    undo_action=rollback,
    nudge=Nudge(
        when="you want the latest image of a tag such as latest, or a pinned tag you bumped by hand",
        before="check the image tags: pinned digests will not change, and a major bump may need a migration",
        after="run docker_ps and look for Restarting or unhealthy; `homelab undo` returns to the previous images",
        avoid="never update the database containers of a service in the same step as the service itself",
    ),
)
def compose_update(ctx, options: Options) -> Plan:
    file = compose_file(ctx, options)
    if not file:
        return Plan(notes=["set file=PATH or stack=NAME (a name from inventory: stacks:)"])
    if not Path(file).expanduser().is_file():
        return Plan(notes=[f"compose file not found: {file}"])
    action = Action(
        kind="plugin",
        describe=f"docker compose -f {file} pull, then up -d {' '.join(options.services)}".strip(),
        args={"file": file, "services": options.services},
    )
    return Plan(
        actions=[action], notes=["the image ids before and after are recorded; undo retags the old ones and recreates"]
    )

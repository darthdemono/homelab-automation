# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Version and health of Sonarr. Read only."""

import json
import urllib.error
import urllib.request

from homelab_plugin_api import Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    limit: int = 20


def fetch(options: Options, path: str, key: str):
    request = urllib.request.Request(options.url.rstrip("/") + path, headers={"X-Api-Key": key})  # noqa: S310
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def more(total: int, shown: int) -> list[str]:
    return [f"... {total - shown} more"] if total > shown else []


@script(
    summary="Version and health issues of Sonarr (read only)",
    tags=["arr", "media", "status"],
    status="stable",
    secrets=["SONARR_API_KEY"],
    options=Options,
    example={"url": "http://localhost:8989"},
    nudge=Nudge(
        when="you need to know whether Sonarr is up and healthy",
        before="nothing to check: it only reads",
        after="look at the queue with sonarr_queue",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def sonarr_status(ctx, options: Options) -> Plan:
    key = ctx.secret("SONARR_API_KEY")
    if not key:
        return Plan(notes=["SONARR_API_KEY is not available; see `homelab secrets`"])
    try:
        status = fetch(options, "/api/v3/system/status", key)
        health = fetch(options, "/api/v3/health", key)
        notes = [f"{status.get('appName', 'Sonarr')} {status.get('version', '?')}"]
        notes += [f"{h.get('type', '?')}: {h.get('message', '')}" for h in health] or ["no health issues"]
        return Plan(notes=notes)
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Version and health of Prowlarr. Read only."""

import json
import urllib.error
import urllib.request

from homelab_plugin_api import Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    limit: int = 30


def fetch(options: Options, path: str, key: str):
    request = urllib.request.Request(options.url.rstrip("/") + path, headers={"X-Api-Key": key})  # noqa: S310
    with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
        return json.load(response)


def more(total: int, shown: int) -> list[str]:
    return [f"... {total - shown} more"] if total > shown else []


@script(
    summary="Version and health issues of Prowlarr (read only)",
    tags=["download", "media", "status"],
    status="stable",
    secrets=["PROWLARR_API_KEY"],
    options=Options,
    example={"url": "http://localhost:9696"},
    nudge=Nudge(
        when="you need to know whether Prowlarr is up and healthy",
        before="nothing to check: it only reads",
        after="list the indexers with prowlarr_indexers",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def prowlarr_status(ctx, options: Options) -> Plan:
    key = ctx.secret("PROWLARR_API_KEY")
    if not key:
        return Plan(notes=["PROWLARR_API_KEY is not available; see `homelab secrets`"])
    try:
        status = fetch(options, "/api/v1/system/status", key)
        health = fetch(options, "/api/v1/health", key)
        notes = [f"{status.get('appName', 'Prowlarr')} {status.get('version', '?')}"]
        notes += [f"{h.get('type', '?')}: {h.get('message', '')}" for h in health] or ["no health issues"]
        return Plan(notes=notes)
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

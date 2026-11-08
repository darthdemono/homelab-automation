# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The download queue of Radarr. Read only."""

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
    summary="The Radarr download queue with status and progress (read only)",
    tags=["arr", "media", "queue"],
    status="stable",
    secrets=["RADARR_API_KEY"],
    options=Options,
    example={"url": "http://localhost:7878"},
    nudge=Nudge(
        when="you want to know what Radarr is downloading or stuck on",
        before="nothing to check: it only reads",
        after="check the download client if an item is stuck",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def radarr_queue(ctx, options: Options) -> Plan:
    key = ctx.secret("RADARR_API_KEY")
    if not key:
        return Plan(notes=["RADARR_API_KEY is not available; see `homelab secrets`"])
    try:
        data = fetch(options, f"/api/v3/queue?pageSize={options.limit}", key)
        records = data.get("records", [])
        notes = [f"{data.get('totalRecords', len(records))} items in the queue"]
        for r in records[: options.limit]:
            size, left = r.get("size", 0), r.get("sizeleft", 0)
            progress = 100 * (size - left) / size if size else 0
            title = r.get("title", "?")
            notes.append(f"{title} | {r.get('status', '?')} | {progress:.1f}%")
        return Plan(notes=notes + more(data.get("totalRecords", len(records)), min(len(records), options.limit)))
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

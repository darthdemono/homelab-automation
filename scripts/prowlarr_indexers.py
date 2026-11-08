# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The indexers of Prowlarr. Read only."""

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
    summary="Prowlarr indexers: which are enabled and their protocol (read only)",
    tags=["download", "media", "list"],
    status="stable",
    secrets=["PROWLARR_API_KEY"],
    options=Options,
    example={"url": "http://localhost:9696"},
    nudge=Nudge(
        when="an indexer search finds nothing and you want to see which indexers are on",
        before="nothing to check: it only reads",
        after="enable or fix an indexer in Prowlarr itself",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def prowlarr_indexers(ctx, options: Options) -> Plan:
    key = ctx.secret("PROWLARR_API_KEY")
    if not key:
        return Plan(notes=["PROWLARR_API_KEY is not available; see `homelab secrets`"])
    try:
        items = sorted(fetch(options, "/api/v1/indexer", key), key=lambda i: i.get("name", ""))
        enabled = sum(1 for i in items if i.get("enable"))
        notes = [f"{len(items)} indexers, {enabled} enabled"]
        notes += [
            f"{i.get('name', '?')} | {'yes' if i.get('enable') else 'no'} | {i.get('protocol', '?')}"
            for i in items[: options.limit]
        ]
        return Plan(notes=notes + more(len(items), min(len(items), options.limit)))
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

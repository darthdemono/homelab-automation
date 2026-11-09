# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The torrents of qBittorrent with state, progress and ratio. Read only."""

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from homelab_plugin_api import Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    limit: int = 20
    filter: str = "all"


def login(options: Options, user: str, password: str):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = options.url.rstrip("/")
    form = urllib.parse.urlencode({"username": user, "password": password}).encode()
    request = urllib.request.Request(base + "/api/v2/auth/login", data=form, headers={"Referer": base})  # noqa: S310
    with opener.open(request, timeout=20) as response:
        if response.read().decode().strip() != "Ok.":
            raise ValueError("login failed")
    return opener


def call(opener, options: Options, path: str) -> bytes:
    base = options.url.rstrip("/")
    request = urllib.request.Request(base + path, headers={"Referer": base})  # noqa: S310
    with opener.open(request, timeout=20) as response:
        return response.read()


@script(
    summary="qBittorrent torrents with state, progress and ratio (read only)",
    tags=["download", "media", "list"],
    status="stable",
    secrets=["QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"],
    options=Options,
    example={"url": "http://localhost:8080"},
    nudge=Nudge(
        when="you want to see what is downloading, stalled or seeding",
        before="nothing to check: it only reads",
        after="fix a stalled torrent in qBittorrent itself",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def qbittorrent_torrents(ctx, options: Options) -> Plan:
    user, password = ctx.secret("QBITTORRENT_USERNAME"), ctx.secret("QBITTORRENT_PASSWORD")
    for name, value in (("QBITTORRENT_USERNAME", user), ("QBITTORRENT_PASSWORD", password)):
        if not value:
            return Plan(notes=[f"{name} is not available; see `homelab secrets`"])
    try:
        opener = login(options, user, password)
        query = urllib.parse.urlencode({"filter": options.filter})
        torrents = sorted(
            json.loads(call(opener, options, f"/api/v2/torrents/info?{query}")), key=lambda t: t.get("name", "")
        )
        notes = [f"{len(torrents)} torrents ({options.filter})"]
        for t in torrents[: options.limit]:
            progress, ratio = 100 * t.get("progress", 0), t.get("ratio", 0)
            notes.append(f"{t.get('name', '?')} | {t.get('state', '?')} | {progress:.1f}% | {ratio:.2f}")
        if len(torrents) > options.limit:
            notes.append(f"... {len(torrents) - options.limit} more")
        return Plan(notes=notes)
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

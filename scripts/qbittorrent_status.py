# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Version and transfer speed of qBittorrent. Read only."""

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
    summary="qBittorrent version, transfer speeds and connection state (read only)",
    tags=["download", "media", "status"],
    status="stable",
    secrets=["QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"],
    options=Options,
    example={"url": "http://localhost:8080"},
    nudge=Nudge(
        when="you need to know whether qBittorrent is up and moving data",
        before="nothing to check: it only reads",
        after="list the torrents with qbittorrent_torrents",
        avoid="never use it to change anything; it has no write calls",
    ),
)
def qbittorrent_status(ctx, options: Options) -> Plan:
    user, password = ctx.secret("QBITTORRENT_USERNAME"), ctx.secret("QBITTORRENT_PASSWORD")
    for name, value in (("QBITTORRENT_USERNAME", user), ("QBITTORRENT_PASSWORD", password)):
        if not value:
            return Plan(notes=[f"{name} is not available; see `homelab secrets`"])
    try:
        opener = login(options, user, password)
        version = call(opener, options, "/api/v2/app/version").decode().strip()
        info = json.loads(call(opener, options, "/api/v2/transfer/info"))
        mib = 1024 * 1024
        return Plan(
            notes=[
                f"qBittorrent {version}",
                f"down {info.get('dl_info_speed', 0) / mib:.1f} MiB/s, "
                f"up {info.get('up_info_speed', 0) / mib:.1f} MiB/s",
                f"connection: {info.get('connection_status', '?')}",
            ]
        )
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])

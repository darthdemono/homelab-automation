# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Pause the torrents that are running (optionally only some by name), and undo by resuming exactly those."""

import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict

PAUSED = ("pausedDL", "pausedUP", "stoppedDL", "stoppedUP", "error", "missingFiles")


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    name_contains: str = ""  # only torrents whose name holds this text; empty means every running torrent


def login(options: Options, user: str, password: str):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = options.url.rstrip("/")
    form = urllib.parse.urlencode({"username": user, "password": password}).encode()
    request = urllib.request.Request(base + "/api/v2/auth/login", data=form, headers={"Referer": base})  # noqa: S310
    with opener.open(request, timeout=20) as response:
        if response.read().decode().strip() != "Ok.":
            raise ValueError("login failed")
    return opener


def post(opener, options: Options, path: str, fields: dict) -> bytes:
    base = options.url.rstrip("/")
    request = urllib.request.Request(  # noqa: S310
        base + path, data=urllib.parse.urlencode(fields).encode(), headers={"Referer": base}
    )
    with opener.open(request, timeout=20) as response:
        return response.read()


def get(opener, options: Options, path: str) -> bytes:
    base = options.url.rstrip("/")
    with opener.open(urllib.request.Request(base + path, headers={"Referer": base}), timeout=20) as response:  # noqa: S310
        return response.read()


def session(ctx, options: Options):
    user, password = ctx.secret("QBITTORRENT_USERNAME"), ctx.secret("QBITTORRENT_PASSWORD")
    if not user or not password:
        raise ValueError("QBITTORRENT_USERNAME and QBITTORRENT_PASSWORD must be available; see `homelab secrets`")
    return login(options, user, password)


def switch(opener, options: Options, hashes: list[str], stop: bool) -> None:
    """qBittorrent 5 renamed pause and resume to stop and start; try the new names, then the old."""
    fields = {"hashes": "|".join(hashes)}
    for path in (
        ("/api/v2/torrents/stop", "/api/v2/torrents/pause")
        if stop
        else ("/api/v2/torrents/start", "/api/v2/torrents/resume")
    ):
        try:
            post(opener, options, path, fields)
            return
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
    raise ValueError("this qBittorrent has neither the stop nor the pause call")


def apply_action(ctx, options: Options, action: Action) -> dict:
    switch(session(ctx, options), options, action.args["hashes"], stop=True)
    return {"hashes": action.args["hashes"]}


def undo_action(ctx, options: Options, data: dict) -> None:
    switch(session(ctx, options), options, data["hashes"], stop=False)


@script(
    summary="Pause the running torrents (all, or those whose name holds some text); undo resumes exactly those",
    tags=["download", "media", "pause"],
    status="stable",
    network=["the host in options.url"],
    secrets=["QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD"],
    options=Options,
    example={"url": "http://localhost:8080"},
    apply_action=apply_action,
    undo_action=undo_action,
    nudge=Nudge(
        when="downloads must stop for a while, for example before moving files or when the disk is nearly full",
        before="the plan lists the torrents; paused or errored ones are left out so undo cannot start them",
        after="check qBittorrent; `homelab undo latest --yes` resumes only the torrents this run paused",
        avoid="do not pause torrents that another job is working on; narrow it with name_contains",
    ),
)
def qbittorrent_pause(ctx, options: Options) -> Plan:
    try:
        opener = session(ctx, options)
        torrents = json.loads(get(opener, options, "/api/v2/torrents/info"))
    except (urllib.error.URLError, OSError, ValueError) as err:
        return Plan(notes=[f"{type(err).__name__}: {err}"])
    chosen = [
        t
        for t in torrents
        if t.get("state") not in PAUSED and options.name_contains.lower() in t.get("name", "").lower()
    ]
    if not chosen:
        return Plan(notes=["no running torrent matches"])
    names = ", ".join(t.get("name", "?") for t in chosen[:5]) + (" ..." if len(chosen) > 5 else "")
    return Plan(
        actions=[
            Action(
                kind="plugin",
                describe=f"pause {len(chosen)} torrents: {names}",
                args={"hashes": [t["hash"] for t in chosen]},
            )
        ]
    )

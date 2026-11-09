# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Rename and verify Jellyfin libraries against TVDB (series) and TMDB (movies), with TRaSH-guides naming.

Port of the standalone jellyFix tool. The matching and naming code is the same; applying and undoing is done by
the engine, so every move is reviewed first, journalled, and reversible. Nothing is deleted: empty folders and
folders that only hold regenerable Jellyfin metadata are quarantined by the `prune` job.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from homelab_plugin_api import Action, Job, Manifest, Permissions, Plan, Plugin
from pydantic import BaseModel, ConfigDict

from . import media, planner, providers
from .jobs_ops import JOBS as OPS_JOBS
from .stale_series import plan_stale_series

SIDECAR_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}
REGENERABLE = {".nfo", ".jpg", ".jpeg", ".png", ".xml", ".webp", ".nfo-orig", ".ini", ".bif", ".srt.bak"}
REGENERABLE_DIRS = (".trickplay",)
SECRETS = ["TVDB_API_KEY", "TMDB_V4", "TMDB_V3"]


class Library(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    path: str
    kind: str = "series"  # series | movie
    anime: bool = False  # adds the {absolute:000} token to episode names


class Options(BaseModel):
    model_config = ConfigDict(extra="allow")  # stack settings (root, urls, quarantine, ...) are read by the scripts
    libraries: list[Library] = []
    library: str = "all"
    args: list[str] = []  # extra flags for a maintenance script, for example ["--limit", "5"]
    lines: int = 60


def chosen(options: Options) -> list[Library]:
    if options.library == "all":
        return options.libraries
    hit = [lib for lib in options.libraries if lib.name.lower() == options.library.lower()]
    if not hit:
        raise ValueError(
            f"unknown library {options.library!r}; known: {', '.join(lib.name for lib in options.libraries)}"
        )
    return hit


def sidecars(src: str, dst: str) -> list[tuple[str, str]]:
    """Subtitle files that must travel with the video: 'movie.srt', 'movie.en.forced.srt'."""
    base = os.path.splitext(src)[0]
    out = []
    try:
        names = os.listdir(os.path.dirname(src) or ".")
    except OSError:
        return out
    for name in names:
        path = os.path.join(os.path.dirname(src), name)
        is_sidecar = os.path.isfile(path) and os.path.splitext(name)[1].lower() in SIDECAR_EXT
        if path != src and is_sidecar and path.startswith(base + "."):
            out.append((path, os.path.splitext(dst)[0] + path[len(base) :]))
    return out


def problems(moves: list[tuple[str, str]]) -> list[str]:
    found, seen = [], {}
    sources = {s for s, _ in moves}
    for src, dst in moves:
        if dst in seen:
            found.append(f"duplicate destination {dst} (from {seen[dst]} and {src})")
        seen[dst] = src
        if os.path.exists(dst) and os.path.normpath(dst) != os.path.normpath(src) and dst not in sources:
            found.append(f"destination exists: {dst}")
        if len(os.path.basename(dst).encode()) > 255:
            found.append(f"file name too long: {os.path.basename(dst)}")
    return found


def setup(ctx) -> dict:
    cache = ctx.path("state") / "cache"
    providers.set_cache_dir(cache)
    media.set_cache_dir(cache)
    return {name: ctx.secret(name) or "" for name in SECRETS}


def plan_rename(ctx, options: Options) -> Plan:
    env = setup(ctx)
    plan = Plan(notes=[])
    for lib in chosen(options):
        if not os.path.isdir(lib.path):
            plan.notes.append(f"{lib.name}: folder {lib.path} is missing")
            continue
        spec = lib.model_dump()
        if lib.kind == "series":
            moves, issues = planner.plan_series(spec, tvdb=providers.TVDB(env))
        else:
            moves, issues = planner.plan_movies(spec, tmdb=providers.TMDB(env))
        found = problems(moves)
        by_kind: dict[str, int] = {}
        for item in issues:
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        plan.notes.append(f"{lib.name}: {len(moves)} renames, issues {by_kind or 'none'}")
        plan.notes += [
            f"{lib.name} issue [{i['kind']}] {os.path.basename(i['path'])}: {i['detail']}" for i in issues[:20]
        ]
        if found:
            plan.notes += [f"{lib.name} BLOCKED: {p}" for p in found[:20]]
            continue
        for src, dst in moves:
            plan.actions.append(
                Action(
                    kind="move",
                    describe=f"{os.path.relpath(src, lib.path)} -> {os.path.relpath(dst, lib.path)}",
                    args={"src": src, "dst": dst},
                )
            )
            for s, d in sidecars(src, dst):
                plan.actions.append(
                    Action(kind="move", describe=f"subtitle {os.path.basename(s)} follows", args={"src": s, "dst": d})
                )
    media.save_cache()
    return plan


def plan_verify(ctx, options: Options) -> Plan:
    setup(ctx)
    plan = Plan(notes=[])
    for lib in chosen(options):
        files = planner.video_files(lib.path)
        infos = media.probe_many(files)
        broken = [(f, i) for f, i in infos.items() if not i["ok"]]
        short = [(f, i) for f, i in infos.items() if i["ok"] and i["duration"] and i["duration"] < 60]
        plan.notes.append(f"{lib.name}: {len(files)} files, {len(broken)} broken, {len(short)} very short")
        plan.notes += [f"BROKEN {os.path.relpath(f, lib.path)}: {i['error']}" for f, i in broken[:30]]
        plan.notes += [f"SHORT {os.path.relpath(f, lib.path)}: {i['duration']:.0f}s" for f, i in short[:10]]
    return plan


def orphans(root: str) -> list[str]:
    """Folders under root with no video, only regenerable Jellyfin metadata, or nothing at all."""
    out = []
    for name in sorted(os.listdir(root)):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder) or name.startswith("."):
            continue
        has_video, unknown, count = False, False, 0
        for dirpath, _dirs, files in os.walk(folder):
            for f in files:
                count += 1
                ext = os.path.splitext(f)[1].lower()
                if ext in media.VIDEO_EXT:
                    has_video = True
                elif ext not in REGENERABLE and not any(
                    seg.endswith(REGENERABLE_DIRS) for seg in dirpath.split(os.sep)
                ):
                    unknown = True
        if not has_video and not unknown:
            out.append(folder)
    return out


def plan_prune(ctx, options: Options) -> Plan:
    plan = Plan(notes=[])
    for lib in chosen(options):
        if not os.path.isdir(lib.path):
            continue
        found = orphans(lib.path)
        plan.notes.append(f"{lib.name}: {len(found)} folders hold no video and only regenerable metadata")
        plan.actions += [
            Action(kind="quarantine", describe=f"quarantine {Path(p).name}", args={"path": p}) for p in found
        ]
    return plan


PLUGIN = Plugin(
    manifest=Manifest(
        name="jellyfix",
        version="0.1.0",
        category="media",
        summary="Rename and verify Jellyfin libraries against TVDB and TMDB with TRaSH naming; every move is reversible",
        permissions=Permissions(
            reads=["{media}", "{state}"],
            writes=["{media}", "{state}"],
            network=["api4.thetvdb.com", "api.themoviedb.org"],
            exec=[sys.executable, "ffprobe", "docker", "unrar"],
        ),
        secrets=SECRETS,
    ),
    config_model=Options,
    jobs={
        "rename": Job(
            "Plan renames for one library or all (TVDB series, TMDB movies), subtitles follow their video", plan_rename
        ),
        "verify": Job("ffprobe every video and list broken or very short files (read only)", plan_verify),
        "prune": Job(
            "Quarantine folders that hold no video and only regenerable metadata (run after rename)", plan_prune
        ),
        "stale_series": Job(
            "Report series shown twice because an old folder sits beside the renamed one; quarantine its leftovers",
            plan_stale_series,
        ),
        **OPS_JOBS,
    },
    example={"libraries": [{"name": "Shows", "path": "/mnt/media/Shows", "kind": "series"}]},
)

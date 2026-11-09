# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Find series that a media server shows twice because an old release folder sits beside the renamed one.

After a rename sweep a series can live in `Title (Year) [tvdbid-N]` and also in a pre-rename folder that still holds
video. The server indexes both and keeps two entries. This reports each pair, what is left in the old folder, and
quarantines only the leftovers that are not video (artwork, nfo, thumbnails). Video that is still there needs a
decision: `consolidate_series` and `place_specials` move it into the renamed folder.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from homelab_plugin_api import Action, Plan

from . import media

TAG = re.compile(r"^(?P<title>.+?)(?: \((?P<year>\d{4})\))? \[(?:tvdbid|tmdbid)-\d+\]$")
REGENERABLE = {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".xml", ".bif"}


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def plan_stale_series(ctx, options) -> Plan:
    plan = Plan(notes=[])
    for lib in options.libraries:
        if lib.kind != "series" or not os.path.isdir(lib.path):
            continue
        folders = sorted(e for e in os.listdir(lib.path) if os.path.isdir(os.path.join(lib.path, e)))
        renamed = {}
        for name in folders:
            m = TAG.match(name)
            if m:
                renamed.setdefault(norm(m["title"]), name)
        for name in folders:
            if TAG.match(name) or norm(name) not in renamed:
                continue
            old = Path(lib.path) / name
            video = [p for p in old.rglob("*") if p.is_file() and p.suffix.lower() in media.VIDEO_EXT]
            others = [p for p in old.rglob("*") if p.is_file() and p.suffix.lower() in REGENERABLE]
            plan.notes.append(
                f"{lib.name}: {name!r} duplicates {renamed[norm(name)]!r}: {len(video)} video, {len(others)} regenerable files"
            )
            if video:
                plan.notes.append(f"  video is still there: run consolidate_series or place_specials for {name!r}")
                continue
            plan.actions += [
                Action(kind="quarantine", describe=f"quarantine {p.relative_to(lib.path)}", args={"path": str(p)})
                for p in others
            ]
    if not plan.notes:
        plan.notes.append("no series folder is duplicated by a renamed one")
    return plan

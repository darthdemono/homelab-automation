# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Small checks for the Navidrome artwork cleanup plan."""

from __future__ import annotations

import tempfile
from pathlib import Path

from homelab_musicfix.artwork import plan


def check(label, got, want):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


with tempfile.TemporaryDirectory() as temp:
    album = Path(temp) / "Artist" / "Album"
    album.mkdir(parents=True)
    for name in (
        "Track One.flac",
        "Track One.jpg",
        "Track Two.jpg",
        "cover.png",
        "banner.jpg",
        "Artist_Header.jpg",
        "album.nfo",
    ):
        (album / name).write_bytes(b"x")
    trash = Path(temp) / ".Trash-1000" / "files" / "Ignored"
    trash.mkdir(parents=True)
    (trash / "Ignored.flac").write_bytes(b"x")
    (trash / "album.nfo").write_bytes(b"x")
    actions = plan(temp)
    by_reason = {action["reason"]: action for action in actions}
    check("cover promoted", by_reason["album-cover"]["dst"].name, "folder.png")
    check("track art quarantined", sum(a["reason"] == "extra-cover" for a in actions), 2)
    check("Jellyfin art quarantined", sum(a["reason"] == "jellyfin-art" for a in actions), 2)
    check("nfo quarantined", by_reason["jellyfin-nfo"]["src"].name, "album.nfo")
    check("dot directories ignored", all(".Trash-1000" not in str(a["src"]) for a in actions), True)

print("artwork checks passed")

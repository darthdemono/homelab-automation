# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Move operating-system junk files out of a library into the quarantine folder."""

from pathlib import Path

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict

JUNK = ["Thumbs.db", "desktop.ini", ".DS_Store", "Folder.ico", "AlbumArtSmall.jpg"]


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    names: list[str] = JUNK


@script(
    summary="Quarantine Thumbs.db, desktop.ini, .DS_Store and similar junk files under a path",
    tags=["cleanup", "library"],
    status="stable",
    writes=["{media}"],
    options=Options,
    example={"path": "."},
    nudge=Nudge(
        when="a media or music library is cluttered with files the operating system created",
        before="check the list for a file you recognise; the plan only matches the exact names in `names`",
        after="rescan the library in the media server so it stops listing the folders",
        avoid="never run it on a folder you do not own; deleting real artwork is a different job",
    ),
)
def clean_junk_files(ctx, options: Options) -> Plan:
    root = Path(options.path).expanduser()
    wanted = {n.lower() for n in options.names}
    found = sorted(p for p in root.rglob("*") if p.is_file() and p.name.lower() in wanted)
    return Plan(
        actions=[Action(kind="quarantine", describe=f"quarantine {p}", args={"path": str(p)}) for p in found],
        notes=[f"{len(found)} junk files under {root}"],
    )

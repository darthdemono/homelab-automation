# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Clean, merge, tag and analyse a music library, one reviewable stage at a time.

Job tables live in `stages.py` (and any `jobs_*.py` module); a table exports `JOBS`, a dict of job name to `Job`.
Each stage keeps its own logic and journal: planning runs its dry run, applying runs it for real, and undoing runs
its own restore command (see bridge.py). `opsrt` is the operation runtime for stages written against the engine.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

from homelab_plugin_api import Job, Manifest, Permissions, Plugin
from pydantic import BaseModel, ConfigDict

from . import opsrt


class Options(BaseModel):
    """Common options; a stage may read more (`--set name=value` accepts any extra key, as a string)."""

    model_config = ConfigDict(extra="allow")
    limit: int = 0  # stop after N items, for trying a stage out
    artist: str = ""  # restrict a stage to one album artist
    path: str = ""  # a path under the library, or an input file, where a stage takes one
    lines: int = 60  # how many output lines a reading stage shows
    args: list[str] = []  # extra command-line flags for the stage itself, for example ["--limit", "50"]
    # rules for files another player leaves behind: {exts: [...], name_prefixes: [...], exact_names: [...], dirs: [...]}
    leftovers: dict = {}


def collect() -> dict[str, Job]:
    jobs: dict[str, Job] = {}
    for module in pkgutil.iter_modules(__path__):
        if not (module.name == "stages" or module.name.startswith("jobs_")):
            continue  # only job tables are imported here: a stage module reads its paths when it is imported
        found = getattr(importlib.import_module(f"{__name__}.{module.name}"), "JOBS", None)
        if found:
            clash = jobs.keys() & found.keys()
            if clash:
                raise ValueError(f"job names used twice: {sorted(clash)}")
            jobs.update(found)
    return jobs


PLUGIN = Plugin(
    manifest=Manifest(
        name="musicfix",
        version="0.1.0",
        category="music",
        summary="Clean, merge, tag and analyse a music library; every move and tag write is reviewed and undoable",
        permissions=Permissions(
            reads=["{music}", "{tmp}", "{state}"],
            writes=["{music}", "{tmp}", "{state}"],
            network=["the Navidrome and MusicBrainz hosts a stage is given"],
            exec=[sys.executable, "ffprobe", "ffmpeg", "docker"],
        ),
    ),
    config_model=Options,
    jobs=collect(),
    example={},
    apply_action=opsrt.apply_ops,
    undo_action=opsrt.undo_ops,
)

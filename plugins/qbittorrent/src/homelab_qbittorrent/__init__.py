# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Add, relink, rename, clean and file qBittorrent torrents, with the previous state recorded so it can be undone.

Each job is a module function; a module exports `JOBS`, a dict of job name to `Job`, and this file finds them.
"""

from __future__ import annotations

import importlib
import pkgutil

from homelab_plugin_api import Job, Manifest, Permissions, Plugin
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    """Common options; a job may read more (--set name=value accepts any extra key, as a string)."""

    model_config = ConfigDict(extra="allow")
    url: str = ""
    limit: int = 0
    lines: int = 60


def collect() -> dict[str, Job]:
    jobs: dict[str, Job] = {}
    for module in pkgutil.iter_modules(__path__):
        if module.name.startswith("_"):
            continue
        found = getattr(importlib.import_module(f"{__name__}.{module.name}"), "JOBS", None)
        if found:
            clash = jobs.keys() & found.keys()
            if clash:
                raise ValueError(f"job names used twice: {sorted(clash)}")
            jobs.update(found)
    return jobs


PLUGIN = Plugin(
    manifest=Manifest(
        name="qbittorrent",
        version="0.1.0",
        category="download",
        summary="Add, relink, rename, clean and file qBittorrent torrents; the previous state is recorded for undo",
        permissions=Permissions(reads=["{state}"], writes=["{state}"], network=["the host in options.url"]),
    ),
    config_model=Options,
    jobs=collect(),
    example={},
)

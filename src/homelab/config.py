# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The three YAML files: homelab.yaml (core), plugins.yaml (plugin blocks), jobs.yaml (pipelines)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class Secrets(BaseModel):
    """Where plugin secrets come from. `auto` tries the process environment, then UnENVerse
    (recommended), then a .env file (fallback). Values are never printed or stored."""

    model_config = ConfigDict(extra="forbid")
    provider: Literal["auto", "unv", "dotenv", "env"] = "auto"
    project: str | None = None  # `unv exec --project NAME`
    entries: dict[str, str] = {}  # ENV_VAR -> unv entry (PROVIDER), when the names differ
    dotenv: str = ".env"  # fallback file; refused unless only the owner can read it


class Core(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secrets: Secrets = Secrets()
    state_dir: str = "state"
    custom_dir: str = "custom"
    paths: dict[str, str] = {}
    schedule_wrapper: list[str] = []


class PluginBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    options: dict = {}


class Pipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[str]
    schedule: str | None = None
    apply: bool = False


class Plugins(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plugins: dict[str, PluginBlock] = {}


class Jobs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipelines: dict[str, Pipeline] = {}


class Config:
    def __init__(self, directory: Path, core: Core, plugins: Plugins, jobs: Jobs):
        self.directory = directory
        self.core = core
        self.plugins = plugins.plugins
        self.pipelines = jobs.pipelines

    def resolve(self, text: str) -> Path:
        path = Path(text).expanduser()
        return path if path.is_absolute() else (self.directory.parent / path).resolve()

    @property
    def state_dir(self) -> Path:
        return self.resolve(self.core.state_dir)

    @property
    def custom_dir(self) -> Path:
        return self.resolve(self.core.custom_dir)

    @property
    def paths(self) -> dict[str, Path]:
        return {key: self.resolve(value) for key, value in self.core.paths.items()}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def config_dir(given: str | None = None) -> Path:
    return Path(given or os.environ.get("HOMELAB_CONFIG") or "config").expanduser().resolve()


def load(directory: Path) -> Config:
    return Config(
        directory,
        Core(**_read(directory / "homelab.yaml")),
        Plugins(**_read(directory / "plugins.yaml")),
        Jobs(**_read(directory / "jobs.yaml")),
    )

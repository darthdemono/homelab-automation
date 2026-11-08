# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Public, versioned contract for homelab-automation plugins."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

API_VERSION = "1"

ActionKind = Literal["move", "write", "quarantine", "exec", "plugin"]


class Permissions(BaseModel):
    """What a plugin may touch. The engine refuses any action outside these."""

    model_config = ConfigDict(extra="forbid")
    reads: list[str] = []
    writes: list[str] = []
    network: list[str] = []
    exec: list[str] = []


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    version: str
    api_version: str = API_VERSION
    category: str
    summary: str
    permissions: Permissions = Permissions()
    secrets: list[str] = []


class Action(BaseModel):
    """One intended change.

    move        args: src, dst
    write       args: path, text
    quarantine  args: path (moved into the run's quarantine folder, never deleted)
    exec        args: argv, optional undo_argv
    plugin      args: anything; the plugin's apply_action and undo_action handle it
    """

    kind: ActionKind
    describe: str
    args: dict[str, Any] = {}


class Plan(BaseModel):
    job: str = ""
    actions: list[Action] = []
    notes: list[str] = []


@dataclass
class Context:
    plugin: str
    paths: Mapping[str, Path] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def path(self, key: str) -> Path:
        return self.paths[key]

    def secret(self, env_name: str) -> str | None:
        return self.env.get(env_name) or None


@dataclass
class Job:
    summary: str
    plan: Callable[[Context, Any], Plan]


@dataclass
class Plugin:
    manifest: Manifest
    config_model: type[BaseModel]
    jobs: dict[str, Job]
    example: dict[str, Any] = field(default_factory=dict)
    apply_action: Callable[[Context, Any, Action], dict[str, Any]] | None = None
    undo_action: Callable[[Context, Any, dict[str, Any]], None] | None = None

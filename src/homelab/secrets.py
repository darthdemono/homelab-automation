# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Secrets reach plugins as environment variables and nowhere else.

UnENVerse (`unv`) is the recommended source: the engine re-executes itself under
`unv exec`, so values enter the process environment and never touch stdout or a file.
A `.env` file is the fallback. Neither the engine nor the ledger ever prints a value.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from .config import Config
from .errors import Invalid, Unavailable
from .registry import Found

LOADED = "HOMELAB_SECRETS_LOADED"


def scrub(text: str, values: dict[str, str]) -> str:
    """Replace every secret value in `text` with [redacted:NAME]."""
    for name, value in values.items():
        text = text.replace(value, f"[redacted:{name}]")
    return text


def declared(config: Config, found: Found, plugins: list[str] | None = None) -> dict[str, list[str]]:
    """Secret names that enabled plugins declare, with the plugins that need each."""
    wanted: dict[str, list[str]] = {}
    for name, block in config.plugins.items():
        if not block.enabled or name not in found.plugins or (plugins is not None and name not in plugins):
            continue
        for secret in found.plugins[name].manifest.secrets:
            wanted.setdefault(secret, []).append(name)
    return wanted


def dotenv_values(path: Path, names: set[str]) -> dict[str, str]:
    """Read only the wanted names from a .env file the owner alone can read."""
    if not path.is_file():
        return {}
    if path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise Invalid(f"{path} is readable by other users; run chmod 600 on it")
    found = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        if key in names:
            found[key] = value.strip().strip("'\"")
    return found


def unv_args(config: Config, names: list[str]) -> list[str]:
    args = ["unv", "exec"]
    if config.core.secrets.project:
        args += ["--project", config.core.secrets.project]
    for name in names:
        entry = config.core.secrets.entries.get(name)
        if entry:
            provider, _, field = entry.partition(":")  # "provider" or "provider:field"
            args += ["--entry", f"{provider}={name}" + (f":{field}" if field else "")]
        else:
            args += ["--entry", name]
    return args


def sources(config: Config, names: list[str]) -> dict[str, str]:
    """Where each name would come from right now: env, unv, dotenv or missing. Reads no value."""
    cfg = config.core.secrets
    have_unv = shutil.which("unv") is not None
    file_names = set()
    if cfg.provider in ("auto", "dotenv"):
        try:
            file_names = set(dotenv_values(config.resolve(cfg.dotenv), set(names)))
        except Invalid:
            file_names = set()
    out = {}
    for name in names:
        if name in os.environ and cfg.provider != "dotenv":
            out[name] = "env"
        elif cfg.provider in ("auto", "unv") and have_unv and _probe_unv(config, name):
            out[name] = "unv"
        elif name in file_names:
            out[name] = "dotenv"
        else:
            out[name] = "missing"
    return out


def _probe_unv(config: Config, name: str) -> bool:
    result = subprocess.run([*unv_args(config, [name]), "--", "true"], capture_output=True, check=False)
    return result.returncode == 0


def load(config: Config, names: list[str], argv: list[str]) -> None:
    """Make the wanted secrets available in os.environ, or re-execute under `unv exec`."""
    cfg = config.core.secrets
    missing = [n for n in names if n not in os.environ]
    if not missing or cfg.provider == "env":
        return
    if cfg.provider in ("auto", "unv") and not os.environ.get(LOADED):
        if shutil.which("unv"):
            os.environ[LOADED] = "1"
            command = [*unv_args(config, missing), "--", sys.executable, "-m", "homelab.cli", *argv]
            os.execvp("unv", command)
        if cfg.provider == "unv":
            raise Unavailable(
                "secrets.provider is unv but `unv` is not on PATH (UnENVerse is recommended; or use dotenv)"
            )
    if cfg.provider in ("auto", "dotenv"):
        for key, value in dotenv_values(config.resolve(cfg.dotenv), set(missing)).items():
            os.environ.setdefault(key, value)

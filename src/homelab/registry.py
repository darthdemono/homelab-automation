# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Finds plugins: installed entry points plus the private custom/ folder."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path

from homelab_plugin_api import Plugin


@dataclass
class Found:
    plugins: dict[str, Plugin] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    origin: dict[str, str] = field(default_factory=dict)


def load_plugin_file(name: str, path: Path) -> Plugin:
    spec = importlib.util.spec_from_file_location(
        f"homelab_custom_{name}",
        path,
        submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.PLUGIN


def load_scripts(path: Path) -> list[Plugin]:
    """Every Plugin a script file defines (a decorated function, a script_exec call, or PLUGIN)."""
    spec = importlib.util.spec_from_file_location(f"homelab_script_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    found = [v for v in vars(module).values() if isinstance(v, Plugin) and v.manifest.category == "script"]
    if not found:
        raise ValueError("no script defined: decorate a function with @script or call script_exec")
    return found


def discover(custom_dir: Path, script_dirs: list[Path] | None = None) -> Found:
    found = Found()

    def add(key: str, origin: str, loader) -> None:
        try:
            plugin = loader()
            if plugin.manifest.name in found.plugins:
                raise ValueError(f"name {plugin.manifest.name!r} already used by {found.origin[plugin.manifest.name]}")
            found.plugins[plugin.manifest.name] = plugin
            found.origin[plugin.manifest.name] = origin
        except Exception as err:
            found.errors[key] = f"{type(err).__name__}: {err}"

    for entry in entry_points(group="homelab.plugins"):
        add(entry.name, "installed", entry.load)
    if custom_dir.is_dir():
        for item in sorted(custom_dir.iterdir()):
            if item.name.startswith((".", "_")):
                continue
            if item.is_dir() and (item / "__init__.py").exists():
                add(item.name, "custom", lambda i=item: load_plugin_file(i.name, i / "__init__.py"))
            elif item.suffix == ".py":
                add(item.stem, "custom", lambda i=item: load_plugin_file(i.stem, i))
    for folder, origin in zip(script_dirs or [], ("script", "private-script"), strict=False):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith(("_", ".")):
                continue
            try:
                scripts = load_scripts(path)
            except Exception as err:
                found.errors[f"script:{path.stem}"] = f"{type(err).__name__}: {err}"
                continue
            for plugin in scripts:
                name = plugin.manifest.name
                if name in found.plugins:
                    found.errors[f"script:{path.stem}"] = f"name {name!r} already used by {found.origin[name]}"
                    continue
                found.plugins[name] = plugin
                found.origin[name] = origin
    return found

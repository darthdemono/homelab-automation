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


def discover(custom_dir: Path) -> Found:
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
    return found

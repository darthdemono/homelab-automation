# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""`homelab plugin new`: a private plugin starts as its own folder with a manifest, tests and a README."""

from __future__ import annotations

from pathlib import Path

INIT = '''"""{name}: describe what this plugin does in one line."""

from homelab_plugin_api import Job, Manifest, Permissions, Plan, Plugin
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")


def plan(ctx, options):
    return Plan(notes=["nothing to do yet"])


PLUGIN = Plugin(
    manifest=Manifest(
        name="{name}",
        version="0.1.0",
        category="custom",
        summary="describe this plugin",
        permissions=Permissions(),
    ),
    config_model=Options,
    jobs={{"check": Job(summary="a first job", plan=plan)}},
)
'''

TEST = """from pathlib import Path

from homelab.conformance import check
from homelab.registry import load_plugin_file


def test_conforms():
    plugin = load_plugin_file("{name}", Path(__file__).parent.parent / "__init__.py")
    assert check("{name}", plugin) == []
"""


def create(custom_dir: Path, name: str) -> Path:
    if not name.isidentifier():
        raise ValueError("plugin names are python identifiers, for example my_plugin")
    folder = custom_dir / name
    if folder.exists():
        raise FileExistsError(folder)
    (folder / "tests").mkdir(parents=True)
    (folder / "__init__.py").write_text(INIT.format(name=name))
    (folder / "tests" / "test_plugin.py").write_text(TEST.format(name=name))
    (folder / "README.md").write_text(f"# {name}\n\nPrivate plugin. Options live in `plugins.yaml` under `{name}`.\n")
    return folder


SCRIPT = '''"""{name}: describe what this script does in one line."""

from homelab_plugin_api import Nudge, Plan, script


@script(
    summary="describe what this script does",
    tags=["todo"],
    status="one-off",  # one-off, then stable once it has run twice with a passing test, then retired
    writes=["{{media}}"],  # path prefixes or {{names}} from homelab.yaml; the engine refuses anything else
    nudge=Nudge(
        when="when to reach for this script",
        before="what to check in the plan before applying",
        after="how to verify afterwards",
        avoid="when not to use it, and what to use instead",
    ),
)
def {name}(ctx, options) -> Plan:
    # Return actions instead of doing things: move, write, quarantine or exec. The engine applies exactly
    # the reviewed plan, records it, and can undo it. If this grows past one file, make it a plugin.
    return Plan(notes=["nothing to do yet"])
'''


def create_script(folder: Path, name: str) -> Path:
    if not name.isidentifier():
        raise ValueError("script names are python identifiers, for example fix_season_names")
    path = folder / f"{name}.py"
    if path.exists():
        raise FileExistsError(path)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(SCRIPT.format(name=name))
    return path

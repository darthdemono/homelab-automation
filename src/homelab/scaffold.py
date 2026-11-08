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

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import sys
import textwrap

import pytest
import yaml

from homelab.config import load
from homelab.registry import discover
from homelab.runner import Runner

PLUGIN = textwrap.dedent(
    """
    import sys
    from homelab_plugin_api import Action, Job, Manifest, Permissions, Plan, Plugin
    from pydantic import BaseModel, ConfigDict


    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = "a.txt"


    def tidy(ctx, options):
        lib = ctx.path("lib")
        return Plan(actions=[
            Action(kind="move", describe="move file", args={"src": str(lib / options.name), "dst": str(lib / "sub" / options.name)}),
            Action(kind="write", describe="write note", args={"path": str(lib / "note.txt"), "text": "hello"}),
            Action(kind="quarantine", describe="quarantine junk", args={"path": str(lib / "junk.txt")}),
        ])


    def escape(ctx, options):
        return Plan(actions=[Action(kind="move", describe="leave the library", args={"src": str(ctx.path("lib") / "a.txt"), "dst": "/tmp/outside.txt"})])


    def run(ctx, options):
        return Plan(actions=[Action(kind="exec", describe="run python", args={"argv": [sys.executable, "-c", "pass"]})])


    def custom(ctx, options):
        return Plan(actions=[Action(kind="plugin", describe="custom action", args={"value": 7})])


    LOG = []


    def apply_action(ctx, options, action):
        LOG.append(("apply", action.args["value"]))
        return {"value": action.args["value"]}


    def undo_action(ctx, options, data):
        LOG.append(("undo", data["value"]))


    PLUGIN = Plugin(
        manifest=Manifest(name="demo", version="1.0.0", category="test", summary="test plugin",
                          permissions=Permissions(writes=["{lib}"], exec=[sys.executable])),
        config_model=Options,
        jobs={"tidy": Job("tidy", tidy), "escape": Job("escape", escape), "run": Job("run", run), "custom": Job("custom", custom)},
        apply_action=apply_action,
        undo_action=undo_action,
    )
    """
)


@pytest.fixture
def env(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.txt").write_text("a")
    (tmp_path / "lib" / "junk.txt").write_text("junk")
    (tmp_path / "custom").mkdir()
    (tmp_path / "custom" / "demo.py").write_text(PLUGIN)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp_path / "state"),
                "custom_dir": str(tmp_path / "custom"),
                "scripts_dir": "scripts",
                "paths": {"lib": str(tmp_path / "lib")},
            }
        )
    )
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"demo": {"options": {}}}}))
    (cfg / "jobs.yaml").write_text(
        yaml.dump({"pipelines": {"nightly": {"steps": ["demo.tidy"], "schedule": "*-*-* 04:00:00"}}})
    )
    config = load(cfg)
    found = discover(config.custom_dir)
    return tmp_path, cfg, config, found, Runner(config, found)


def sysexe():
    return sys.executable

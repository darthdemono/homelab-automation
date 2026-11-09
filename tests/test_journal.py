# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The journal action: many moves in one step, a journal written as it goes, undo replays it backwards."""

import json
import textwrap

import pytest
import yaml

from homelab.cli import main

PLUGIN = textwrap.dedent(
    """
    from homelab_plugin_api import Job, Manifest, Permissions, Plan, Plugin, journal_action
    from pydantic import BaseModel, ConfigDict


    class Options(BaseModel):
        model_config = ConfigDict(extra="forbid")
        count: int = 3
        outside: bool = False


    def shuffle(ctx, options):
        lib = ctx.path("lib")
        moves = [(str(lib / f"f{i}.txt"), str(lib / "out" / f"f{i}.txt")) for i in range(options.count)]
        if options.outside:
            moves.append((str(lib / "f0.txt"), "/tmp/outside.txt"))
        return Plan(actions=[journal_action(ctx, moves, "shuffle")])


    PLUGIN = Plugin(
        manifest=Manifest(name="jplug", version="1.0.0", category="test", summary="journal test",
                          permissions=Permissions(writes=["{lib}", "{state}"])),
        config_model=Options,
        jobs={"shuffle": Job("shuffle", shuffle)},
    )
    """
)


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


@pytest.fixture
def setup(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    for i in range(3):
        (lib / f"f{i}.txt").write_text(str(i))
    (tmp_path / "custom").mkdir()
    (tmp_path / "custom" / "jplug.py").write_text(PLUGIN)
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {"state_dir": str(tmp_path / "state"), "custom_dir": str(tmp_path / "custom"), "paths": {"lib": str(lib)}}
        )
    )
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"jplug": {}}}))
    return tmp_path, cfg, lib


def test_apply_moves_everything_and_undo_puts_it_back(setup):
    tmp, cfg, lib = setup
    assert run(cfg, "plan", "jplug.shuffle") == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert sorted(p.name for p in (lib / "out").iterdir()) == ["f0.txt", "f1.txt", "f2.txt"]
    journals = list((tmp / "state" / "runs").glob("*.journal.jsonl"))
    assert len(journals) == 1 and len(journals[0].read_text().splitlines()) == 3
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert sorted(p.name for p in lib.glob("f*.txt")) == ["f0.txt", "f1.txt", "f2.txt"]


def test_a_move_outside_the_declared_writes_is_refused(setup):
    tmp, cfg, lib = setup
    assert run(cfg, "plan", "jplug.shuffle", "--set", "outside=true") == 5
    assert (lib / "f0.txt").exists()


def test_a_file_changed_since_the_plan_stops_everything(setup):
    tmp, cfg, lib = setup
    assert run(cfg, "plan", "jplug.shuffle") == 0
    (lib / "f2.txt").unlink()
    assert run(cfg, "apply", "latest", "--yes") == 6
    assert (lib / "f0.txt").exists()


def test_a_run_that_dies_half_way_can_still_be_undone(setup):
    tmp, cfg, lib = setup
    assert run(cfg, "plan", "jplug.shuffle") == 0
    # the file disappears after the check but before its move: the third move fails
    from homelab import runner

    real = runner.shutil.move
    seen = []

    def flaky(src, dst):
        seen.append(src)
        if len(seen) == 3:
            raise OSError("disk went away")
        return real(src, dst)

    runner.shutil.move = flaky
    try:
        assert run(cfg, "apply", "latest", "--yes") != 0
    finally:
        runner.shutil.move = real
    record = json.loads(next((tmp / "state" / "runs").glob("*.json")).read_text())
    assert record["status"] == "failed"
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert sorted(p.name for p in lib.glob("f*.txt")) == ["f0.txt", "f1.txt", "f2.txt"]

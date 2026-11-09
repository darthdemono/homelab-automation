# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The musicfix operation runtime: planned ops are validated first, journalled, and undone exactly."""

import json
import struct
import textwrap

import pytest
import yaml
from homelab_musicfix import opsrt, tagio

from homelab.cli import main

STUB = textwrap.dedent(
    """
    import json
    from pathlib import Path
    from homelab_plugin_api import Job, Plan
    from homelab_musicfix import opsrt, settings

    OPS = []

    def plan(ctx, options):
        settings.prepare(ctx)
        music = ctx.path("music")
        ops = json.loads((music / "ops.json").read_text())
        return Plan(actions=[opsrt.ops_action(ctx, ops, "test")])

    JOBS = {"stubops": Job("stub", plan)}
    """
)


def flac(path):
    """A minimal valid FLAC file (STREAMINFO only) that mutagen can open and tag."""
    info = struct.pack(">HHBBBBBB", 4096, 4096, 0, 0, 0, 0x0A, 0xC4, 0x42) + b"\xf0\x00\x00\x00\x00" + bytes(16)
    header = b"fLaC" + bytes([0x80]) + struct.pack(">I", 34)[1:]
    path.write_bytes(header + info[:34].ljust(34, b"\x00"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    music, tmp = tmp_path / "music", tmp_path / "tmp"
    (music / "A").mkdir(parents=True)
    tmp.mkdir()
    flac(music / "A" / "one.flac")
    plug = tmp_path / "pkg"
    plug.mkdir()
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp_path / "state"),
                "custom_dir": str(tmp_path / "custom"),
                "paths": {"music": str(music), "tmp": str(tmp)},
            }
        )
    )
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"musicfix": {}}}))
    return tmp_path, cfg, music


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def plan_ops(lib, ops, monkeypatch):
    tmp_path, cfg, music = lib
    (music / "ops.json").write_text(json.dumps(ops))
    monkeypatch.setitem(
        __import__("homelab_musicfix").PLUGIN.jobs,
        "stubops",
        __import__("homelab_plugin_api").Job(
            "stub", lambda ctx, o: __import__("homelab_plugin_api").Plan(actions=[opsrt.ops_action(ctx, ops, "test")])
        ),
    )
    return run(cfg, "plan", "musicfix.stubops", "--json")


def test_tags_round_trip_through_the_runtime(lib, monkeypatch, capsys):
    tmp_path, cfg, music = lib
    target = str(music / "A" / "one.flac")
    ops = [{"op": "tags", "path": target, "before": {}, "after": {"artist": ["X", "Y"], "genre": ["Rock"]}}]
    assert plan_ops(lib, ops, monkeypatch) == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert tagio.read(target, ["artist", "genre"]) == {"artist": ["X", "Y"], "genre": ["Rock"]}
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert tagio.read(target, ["artist", "genre"]) == {}


def test_a_move_then_undo(lib, monkeypatch):
    tmp_path, cfg, music = lib
    src, dst = str(music / "A" / "one.flac"), str(music / "B" / "one.flac")
    assert plan_ops(lib, [{"op": "move", "src": src, "dst": dst}], monkeypatch) == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert (music / "B" / "one.flac").exists()
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (music / "A" / "one.flac").exists()


def test_an_op_outside_the_library_is_refused_before_anything_runs(lib, monkeypatch):
    tmp_path, cfg, music = lib
    ops = [
        {"op": "move", "src": str(music / "A" / "one.flac"), "dst": str(music / "B" / "one.flac")},
        {"op": "create", "path": "/elsewhere/file.txt", "text": "x"},
    ]
    assert plan_ops(lib, ops, monkeypatch) == 0
    assert run(cfg, "apply", "latest", "--yes") != 0
    assert (music / "A" / "one.flac").exists()


def test_tags_changed_since_the_plan_stop_the_run(lib, monkeypatch):
    tmp_path, cfg, music = lib
    target = str(music / "A" / "one.flac")
    ops = [{"op": "tags", "path": target, "before": {}, "after": {"genre": ["Rock"]}}]
    assert plan_ops(lib, ops, monkeypatch) == 0
    tagio.write(target, {"genre": ["Jazz"]})
    assert run(cfg, "apply", "latest", "--yes") != 0
    assert tagio.read(target, ["genre"]) == {"genre": ["Jazz"]}


def test_create_and_replace_are_undone(lib, monkeypatch):
    tmp_path, cfg, music = lib
    note = music / "A" / "note.txt"
    note.write_text("old")
    ops = [
        {"op": "create", "path": str(music / "A" / "new.txt"), "text": "hi"},
        {"op": "replace", "path": str(note), "text": "new"},
    ]
    assert plan_ops(lib, ops, monkeypatch) == 0
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert note.read_text() == "new" and (music / "A" / "new.txt").read_text() == "hi"
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert note.read_text() == "old" and not (music / "A" / "new.txt").exists()

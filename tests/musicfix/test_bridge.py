# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""A real stage end to end on a tiny library: scan, plan a tag fix, apply it, undo it."""

import json
import shutil
import subprocess

import pytest
import yaml
from homelab_musicfix import tagio

from homelab.cli import main

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg")


def make_flac(path, artist):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            "1",
            "-metadata",
            f"artist={artist}",
            "-metadata",
            "title=Song",
            "-metadata",
            "album=Album",
            str(path),
        ],
        check=True,
    )


@pytest.fixture
def lib(tmp_path):
    music, tmp = tmp_path / "music", tmp_path / "tmp"
    make_flac(music / "Alpha" / "Album" / "Alpha - Song.flac", "Alpha; Beta")
    tmp.mkdir()
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
    return cfg, music


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def test_scan_then_fixtags_then_undo(lib, capsys):
    cfg, music = lib
    song = music / "Alpha" / "Album" / "Alpha - Song.flac"
    assert run(cfg, "plan", "musicfix.scan", "--json") == 0
    assert "1 audio" in " ".join(json.loads(capsys.readouterr().out)["data"]["plan"]["notes"])
    assert run(cfg, "plan", "musicfix.fixtags", "--json") == 0
    plan = json.loads(capsys.readouterr().out)["data"]["plan"]
    assert [a["kind"] for a in plan["actions"]] == ["exec"] and "undo_argv" in plan["actions"][0]["args"]
    assert tagio.read(song, ["artist"]) == {"artist": ["Alpha; Beta"]}
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert tagio.read(song, ["artist"]) == {"artist": ["Alpha", "Beta"]}
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert tagio.read(song, ["artist"]) == {"artist": ["Alpha; Beta"]}


def test_a_read_only_stage_has_no_actions(lib, capsys):
    cfg, _ = lib
    assert run(cfg, "plan", "musicfix.scan") == 0
    capsys.readouterr()
    assert run(cfg, "plan", "musicfix.report", "--json") == 0
    assert json.loads(capsys.readouterr().out)["data"]["plan"]["actions"] == []

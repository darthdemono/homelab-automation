# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The jellyfix plugin with the metadata lookups replaced, so no network and no ffprobe is needed."""

import json

import pytest
import yaml
from homelab_jellyfix import planner

from homelab.cli import main

SHOW = "Show.S01E01.mkv"


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def data(capsys):
    return json.loads(capsys.readouterr().out)["data"]


@pytest.fixture
def lib(env, monkeypatch):
    tmp, cfg = env[0], env[1]
    media = tmp / "media"
    (media / "Shows" / "raw").mkdir(parents=True)
    (media / "Shows" / "raw" / SHOW).write_text("video")
    (media / "Shows" / "raw" / "Show.S01E01.en.srt").write_text("sub")
    home = yaml.safe_load((cfg / "homelab.yaml").read_text())
    home["paths"]["media"] = str(media)
    (cfg / "homelab.yaml").write_text(yaml.dump(home))
    (cfg / "plugins.yaml").write_text(
        yaml.dump(
            {
                "plugins": {
                    "demo": {},
                    "jellyfix": {"options": {"libraries": [{"name": "Shows", "path": str(media / "Shows")}]}},
                }
            }
        )
    )
    src = str(media / "Shows" / "raw" / SHOW)
    dst = str(media / "Shows" / "Show (2020)" / "Season 01" / "Show (2020) - S01E01.mkv")
    monkeypatch.setattr(planner, "plan_series", lambda spec, tvdb=None, tmdb=None, anime=None: ([(src, dst)], []))
    for name in ("TVDB_API_KEY", "TMDB_V4", "TMDB_V3"):
        monkeypatch.setenv(name, "x" * 12)
    return media, cfg, src, dst


def test_rename_moves_video_and_subtitle_then_undo_restores(lib, capsys):
    media, cfg, src, dst = lib
    assert run(cfg, "plan", "jellyfix.rename", "--json") == 0
    plan = data(capsys)["plan"]
    assert len(plan["actions"]) == 2
    assert run(cfg, "apply", "latest", "--yes") == 0
    folder = media / "Shows" / "Show (2020)" / "Season 01"
    assert sorted(p.name for p in folder.iterdir()) == ["Show (2020) - S01E01.en.srt", "Show (2020) - S01E01.mkv"]
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (media / "Shows" / "raw" / SHOW).read_text() == "video"
    assert (media / "Shows" / "raw" / "Show.S01E01.en.srt").exists()


def test_a_blocked_plan_has_no_actions(lib, capsys, monkeypatch):
    media, cfg, src, dst = lib
    (media / "Shows" / "raw" / "other.mkv").write_text("v")
    other = str(media / "Shows" / "raw" / "other.mkv")
    monkeypatch.setattr(
        planner, "plan_series", lambda spec, tvdb=None, tmdb=None, anime=None: ([(src, dst), (other, dst)], [])
    )
    assert run(cfg, "plan", "jellyfix.rename", "--json") == 0
    plan = data(capsys)["plan"]
    assert plan["actions"] == [] and any("BLOCKED" in n for n in plan["notes"])


def test_unknown_library_is_an_error(lib, capsys):
    media, cfg, *_ = lib
    assert run(cfg, "plan", "jellyfix.rename", "--set", "library=Nope", "--json") != 0


def test_prune_quarantines_metadata_only_folders(lib, capsys):
    media, cfg, *_ = lib
    gone = media / "Shows" / "Old Show"
    gone.mkdir()
    (gone / "tvshow.nfo").write_text("x")
    (gone / "poster.jpg").write_text("x")
    assert run(cfg, "plan", "jellyfix.prune", "--json") == 0
    plan = data(capsys)["plan"]
    names = [a["describe"] for a in plan["actions"]]
    assert names == ["quarantine Old Show"]
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert not gone.exists()
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (gone / "tvshow.nfo").exists()

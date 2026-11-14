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


# ---- the fixed naming convention and the TVDB fixes of 13-11-2026
def test_episode_name_has_one_numbering_and_no_title():
    from homelab_jellyfix import naming

    info = {"width": 1920, "height": 1080, "bit_depth": 8, "vcodec": "h264", "acodec": "eac3", "channels": 6}
    name = naming.episode_name("Show", 2020, 1, 5, "A Long Title", info, source="WEBDL", group="GRP", absolute=415)
    assert name.startswith("Show (2020) - S01E05") and "415" not in name and "A Long Title" not in name
    assert naming.episode_name("Show", 2020, 5, 25, "x", info, episode_end=38).startswith("Show (2020) - S05E25-E38")


def test_english_title_accepts_alias_objects_and_strings():
    from homelab_jellyfix import planner

    native = "ブラッククローバー"
    assert (
        planner.english_title(
            {
                "name": native,
                "aliases": [{"language": "jpn", "name": native}, {"language": "eng", "name": "Black Clover"}],
            }
        )
        == "Black Clover"
    )
    assert planner.english_title({"name": native, "aliases": [native, "Black Clover"]}) == "Black Clover"
    assert planner.english_title({"name": "Plain", "aliases": [{"name": "Other"}]}) == "Plain"


def test_a_folder_that_already_carries_its_id_keeps_its_own_case(monkeypatch):
    from homelab_jellyfix import planner

    class Tvdb:
        def series(self, sid):
            return {"name": "Bleach", "firstAired": "2004-10-05"}

    sid, title, year = planner._identify_series(
        "BLEACH (2004) [tvdbid-74796]", "/x/BLEACH (2004) [tvdbid-74796]", Tvdb(), []
    )
    assert (sid, title, year) == (74796, "BLEACH", "2004")  # only the case differs, so the folder's spelling wins
    sid, title, _ = planner._identify_series("Other (2004) [tvdbid-74796]", "/x", Tvdb(), [])
    assert title == "Bleach"  # a different word is not overridden


def test_tvdb_series_adds_the_english_translation(monkeypatch):
    from homelab_jellyfix import providers

    calls = []

    def fake_auth_get(self, path):
        calls.append(path)
        if path.endswith("/translations/eng"):
            return {"data": {"name": "Black Clover"}}
        return {"data": {"name": "ブラッククローバー"}}

    monkeypatch.setattr(providers.TVDB, "_auth_get", fake_auth_get)
    monkeypatch.setattr(providers, "_cached", lambda key, go, ttl=0: go())
    info = providers.TVDB({"TVDB_API_KEY": "k"}).series(331753)
    assert info["translations"]["eng"] == "Black Clover" and calls == [
        "/series/331753",
        "/series/331753/translations/eng",
    ]

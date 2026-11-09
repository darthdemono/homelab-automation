# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The maintenance scripts as jobs: every script loads, and one runs end to end with undo."""

import glob
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from homelab.cli import main

ROOT = Path(__file__).resolve().parent.parent.parent
OPS = sorted(glob.glob(str(ROOT / "plugins" / "*" / "src" / "*" / "ops" / "*.py")))


@pytest.mark.parametrize("path", [p for p in OPS if not p.endswith("__init__.py")], ids=lambda p: Path(p).stem)
def test_every_script_loads_without_running(path, tmp_path):
    """Import only (never run as __main__): several scripts configure live services when run."""
    plugin = Path(path).parents[1].name
    module = f"{plugin}.ops.{Path(path).stem}"
    fake = {}
    for app in ("sonarr", "radarr", "prowlarr"):
        (tmp_path / app).mkdir()
        (tmp_path / app / "config.xml").write_text("<Config><ApiKey>0123456789abcdef</ApiKey></Config>")
        fake[app] = str(tmp_path / app / "config.xml")
    code = (
        "import importlib, json, sys\n"
        "from homelab_jellyfix import stack\n"
        f"stack.configure({{'root': {str(tmp_path)!r}, 'state': {str(tmp_path / 'state')!r}, 'config_files': {fake!r}}})\n"
        f"importlib.import_module({module!r})\n"
    )
    env = {"PATH": "/usr/bin:/bin", "JELLYFIN_KEY": "x", "QUI_API_KEY": "x", "HOME": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False, env=env
    )  # noqa: S603
    assert done.returncode == 0, done.stderr[-500:]


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


@pytest.fixture
def lib(tmp_path, monkeypatch):
    for name in ("TVDB_API_KEY", "TMDB_V4", "TMDB_V3"):
        monkeypatch.setenv(name, "x" * 12)
    media = tmp_path / "media"
    shows = media / "Shows"
    (shows / "Old Release" / "extras").mkdir(parents=True)
    (shows / "Old Release" / "poster.jpg").write_text("x")
    (shows / "Real Show (2020) [tvdbid-1]").mkdir()
    (shows / "Real Show (2020) [tvdbid-1]" / "ep.mkv").write_text("v")
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp_path / "state"),
                "custom_dir": str(tmp_path / "custom"),
                "paths": {"media": str(media)},
            }
        )
    )
    libs = [{"name": "Shows", "path": str(shows), "kind": "series"}]
    (cfg / "plugins.yaml").write_text(
        yaml.dump({"plugins": {"jellyfix": {"options": {"libraries": libs, "root": str(media)}}}})
    )
    return cfg, shows


def test_prune_dead_dirs_plans_applies_and_undoes(lib, capsys):
    cfg, shows = lib
    assert run(cfg, "plan", "jellyfix.prune_dead_dirs", "--json") == 0
    plan = json.loads(capsys.readouterr().out)["data"]["plan"]
    assert [a["kind"] for a in plan["actions"]] == ["exec"] and "undo_argv" in plan["actions"][0]["args"]
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert not (shows / "Old Release").exists()
    assert (shows / "Real Show (2020) [tvdbid-1]" / "ep.mkv").exists()
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (shows / "Old Release" / "poster.jpg").exists()


def test_stale_series_quarantines_leftovers_of_a_duplicated_folder(lib, capsys):
    cfg, shows = lib
    (shows / "Real Show").mkdir()
    (shows / "Real Show" / "poster.jpg").write_text("x")
    assert run(cfg, "plan", "jellyfix.stale_series", "--json") == 0
    plan = json.loads(capsys.readouterr().out)["data"]["plan"]
    assert [a["kind"] for a in plan["actions"]] == ["quarantine"]
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert not (shows / "Real Show" / "poster.jpg").exists()
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (shows / "Real Show" / "poster.jpg").exists()

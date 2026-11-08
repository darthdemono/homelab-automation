# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import json
import re
import tomllib
from pathlib import Path

from homelab.cli import main

ROOT = Path(__file__).resolve().parent.parent


def run(*argv):
    return main(list(argv))


def test_the_packaged_skill_matches_the_skill_in_the_repository():
    assert (ROOT / "src" / "homelab" / "homelab-ops.md").read_text() == (
        ROOT / "skills" / "homelab-ops" / "SKILL.md"
    ).read_text()


def test_skill_prints_and_installs(tmp_path, capsys):
    assert run("skill") == 0 and "homelab-ops" in capsys.readouterr().out
    assert run("skill", "--install", str(tmp_path), "--json") == 0
    assert (tmp_path / "homelab-ops" / "SKILL.md").read_text().startswith("---")


def test_the_demo_sandbox_runs_the_whole_loop(tmp_path, capsys):
    target = tmp_path / "d"
    assert run("demo", "--dir", str(target), "--json") == 0
    steps = json.loads(capsys.readouterr().out)["data"]["try"]
    cfg = str(target / "config")
    assert main(["--config", cfg, "plan", "clean_junk_files", "--set", f"path={target / 'media'}"]) == 0
    assert main(["--config", cfg, "apply", "latest", "--yes"]) == 0
    assert (
        not (target / "media" / "Thumbs.db").exists()
        and (target / "media" / "Music" / "Album" / "01-track.flac").exists()
    )
    assert main(["--config", cfg, "undo", "latest", "--yes"]) == 0
    assert (target / "media" / "Thumbs.db").exists() and len(steps) == 4
    assert run("demo", "--dir", str(target)) == 6


def test_manifest_versions_match_the_project_and_are_valid_json():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", "gemini-extension.json"):
        assert json.loads((ROOT / name).read_text())["version"] == version, name
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["plugins"][0]["version"] == version and (ROOT / "skills" / "homelab-ops" / "SKILL.md").exists()


def test_llms_txt_links_only_files_that_exist():
    text = (ROOT / "llms.txt").read_text()
    links = re.findall(r"\]\(([^)]+)\)", text)
    assert links and all((ROOT / link).exists() for link in links)

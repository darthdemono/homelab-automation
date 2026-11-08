# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import json
import subprocess
from pathlib import Path

import yaml

from homelab.cli import main

ROOT = Path(__file__).resolve().parent.parent


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def test_the_history_and_notes_of_an_old_repo_come_in_locally(env, capsys):
    tmp, cfg = env[0], env[1]
    old = tmp / "old"
    (old / "docs").mkdir(parents=True)
    (old / "handoff-1.md").write_text("# Handoff 1\n\nfirst session\n")
    (old / "docs" / "notes.md").write_text("Plain note\n")
    (old / "secret.bin").write_text("not a note")
    git(old, "init", "-q", "-b", "work")
    git(old, "config", "user.email", "t@example.com")
    git(old, "config", "user.name", "t")
    git(old, "add", "-A")
    git(old, "commit", "-q", "-m", "First change")
    project = tmp / "project"
    project.mkdir()
    git(project, "init", "-q", "-b", "main")
    agents = tmp / "agents"
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "scripts"),
                "paths": {"lib": str(tmp / "lib"), "project": str(project), "agents": str(agents)},
            }
        )
    )
    assert run(cfg, "plan", "import_repo_history", "--set", f"repo={old}", "--set", "name=old", "--json") == 0
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["plan"]["notes"] == ["2 notes, 1 branches, local only"] and data["ask_owner"] is True
    assert not (agents / "history").exists()
    assert run(cfg, "apply", "latest", "--yes") == 0
    target = agents / "history" / "old"
    assert (target / "handoff-1.md").read_text().startswith("# Handoff 1")
    assert (target / "docs__notes.md").exists() and not (target / "secret.bin").exists()
    index = (target / "INDEX.md").read_text()
    assert "history/full/old/<branch>" in index and "`docs__notes.md`" in index and "First change" in index
    branches = subprocess.run(["git", "-C", str(project), "branch", "--list"], capture_output=True, text=True).stdout
    assert "history/full/old/work" in branches
    assert run(cfg, "apply", "latest", "--yes") == 6  # an applied run cannot be applied twice


def test_a_non_repository_is_a_note_not_an_action(env, capsys):
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "scripts"),
                "paths": {"lib": str(tmp / "lib"), "project": str(tmp), "agents": str(tmp / "agents")},
            }
        )
    )
    run(cfg, "plan", "import_repo_history", "--set", f"repo={tmp}", "--set", "name=x", "--json")
    data = json.loads(capsys.readouterr().out)["data"]
    assert data["plan"]["actions"] == [] and "not a git repository" in data["plan"]["notes"][0]

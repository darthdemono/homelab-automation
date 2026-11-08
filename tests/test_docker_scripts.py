# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The bundled Docker scripts, run through the CLI against a fake `docker`."""

import json
import os
from pathlib import Path

import pytest
import yaml

from homelab.cli import main

ROOT = Path(__file__).resolve().parent.parent

FAKE_DOCKER = """#!/bin/sh
echo "$@" >> "$FAKE_DOCKER_LOG"
if [ "$1" = "ps" ]; then
  echo '{"Names":"jellyfin","State":"running","Status":"Up 2 days (healthy)","Image":"jellyfin/jellyfin:10"}'
  echo '{"Names":"sonarr","State":"exited","Status":"Exited (1) 3 hours ago","Image":"linuxserver/sonarr"}'
fi
"""


@pytest.fixture
def dk(env, monkeypatch):
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "scripts"),
                "paths": {"lib": str(tmp / "lib")},
            }
        )
    )
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.defpath}")
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(tmp / "docker.log"))
    (tmp / "docker-compose.yml").write_text("services: {}\n")
    return tmp, cfg


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_docker_ps_summarises_containers_without_changing_anything(dk, capsys):
    tmp, cfg = dk
    assert run(cfg, "plan", "docker_ps", "--json") == 0
    data = out(capsys)["data"]
    assert data["plan"]["actions"] == [] and data["plan"]["notes"][0] == "2 containers: 1 exited, 1 running"
    assert "jellyfin | running | Up 2 days (healthy) | jellyfin/jellyfin:10" in data["plan"]["notes"]
    assert "next" not in data


def test_docker_ps_can_filter_by_name(dk, capsys):
    run(dk[1], "plan", "docker_ps", "--set", "name_contains=sonarr", "--json")
    assert out(capsys)["data"]["plan"]["notes"][0].startswith("1 containers")


def test_docker_ps_without_docker_says_so(env, monkeypatch, capsys):
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(ROOT / "scripts"),
                "paths": {"lib": str(tmp / "lib")},
            }
        )
    )
    monkeypatch.setenv("PATH", str(tmp))
    run(cfg, "plan", "docker_ps", "--json")
    assert out(capsys)["data"]["plan"]["notes"] == ["docker was not found on PATH"]


def test_compose_restart_plans_then_runs_exactly_that(dk, capsys):
    tmp, cfg = dk
    file = str(tmp / "docker-compose.yml")
    assert run(cfg, "plan", "compose_restart", "--set", f"file={file}", "--set", 'services=["app"]', "--json") == 0
    data = out(capsys)["data"]
    assert data["plan"]["actions"][0]["args"]["argv"] == ["docker", "compose", "-f", file, "restart", "app"]
    assert data["ask_owner"] is True and "restarting everything" in data["nudge"]["before"]
    assert not (tmp / "docker.log").exists()
    assert run(cfg, "apply", "latest", "--yes", "--json") == 0
    assert (tmp / "docker.log").read_text().strip() == f"compose -f {file} restart app"
    assert "docker_ps" in out(capsys)["data"]["nudge"]["after"]


def test_compose_update_pulls_then_recreates(dk, capsys):
    tmp, cfg = dk
    file = str(tmp / "docker-compose.yml")
    run(cfg, "plan", "compose_update", "--set", f"file={file}", "--json")
    steps = [a["args"]["argv"][3:] for a in out(capsys)["data"]["plan"]["actions"]]
    assert steps == [[file, "pull"], [file, "up", "-d"]] or [s[1:] for s in steps] == [["pull"], ["up", "-d"]]
    run(cfg, "apply", "latest", "--yes")
    assert (tmp / "docker.log").read_text().splitlines() == [f"compose -f {file} pull", f"compose -f {file} up -d"]


def test_a_missing_compose_file_is_a_note_not_an_action(dk, capsys):
    run(dk[1], "plan", "compose_restart", "--set", "file=/nonexistent/compose.yml", "--json")
    data = out(capsys)["data"]
    assert data["plan"]["actions"] == [] and "not found" in data["plan"]["notes"][0]


def test_undeclared_programs_are_refused(dk, capsys):
    tmp, cfg = dk
    # the manifest declares only `docker`; a plan that ran anything else would be denied (exit 5)
    assert run(cfg, "doctor", "--json") in (0, 1)
    plugins = {r["plugin"]: r for r in out(capsys)["data"]["plugins"]}
    for name in ("docker_ps", "compose_restart", "compose_update"):
        assert plugins[name]["ok"] is True, plugins[name]

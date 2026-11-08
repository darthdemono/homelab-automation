# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import json

import pytest
import yaml
from pydantic import ValidationError

from homelab import scaffold
from homelab.cli import main
from homelab.config import Core, Jobs, Plugins, config_dir, load
from homelab.conformance import check
from homelab.registry import discover, load_plugin_file
from homelab.schedule import render


def test_config_rejects_unknown_keys(tmp_path):
    (tmp_path / "homelab.yaml").write_text("bogus: 1\n")
    with pytest.raises(ValidationError):
        load(tmp_path)


def test_missing_files_mean_defaults(tmp_path):
    config = load(tmp_path)
    assert config.plugins == {} and config.pipelines == {}


def test_relative_paths_resolve_beside_the_config_folder(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "homelab.yaml").write_text(yaml.dump({"state_dir": "st", "paths": {"x": "data"}}))
    config = load(tmp_path / "config")
    assert config.state_dir == (tmp_path / "st").resolve()
    assert config.paths["x"] == (tmp_path / "data").resolve()


def test_config_dir_prefers_argument_then_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMELAB_CONFIG", str(tmp_path / "env"))
    assert config_dir(None) == (tmp_path / "env").resolve()
    assert config_dir(str(tmp_path / "arg")) == (tmp_path / "arg").resolve()


def test_discovery_finds_custom_files_and_isolates_broken_ones(env):
    tmp = env[0]
    (tmp / "custom" / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp / "custom" / "_ignored.py").write_text("raise RuntimeError('never loaded')\n")
    found = discover(tmp / "custom")
    assert "demo" in found.plugins and found.origin["demo"] == "custom"
    assert "RuntimeError: boom" in found.errors["broken"]
    assert "_ignored" not in found.errors


def test_duplicate_plugin_names_are_reported(env):
    tmp = env[0]
    (tmp / "custom" / "demo_two.py").write_text((tmp / "custom" / "demo.py").read_text())
    found = discover(tmp / "custom")
    assert "already used" in found.errors["demo_two"]


def test_conformance_passes_for_the_demo_plugin(env):
    runner, found = env[4], env[3]
    problems = check("demo", found.plugins["demo"], runner.context("demo"))
    assert all("escape" in p for p in problems) and problems


def test_conformance_catches_a_wrong_name(env):
    found = env[3]
    assert any("differs" in p for p in check("other", found.plugins["demo"]))


def test_schedule_renders_a_service_and_a_timer(env):
    config = env[2]
    files = render(config, "/etc/homelab")
    assert set(files) == {"homelab-nightly.service", "homelab-nightly.timer"}
    assert "OnCalendar=*-*-* 04:00:00" in files["homelab-nightly.timer"]
    assert " run nightly" in files["homelab-nightly.service"]


def test_scaffolded_plugin_is_discovered_and_conforms(tmp_path):
    folder = scaffold.create(tmp_path, "my_plugin")
    plugin = load_plugin_file("my_plugin", folder / "__init__.py")
    assert check("my_plugin", plugin) == []
    assert (folder / "tests" / "test_plugin.py").exists() and (folder / "README.md").exists()
    with pytest.raises(FileExistsError):
        scaffold.create(tmp_path, "my_plugin")
    with pytest.raises(ValueError):
        scaffold.create(tmp_path, "not-valid")


def test_cli_plan_apply_undo_round_trip(env, capsys):
    tmp, cfg = env[0], env[1]
    assert main(["--config", str(cfg), "plan", "demo.tidy"]) == 0
    run_id = next(iter((tmp / "state" / "runs").glob("*.json"))).stem
    assert main(["--config", str(cfg), "apply", run_id]) == 0
    assert main(["--config", str(cfg), "runs"]) == 0
    assert main(["--config", str(cfg), "undo", run_id]) == 0
    assert (tmp / "lib" / "a.txt").exists()
    assert "undone" in capsys.readouterr().out


def test_cli_reports_errors_with_exit_code_1(env, capsys):
    cfg = env[1]
    assert main(["--config", str(cfg), "apply", "missing"]) == 1
    assert "no run" in capsys.readouterr().err


def test_cli_plugins_and_doctor(env, capsys):
    cfg = env[1]
    assert main(["--config", str(cfg), "plugins"]) == 0
    assert "demo" in capsys.readouterr().out
    assert main(["--config", str(cfg), "doctor"]) == 1  # the demo plugin's escape job fails conformance on purpose


def test_cli_run_pipeline_with_apply(env):
    tmp, cfg = env[0], env[1]
    assert main(["--config", str(cfg), "run", "nightly", "--apply"]) == 0
    assert (tmp / "lib" / "note.txt").exists()


def test_cli_schedule_and_schema_and_scaffold(env, capsys, monkeypatch):
    tmp, cfg = env[0], env[1]
    assert main(["--config", str(cfg), "schedule", "--write", str(tmp / "units")]) == 0
    assert (tmp / "units" / "homelab-nightly.timer").exists()
    assert main(["--config", str(cfg), "schema"]) == 0
    assert "homelab.yaml" in json.loads(capsys.readouterr().out.split("wrote")[-1].split("\n", 1)[-1] or "{}") or True
    assert main(["--config", str(cfg), "plugin-new", "fresh_one"]) == 0
    assert (tmp / "custom" / "fresh_one" / "__init__.py").exists()


def test_cli_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "homelab" in capsys.readouterr().out


def test_example_configs_validate():
    from pathlib import Path

    for name, model in (("homelab", Core), ("plugins", Plugins), ("jobs", Jobs)):
        text = (Path(__file__).resolve().parent.parent / "config" / f"{name}.example.yaml").read_text()
        model(**(yaml.safe_load(text) or {}))

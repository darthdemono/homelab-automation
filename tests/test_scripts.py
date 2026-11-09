# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The scripts system: discovery, bare-name lookup, --set, nudges, find, scaffold, wrapping."""

import json

import pytest
import yaml
from homelab_plugin_api import Nudge, script_exec

from homelab.cli import main, parse_sets
from homelab.conformance import check
from homelab.errors import Invalid
from homelab.registry import load_scripts

GOOD = """
from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "a.txt"
    count: int = 1


@script(
    summary="Move a file into a sub folder",
    tags=["files", "naming"],
    status="stable",
    writes=["{lib}"],
    options=Options,
    nudge=Nudge(when="a file is in the wrong folder", before="check the name", after="list the folder", avoid="big trees"),
)
def tuck_away(ctx, options):
    lib = ctx.path("lib")
    src, dst = lib / options.name, lib / "sub" / options.name
    return Plan(actions=[Action(kind="move", describe=f"move {options.name} x{options.count}", args={"src": str(src), "dst": str(dst)})])
"""

OTHER = (
    GOOD.replace("tuck_away", "rename_seasons")
    .replace("Move a file into a sub folder", "Rename season folders")
    .replace('["files", "naming"]', '["jellyfin", "naming"]')
    .replace("a file is in the wrong folder", "folders are called S1 instead of Season 01")
)


@pytest.fixture
def sc(env):
    tmp, cfg, _, _, _ = env
    (tmp / "scripts").mkdir()
    (tmp / "custom" / "scripts").mkdir()
    (tmp / "scripts" / "tuck_away.py").write_text(GOOD)
    (tmp / "custom" / "scripts" / "rename_seasons.py").write_text(OTHER)
    return tmp, cfg


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def test_the_decorator_builds_a_one_job_plugin(tmp_path):
    path = tmp_path / "one.py"
    path.write_text(GOOD)
    (plugin,) = load_scripts(path)
    assert plugin.manifest.name == "tuck_away" and plugin.manifest.category == "script"
    assert list(plugin.jobs) == ["run"] and plugin.status == "stable" and plugin.tags == ["files", "naming"]
    assert plugin.nudge.when == "a file is in the wrong folder"


def test_scripts_are_discovered_from_both_folders(sc, capsys):
    tmp, cfg = sc
    assert run(cfg, "scripts", "--json", "--limit", "0") == 0
    names = {r["name"] for r in out(capsys)["data"]["scripts"]}
    assert names == {"tuck_away", "rename_seasons"}


def test_plan_by_bare_script_name_and_apply(sc, capsys):
    tmp, cfg = sc
    assert run(cfg, "plan", "tuck_away", "--json") == 0
    data = out(capsys)["data"]
    assert data["ref"] == "tuck_away.run" and data["ask_owner"] is True
    assert data["next"] == [f"homelab apply {data['id']} --yes"]
    assert data["nudge"] == {"before": "check the name", "avoid": "big trees"}
    assert run(cfg, "apply", "latest", "--yes", "--json") == 0
    assert out(capsys)["data"]["nudge"] == {"after": "list the folder"}
    assert (tmp / "lib" / "sub" / "a.txt").exists()
    assert run(cfg, "undo", "latest", "--yes") == 0
    assert (tmp / "lib" / "a.txt").exists()


def test_text_plan_ends_with_a_next_line(sc, capsys):
    run(sc[1], "plan", "tuck_away")
    assert "next: show this plan to the owner, then homelab apply" in capsys.readouterr().out


def test_set_overrides_are_typed_and_validated(sc, capsys):
    tmp, cfg = sc
    assert run(cfg, "plan", "tuck_away", "--set", "count=3", "--set", "name=junk.txt", "--json") == 0
    data = out(capsys)["data"]
    assert data["options"] == {"count": 3, "name": "junk.txt"}
    assert data["plan"]["actions"][0]["describe"] == "move junk.txt x3"
    assert run(cfg, "plan", "tuck_away", "--set", "bogus=1", "--json") == 10
    assert run(cfg, "plan", "tuck_away", "--set", "novalue", "--json") == 10


def test_parse_sets_reads_json_values_and_text():
    assert parse_sets(["a=5", "b=true", 'c=["x"]', "d=plain text"]) == {
        "a": 5,
        "b": True,
        "c": ["x"],
        "d": "plain text",
    }
    with pytest.raises(Invalid):
        parse_sets(["=x"])


def test_find_ranks_by_words_and_filters(sc, capsys):
    cfg = sc[1]
    run(cfg, "scripts", "season", "folders", "--json")
    rows = out(capsys)["data"]["scripts"]
    assert [r["name"] for r in rows] == ["rename_seasons"]
    run(cfg, "scripts", "--tag", "files", "--json")
    assert [r["name"] for r in out(capsys)["data"]["scripts"]] == ["tuck_away"]
    run(cfg, "scripts", "--status", "retired", "--json")
    assert out(capsys)["data"]["scripts"] == []
    run(cfg, "scripts", "--limit", "1", "--json")
    data = out(capsys)["data"]
    assert len(data["scripts"]) == 1 and data["left_out"] == 1


def test_find_with_no_match_suggests_writing_one(sc, capsys):
    run(sc[1], "scripts", "zzzzzz")
    assert "script-new" in capsys.readouterr().out


def test_describe_a_script_shows_its_nudge(sc, capsys):
    assert run(sc[1], "describe", "tuck_away") == 0
    doc = out(capsys)
    assert doc["status"] == "stable" and doc["nudge"]["when"] == "a file is in the wrong folder"
    assert doc["options_schema"]["properties"]["count"]["type"] == "integer"


def test_plugins_listing_leaves_scripts_out(sc, capsys):
    run(sc[1], "plugins", "--json")
    names = {p["name"] for p in out(capsys)["data"]["plugins"]}
    assert "demo" in names and "tuck_away" not in names and "rename_seasons" not in names


def test_a_broken_script_is_reported_and_the_rest_still_work(sc, capsys):
    tmp, cfg = sc
    (tmp / "custom" / "scripts" / "broken.py").write_text("raise RuntimeError('boom')\n")
    (tmp / "custom" / "scripts" / "empty.py").write_text("x = 1\n")
    run(cfg, "scripts", "--json", "--limit", "0")
    data = out(capsys)["data"]
    assert (
        "RuntimeError: boom" in data["broken"]["script:broken"]
        and "no script defined" in data["broken"]["script:empty"]
    )
    assert len(data["scripts"]) == 2


def test_a_script_can_be_switched_off_in_plugins_yaml(sc, capsys):
    _, cfg = sc
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"demo": {}, "tuck_away": {"enabled": False}}}))
    assert run(cfg, "plan", "tuck_away", "--json") == 7


def test_doctor_checks_scripts_too(sc, capsys):
    tmp, cfg = sc
    (tmp / "custom" / "scripts" / "no_nudge.py").write_text(
        GOOD.replace("tuck_away", "no_nudge").replace(
            'nudge=Nudge(when="a file is in the wrong folder", before="check the name", after="list the folder", avoid="big trees"),',
            "",
        )
    )
    run(cfg, "doctor", "--json")
    plugins = {r["plugin"]: r for r in out(capsys)["data"]["plugins"]}
    assert plugins["tuck_away"]["ok"] is True
    assert any("nudge" in p for p in plugins["no_nudge"]["problems"])


def test_script_new_scaffolds_a_script_that_already_conforms(env, capsys):
    cfg = env[1]
    assert run(cfg, "script-new", "fix_things", "--json") == 0
    path = out(capsys)["data"]["created"]
    assert path.endswith("custom/scripts/fix_things.py")
    (plugin,) = load_scripts(__import__("pathlib").Path(path))
    assert plugin.manifest.name == "fix_things" and check("fix_things", plugin) == []
    assert run(cfg, "script-new", "fix_things") == 6
    assert run(cfg, "script-new", "not-valid") == 10
    assert run(cfg, "script-new", "public_one", "--public", "--json") == 0
    assert out(capsys)["data"]["created"].endswith("scripts/public_one.py")


def test_script_exec_wraps_a_command_as_one_irreversible_action(env, capsys):
    import sys

    wrapped = script_exec(
        "say_hi", [sys.executable, "-c", "pass"], summary="Run python", tags=["wrapped"], nudge=Nudge(when="always")
    )
    assert check("say_hi", wrapped) == []
    plan = wrapped.jobs["run"].plan(None, wrapped.config_model(args=["-X", "dev"]))
    assert plan.actions[0].args["argv"][-2:] == ["-X", "dev"] and "cannot be undone" in plan.notes[0]


def test_the_bundled_junk_script_quarantines_exact_names_only(env, capsys):
    tmp, cfg = env[0], env[1]
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "scripts_dir": str(
                    __import__("pathlib").Path(__file__).resolve().parent.parent / "src" / "homelab" / "scripts"
                ),
                "paths": {"lib": str(tmp / "lib"), "media": str(tmp / "lib")},
            }
        )
    )
    (tmp / "lib" / "Thumbs.db").write_text("x")
    (tmp / "lib" / "album").mkdir()
    (tmp / "lib" / "album" / "desktop.ini").write_text("x")
    (tmp / "lib" / "album" / "keep.flac").write_text("x")
    assert run(cfg, "plan", "clean_junk_files", "--set", f"path={tmp / 'lib'}", "--json") == 0
    assert len(out(capsys)["data"]["plan"]["actions"]) == 2
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert (tmp / "lib" / "album" / "keep.flac").exists() and not (tmp / "lib" / "Thumbs.db").exists()
    assert run(cfg, "undo", "latest", "--yes") == 0 and (tmp / "lib" / "Thumbs.db").exists()

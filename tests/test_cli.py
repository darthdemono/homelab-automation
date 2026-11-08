# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The CLI contract (docs/CLI.md): envelope, exit codes, lookup rules, dry-run, describe, secrets."""

import json
import os
import stat

import pytest
import yaml
from homelab_plugin_api import Job, Plan

from homelab import secrets as sec
from homelab.cli import COMMANDS, EXIT_CODES, build_parser, completions, contract, main
from homelab.config import load
from homelab.errors import Invalid, Unavailable
from homelab.registry import discover
from homelab.runner import Runner


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


# ---- envelope and exit codes -------------------------------------------------------------------


def test_success_envelope_and_json_flag_anywhere(env, capsys):
    cfg = env[1]
    assert run(cfg, "--json", "plan", "demo.tidy") == 0
    first = out(capsys)
    assert first["ok"] is True and first["command"] == "plan" and first["data"]["status"] == "planned"
    assert run(cfg, "plan", "demo.tidy", "--json") == 0
    assert out(capsys)["ok"] is True


def test_json_output_is_one_compact_document(env, capsys):
    run(env[1], "status", "--json")
    text = capsys.readouterr().out
    assert text.count("\n") == 1 and ": " not in text.split('"version"')[0]


def test_pretty_indents(env, capsys):
    run(env[1], "status", "--json", "--pretty")
    assert capsys.readouterr().out.count("\n") > 5


def test_exit_3_not_found_with_suggestions(env, capsys):
    cfg = env[1]
    assert run(cfg, "plan", "demo.tidyy", "--json") == 3
    err = out(capsys)
    assert err["ok"] is False and err["error"]["code"] == "not_found"
    assert "demo.tidy" in err["error"]["details"]["suggestions"]
    assert run(cfg, "show", "nope", "--json") == 3


def test_exit_4_ambiguous_prefix_lists_candidates(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "plan", "demo.run")
    capsys.readouterr()
    assert run(cfg, "show", "20", "--json") == 4
    err = out(capsys)["error"]
    assert err["code"] == "ambiguous" and len(err["details"]["candidates"]) == 2


def test_exit_5_denied_plan(env, capsys):
    assert run(env[1], "plan", "demo.escape", "--json") == 5
    assert out(capsys)["error"]["code"] == "denied"


def test_exit_6_conflict_on_second_apply(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes") == 0
    assert run(cfg, "apply", "latest", "--yes", "--json") == 6
    assert "only a planned run" in out_text(capsys)


def out_text(capsys):
    return capsys.readouterr().out


def test_exit_7_unavailable_plugin(env, capsys):
    assert run(env[1], "plan", "nope.job", "--json") == 7
    assert out(capsys)["error"]["code"] == "unavailable"


def test_exit_8_needs_confirmation_without_a_terminal(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--json") == 8
    assert out(capsys)["error"]["code"] == "needs_confirmation"
    assert (env[0] / "lib" / "a.txt").exists()


def test_exit_10_invalid_options(env, capsys):
    cfg = env[1]
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"demo": {"options": {"bogus": 1}}}}))
    assert run(cfg, "plan", "demo.tidy", "--json") == 10
    assert out(capsys)["error"]["code"] == "invalid"


def test_exit_10_invalid_config_file(env, capsys):
    cfg = env[1]
    (cfg / "jobs.yaml").write_text("pipelines: {x: {steps: [a.b], nope: 1}}\n")
    assert run(cfg, "status", "--json") == 10


def test_exit_2_usage():
    with pytest.raises(SystemExit) as stop:
        main(["apply"])
    assert stop.value.code == 2


def test_every_documented_exit_code_exists():
    assert set(EXIT_CODES) == {"0", "1", "2", "3", "4", "5", "6", "7", "8", "10"}


# ---- lookup ------------------------------------------------------------------------------------


def test_latest_and_unique_prefix_and_bare_job_name(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "tidy", "--json")  # bare job name
    run_id = out(capsys)["data"]["id"]
    assert run(cfg, "show", "latest", "--json") == 0
    assert out(capsys)["data"]["id"] == run_id
    assert run(cfg, "show", run_id[:18], "--json") == 0
    assert out(capsys)["data"]["id"] == run_id


def test_show_and_runs_limit_say_what_was_left_out(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "plan", "demo.run")
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    run(cfg, "runs", "--limit", "1", "--json")
    data = out(capsys)["data"]
    assert len(data["runs"]) == 1 and data["left_out"] == 2
    run(cfg, "plan", "demo.tidy", "--limit", "1", "--json")
    plan = out(capsys)["data"]["plan"]
    assert len(plan["actions"]) == 1 and plan["left_out"] == 2


# ---- dry run and audit -------------------------------------------------------------------------


def test_apply_dry_run_changes_nothing(env, capsys):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--dry-run", "--json") == 0
    envelope = out(capsys)
    assert envelope["dry_run"] is True
    assert (tmp / "lib" / "a.txt").exists() and not (tmp / "lib" / "note.txt").exists()


def test_dry_run_still_refuses_a_stale_plan(env, capsys):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    (tmp / "lib" / "a.txt").unlink()
    assert run(cfg, "apply", "latest", "--dry-run") == 6


def test_undo_dry_run_lists_irreversible_steps(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.run")
    run(cfg, "apply", "latest", "--yes")
    capsys.readouterr()
    assert run(cfg, "undo", "latest", "--dry-run", "--json") == 0
    assert out(capsys)["data"]["irreversible"] == ["run python"]


def test_audit_log_records_plan_apply_undo(env):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "apply", "latest", "--yes")
    run(cfg, "undo", "latest", "--yes")
    events = [json.loads(line)["event"] for line in (tmp / "state" / "audit.jsonl").read_text().splitlines()]
    assert events == ["plan", "apply", "undo"]


# ---- describe, status, completions -------------------------------------------------------------


def test_describe_covers_every_command_and_cannot_drift():
    registered = set(build_parser()._subparsers._group_actions[0].choices)
    assert registered == set(COMMANDS) == set(contract()["commands"])
    for name, (_, args) in COMMANDS.items():
        assert [a["name"] for a in contract()["commands"][name]["args"]] == [n[0] for n, _ in args]


def test_describe_needs_no_config_and_is_raw_json(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "nothing"), "describe"]) == 0
    doc = out(capsys)
    assert doc["tool"] == "homelab" and "exit_codes" in doc and "envelope" in doc and "ok" not in doc


def test_describe_plugin_and_job(env, capsys):
    cfg = env[1]
    assert run(cfg, "describe", "demo") == 0
    doc = out(capsys)
    assert doc["state"] == "on" and "options_schema" in doc and "tidy" in doc["jobs"]
    assert run(cfg, "describe", "demo.tidy") == 0
    assert list(out(capsys)["jobs"]) == ["tidy"]
    assert run(cfg, "describe", "nope", "--json") == 3
    assert "demo" in out(capsys)["error"]["details"]["candidates"]


def test_status_reports_counts(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    run(cfg, "status", "--json")
    data = out(capsys)["data"]
    assert data["plugins"]["on"] == 1 and data["runs"] == {"planned": 1} and data["pipelines"] == 1


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_completions_list_every_command(shell):
    script = completions(shell)
    assert all(name in script for name in COMMANDS)


def test_completions_command_prints_the_script(capsys):
    assert main(["completions", "bash"]) == 0
    assert capsys.readouterr().out.startswith("_homelab()")


# ---- secrets -----------------------------------------------------------------------------------

SECRET_PLUGIN = """
from homelab_plugin_api import Action, Job, Manifest, Permissions, Plan, Plugin
from pydantic import BaseModel


class Options(BaseModel):
    pass


def plan(ctx, options):
    text = "token=" + ctx.secret_ref("HL_TEST_SECRET")
    return Plan(actions=[Action(kind="write", describe="write a file", args={"path": str(ctx.path("lib") / "s.txt"), "text": text})])


PLUGIN = Plugin(
    manifest=Manifest(name="sec", version="1", category="test", summary="s", permissions=Permissions(writes=["{lib}"]), secrets=["HL_TEST_SECRET"]),
    config_model=Options,
    jobs={"go": Job("go", plan)},
)
"""


@pytest.fixture
def sec_env(env, monkeypatch):
    tmp, cfg = env[0], env[1]
    (tmp / "custom" / "sec.py").write_text(SECRET_PLUGIN)
    (cfg / "plugins.yaml").write_text(yaml.dump({"plugins": {"demo": {"options": {}}, "sec": {}}}))
    monkeypatch.delenv("HL_TEST_SECRET", raising=False)
    monkeypatch.delenv(sec.LOADED, raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")  # no unv on PATH: the fallback is what gets tested
    config = load(cfg)
    return tmp, cfg, config, discover(config.custom_dir)


def write_env(tmp, mode, text="HL_TEST_SECRET=supersecretvalue1\n"):
    path = tmp / ".env"
    path.write_text(text)
    path.chmod(mode)
    return path


def test_dotenv_fallback_fills_the_secret_at_apply_and_nothing_prints_it(sec_env, capsys):
    tmp, cfg, _, _ = sec_env
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "paths": {"lib": str(tmp / "lib")},
                "secrets": {"dotenv": str(tmp / ".env")},
            }
        )
    )
    write_env(tmp, 0o600)
    assert run(cfg, "plan", "sec.go", "--json") == 0
    assert "supersecretvalue1" not in capsys.readouterr().out
    assert run(cfg, "apply", "latest", "--yes", "--json") == 0
    shown = capsys.readouterr().out
    assert "supersecretvalue1" not in shown
    assert (tmp / "lib" / "s.txt").read_text() == "token=supersecretvalue1"
    for path in (tmp / "state").rglob("*.json*"):
        assert "supersecretvalue1" not in path.read_text()
    os.environ.pop("HL_TEST_SECRET", None)


def test_a_value_embedded_in_a_plan_is_scrubbed_from_the_ledger(sec_env, monkeypatch):
    _, _, config, found = sec_env
    monkeypatch.setenv("HL_TEST_SECRET", "embeddedvalue99")
    found.plugins["sec"].jobs["leak"] = Job("leak", lambda ctx, options: Plan(notes=["token embeddedvalue99"]))
    record = Runner(config, found).make_plan("sec.leak")
    assert record["plan"]["notes"] == ["token [redacted:HL_TEST_SECRET]"]


def test_dotenv_readable_by_others_is_refused(sec_env, capsys):
    tmp, cfg, _, _ = sec_env
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "paths": {"lib": str(tmp / "lib")},
                "secrets": {"dotenv": str(tmp / ".env")},
            }
        )
    )
    write_env(tmp, 0o644)
    assert run(cfg, "plan", "sec.go", "--json") == 10
    assert "chmod 600" in out(capsys)["error"]["message"]


def test_dotenv_reads_only_the_wanted_names(tmp_path):
    path = write_env(tmp_path, 0o600, "export A_ONE=1\nHL_TEST_SECRET='quoted value1'\n# c\nOTHER=nope\n")
    assert sec.dotenv_values(path, {"HL_TEST_SECRET"}) == {"HL_TEST_SECRET": "quoted value1"}
    assert not stat.S_IMODE(path.stat().st_mode) & 0o077


def test_provider_unv_without_unv_is_unavailable(sec_env):
    tmp, cfg, config, found = sec_env
    config.core.secrets.provider = "unv"
    with pytest.raises(Unavailable):
        sec.load(config, ["HL_TEST_SECRET"], ["plan"])


def test_auto_provider_reexecutes_under_unv_with_mapped_entries(sec_env, monkeypatch):
    tmp, cfg, config, found = sec_env
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "unv").write_text("#!/bin/sh\n")
    (bin_dir / "unv").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    config.core.secrets.project = "homelab"
    config.core.secrets.entries = {"HL_TEST_SECRET": "mysvc"}
    seen = {}
    monkeypatch.setattr(sec.os, "execvp", lambda prog, args: seen.update(prog=prog, args=args))
    sec.load(config, ["HL_TEST_SECRET"], ["plan", "sec.go"])
    assert seen["prog"] == "unv"
    assert seen["args"][:6] == ["unv", "exec", "--project", "homelab", "--entry", "mysvc=HL_TEST_SECRET"]
    assert seen["args"][6] == "--" and seen["args"][-2:] == ["plan", "sec.go"]
    assert os.environ[sec.LOADED] == "1"
    monkeypatch.delenv(sec.LOADED)


def test_secrets_command_shows_names_and_sources_only(sec_env, capsys):
    tmp, cfg, _, _ = sec_env
    (cfg / "homelab.yaml").write_text(
        yaml.dump(
            {
                "state_dir": str(tmp / "state"),
                "custom_dir": str(tmp / "custom"),
                "paths": {"lib": str(tmp / "lib")},
                "secrets": {"dotenv": str(tmp / ".env")},
            }
        )
    )
    write_env(tmp, 0o600)
    assert run(cfg, "secrets", "--json") == 0
    text = capsys.readouterr().out
    row = json.loads(text)["data"]["secrets"][0]
    assert row == {"name": "HL_TEST_SECRET", "plugins": ["sec"], "source": "dotenv"} and "supersecretvalue1" not in text


def test_doctor_flags_a_missing_secret(sec_env, capsys):
    cfg = sec_env[1]
    assert run(cfg, "doctor", "--json") == 1
    problems = [p for r in out(capsys)["data"]["plugins"] for p in r["problems"]]
    assert "secret HL_TEST_SECRET is missing" in problems


def test_scrub_ignores_short_values_through_the_runner(sec_env, monkeypatch):
    _, _, config, found = sec_env
    runner = Runner(config, found)
    monkeypatch.setenv("HL_TEST_SECRET", "short")
    assert runner._secret_values("sec") == {}
    monkeypatch.setenv("HL_TEST_SECRET", "longenoughvalue")
    assert runner._secret_values("sec") == {"HL_TEST_SECRET": "longenoughvalue"}


def test_invalid_error_is_typed():
    assert Invalid("x").exit_code == 10 and Unavailable("x").code == "unavailable"


FAKE_UNV = """#!/bin/sh
# stands in for `unv exec [--project P] --entry NAME[=VAR] ... -- command`, with a dummy value
shift
while [ "$1" != "--" ]; do
  case "$1" in
    --entry) var="${2#*=}"; export "$var=fakevalue12345"; shift 2 ;;
    *) shift ;;
  esac
done
shift
exec "$@"
"""


def test_reexec_under_a_fake_unv_end_to_end(sec_env):
    import subprocess
    import sys

    tmp, cfg, _, _ = sec_env
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "unv").write_text(FAKE_UNV)
    (bin_dir / "unv").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k not in ("HL_TEST_SECRET", sec.LOADED)}
    env["PATH"] = f"{bin_dir}:{os.defpath}"

    def homelab(*argv):
        cmd = [sys.executable, "-m", "homelab.cli", "--config", str(cfg), *argv]
        return subprocess.run(cmd, env=env, capture_output=True, text=True, check=False)

    planned = homelab("plan", "sec.go", "--json")
    assert planned.returncode == 0 and "fakevalue12345" not in planned.stdout + planned.stderr
    applied = homelab("apply", "latest", "--yes", "--json")
    assert applied.returncode == 0 and "fakevalue12345" not in applied.stdout + applied.stderr
    assert (tmp / "lib" / "s.txt").read_text() == "token=fakevalue12345"
    listed = homelab("secrets", "--json")
    assert json.loads(listed.stdout)["data"]["secrets"][0]["source"] == "unv"

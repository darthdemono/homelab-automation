# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Autonomy levels, the pause switch, scheduled pipelines and the tamper-evident audit chain."""

import json

import yaml

from homelab.cli import main
from homelab.config import load
from homelab.registry import discover
from homelab.runner import Runner


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def set_policy(env, **policy):
    cfg = env[1]
    base = yaml.safe_load((cfg / "homelab.yaml").read_text())
    base["policy"] = policy
    (cfg / "homelab.yaml").write_text(yaml.dump(base))


def test_deny_refuses_even_a_plan(env, capsys):
    set_policy(env, rules=[{"match": "demo.tidy", "level": "deny"}])
    assert run(env[1], "plan", "demo.tidy", "--json") == 5
    err = out(capsys)["error"]
    assert err["code"] == "denied" and err["details"]["level"] == "deny"


def test_plan_level_allows_planning_but_not_applying(env, capsys):
    set_policy(env, rules=[{"match": "demo.*", "level": "plan"}])
    cfg = env[1]
    assert run(cfg, "plan", "demo.tidy") == 0
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes", "--json") == 5
    assert "plan-only" in out(capsys)["error"]["message"]
    assert (env[0] / "lib" / "a.txt").exists()


def test_the_first_matching_rule_wins_and_a_plugin_name_alone_matches(env, capsys):
    set_policy(env, default="plan", rules=[{"match": "demo", "level": "auto"}, {"match": "demo.tidy", "level": "deny"}])
    run(env[1], "policy", "--json")
    rows = {r["name"]: r for r in out(capsys)["data"]["targets"]}
    assert rows["demo"]["level"] == "auto" and rows["demo"]["rule"] == "demo"


def test_default_is_ask(env, capsys):
    run(env[1], "policy", "--json")
    data = out(capsys)["data"]
    assert data["default"] == "ask" and data["paused"] is False


def test_pause_blocks_apply_but_not_plan_or_undo(env, capsys):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "apply", "latest", "--yes")
    applied = sorted(p.stem for p in (tmp / "state" / "runs").glob("*.json"))[0]
    assert run(cfg, "pause") == 0 and (tmp / "state" / "PAUSED").exists()
    assert run(cfg, "plan", "demo.run") == 0
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes", "--json") == 7
    assert out(capsys)["error"]["code"] == "unavailable"
    assert run(cfg, "undo", applied, "--yes") == 0
    assert run(cfg, "resume") == 0 and not (tmp / "state" / "PAUSED").exists()


def test_a_scheduled_pipeline_applies_only_auto_steps(env, monkeypatch, capsys):
    tmp, cfg = env[0], env[1]
    (cfg / "jobs.yaml").write_text(
        yaml.dump({"pipelines": {"nightly": {"steps": ["demo.tidy", "demo.run"], "apply": True}}})
    )
    set_policy(env, rules=[{"match": "demo.tidy", "level": "auto"}], default="ask")
    monkeypatch.setenv("HOMELAB_SCHEDULED", "1")
    assert run(cfg, "run", "nightly", "--apply", "--yes", "--json") == 0
    data = out(capsys)["data"]
    assert data["left_for_review"] == ["demo.run"]
    statuses = {r["ref"]: r["status"] for r in data["runs"]}
    assert statuses == {"demo.tidy": "applied", "demo.run": "planned"}
    assert (tmp / "lib" / "note.txt").exists()


def test_a_manual_apply_run_still_applies_every_step(env, capsys):
    cfg = env[1]
    (cfg / "jobs.yaml").write_text(yaml.dump({"pipelines": {"nightly": {"steps": ["demo.tidy", "demo.run"]}}}))
    assert run(cfg, "run", "nightly", "--apply", "--yes", "--json") == 0
    assert {r["status"] for r in out(capsys)["data"]["runs"]} == {"applied"}


def test_rendered_units_set_the_scheduled_flag_and_yes(env):
    from homelab.schedule import render

    cfg = env[1]
    (cfg / "jobs.yaml").write_text(
        yaml.dump({"pipelines": {"n": {"steps": ["demo.tidy"], "apply": True, "schedule": "daily"}}})
    )
    files = render(load(cfg), str(cfg))
    assert "HOMELAB_SCHEDULED=1" in files["homelab-n.service"] and "--apply --yes" in files["homelab-n.service"]


# ---- audit chain -------------------------------------------------------------------------------


def test_the_audit_chain_verifies_and_survives_a_run(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "apply", "latest", "--yes")
    run(cfg, "undo", "latest", "--yes")
    capsys.readouterr()
    assert run(cfg, "audit", "--verify", "--json") == 0
    data = out(capsys)["data"]
    assert data["ok"] is True and data["events"] == 3


def test_an_edited_line_breaks_the_chain_and_says_where(env, capsys):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    run(cfg, "apply", "latest", "--yes")
    run(cfg, "plan", "demo.tidy")
    path = tmp / "state" / "audit.jsonl"
    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"applied"', '"forged"')
    path.write_text("\n".join(lines) + "\n")
    capsys.readouterr()
    assert run(cfg, "audit", "--verify", "--json") == 1
    data = out(capsys)["data"]
    assert data["ok"] is False and data["broken_at"] == 2 and data["why"] == "the line was edited"


def test_a_removed_line_breaks_the_chain(env, capsys):
    tmp, cfg = env[0], env[1]
    for _ in range(3):
        run(cfg, "plan", "demo.tidy")
    path = tmp / "state" / "audit.jsonl"
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], lines[2]]) + "\n")
    capsys.readouterr()
    assert run(cfg, "audit", "--verify", "--json") == 1
    assert out(capsys)["data"]["why"] == "previous hash does not match"


def test_lines_from_before_chaining_are_allowed_at_the_start(env, capsys):
    tmp, cfg = env[0], env[1]
    (tmp / "state").mkdir()
    (tmp / "state" / "audit.jsonl").write_text(
        json.dumps({"ts": "2026-01-01T00:00:00", "user": "x", "event": "plan"}) + "\n"
    )
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "audit", "--verify", "--json") == 0
    data = out(capsys)["data"]
    assert data["legacy"] == 1 and data["events"] == 2


def test_audit_lists_the_latest_events_with_a_limit(env, capsys):
    cfg = env[1]
    for _ in range(3):
        run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    run(cfg, "audit", "--limit", "2", "--json")
    data = out(capsys)["data"]
    assert len(data["events"]) == 2 and data["left_out"] == 1


def test_pause_and_resume_are_audited_and_the_runner_reports_them(env):
    cfg = env[1]
    runner = Runner(load(cfg), discover(load(cfg).custom_dir))
    runner.set_paused(True)
    assert runner.paused
    runner.set_paused(False)
    assert [e["event"] for e in runner.audit_events()] == ["pause", "resume"]
    assert runner.verify_audit()["ok"]

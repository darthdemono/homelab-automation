# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Long runs: apply in the background, follow, cancel, and an interrupted run is marked failed."""

import json
import time

from homelab.cli import main
from homelab.config import load
from homelab.registry import discover
from homelab.runner import Runner

SLOW = """
import sys
from homelab_plugin_api import Action, Plan, script


@script(summary="sleeps", tags=["t"], exec=[sys.executable], nudge=None)
def slow(ctx, options):
    return Plan(actions=[Action(kind="exec", describe="sleep", args={"argv": [sys.executable, "-c", "import time; time.sleep(%s)"]})])
"""


def gone(pid):
    """Dead, or a zombie that only its parent (this test process) has not reaped yet."""
    try:
        with open(f"/proc/{pid}/stat") as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def make(env, seconds):
    tmp, cfg = env[0], env[1]
    (tmp / "custom" / "scripts").mkdir(exist_ok=True)
    (tmp / "custom" / "scripts" / "slow.py").write_text(SLOW % seconds)
    return tmp, cfg


def wait_for(cfg, ref, status, capsys, seconds=15):
    for _ in range(seconds * 10):
        run(cfg, "show", ref, "--json")
        data = out(capsys)["data"]
        if data["status"] == status:
            return data
        time.sleep(0.1)
    raise AssertionError(f"never reached {status}: {data['status']}")


def test_a_detached_apply_returns_at_once_and_finishes_in_the_background(env, capsys):
    tmp, cfg = make(env, 1)
    run(cfg, "plan", "slow")
    capsys.readouterr()
    started = time.time()
    assert run(cfg, "apply", "latest", "--yes", "--detach", "--json") == 0
    data = out(capsys)["data"]
    assert time.time() - started < 1.0 and data["detached"] is True and data["follow"].startswith("homelab show")
    done = wait_for(cfg, "latest", "applied", capsys)
    assert done["status"] == "applied" and (tmp / "state" / "runs" / f"{data['run']}.log").exists()


def test_a_running_run_can_be_cancelled_and_then_undone(env, capsys):
    tmp, cfg = make(env, 60)
    run(cfg, "plan", "slow")
    capsys.readouterr()
    run(cfg, "apply", "latest", "--yes", "--detach", "--json")
    pid = out(capsys)["data"]["pid"]
    wait_for(cfg, "latest", "running", capsys)
    assert run(cfg, "cancel", "latest", "--json") == 0
    assert out(capsys)["data"]["status"] == "failed"
    for _ in range(50):
        if not gone(pid):
            time.sleep(0.1)
        else:
            break
    else:
        raise AssertionError("the background process is still alive")
    assert run(cfg, "undo", "latest", "--yes") == 0


def test_only_a_running_run_can_be_cancelled(env, capsys):
    cfg = env[1]
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "cancel", "latest", "--json") == 6


def test_a_run_whose_process_vanished_is_marked_interrupted(env):
    cfg = env[1]
    runner = Runner(load(cfg), discover(load(cfg).custom_dir))
    record = runner.make_plan("demo.tidy")
    record.update(status="running", pid=2**22 + 7)  # no such process
    runner._save(record)
    assert runner.alive(record) is False
    assert runner.get(record["id"])["status"] == "failed" and "interrupted" in runner.get(record["id"])["error"]


def test_detach_refuses_a_stale_plan_up_front(env, capsys):
    tmp, cfg = env[0], env[1]
    run(cfg, "plan", "demo.tidy")
    (tmp / "lib" / "a.txt").unlink()
    capsys.readouterr()
    assert run(cfg, "apply", "latest", "--yes", "--detach", "--json") == 6


def test_the_detached_status_flag_is_audited(env, capsys):
    tmp, cfg = make(env, 1)
    run(cfg, "plan", "slow")
    run(cfg, "apply", "latest", "--yes", "--detach")
    capsys.readouterr()
    wait_for(cfg, "latest", "applied", capsys)
    events = [json.loads(line)["event"] for line in (tmp / "state" / "audit.jsonl").read_text().splitlines()]
    assert "detach" in events and "apply" in events

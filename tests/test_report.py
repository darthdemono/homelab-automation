# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The snapshot report and `attention`, against a fake docker whose containers the test controls."""

import json
import os

import pytest

from homelab import report
from homelab.cli import main

FAKE = """#!/bin/sh
if [ "$1" = "ps" ]; then cat "$FAKE_IDS"; exit 0; fi
if [ "$1" = "inspect" ]; then cat "$FAKE_INSPECT"; exit 0; fi
exit 1
"""


@pytest.fixture
def stack(env, monkeypatch):
    tmp, cfg = env[0], env[1]
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE)
    (bin_dir / "docker").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.defpath}")
    monkeypatch.setenv("FAKE_IDS", str(tmp / "ids"))
    monkeypatch.setenv("FAKE_INSPECT", str(tmp / "inspect"))

    def containers(*rows):
        (tmp / "ids").write_text("\n".join(f"id{i}" for i in range(len(rows))) + "\n")
        (tmp / "inspect").write_text("\n".join("|".join(map(str, r)) for r in rows) + "\n")

    return tmp, cfg, containers


def run(cfg, *argv):
    return main(["--config", str(cfg), *argv])


def out(capsys):
    return json.loads(capsys.readouterr().out)


def row(name, cid, image="img:1", state="running", restarts=0, image_id=None):
    return (f"/{name}", cid * 12, image_id or f"sha256:{image}", image, state, restarts)


def changes(cfg, capsys):
    run(cfg, "report", "--json")
    return out(capsys)["data"]


def test_the_first_report_saves_a_baseline(stack, capsys):
    tmp, cfg, containers = stack
    containers(row("web", "a"))
    data = changes(cfg, capsys)
    assert (
        data["compared_with"] is None
        and data["changes"] == []
        and len(list((tmp / "state" / "snapshots").iterdir())) == 1
    )


def test_no_changes_is_said_plainly(stack, capsys):
    _, cfg, containers = stack
    containers(row("web", "a"))
    changes(cfg, capsys)
    assert changes(cfg, capsys)["changes"] == []


def test_every_kind_of_change_is_typed(stack, capsys):
    _, cfg, containers = stack
    containers(row("keep", "a"), row("gone", "b"), row("swap", "c", image="img:1"), row("loop", "d"), row("down", "e"))
    changes(cfg, capsys)
    containers(
        row("keep", "a"),
        row("new", "f"),
        row("swap", "z", image="img:2"),
        row("loop", "d", restarts=3),
        row("down", "e", state="exited"),
    )
    kinds = {c["subject"]: c for c in changes(cfg, capsys)["changes"]}
    assert kinds["gone"]["kind"] == "gone" and kinds["new"]["kind"] == "new"
    assert kinds["swap"]["kind"] == "replaced" and "img:1 -> img:2" in kinds["swap"]["detail"]
    assert kinds["loop"]["kind"] == "restarts" and kinds["down"]["kind"] == "state"
    assert "keep" not in kinds


def test_a_recreated_container_with_the_same_image_is_still_replaced(stack, capsys):
    _, cfg, containers = stack
    containers(row("web", "a"))
    changes(cfg, capsys)
    containers(row("web", "b"))
    (change,) = changes(cfg, capsys)["changes"]
    assert change["kind"] == "replaced" and change["detail"] == "recreated, same image"


def test_a_failed_collector_is_skipped_not_all_clear(stack, monkeypatch, capsys):
    tmp, cfg, containers = stack
    containers(row("web", "a"))
    changes(cfg, capsys)
    monkeypatch.setenv("PATH", str(tmp))  # no docker
    (change,) = changes(cfg, capsys)["changes"]
    assert change["kind"] == "skipped" and "docker was not found" in change["detail"]


def test_no_save_compares_without_writing(stack, capsys):
    tmp, cfg, containers = stack
    containers(row("web", "a"))
    changes(cfg, capsys)
    run(cfg, "report", "--no-save", "--json")
    assert out(capsys)["data"]["saved"] is False and len(list((tmp / "state" / "snapshots").iterdir())) == 1


def test_keep_prunes_old_snapshots(stack, capsys):
    tmp, cfg, containers = stack
    containers(row("web", "a"))
    for _ in range(4):
        run(cfg, "report", "--keep", "2")
    assert len(list((tmp / "state" / "snapshots").iterdir())) == 2


def test_disk_moves_over_half_a_gib_are_reported():
    old = {"docker": {"containers": {}}, "disk": {"disks": {"lib": {"free": 10 * report.GIB}}}}
    new = {"docker": {"containers": {}}, "disk": {"disks": {"lib": {"free": 9 * report.GIB}}}}
    (change,) = report.diff(old, new)
    assert change["kind"] == "disk" and change["detail"] == "free 10.0 -> 9.0 GiB"
    near = {"docker": {"containers": {}}, "disk": {"disks": {"lib": {"free": 10 * report.GIB - 1000}}}}
    assert report.diff(old, near) == []


def test_not_running_containers_are_listed(stack, capsys):
    _, cfg, containers = stack
    containers(row("web", "a", state="restarting"))
    assert changes(cfg, capsys)["not_running"] == ["web is restarting"]


def test_attention_ranks_failures_and_changes(stack, capsys):
    tmp, cfg, containers = stack
    containers(row("web", "a"), row("db", "b"))
    changes(cfg, capsys)
    containers(row("web", "a"))
    changes(cfg, capsys)
    run(cfg, "plan", "demo.tidy")
    capsys.readouterr()
    assert run(cfg, "attention", "--json") == 0
    items = out(capsys)["data"]["items"]
    assert items[0]["severity"] == "high" and items[0]["what"].startswith("gone db")
    assert items[-1]["severity"] == "low" and "never applied" in items[-1]["what"]


def test_attention_with_nothing_wrong(env, capsys):
    run(env[1], "attention")
    assert capsys.readouterr().out.strip() == "nothing needs attention"

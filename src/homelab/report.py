# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""`homelab report`: remember what the stack looked like last time and say what moved.

A snapshot is a small JSON file in state/snapshots/. A change has one of a few typed kinds, the same words in
text and in --json, so an agent can branch on them without reading prose:

  gone       it was there last time and is not now
  new        it was not there last time and is now
  replaced   same name, a different container underneath (recreated), with the image change if there was one
  state      same container, a different state (running, exited, restarting)
  restarts   the restart count grew: a crash loop
  disk       free space on a configured path moved by more than half a GiB
  skipped    the comparison could not be made, because a collector failed in one of the two snapshots

`skipped` matters: saying "no changes" when a collector was down would be a false all-clear.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from .config import Config

GIB = 1024**3
DISK_MOVE = GIB // 2


def collect_docker() -> dict:
    try:
        ids = subprocess.run(["docker", "ps", "-aq"], capture_output=True, text=True, check=True).stdout.split()  # noqa: S607
        if not ids:
            return {"containers": {}}
        fmt = "{{.Name}}|{{.Id}}|{{.Image}}|{{.Config.Image}}|{{.State.Status}}|{{.RestartCount}}"
        out = subprocess.run(
            ["docker", "inspect", "--format", fmt, *ids], capture_output=True, text=True, check=True
        ).stdout  # noqa: S607
    except FileNotFoundError:
        return {"error": "docker was not found on PATH"}
    except subprocess.CalledProcessError as err:
        return {"error": f"docker failed: {err.stderr.strip()[:150]}"}
    containers = {}
    for line in out.splitlines():
        name, cid, image_id, image, state, restarts = (line.split("|") + [""] * 6)[:6]
        containers[name.lstrip("/")] = {
            "id": cid[:12],
            "image_id": image_id[:19],
            "image": image,
            "state": state,
            "restarts": int(restarts or 0),
        }
    return {"containers": containers}


def collect_disk(config: Config) -> dict:
    disks = {}
    for name, path in config.paths.items():
        if path.exists():
            usage = shutil.disk_usage(path)
            disks[name] = {"path": str(path), "free": usage.free, "total": usage.total}
    return {"disks": disks}


def take(config: Config) -> dict:
    return {
        "ts": datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"),
        "docker": collect_docker(),
        "disk": collect_disk(config),
    }


def snapshots_dir(config: Config) -> Path:
    return config.state_dir / "snapshots"


def saved(config: Config) -> list[Path]:
    folder = snapshots_dir(config)
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def save(config: Config, snap: dict, keep: int) -> Path:
    folder = snapshots_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{snap['ts']}.json"
    path.write_text(json.dumps(snap, indent=2))
    for old in saved(config)[:-keep] if keep else []:
        old.unlink()
    return path


def diff(old: dict, new: dict) -> list[dict]:
    changes: list[dict] = []
    if "error" in old.get("docker", {}) or "error" in new.get("docker", {}):
        why = old["docker"].get("error") or new["docker"].get("error")
        changes.append({"kind": "skipped", "subject": "docker", "detail": f"could not compare containers: {why}"})
    else:
        before, after = old["docker"]["containers"], new["docker"]["containers"]
        for name in sorted(before.keys() - after.keys()):
            changes.append(
                {"kind": "gone", "subject": name, "detail": f"was {before[name]['state']} on {before[name]['image']}"}
            )
        for name in sorted(after.keys() - before.keys()):
            changes.append(
                {"kind": "new", "subject": name, "detail": f"{after[name]['state']} on {after[name]['image']}"}
            )
        for name in sorted(before.keys() & after.keys()):
            a, b = before[name], after[name]
            if a["id"] != b["id"]:
                change = f"image {a['image']} -> {b['image']}" if a["image_id"] != b["image_id"] else "same image"
                changes.append({"kind": "replaced", "subject": name, "detail": f"recreated, {change}"})
                continue
            if a["state"] != b["state"]:
                changes.append({"kind": "state", "subject": name, "detail": f"{a['state']} -> {b['state']}"})
            if b["restarts"] > a["restarts"]:
                changes.append(
                    {"kind": "restarts", "subject": name, "detail": f"{a['restarts']} -> {b['restarts']} restarts"}
                )
    for name in sorted(old.get("disk", {}).get("disks", {}).keys() & new.get("disk", {}).get("disks", {}).keys()):
        was, now = old["disk"]["disks"][name]["free"], new["disk"]["disks"][name]["free"]
        if abs(now - was) > DISK_MOVE:
            changes.append({"kind": "disk", "subject": name, "detail": f"free {was / GIB:.1f} -> {now / GIB:.1f} GiB"})
    return changes


def trouble(snap: dict) -> list[str]:
    """Containers that are not running right now, for the header of a report."""
    containers = snap.get("docker", {}).get("containers", {})
    return sorted(f"{n} is {c['state']}" for n, c in containers.items() if c["state"] not in ("running", "created"))

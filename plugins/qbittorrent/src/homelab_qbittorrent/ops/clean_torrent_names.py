# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Strip tracker spam from every finished torrent's name, folders and files.

Covers torrents that are not in the Jellyfin libraries (games, OS images, loose
movies): `www.UIndex.org    -    Name` folders, `Name [ UIndex.org ]` titles and
`Torrent Downloaded From UIndex.org.txt` files. qBittorrent renames on disk and
keeps seeding, so nothing is re-downloaded or re-checked. Library torrents are
named by rename_torrents.py and import_watch.py; this only removes the spam.

Dry run unless --apply. Journal in plans/ so --undo restores every name.
"""

import argparse
import json
import os
import re
import subprocess
import time

from homelab_jellyfix import stack

PLANS = stack.PLANS
SPAM = [
    (re.compile(r"^www\.UIndex\.org\s+-\s+", re.I), ""),
    (re.compile(r"\s*\[\s*UIndex\.org\s*\]", re.I), ""),
    (re.compile(r"^Torrent Downloaded From\s+UIndex\.org\s*\.txt$", re.I), "Torrent Downloaded From UIndex.org.txt"),
]


def clean(part):
    for rx, rep in SPAM:
        part = rx.sub(rep, part)
    return part.strip()


def qbt(path, data=None):
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "60", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def plan_for(t):
    files = json.loads(qbt("torrents/files", {"hash": t["hash"]}) or "[]")
    renames = []  # (kind, old, new) in apply order: files first, then name
    for f in files:
        parts = f["name"].split("/")
        new = "/".join(clean(p) for p in parts)
        if new != f["name"]:
            renames.append(("file", f["name"], new))
    name = clean(t["name"])
    if name != t["name"]:
        renames.append(("name", t["name"], name))
    return renames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    a = ap.parse_args()
    if a.undo:
        for r in reversed(json.load(open(a.undo))["renames"]):
            if r["kind"] == "file":
                qbt("torrents/renameFile", {"hash": r["hash"], "oldPath": r["new"], "newPath": r["old"]})
            else:
                qbt("torrents/rename", {"hash": r["hash"], "name": r["old"]})
        return
    done = []
    for t in json.loads(qbt("torrents/info") or "[]"):
        if t["progress"] < 1:
            continue
        for kind, old, new in plan_for(t):
            print(f"  {kind}: {old[:90]}\n     -> {new[:90]}")
            if a.apply:
                out = (
                    qbt("torrents/renameFile", {"hash": t["hash"], "oldPath": old, "newPath": new})
                    if kind == "file"
                    else qbt("torrents/rename", {"hash": t["hash"], "name": new})
                )
                if out:
                    print("     failed:", out[:120])
                else:
                    done.append({"hash": t["hash"], "kind": kind, "old": old, "new": new})
    if a.apply and done:
        p = os.path.join(PLANS, time.strftime("%Y%m%d-%H%M%S") + "-cleantorrents.json")
        json.dump({"renames": done}, open(p, "w"), indent=1)
        print("journal:", p)


if __name__ == "__main__":
    main()

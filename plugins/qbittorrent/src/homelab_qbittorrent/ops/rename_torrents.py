# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Retitle qBittorrent's torrents to the name of the content they actually hold.

A torrent's display name is whatever the tracker called it — `[Judas] Katainaka
no Ossan, Kensei ni Naru (From Old Country Bumpkin to Master Swordsman) - S02E07
[1080p][HEVC x265 10bit][Dual-Audio][Multi-Subs] (Weekly)`. Once the content has
been filed into the library it already carries a good name, so the torrent is
renamed to match what it is seeding.

This touches the display name only. Files are not moved, renamed or re-checked,
and seeding is unaffected.

The library is the only source of names used. A torrent whose content sits
outside the library — games, music, books, anything not filed — is reported and
left alone, because there is nothing authoritative to rename it to.

Idempotent, and --undo restores every previous name from the journal.
"""

import argparse
import json
import os
import re
import subprocess
import time

from homelab_jellyfix import stack

LIBRARY_ROOTS = [
    stack.lib("Anime"),
    stack.lib("Shows"),
    stack.lib("Animated Shows"),
    stack.lib("Movies"),
    stack.lib("Anime Movies"),
    stack.lib("Animated Movies"),
]
HOST_TO_CT = stack.container_map() or {stack.DOWNLOADS: "/downloads", stack.ROOT: "/media"}
PLANS = stack.PLANS
VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}
# A folder jellyFix has claimed. Only names under one of these are trustworthy;
# everywhere else the on-disk name is still the raw release name, and copying it
# onto the torrent makes the title worse, not better.
CLAIMED = re.compile(r"\[(?:tvdb|tmdb|imdb)id-[^\]]+\]")


def qbt(path, data=None):
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "60", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"qbt {path}: {r.stderr[:200]}")
    return r.stdout.strip()


def to_host(p):
    for host, ct in sorted(HOST_TO_CT.items(), key=lambda kv: -len(kv[1])):
        if p == ct or p.startswith(ct + "/"):
            return host + p[len(ct) :]
    return p


def in_library(p):
    return any(p == r or p.startswith(r + os.sep) for r in LIBRARY_ROOTS)


EP = re.compile(
    r"^(?P<show>.+?) - S(?P<s>\d+)E(?P<e>\d+)(?: - (?P<abs>\d{2,4}))? - .+?"
    r"(?P<tags>(?: ?\[[^\]]+\])+)?(?P<grp>-[^.\[\]]+)?\.\w+$"
)


def batch_name(t):
    """A torrent holding several episodes is named by the range it holds."""
    eps = []
    for f in json.loads(qbt("torrents/files", {"hash": t["hash"]}) or "[]"):
        if os.path.splitext(f["name"])[1].lower() not in VIDEO or f["priority"] == 0:
            continue
        m = EP.match(os.path.basename(f["name"]))
        if m:
            eps.append(m)
    if len(eps) < 2:
        return None
    key = lambda m: (int(m["s"]), int(m["e"]))
    lo, hi = min(eps, key=key), max(eps, key=key)
    rng = f"S{int(lo['s']):02d}E{int(lo['e']):02d}-" + (
        f"E{int(hi['e']):02d}" if lo["s"] == hi["s"] else f"S{int(hi['s']):02d}E{int(hi['e']):02d}"
    )
    absn = [int(m["abs"]) for m in eps if m["abs"]]
    if len(absn) == len(eps):
        rng += f" - {min(absn):03d}-{max(absn):03d}"
    tag = lambda m: (m["tags"] or "").strip() + (m["grp"] or "")
    common = max(sorted({tag(m) for m in eps}), key=lambda x: sum(tag(m) == x for m in eps))
    return f"{lo['show']} - {rng} {common}".strip()


def good_name(t):
    """-> (name, why) or (None, why-not)."""
    content = to_host(t.get("content_path") or "").rstrip("/")
    if not content or not in_library(content):
        return None, "content is not in the library"
    if content in LIBRARY_ROOTS:
        return None, "content sits loose in the library root"
    if not CLAIMED.search(content):
        return None, "not in a folder jellyFix has renamed yet"

    if os.path.isfile(content):
        return os.path.splitext(os.path.basename(content))[0], "episode/movie file"

    multi = batch_name(t)
    if multi:
        return multi, "batch of episodes"
    base = os.path.basename(content)
    if base.lower().startswith(("season ", "specials")):
        parent = os.path.basename(os.path.dirname(content))
        return f"{parent} - {base}", "season folder"
    return base, "series/movie folder"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        data = json.load(open(args.undo))
        n = 0
        for r in reversed(data["renames"]):
            out = qbt("torrents/rename", {"hash": r["hash"], "name": r["old"]})
            if out:
                print(f"  failed {r['old'][:60]}: {out[:120]}")
            else:
                n += 1
        print(f"restored {n} names")
        return

    torrents = json.loads(qbt("torrents/info") or "[]")
    plan, skip = [], []
    for t in torrents:
        name, why = good_name(t)
        if not name:
            skip.append((t["name"], t.get("category", ""), why))
        elif name == t["name"] or name == os.path.splitext(t["name"])[0] or os.path.splitext(t["name"])[0] == name:
            continue
        else:
            plan.append({"hash": t["hash"], "old": t["name"], "new": name, "why": why})

    print(f"=== {len(plan)} torrents to rename")
    for p in plan:
        print(f"  {p['old'][:88]}")
        print(f"    -> {p['new'][:88]}")
    print(f"\n=== {len(skip)} left alone")
    for n, cat, why in skip:
        print(f"  [{cat}] {n[:70]}\n      {why}")

    if not plan:
        return
    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    done = []
    for p in plan:
        out = qbt("torrents/rename", {"hash": p["hash"], "name": p["new"]})
        if out:
            print(f"  FAILED {p['old'][:50]}: {out[:120]}")
            continue
        done.append(p)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    j = os.path.join(PLANS, f"{stamp}-torrentnames.json")
    json.dump({"renames": done}, open(j, "w"), indent=2)
    print(f"\nrenamed {len(done)}\njournal: {j}")
    print(f"undo: ./ops/rename_torrents.py --undo {j}")


if __name__ == "__main__":
    main()

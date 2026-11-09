# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Re-point qBittorrent torrents at files jellyFix has since renamed or moved.

jellyFix renames the library; qBittorrent keeps seeding the old paths and every
affected torrent falls to `missingFiles`. The undo journals in `plans/` record
the moves, but they chain through quarantine hops and directory-level renames,
and Sonarr/Radarr moved some files on their own — so the journals alone do not
cover every torrent.

Exact file size is the reliable key instead. A 650 MB episode's byte count is
unique across a library in practice, so each torrent file is matched against an
on-disk inventory by size, with the basename and, when several files share a
size, path similarity breaking ties. Journals are consulted first because an
exact journal hit needs no guessing at all.

Only torrents in `missingFiles` are touched: they hold no data, so setLocation
cannot move anything, and a wrong guess costs a recheck, never a file. A torrent
is relinked only when *every* one of its files is found — a partial match would
leave it unseedable anyway. Everything else is reported and left alone.

    ops/relink_torrents.py                 # plan
    ops/relink_torrents.py --apply         # stop, re-point, recheck, resume
"""

import argparse
import difflib
import json
import os
import subprocess
import time
from collections import defaultdict

from homelab_jellyfix import stack

ROOT = stack.ROOT
# Walking all of ROOT means walking Music, Immich, Torrents, Backup and the
# Windows recycler too -- ~400k entries, and at least one directory on this
# volume blocks forever inside FUSE rather than returning an error, so the walk
# never comes back. The libraries plus the download directory are the only
# places a torrent's files can actually be, and they scan in about 30 seconds.
DEFAULT_ROOTS = [
    stack.lib("Anime"),
    stack.lib("Shows"),
    stack.lib("Animated Shows"),
    stack.lib("Movies"),
    stack.lib("Anime Movies"),
    stack.lib("Animated Movies"),
    stack.DOWNLOADS,
]
HOST_TO_CT = stack.container_map() or {stack.DOWNLOADS: "/downloads", stack.ROOT: "/media"}
CT_TO_HOST = {v: k for k, v in HOST_TO_CT.items()}
SKIP_DIRS = {"$RECYCLE.BIN", "System Volume Information", "msdownld.tmp", "_jellyfix_quarantine", ".Trash-1000"}
PLANS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plans")
MIN_SIZE = 1 << 20  # below this, size is not a fingerprint


def qbt(path, data=None):
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "120", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"qbt {path} failed: {r.stderr[:300]}")
    return r.stdout.strip()


def to_host(p):
    for ct, host in sorted(CT_TO_HOST.items(), key=lambda x: -len(x[0])):
        if p == ct or p.startswith(ct + "/"):
            return host + p[len(ct) :]
    return p


def to_container(p):
    for host, ct in sorted(HOST_TO_CT.items(), key=lambda x: -len(x[0])):
        if p == host or p.startswith(host + "/"):
            return ct + p[len(host) :]
    raise SystemExit(f"{p} is outside anything the container can see")


# ------------------------------------------------------------------ inventory
def inventory(roots):
    """size -> [paths] for every file under any of `roots`."""
    by_size = defaultdict(list)
    stack = list(roots)
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP_DIRS:
                        stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    if st.st_size:
                        by_size[st.st_size].append(e.path)
            except OSError:
                continue
    return by_size


def journal_map():
    """src -> dst over every recorded move, including directory renames."""
    m = {}
    for f in sorted(os.listdir(PLANS)):
        if not f.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(PLANS, f)))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("dry_run"):
            continue
        for key in ("moves", "renames", "filed"):
            for mv in d.get(key) or []:
                if isinstance(mv, dict) and mv.get("src") and mv.get("dst"):
                    m[mv["src"]] = mv["dst"]
    return m


def follow(path, jmap, seen=None):
    """Resolve a path through chained moves, including moves of a parent dir."""
    seen = seen or set()
    if path in seen:
        return path
    seen.add(path)
    if path in jmap:
        return follow(jmap[path], jmap, seen)
    # a parent directory may have been renamed instead of the file itself
    head, tail = os.path.split(path)
    while head and head != "/":
        if head in jmap:
            return follow(os.path.join(jmap[head], tail), jmap, seen)
        head, name = os.path.split(head)
        tail = os.path.join(name, tail)
    return path


def pick(cands, want_name, want_path):
    """Choose among same-size files: exact basename, then closest full path."""
    exact = [c for c in cands if os.path.basename(c) == want_name]
    if len(exact) == 1:
        return exact[0], "size+name"
    pool = exact or cands
    if len(pool) == 1:
        return pool[0], "size"
    stem = os.path.splitext(want_name)[0]
    best = max(
        pool,
        key=lambda c: difflib.SequenceMatcher(
            None, stem.lower(), os.path.splitext(os.path.basename(c))[0].lower()
        ).ratio(),
    )
    return best, "size+similar"


# ----------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--start-only",
        action="store_true",
        help="skip relinking; just start every stopped torrent that is complete, for a run whose rechecks outlived it",
    )
    ap.add_argument("--hash", action="append", help="limit to these torrents")
    ap.add_argument(
        "--index",
        metavar="JSON",
        help="cache the size index here and reuse it; scanning the "
        "volume takes ~10 minutes, so a plan and its apply "
        "should share one. Delete it after the library moves",
    )
    ap.add_argument(
        "--roots",
        action="append",
        metavar="DIR",
        help="directories to index, repeatable. Defaults to the six "
        "libraries plus Downloads; pass <root> to "
        "index the whole volume, which can hang on a damaged "
        "directory",
    )
    ap.add_argument("--no-recheck", action="store_true", help="re-point only; skip the hash recheck afterwards")
    args = ap.parse_args()

    torrents = json.loads(qbt("torrents/info") or "[]")
    if args.start_only:
        stopped = [t for t in torrents if t["state"] in ("stoppedUP", "pausedUP") and t["progress"] >= 1]
        print(f"starting {len(stopped)} complete but stopped torrents")
        start(stopped)
        return
    broken = [t for t in torrents if t["state"] == "missingFiles"]
    if args.hash:
        want = {h[:8] for h in args.hash}
        broken = [t for t in broken if t["hash"][:8] in want]
    if not broken:
        raise SystemExit("no torrents in missingFiles state")

    if args.index and os.path.exists(args.index):
        print(f"{len(broken)} torrents in missingFiles; reusing index {args.index}")
        by_size = defaultdict(list, {int(k): v for k, v in json.load(open(args.index)).items()})
    else:
        roots = args.roots or DEFAULT_ROOTS
        print(
            f"{len(broken)} torrents in missingFiles; indexing "
            + ", ".join(os.path.basename(r) or r for r in roots)
            + " ..."
        )
        by_size = inventory(roots)
        if args.index:
            json.dump(by_size, open(args.index, "w"))
    jmap = journal_map()
    print(f"  {sum(len(v) for v in by_size.values())} files indexed, {len(jmap)} recorded moves\n")

    plan, unresolved = [], []
    for t in sorted(broken, key=lambda x: x["name"].lower()):
        files = json.loads(qbt(f"torrents/files?hash={t['hash']}") or "[]")
        # Priority 0 means the file was deselected in qBittorrent and never
        # downloaded -- the `.unwanted\` sample images, MediaInfo dumps and
        # tracker adverts. They are not on disk and never were, so requiring
        # them to be found is what left nine of these torrents "unresolved"
        # when every file that actually exists had in fact been located.
        skipped = [f for f in files if f.get("priority", 1) == 0]
        files = [f for f in files if f.get("priority", 1) != 0]
        save = to_host(t["save_path"])
        mapped, misses = [], []
        for f in files:
            old = os.path.join(save, f["name"])
            new, how = None, None
            j = follow(old, jmap)
            if j != old and os.path.isfile(j) and os.path.getsize(j) == f["size"]:
                new, how = j, "journal"
            else:
                cands = by_size.get(f["size"], [])
                if cands and f["size"] >= MIN_SIZE:
                    new, how = pick(cands, os.path.basename(f["name"]), old)
                elif len(cands) == 1:
                    new, how = cands[0], "size"
            if new:
                mapped.append((f["name"], new, how))
            else:
                misses.append((f["name"], f["size"]))
        if misses or not mapped:
            unresolved.append((t, len(mapped), misses))
            continue
        newpaths = [n for _, n, _ in mapped]
        # renameFile takes an arbitrary relative path, so any common ancestor of
        # the matched files works as the save location.
        loc = os.path.commonpath(newpaths) if len(newpaths) > 1 else os.path.dirname(newpaths[0])
        renames = [(old, os.path.relpath(new, loc)) for old, new, _ in mapped]
        plan.append({"t": t, "loc": loc, "renames": renames, "mapped": mapped, "skipped": len(skipped)})

    for t, n, misses in unresolved:
        print(f"UNRESOLVED {t['hash'][:8]} {t['name'][:70]}")
        print(
            f"    {n} of {n + len(misses)} files found; missing "
            + ", ".join(f"{os.path.basename(m)} ({s / 1e6:.0f}MB)" for m, s in misses[:3])
            + (" ..." if len(misses) > 3 else "")
        )
    if unresolved:
        print()

    for p in plan:
        t = p["t"]
        print(f"RELINK {t['hash'][:8]} {t['name'][:70]}")
        print(f"    location: {p['loc']}")
        for old, new, how in p["mapped"][:4]:
            print(f"    {how:12} {os.path.basename(old)[:60]}")
            print(f"    {'':12} -> {os.path.basename(new)[:60]}")
        if len(p["mapped"]) > 4:
            print(f"    ... {len(p['mapped']) - 4} more files")

    print(f"\n{len(plan)} relinkable, {len(unresolved)} unresolved")
    if not args.apply:
        print("[plan only] re-run with --apply")
        return

    done, started = 0, []
    for p in plan:
        t = p["t"]
        qbt("torrents/stop", {"hashes": t["hash"]})
        qbt("torrents/pause", {"hashes": t["hash"]})
        ok = True
        for old, new in p["renames"]:
            if old != new:
                r = qbt("torrents/renameFile", {"hash": t["hash"], "oldPath": old, "newPath": new})
                if r:
                    print(f"  rename FAILED {t['hash'][:8]} {old}: {r[:120]}")
                    ok = False
        qbt("torrents/setLocation", {"hashes": t["hash"], "location": to_container(p["loc"])})
        if ok and not args.no_recheck:
            qbt("torrents/recheck", {"hashes": t["hash"]})
        if ok:
            started.append(t)
        done += ok
        print(f"  + {t['hash'][:8]} {t['name'][:60]}")

    print(f"\nrelinked {done} torrents")
    start(started)


CHECKING = {"checkingUP", "checkingDL", "checkingResumeData", "queuedForChecking", "moving"}


def start(torrents, tries=240, wait=15):
    """Start relinked torrents once qBittorrent has finished rechecking them.

    A start issued while a torrent is still checking is dropped on the floor,
    which is how a relink ends up correct on disk and still seeding nothing.
    """
    if not torrents:
        return
    want = {t["hash"]: t for t in torrents}
    for i in range(tries):
        info = {t["hash"]: t for t in json.loads(qbt("torrents/info") or "[]")}
        busy = [h for h in want if info.get(h, {}).get("state") in CHECKING]
        if not busy:
            break
        if i % 4 == 0:
            print(f"  waiting on {len(busy)} rechecks ...")
        time.sleep(wait)
    else:
        print("  rechecks still running; start them again with --start-only")
    hashes = "|".join(want)
    qbt("torrents/start", {"hashes": hashes})
    qbt("torrents/resume", {"hashes": hashes})
    forced = "|".join(h for h, t in want.items() if t.get("force_start"))
    if forced:
        qbt("torrents/setForceStart", {"hashes": forced, "value": "true"})
    print(f"started {len(want)} torrents")


if __name__ == "__main__":
    main()

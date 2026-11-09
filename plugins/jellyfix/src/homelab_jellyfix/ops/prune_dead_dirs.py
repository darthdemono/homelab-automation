# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Remove library directories that hold no video anywhere below them.

After a rename sweep the old release folders are left behind holding nothing but
regenerable metadata. This deletes those, and nothing else.

The guards matter more than the rule, because "no videos found" and "no videos
there" are not the same statement:

  * a subtree containing a directory that cannot be read is never a candidate.
    Madoka's `Season 01` returns EIO from a damaged NTFS inode, so it scans as
    empty while very likely still holding every episode;
  * a subtree holding anything that is not regenerable metadata is kept, and the
    file that saved it is named in the report — subtitles, PDFs, archives, the
    `.ignore` that stops Jellyfin wedging, or any extension not recognised;
  * `.trickplay` directories and dotfiles directories are never candidates in
    their own right;
  * the library roots themselves are never candidates.

Directories move to _jellyfix_quarantine/ with a journal, same as the sidecar
sweep. Nothing is unlinked, so a wrong call costs a --undo rather than data.
"""

import argparse
import json
import os
import shutil
import time

from homelab_jellyfix import stack

ROOTS = [
    stack.lib("Anime"),
    stack.lib("Shows"),
    stack.lib("Animated Shows"),
    stack.lib("Movies"),
    stack.lib("Anime Movies"),
    stack.lib("Animated Movies"),
]
QUARANTINE = stack.QUARANTINE
PLANS = stack.PLANS

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg"}
# Jellyfin rebuilds all of these on the next scan; nothing else is disposable.
REGENERABLE = {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn", ".xml", ".bif"}


def inspect(top):
    """-> (n_videos, keeper_or_None, unreadable)

    Walks by hand rather than with os.walk. os.walk's onerror hands you the
    failure and then simply omits the directory, so an unreadable subtree scans
    as an empty one — which is the exact case this guard exists to catch.
    """
    videos, keeper, blind = 0, None, False
    stack = [top]
    seen = set()
    while stack:
        d = stack.pop()
        if d in seen:
            continue
        seen.add(d)
        try:
            entries = list(os.scandir(d))
        except OSError:
            blind = True
            continue
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                blind = True
                continue
            if is_dir:
                stack.append(e.path)
                continue
            ext = os.path.splitext(e.name)[1].lower()
            if ext in VIDEO:
                videos += 1
            elif ext not in REGENERABLE and keeper is None:
                keeper = e.path
    return videos, keeper, blind


def candidates():
    out = []
    for root in ROOTS:
        try:
            names = sorted(os.listdir(root))
        except OSError as e:
            print(f"UNREADABLE {root}: {e}")
            continue
        for name in names:
            p = os.path.join(root, name)
            if not os.path.isdir(p) or name.startswith(".") or name.lower().endswith(".trickplay"):
                continue
            # a series folder can be alive while one season inside it is dead
            targets = [p]
            v, _k, _b = inspect(p)
            if v:
                targets = [
                    os.path.join(p, s)
                    for s in sorted(os.listdir(p))
                    if os.path.isdir(os.path.join(p, s))
                    and not s.startswith(".")
                    and not s.lower().endswith(".trickplay")
                ]
            for t in targets:
                vids, keeper, blind = inspect(t)
                if vids:
                    continue
                if blind:
                    out.append((t, "SKIP", "contains a directory that cannot be read"))
                elif keeper:
                    out.append((t, "SKIP", f"holds {os.path.basename(keeper)}"))
                else:
                    n = sum(
                        len(fs) for _d, _s, fs in os.walk(t, onerror=lambda e: None)
                    )  # count only, tree already proven readable
                    out.append((t, "DEAD", f"{n} regenerable files, no video"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        data = json.load(open(args.undo))
        n = 0
        for mv in reversed(data["moves"]):
            if os.path.exists(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                shutil.move(mv["dst"], mv["src"])
                n += 1
        print(f"restored {n} directories")
        return

    found = candidates()
    dead = [x for x in found if x[1] == "DEAD"]
    kept = [x for x in found if x[1] == "SKIP"]

    print(f"=== {len(dead)} directories to remove")
    for p, _s, why in dead:
        print(f"  {p}\n      {why}")
    print(f"\n=== {len(kept)} video-less directories kept")
    for p, _s, why in kept:
        print(f"  {p}\n      {why}")

    if not dead:
        return
    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(QUARANTINE, f"deaddirs-{stamp}")
    moves = []
    for p, _s, _w in dead:
        rel = os.path.relpath(p, stack.ROOT)
        dst = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(p, dst)
        moves.append({"src": p, "dst": dst})
        print(f"  removed {p}")
    jpath = os.path.join(PLANS, f"{stamp}-deaddirs.json")
    json.dump({"quarantine": dest_root, "moves": moves}, open(jpath, "w"), indent=2)
    print(f"\nmoved {len(moves)} directories to {dest_root}")
    print(f"journal: {jpath}")
    print(f"undo: ./ops/prune_dead_dirs.py --undo {jpath}")


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Lift movie folders out of collection folders so Radarr can see them.

Radarr treats each immediate child of a root folder as one movie. A box set
downloaded as `Marvel Cinematic Universe [MCU] .../Iron Man/Iron Man 2008 ....mkv`
puts the actual film two levels down, so Radarr sees one enormous "movie" it
cannot identify and every film inside it stays invisible.

This moves every nested movie folder up to the root and then removes the
collection wrapper, which by then holds nothing but its own artwork.

A folder is only moved when it is unambiguous:

  * it must contain video directly inside it, so season-style nesting and
    `.trickplay` directories are never candidates;
  * a name collision at the root is reported and skipped rather than merged —
    two folders wanting the same name usually means the film is already there;
  * a wrapper is only removed once everything with video is out of it and what
    remains is regenerable metadata. Anything else and the wrapper stays.

Journalled to plans/ and reversible with --undo.
"""

import argparse
import json
import os
import shutil
import time

from homelab_jellyfix import stack

ROOTS = [stack.lib("Movies"), stack.lib("Anime Movies"), stack.lib("Animated Movies")]
QUARANTINE = stack.QUARANTINE
PLANS = stack.PLANS

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}
# Jellyfin/Kodi bonus-content folders. They hold video and sit one level down,
# which is exactly the shape of a nested movie — but they belong to the film
# above them, and lifting one to the root creates a "movie" called Extras.
EXTRAS = {
    "extras",
    "featurettes",
    "behind the scenes",
    "deleted scenes",
    "interviews",
    "scenes",
    "shorts",
    "trailers",
    "other",
    "specials",
    "samples",
    "sample",
    "bonus",
    "making of",
    "subs",
    "screenshots",
}
REGENERABLE = {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn", ".xml", ".bif"}


def has_video_directly(d):
    try:
        return any(os.path.splitext(e.name)[1].lower() in VIDEO for e in os.scandir(d) if e.is_file())
    except OSError:
        return False


def only_regenerable(d):
    """True when nothing under d is worth keeping. Unreadable => False."""
    stack, ok = [d], True
    while stack:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            return False
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                    continue
            except OSError:
                return False
            if os.path.splitext(e.name)[1].lower() not in REGENERABLE:
                ok = False
    return ok


def survey():
    moves, drops, skips = [], [], []
    for root in ROOTS:
        try:
            tops = sorted(os.listdir(root))
        except OSError as e:
            skips.append((root, str(e)))
            continue
        for name in tops:
            wrapper = os.path.join(root, name)
            if not os.path.isdir(wrapper) or name.startswith("."):
                continue
            if has_video_directly(wrapper):
                continue  # already a normal movie folder
            try:
                children = sorted(os.listdir(wrapper))
            except OSError as e:
                skips.append((wrapper, str(e)))
                continue
            nested = [
                os.path.join(wrapper, c)
                for c in children
                if os.path.isdir(os.path.join(wrapper, c))
                and not c.lower().endswith(".trickplay")
                and c.lower() not in EXTRAS
                and has_video_directly(os.path.join(wrapper, c))
            ]
            if not nested:
                continue
            for n in nested:
                dst = os.path.join(root, os.path.basename(n))
                if os.path.exists(dst):
                    skips.append((n, f"{os.path.basename(dst)} already exists in {os.path.basename(root)}"))
                    continue
                moves.append({"src": n, "dst": dst})
            drops.append(wrapper)
    return moves, drops, skips


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        d = json.load(open(args.undo))
        n = 0
        for mv in reversed(d["moves"] + d.get("wrappers", [])):
            if os.path.exists(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                shutil.move(mv["dst"], mv["src"])
                n += 1
        print(f"restored {n}")
        return

    moves, drops, skips = survey()
    print(f"=== {len(moves)} movie folders to lift to their root")
    for m in moves:
        print(f"  {os.path.relpath(m['src'], stack.ROOT)}")
        print(f"    -> {os.path.relpath(m['dst'], stack.ROOT)}")
    print(f"\n=== {len(drops)} collection wrappers, removed once emptied")
    for d in drops:
        print(f"  {os.path.basename(d)}")
    if skips:
        print(f"\n=== {len(skips)} skipped")
        for p, why in skips:
            print(f"  {os.path.basename(p)}\n      {why}")

    if not moves or not args.apply:
        if moves:
            print("\n[plan only] re-run with --apply")
        return

    for m in moves:
        shutil.move(m["src"], m["dst"])
        print(f"  lifted {os.path.basename(m['dst'])}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(QUARANTINE, f"collections-{stamp}")
    wrappers = []
    for d in drops:
        if has_video_directly(d) or not only_regenerable(d):
            print(f"  kept {os.path.basename(d)} — still holds something")
            continue
        rel = os.path.relpath(d, stack.ROOT)
        dst = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(d, dst)
        wrappers.append({"src": d, "dst": dst})
        print(f"  removed wrapper {os.path.basename(d)}")

    j = os.path.join(PLANS, f"{stamp}-flatten.json")
    json.dump({"moves": moves, "wrappers": wrappers}, open(j, "w"), indent=2)
    print(f"\nlifted {len(moves)}, removed {len(wrappers)} wrappers")
    print(f"journal: {j}\nundo: ./ops/flatten_movie_dirs.py --undo {j}")


if __name__ == "__main__":
    main()

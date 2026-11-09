# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Sweep orphaned sidecars out of the Jellyfin libraries.

Renaming moved thousands of videos, and their sidecars did not all follow. What
is left behind is `.nfo` and `-thumb.jpg` naming episodes that are no longer in
that directory, folder artwork for movies that moved out, and `.trickplay` tile
directories whose video is gone. Jellyfin regenerates every one of these on the
next scan, so they are clutter, not data.

Classification is by prefix, not by a list of known suffixes: a sidecar belongs
to a video when some video stem in the same directory is a prefix of it and the
remainder starts with a separator. `X-backdrop.jpg` belongs to `X.mkv`; nothing
in `Dragon Ball Z/Season 3 - Frieza Saga` belongs to anything, because the
episodes were renamed away.

Nothing is deleted outright. Everything moves to a timestamped folder under
_jellyfix_quarantine/ with its library path preserved, and a journal is written
to plans/ so the whole sweep can be reversed.

Deliberately kept:
  * the Dragon Ball `.pdf` scans — content, not metadata;
  * `[Judas] Bleach 056-111 …/.ignore` — load-bearing, it is what stops Jellyfin
    wedging on the damaged NTFS inode;
  * anything under a `.claude/` directory;
  * every sidecar that still pairs with a video, and folder artwork in any
    directory that still has a video somewhere below it.
"""

import argparse
import json
import os
import re
import shutil
import time
from collections import Counter

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
# Regenerable metadata. Subtitles are NOT in here — they are never regenerable.
SIDECAR = {".nfo", ".jpg", ".jpeg", ".png", ".webp", ".tbn", ".xml"}
SUBTITLE = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup", ".smi"}
# Leftovers with no purpose: our own season.nfo backups, pre-rename nfo copies,
# tracker adverts, release screenshots, an aborted download fragment.
JUNK_EXT = {".bak", ".nfo-orig", ".txt", ".svg", ".parts"}

FOLDER_ASSETS = {
    "folder",
    "poster",
    "cover",
    "banner",
    "backdrop",
    "backdrop1",
    "backdrop2",
    "backdrop3",
    "fanart",
    "fanart1",
    "fanart2",
    "landscape",
    "logo",
    "clearlogo",
    "clearart",
    "disc",
    "discart",
    "thumb",
    "season",
    "tvshow",
    "movie",
    "characters",
    "keyart",
    "boxset",
}
SEASON_ASSET = re.compile(r"^season(\d+|-specials|-all)?(-poster|-banner|-fanart|-landscape)?$", re.I)

KEEP_EXT = {".pdf"}  # Dragon Ball scans
KEEP_NAMES = {".ignore"}  # keeps Jellyfin off the damaged inode
KEEP_DIR_PARTS = {".claude"}  # not ours


def keep(path, name):
    if os.path.splitext(name)[1].lower() in KEEP_EXT or name in KEEP_NAMES:
        return True
    return any(p in KEEP_DIR_PARTS for p in path.split(os.sep))


def paired_with(stem, vids):
    """A video stem that is a prefix of this sidecar stem, at a separator."""
    for v in vids:
        if stem == v:
            return v
        if stem.startswith(v) and stem[len(v)] in "-._":
            return v
    return None


UNREADABLE = re.compile(r"'(/mnt/[^']+)'")


def scan():
    """-> (list of (path, reason), list of unreadable dirs)"""
    errors = []
    # First pass only to learn which directories cannot be read. A tree holding
    # one of them is skipped entirely: the videos its artwork belongs to may be
    # sitting in the part we cannot see. (Madoka's Season 01 is such a dir —
    # a damaged NTFS inode that returns EIO on any read.)
    for root in ROOTS:
        for _ in os.walk(root, onerror=lambda e: errors.append(str(e))):
            pass
    blind = set()
    for e in errors:
        m = UNREADABLE.search(e)
        if m:
            blind.add(os.path.dirname(m.group(1)))

    doomed = []
    for root in ROOTS:
        for dp, dn, fn in os.walk(root, onerror=lambda e: None):
            if any(dp == b or dp.startswith(b + os.sep) or b.startswith(dp + os.sep) for b in blind):
                continue
            if ".trickplay" in dp:  # handled at the parent
                continue
            vids = {os.path.splitext(f)[0] for f in fn if os.path.splitext(f)[1].lower() in VIDEO}

            for d in list(dn):
                if d.lower().endswith(".trickplay") and not paired_with(d[: -len(".trickplay")], vids):
                    doomed.append((os.path.join(dp, d), "trickplay-orphan"))

            video_below = False
            for _d, _sd, _sf in os.walk(dp):
                if ".trickplay" in _d:
                    continue
                if any(os.path.splitext(x)[1].lower() in VIDEO for x in _sf):
                    video_below = True
                    break

            for f in fn:
                p = os.path.join(dp, f)
                if keep(dp, f):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO or ext in SUBTITLE:
                    continue
                stem = os.path.splitext(f)[0]
                if ext in JUNK_EXT:
                    doomed.append((p, "junk"))
                elif ext not in SIDECAR:
                    continue  # unrecognised: leave it alone
                elif paired_with(stem, vids):
                    continue
                elif stem.lower() in FOLDER_ASSETS or SEASON_ASSET.match(stem):
                    if not video_below:
                        doomed.append((p, "folder-art-orphan"))
                else:
                    doomed.append((p, "sidecar-orphan"))
    return doomed, errors


def prune_empty(dirs, dry):
    """Remove directories the sweep emptied. Deepest first, and only when
    genuinely empty — a directory still holding anything is kept, always."""
    removed = []
    for d in sorted(dirs, key=lambda x: x.count(os.sep), reverse=True):
        while d not in ROOTS and any(d.startswith(r + os.sep) for r in ROOTS):
            try:
                if os.listdir(d):
                    break
            except OSError:
                break
            removed.append(d)
            if not dry:
                try:
                    os.rmdir(d)
                except OSError:
                    break
            d = os.path.dirname(d)
    return removed


def undo(jpath):
    with open(jpath) as f:
        data = json.load(f)
    back = failed = 0
    for m in reversed(data["moves"]):
        if not os.path.exists(m["dst"]):
            print(f"  missing, skipped: {m['dst']}")
            failed += 1
            continue
        os.makedirs(os.path.dirname(m["src"]), exist_ok=True)
        try:
            shutil.move(m["dst"], m["src"])
            back += 1
        except OSError as e:
            print(f"  FAILED {m['src']}: {e}")
            failed += 1
    print(f"restored {back} items" + (f", {failed} failed" if failed else ""))
    if not failed:
        print(f"quarantine now empty: {data['quarantine']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, move nothing")
    ap.add_argument("--verbose", action="store_true", help="list every file")
    ap.add_argument("--undo", metavar="JOURNAL", help="restore everything a previous sweep quarantined")
    args = ap.parse_args()
    if args.undo:
        undo(args.undo)
        return
    dry = args.dry_run

    doomed, errors = scan()
    for e in errors:
        print(f"UNREADABLE {e}")

    by_reason = Counter(r for _p, r in doomed)
    total = 0
    for p, _r in doomed:
        if os.path.isdir(p):
            for dr, _sd, sf in os.walk(p):
                total += sum(os.path.getsize(os.path.join(dr, x)) for x in sf if os.path.exists(os.path.join(dr, x)))
        elif os.path.exists(p):
            total += os.path.getsize(p)

    print(f"\n{len(doomed)} items, {total / 1e6:.1f} MB")
    for r, n in by_reason.most_common():
        print(f"  {n:6}  {r}")
    print("\nby library:")
    for lib, n in Counter(next(r for r in ROOTS if p.startswith(r)).rsplit("/", 1)[1] for p, _ in doomed).most_common():
        print(f"  {n:6}  {lib}")
    if args.verbose:
        for p, r in sorted(doomed):
            print(f"  {r:18} {p}")

    if not doomed:
        return
    if dry:
        print("\n[dry-run] nothing moved")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(QUARANTINE, f"sidecars-{stamp}")
    journal, touched = [], set()
    for p, reason in doomed:
        rel = os.path.relpath(p, stack.ROOT)
        dst = os.path.join(dest_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.move(p, dst)
        except OSError as e:
            print(f"  FAILED {p}: {e}")
            continue
        journal.append({"src": p, "dst": dst, "reason": reason})
        touched.add(os.path.dirname(p))

    pruned = prune_empty(touched, dry=False)

    os.makedirs(PLANS, exist_ok=True)
    jpath = os.path.join(PLANS, f"{stamp}-sidecars.json")
    with open(jpath, "w") as f:
        json.dump({"quarantine": dest_root, "moves": journal, "pruned_dirs": pruned}, f, indent=2)

    print(f"\nmoved {len(journal)} items to {dest_root}")
    print(f"pruned {len(pruned)} emptied directories")
    print(f"journal: {jpath}")
    print("\nreverse with:")
    print(f"  ./ops/clean_sidecars.py --undo {jpath}")


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Generate Clementine/Amarok/Strawberry .mood sidecars for every track.

A .mood file is 1000 RGB triplets describing spectral energy across the track;
players that support it render the coloured "moodbar" under the seek bar. We
write one next to each audio file, sharing its stem, exactly like the .lrc and
.jpg sidecars the library already uses:

    <Album Artist>/<Album>/<Artist> - <Title>.flac
    <Album Artist>/<Album>/<Artist> - <Title>.mood     <- generated

Generation runs in the `musicfix-moodbar` container (the moodbar tool is a
GStreamer program and is far easier to ship in Debian than to assemble on
Fedora). The run is resumable -- an existing .mood is skipped -- and dry-run by
default like every mutating entry point here. Creations are journalled to
reports/moodbar_journal.jsonl so `--clean --commit` can remove exactly the
files this tool made and nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS

IMAGE = "musicfix-moodbar:latest"
JOURNAL = REPORTS / "moodbar_journal.jsonl"


def mood_path_for(rel):
    return os.path.splitext(rel)[0] + ".mood"


def load_inventory(inv):
    return [json.loads(l) for l in open(inv, encoding="utf-8") if l.strip()]


def build_worklist(records, root, *, force):
    """(worklist_lines, targets) for tracks still needing a .mood."""
    lines, targets = [], []
    for r in records:
        if r.get("error"):
            continue
        rel = r["path"]
        if os.path.splitext(rel)[1].lower() not in AUDIO_EXTS:
            continue
        mood_rel = mood_path_for(rel)
        if os.path.exists(os.path.join(root, mood_rel)) and not force:
            continue
        lines.append(f"/music/{rel}\t/music/{mood_rel}")
        targets.append(mood_rel)
    return lines, targets


def phase_clean(root, *, commit):
    if not JOURNAL.exists():
        print(f"[moodbar] no journal at {JOURNAL}")
        return 1
    made = []
    for line in open(JOURNAL, encoding="utf-8"):
        if line.strip():
            made.append(json.loads(line)["mood"])
    made = sorted(set(made))
    print(f"[moodbar] {'COMMIT' if commit else 'DRY-RUN'} clean: {len(made)} .mood files")
    if not commit:
        return 0
    removed = 0
    for rel in made:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            os.remove(p)
            removed += 1
    print(f"[moodbar] removed {removed}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate .mood sidecars (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--force", action="store_true", help="regenerate even if a .mood exists")
    ap.add_argument("--clean", action="store_true", help="remove .mood files this tool made")
    args = ap.parse_args(argv)

    if args.clean:
        return phase_clean(args.root, commit=args.commit)

    records = load_inventory(args.inventory)
    if args.limit:
        records = records[: args.limit]
    lines, targets = build_worklist(records, args.root, force=args.force)
    print(f"[moodbar] {'COMMIT' if args.commit else 'DRY-RUN'}: {len(lines)} tracks need a .mood")
    for rel in targets[:8]:
        print(f"  + {rel}")
    if len(targets) > 8:
        print(f"  ... and {len(targets) - 8} more")
    if not lines:
        return 0
    if not args.commit:
        print("[moodbar] nothing written; re-run with --commit")
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    wl_path = REPORTS / "moodbar_worklist.tsv"
    wl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{args.root}:/music",
        "-v",
        f"{wl_path}:/wl.tsv:ro",
        IMAGE,
        "--worklist",
        "/wl.tsv",
    ]
    rc = subprocess.call(cmd)

    # journal whatever actually landed, so --clean only ever removes our files
    made = 0
    with open(JOURNAL, "a", encoding="utf-8") as jf:
        for rel in targets:
            if os.path.exists(os.path.join(args.root, rel)):
                jf.write(json.dumps({"ts": time.time(), "mood": rel}, ensure_ascii=False) + "\n")
                made += 1
    print(f"[moodbar] journalled {made} .mood files")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

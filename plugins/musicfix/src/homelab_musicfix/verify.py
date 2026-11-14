# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Post-move consistency checks, to run before deleting anything.

The whole workflow is safe only because nothing is deleted until this passes.
It answers: did every file arrive, is every track where its tags say it should
be, did any sidecar get orphaned, and is the quarantine accounted for.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

from .settings import AUDIO_EXTS, IMAGE_EXTS, LYRIC_EXTS, MUSIC_ROOT, REPORTS, TMP_ROOT


def load(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify library state after applying a plan.")
    ap.add_argument("--music", default=str(MUSIC_ROOT))
    ap.add_argument("--plan", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument("--journal", default=str(REPORTS / "journal.jsonl"))
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    args = ap.parse_args(argv)

    plan = load(args.plan)
    journal = load(args.journal)
    inv = load(args.inventory)
    problems = []

    # 1. Every planned rename target should now exist.
    missing_targets = []
    for a in plan:
        if a["kind"] not in ("rename", "rename-sidecar"):
            continue
        dst = os.path.join(args.music, a["dst"])
        if not os.path.exists(dst):
            missing_targets.append(a["dst"])
    if missing_targets:
        problems.append(f"{len(missing_targets)} planned targets are missing")

    # 2. Nothing should be left behind in staging.
    staging = TMP_ROOT / "staging"
    stranded = []
    if os.path.isdir(staging):
        for dirpath, _d, files in os.walk(str(staging)):
            stranded.extend(os.path.join(dirpath, f) for f in files)
    if stranded:
        problems.append(f"{len(stranded)} files still stranded in {staging}")

    # 3. Track count must be conserved: every file is either in the library or
    #    in quarantine. A file in neither has been lost, which is the one
    #    outcome this whole design exists to prevent.
    def count_audio(root):
        n = 0
        for dirpath, dirnames, files in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".Trash")]
            n += sum(1 for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS)
        return n

    in_library = count_audio(args.music)
    in_quarantine = count_audio(str(TMP_ROOT)) if os.path.isdir(str(TMP_ROOT)) else 0
    expected = len(inv)
    print(f"[verify] audio in library:    {in_library}")
    print(f"[verify] audio in tmp/:       {in_quarantine}")
    print(f"[verify] audio at scan time:  {expected}")
    if expected and in_library + in_quarantine < expected:
        problems.append(
            f"FILE LOSS: {expected - in_library - in_quarantine} audio files "
            f"unaccounted for (library + tmp < inventory)"
        )

    # 4. Sidecars should still sit beside a track of the same stem. Folder-level
    #    artwork (folder.jpg, backdrop.jpg, cover (1).jpg, <Artist>_Avatar.jpg)
    #    is not a per-track sidecar and is excluded, matching plan.py's rules.
    from .plan import (
        FOLDER_ART_STEMS,
        _ARTIST_IMAGE_RE,
        _ART_INFIX_RE,  # noqa: PLC0415
        _COPY_SUFFIX_RE,
        find_sidecar_owner,
        norm,
    )

    orphans = []
    for dirpath, dirnames, files in os.walk(args.music):
        dirnames[:] = [d for d in dirnames if not d.startswith(".Trash")]
        tracks = [
            {"stem": os.path.splitext(f)[0], "n_title": norm(os.path.splitext(f)[0].split(" - ", 1)[-1]), "rec": {}}
            for f in files
            if os.path.splitext(f)[1].lower() in AUDIO_EXTS
        ]
        for f in files:
            stem, ext = os.path.splitext(f)
            if ext.lower() not in (LYRIC_EXTS | IMAGE_EXTS):
                continue
            bare = _COPY_SUFFIX_RE.sub("", _ART_INFIX_RE.sub("", stem)).lower()
            if bare in FOLDER_ART_STEMS or _ARTIST_IMAGE_RE.search(bare):
                continue
            if find_sidecar_owner(f, tracks) is None:
                orphans.append(os.path.relpath(os.path.join(dirpath, f), args.music))
    if orphans:
        problems.append(f"{len(orphans)} orphaned sidecars (no matching track)")

    # 5. No empty directories should remain.
    empties = []
    for dirpath, dirnames, files in os.walk(args.music, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(args.music):
            continue
        if not dirnames and not files:
            empties.append(os.path.relpath(dirpath, args.music))
    if empties:
        problems.append(f"{len(empties)} empty directories remain")

    # 6. No reserved characters or trailing junk should survive in a name.
    bad_names = []
    for dirpath, dirnames, files in os.walk(args.music):
        for name in list(dirnames) + list(files):
            if any(c in name for c in '<>:"\\|?*') or name != name.rstrip(" .") or "  " in name:
                bad_names.append(os.path.relpath(os.path.join(dirpath, name), args.music))
    if bad_names:
        problems.append(f"{len(bad_names)} names still contain reserved chars or spacing damage")

    print()
    for label, items in (
        ("missing targets", missing_targets),
        ("stranded in staging", stranded),
        ("orphan sidecars", orphans),
        ("empty dirs", empties),
        ("bad names", bad_names),
    ):
        if items:
            print(f"[verify] {label} ({len(items)}), first 10:")
            for i in items[:10]:
                print(f"    {i}")

    print()
    if problems:
        print("[verify] NOT CLEAN — do not delete the quarantine yet:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("[verify] all checks passed; quarantine is safe to review and delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

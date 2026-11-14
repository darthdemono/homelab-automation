# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Read-only inventory pass over the library.

Writes one JSON object per audio file to reports/inventory.jsonl, plus a
separate listing of non-audio files so sidecars, junk and strays can be
reasoned about later. This script never writes to the music volume.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .quality import probe
from .settings import AUDIO_EXTS, IMAGE_EXTS, JUNK_PATTERNS, LYRIC_EXTS, MUSIC_ROOT, REPORTS
from .tags import read_tags

JUNK_RES = [re.compile(p) for p in JUNK_PATTERNS]


def is_junk(name: str) -> bool:
    return any(r.match(name) for r in JUNK_RES)


# Trash and metadata directories are not part of the library.
SKIP_DIRS = {
    ".Trash-1000",
    ".Trash",
    "$RECYCLE.BIN",
    "System Volume Information",
    "@eaDir",
    ".stfolder",
    "lost+found",
    "__MACOSX",
}


def _listdir(path):
    """One directory listing, split into subdirs and files.

    Uses entry.is_dir() so the d_type from readdir is enough and no stat(2) is
    issued per entry. On this ntfs-3g volume every stat is a userspace round
    trip, so avoiding them is the difference between minutes and an hour.
    """
    subdirs, files = [], []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name in SKIP_DIRS or entry.name.startswith(".Trash"):
                            continue
                        subdirs.append(entry.path)
                    else:
                        files.append(entry.name)
                except OSError:
                    continue
    except OSError as exc:
        print(f"WALK ERROR {path}: {exc}", file=sys.stderr)
    return sorted(subdirs), sorted(files)


def walk(root, workers=16):
    """Yield (dirpath, subdirs, filenames), listing directories in parallel.

    The bottleneck is FUSE request latency rather than CPU or bandwidth, so
    issuing many listings concurrently gives a near-linear speedup.
    """
    frontier = [root]
    while frontier:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_listdir, frontier))
        nxt = []
        for path, (subdirs, files) in zip(frontier, results):
            yield path, subdirs, files
            nxt.extend(subdirs)
        frontier = nxt


def scan_file(path, root):
    rel = os.path.relpath(path, root)
    rec = {"path": rel, "abspath": path}
    try:
        st = os.stat(path)
        rec["size"] = st.st_size
        rec["mtime"] = int(st.st_mtime)
    except OSError as exc:
        rec["error"] = f"stat: {exc}"
        return rec
    if rec["size"] == 0:
        rec["error"] = "zero-byte file"
        return rec
    rec["quality"] = probe(path)
    rec["tags"] = read_tags(path)
    return rec


def changed_paths_from_journals(journals, root):
    """Library-relative paths touched by a set of journals, src and dst alike.

    A move invalidates two rows: the one it left (now gone) and the one it
    landed on. A tag write invalidates one. Both are recorded, so the paths that
    need re-probing can be read off rather than rediscovered by a full walk.
    """
    out = set()
    root = os.path.abspath(root)
    for jp in journals:
        if not os.path.exists(jp):
            continue
        with open(jp, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                for key in ("src", "dst", "path"):
                    val = e.get(key)
                    if not val:
                        continue
                    full = os.path.abspath(val)
                    if full.startswith(root + os.sep):
                        out.add(os.path.relpath(full, root))
    return out


def update_inventory(root, out_path, rels, workers):
    """Re-probe just `rels` and patch them into the inventory already on disk.

    A full scan is one ffprobe subprocess per file -- ~9.5/s over FUSE, so about
    19 minutes for this library. That is the right price for a cold inventory
    and entirely the wrong one for a pass that touched 160 files. Rows whose
    file no longer exists are dropped, changed rows are replaced, new ones are
    appended; everything else is left exactly as it was.
    """
    existing = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    existing[rec["path"]] = rec

    rels = [r for r in rels if os.path.splitext(r)[1].lower() in AUDIO_EXTS]
    gone = [r for r in rels if not os.path.exists(os.path.join(root, r))]
    live = [r for r in rels if os.path.exists(os.path.join(root, r))]
    print(
        f"[scan] incremental: {len(live)} to re-probe, {len(gone)} vanished (inventory has {len(existing)})", flush=True
    )

    for r in gone:
        existing.pop(r, None)
    if live:
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for rec in pool.map(lambda p: scan_file(os.path.join(root, p), root), live):
                existing[rec["path"]] = rec
        print(f"[scan] re-probed {len(live)} in {time.time() - t0:.1f}s", flush=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        for _, rec in sorted(existing.items()):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[scan] inventory now {len(existing)} rows -> {out_path}", flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inventory the music library (read-only).")
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--out", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--others-out", default=str(REPORTS / "non_audio.jsonl"))
    ap.add_argument(
        "--workers",
        type=int,
        default=12,
        help="parallel probes; I/O bound, but this volume is ntfs-3g so don't go wild",
    )
    ap.add_argument("--limit", type=int, default=0, help="stop after N audio files (for testing)")
    ap.add_argument(
        "--journal",
        action="append",
        default=[],
        help="patch the inventory from this journal instead of walking the "
        "whole library. Repeatable. Seconds instead of ~19 minutes.",
    )
    ap.add_argument("--paths", help="file of library-relative paths to re-probe (same idea)")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    if args.journal or args.paths:
        rels = changed_paths_from_journals(args.journal, root)
        if args.paths:
            with open(args.paths, encoding="utf-8") as fh:
                rels |= {l.strip() for l in fh if l.strip()}
        return update_inventory(root, args.out, sorted(rels), args.workers)

    print(f"[scan] walking {root} ...", flush=True)
    audio, others, dirs = [], [], []
    t0 = time.time()
    for dirpath, dirnames, filenames in walk(root, workers=args.workers):
        dirs.append(
            {
                "path": os.path.relpath(dirpath, root),
                "n_subdirs": len(dirnames),
                "n_files": len(filenames),
            }
        )
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            ext = os.path.splitext(fn)[1].lower()
            if ext in AUDIO_EXTS:
                audio.append(full)
            else:
                kind = (
                    "junk"
                    if is_junk(fn)
                    else "lyrics"
                    if ext in LYRIC_EXTS
                    else "image"
                    if ext in IMAGE_EXTS
                    else "nfo"
                    if ext == ".nfo"
                    else "other"
                )
                # Deliberately no stat() here: size is not needed to plan a
                # sidecar move, and one stat per file dominates the walk.
                others.append(
                    {
                        "path": os.path.relpath(full, root),
                        "kind": kind,
                        "ext": ext,
                    }
                )
        if len(dirs) % 500 == 0:
            print(
                f"[scan]   {len(dirs)} dirs, {len(audio)} audio, {len(others)} other ({time.time() - t0:.0f}s)",
                flush=True,
            )
        if args.limit and len(audio) >= args.limit:
            break
    if args.limit:
        audio = audio[: args.limit]
    print(
        f"[scan] walk done in {time.time() - t0:.1f}s: {len(audio)} audio, {len(others)} non-audio, {len(dirs)} dirs",
        flush=True,
    )

    with open(args.others_out, "w", encoding="utf-8") as fh:
        for rec in others:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(str(REPORTS / "dirs.jsonl"), "w", encoding="utf-8") as fh:
        for rec in dirs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[scan] probing {len(audio)} audio files with {args.workers} workers ...", flush=True)
    t0 = time.time()
    done = 0
    with open(args.out, "w", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for rec in pool.map(lambda p: scan_file(p, root), audio):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done += 1
            if done % 250 == 0:
                rate = done / max(time.time() - t0, 0.001)
                eta = (len(audio) - done) / max(rate, 0.001)
                print(f"[scan] {done}/{len(audio)}  {rate:.1f}/s  eta {eta / 60:.1f}m", flush=True)
    print(f"[scan] complete in {(time.time() - t0) / 60:.1f}m -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

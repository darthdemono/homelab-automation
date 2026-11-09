# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Reconcile a replica library against recovery music.

The recovery tree is not assumed to mirror the canonical layout.  Tracks are
matched only after both sides derive the same canonical path from their tags.
An unreadable target at that path is a repair candidate; an absent path is
reported as missing, not silently imported as a different release.  ``--repair``
copies only those candidates and moves the unreadable WD original aside.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from .settings import AUDIO_EXTS, REPORTS
from .naming import canonical_paths
from .scan import scan_file, walk
from .tags import build_vocabulary


def scan(root: str, workers: int) -> list[dict]:
    files = [
        os.path.join(directory, name)
        for directory, _dirs, names in walk(root, workers=workers)
        for name in names
        if os.path.splitext(name)[1].lower() in AUDIO_EXTS
    ]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda path: scan_file(path, root), files))


def classify(source: dict, target: dict | None) -> str:
    if (source.get("quality") or {}).get("error") or source.get("error"):
        return "source-unreadable"
    if target is None:
        return "target-missing"
    if (target.get("quality") or {}).get("error") or target.get("error"):
        return "repair-candidate"
    return "matching-readable"


def repair(source: dict, target: dict, target_root: str, recovery_root: str):
    """Copy a confirmed original over an unreadable target without data loss."""
    src = source["abspath"]
    dst = target["abspath"]
    rel = os.path.relpath(dst, target_root)
    saved = os.path.join(recovery_root, rel)
    part = dst + ".musicfix-part"
    os.makedirs(os.path.dirname(saved), exist_ok=True)
    shutil.copy2(src, part)
    if _sha256(src) != _sha256(part):
        os.unlink(part)
        raise OSError("copy checksum verification failed")
    shutil.move(dst, saved)
    os.replace(part, dst)
    return saved


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit WD Blue Music against Big Chungus Dupe; read-only.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--out", default=str(REPORTS / "dupe_reconcile.jsonl"))
    parser.add_argument(
        "--repair", action="store_true", help="copy confirmed unreadable matches; default is report-only"
    )
    parser.add_argument("--recovery-root", required=True)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)

    print(f"[reconcile] scanning source {args.source}", flush=True)
    sources = scan(args.source, args.workers)
    vocabulary = build_vocabulary(sources)

    counts = Counter()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as report:
        for source in sources:
            canonical = canonical_paths(source, vocabulary=vocabulary).get("rel_path")
            target_path = os.path.join(args.target, canonical) if canonical else ""
            target = scan_file(target_path, args.target) if target_path and os.path.exists(target_path) else None
            status = classify(source, target)
            saved = None
            if status == "repair-candidate" and args.repair:
                saved = repair(source, target, args.target, args.recovery_root)
                status = "repaired"
            entry = {
                "status": status,
                "canonical_path": canonical,
                "source": source["path"],
                "target": target["path"] if target else None,
                "source_error": source.get("error") or (source.get("quality") or {}).get("error"),
                "target_error": (target.get("error") or (target.get("quality") or {}).get("error")) if target else None,
                "saved_original": saved,
            }
            report.write(json.dumps(entry, ensure_ascii=False) + "\n")
            counts[status] += 1

    print(f"[reconcile] report -> {args.out}")
    for status, count in sorted(counts.items()):
        print(f"[reconcile] {status:20} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Execute a plan. Dry-run unless --commit is passed. Nothing is ever deleted.

The library and the temp area live on the same volume (the library volume), so
every operation here is a rename(2): instant, atomic, and no data is copied.
That is what makes the stage-and-commit approach cheap enough to use.

Phases, in order:

  quarantine  junk / broken / duplicate files  ->  tmp/quarantine/<kind>/<orig path>
  stage       every file that needs renaming   ->  tmp/staging/<final path>
  commit      staged files                     ->  Music/<final path>
  prune       now-empty directories            ->  recorded, then removed

Renaming through a staging area rather than in place is deliberate. Direct
in-place renames can collide (A->B while B->C) and can interleave badly with a
Navidrome scan; moving out and back never can. Every operation is appended to
reports/journal.jsonl so `undo` can walk it backwards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

from .settings import MUSIC_ROOT, Q_BADTAGS, Q_DUPES, Q_EMPTY, Q_JUNK, Q_LEFTOVERS, Q_UNKNOWN, REPORTS, TMP_ROOT

STAGING = TMP_ROOT / "staging"

QUARANTINE_DIR = {
    "quarantine-junk": Q_JUNK,
    "quarantine-dupe": Q_DUPES,
    "quarantine-badtag": Q_BADTAGS,
    "quarantine-leftover": Q_LEFTOVERS,
    "orphan-sidecar": Q_UNKNOWN,
}


class Journal:
    """Append-only record of completed operations, for undo."""

    def __init__(self, path, enabled=True):
        self.path = path
        self.enabled = enabled
        self.fh = None
        if enabled:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.fh = open(path, "a", encoding="utf-8")

    def record(self, op, src, dst, **extra):
        if not self.fh:
            return
        rec = {"ts": time.time(), "op": op, "src": src, "dst": dst, **extra}
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()


def safe_move(src, dst, journal, *, commit, op):
    """Move src to dst, never overwriting. Returns the path actually used."""
    if not os.path.exists(src):
        return None, "source missing"
    if os.path.exists(dst):
        # Same file already there (case-insensitive volume) - nothing to do.
        try:
            if os.path.samefile(src, dst):
                return dst, None
        except OSError:
            pass
        base, ext = os.path.splitext(dst)
        n = 2
        while os.path.exists(f"{base} ({n}){ext}"):
            n += 1
        dst = f"{base} ({n}){ext}"
    if not commit:
        return dst, None
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError:
        # Different filesystem, or ntfs-3g refusing an in-place rename.
        shutil.move(src, dst)
    journal.record(op, src, dst)
    return dst, None


def phase_quarantine(actions, music, journal, *, commit):
    n, errors = 0, []
    for a in actions:
        kind = a["kind"]
        base = QUARANTINE_DIR.get(kind)
        if base is None:
            continue
        src = os.path.join(music, a["src"])
        dst = os.path.join(str(base), a["src"])
        got, err = safe_move(src, dst, journal, commit=commit, op=kind)
        if err:
            errors.append((a["src"], err))
        else:
            n += 1
            print(f"  {kind}: {a['src']}" + (f"\n      -> kept: {a['kept']}" if a.get("kept") else ""))
    return n, errors


def phase_stage(actions, music, journal, *, commit):
    """Move everything that needs a new name out to staging, keyed by final path."""
    n, errors = 0, []
    for a in actions:
        if a["kind"] not in ("rename", "rename-sidecar"):
            continue
        src = os.path.join(music, a["src"])
        dst = os.path.join(str(STAGING), a["dst"])
        got, err = safe_move(src, dst, journal, commit=commit, op="stage")
        if err:
            errors.append((a["src"], err))
        else:
            n += 1
    return n, errors


def phase_commit(music, journal, *, commit):
    """Move staged files back into the library at their final paths."""
    n, errors = 0, []
    if not os.path.isdir(STAGING):
        return 0, []
    for dirpath, _dirnames, filenames in os.walk(str(STAGING)):
        for fn in filenames:
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, str(STAGING))
            dst = os.path.join(music, rel)
            got, err = safe_move(src, dst, journal, commit=commit, op="commit")
            if err:
                errors.append((rel, err))
            else:
                n += 1
    return n, errors


def phase_prune(music, journal, *, commit):
    """Remove directories that are now empty, recording them first.

    Directories hold no data, so this is the one place we remove rather than
    move. The path is journalled so undo can recreate it.
    """
    removed = []
    for dirpath, dirnames, filenames in os.walk(music, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(music):
            continue
        try:
            if not os.listdir(dirpath):
                rel = os.path.relpath(dirpath, music)
                removed.append(rel)
                if commit:
                    os.rmdir(dirpath)
                    journal.record("rmdir", dirpath, None)
        except OSError as exc:
            print(f"  prune skip {dirpath}: {exc}", file=sys.stderr)
    if removed:
        os.makedirs(str(Q_EMPTY), exist_ok=True)
        with open(os.path.join(str(Q_EMPTY), "removed-dirs.txt"), "a", encoding="utf-8") as fh:
            for r in removed:
                fh.write(r + "\n")
    return len(removed), []


PHASES = {
    "quarantine": phase_quarantine,
    "stage": phase_stage,
    "commit": phase_commit,
    "prune": phase_prune,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Execute a plan (dry-run by default).")
    ap.add_argument("--plan", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument("--music", default=str(MUSIC_ROOT))
    ap.add_argument("--phase", required=True, choices=list(PHASES) + ["all"])
    ap.add_argument("--commit", action="store_true", help="actually move files; without this nothing is touched")
    ap.add_argument("--journal", default=str(REPORTS / "journal.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    actions = []
    if os.path.exists(args.plan):
        with open(args.plan, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    actions.append(json.loads(line))
    if args.limit:
        actions = actions[: args.limit]

    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[apply] {mode}  phase={args.phase}  actions={len(actions)}")
    if not args.commit:
        print("[apply] nothing will be moved; re-run with --commit when the plan looks right")

    journal = Journal(args.journal, enabled=args.commit)
    try:
        phases = list(PHASES) if args.phase == "all" else [args.phase]
        for name in phases:
            fn = PHASES[name]
            print(f"[apply] --- {name} ---")
            if name in ("commit", "prune"):
                n, errors = fn(args.music, journal, commit=args.commit)
            else:
                n, errors = fn(actions, args.music, journal, commit=args.commit)
            print(f"[apply] {name}: {n} operations, {len(errors)} errors")
            for path, err in errors[:20]:
                print(f"    ERROR {path}: {err}")
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

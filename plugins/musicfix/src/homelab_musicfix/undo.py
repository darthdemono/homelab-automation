# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Reverse a journal produced by `apply`, newest operation first.

Because apply only ever renames (and records every rename), an undo is exact:
each entry moves back to where it came from. Directories removed by the prune
phase are recreated.
"""

from __future__ import annotations

import argparse
import json
import os

from .settings import REPORTS


def main(argv=None):
    ap = argparse.ArgumentParser(description="Undo operations from a journal (dry-run by default).")
    ap.add_argument("--journal", default=str(REPORTS / "journal.jsonl"))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--since", type=float, default=0.0, help="only undo operations with ts >= this epoch time")
    args = ap.parse_args(argv)

    if not os.path.exists(args.journal):
        print(f"[undo] no journal at {args.journal}")
        return 1

    entries = []
    with open(args.journal, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                e = json.loads(line)
                if e.get("ts", 0) >= args.since:
                    entries.append(e)

    print(f"[undo] {'COMMIT' if args.commit else 'DRY-RUN'}  {len(entries)} operations to reverse")
    ok = failed = 0
    for e in reversed(entries):
        op, src, dst = e["op"], e["src"], e.get("dst")
        if op == "rmdir":
            if args.commit:
                os.makedirs(src, exist_ok=True)
            ok += 1
            continue
        if not dst:
            continue
        if not os.path.exists(dst):
            print(f"  MISSING {dst}")
            failed += 1
            continue
        if os.path.exists(src):
            print(f"  BLOCKED original path occupied: {src}")
            failed += 1
            continue
        if args.commit:
            os.makedirs(os.path.dirname(src), exist_ok=True)
            os.rename(dst, src)
        ok += 1
    print(f"[undo] {ok} reversed, {failed} could not be reversed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

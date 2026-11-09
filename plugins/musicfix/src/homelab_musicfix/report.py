# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Human-readable summaries of the inventory and the plan.

Reading 10k lines of JSONL to decide whether a plan is sane does not work. This
renders the parts a human actually needs to eyeball before committing: what is
being quarantined and why, which duplicate lost and by how much, and a sample of
the renames.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict

from .settings import REPORTS


def load(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def human_rate(bps):
    return f"{bps // 1000}k" if bps else "?"


def q(d):
    if not d:
        return "?"
    tier = "lossless" if d.get("lossless") else "lossy"
    depth = f"{d['bit_depth']}bit " if d.get("bit_depth") else ""
    rate = f"{d['sample_rate'] // 1000}kHz " if d.get("sample_rate") else ""
    return f"{d.get('codec', '?'):5} {human_rate(d.get('bit_rate')):>6} {depth}{rate}{tier}"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarise the inventory and plan.")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--plan", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument("--samples", type=int, default=12)
    args = ap.parse_args(argv)

    inv = load(args.inventory)
    plan = load(args.plan)

    print("=" * 78)
    print(f"INVENTORY  {len(inv)} audio files")
    print("=" * 78)
    codecs = Counter((r.get("quality") or {}).get("codec") for r in inv)
    for c, n in codecs.most_common():
        print(f"  {str(c):10} {n:6}")
    lossless = sum(1 for r in inv if (r.get("quality") or {}).get("lossless"))
    total_size = sum(r.get("size") or 0 for r in inv)
    print(f"  {'lossless':10} {lossless:6}  ({100 * lossless / max(len(inv), 1):.1f}%)")
    print(f"  {'total':10} {total_size / 2**30:6.1f} GiB")

    errs = [r for r in inv if r.get("error") or (r.get("quality") or {}).get("error")]
    if errs:
        print(f"\n  unreadable: {len(errs)}")
        for r in errs[: args.samples]:
            print(f"    {r['path']}: {r.get('error') or r['quality']['error']}")

    print()
    print("=" * 78)
    print(f"PLAN  {len(plan)} actions")
    print("=" * 78)
    kinds = Counter(a["kind"] for a in plan)
    for k, n in kinds.most_common():
        print(f"  {k:20} {n:6}")

    problems = Counter()
    for a in plan:
        for p in a.get("problems") or []:
            problems[p] += 1
    if problems:
        print("\n  metadata problems found:")
        for p, n in problems.most_common():
            print(f"    {p:28} {n:6}")

    for kind, title in (
        ("quarantine-dupe", "DUPLICATES (loser -> kept)"),
        ("quarantine-badtag", "UNUSABLE METADATA"),
        ("quarantine-leftover", "PLAYER LEFTOVERS"),
        ("quarantine-junk", "JUNK"),
        ("rename", "RENAMES"),
        ("rename-sidecar", "SIDECAR RENAMES"),
        ("orphan-sidecar", "ORPHAN SIDECARS"),
    ):
        items = [a for a in plan if a["kind"] == kind]
        if not items:
            continue
        print()
        print("-" * 78)
        print(f"{title}  ({len(items)})")
        print("-" * 78)
        for a in items[: args.samples]:
            if kind == "quarantine-dupe":
                print(f"  DROP {a['src']}")
                print(f"       {q(a.get('this'))}")
                print(f"  KEEP {a['kept']}")
                print(f"       {q(a.get('kept_quality'))}")
                print()
            elif kind in ("rename", "rename-sidecar"):
                print(f"  {a['src']}")
                print(f"    -> {a['dst']}")
            else:
                print(f"  {a['src']}   ({a.get('reason', '')})")
        if len(items) > args.samples:
            print(f"  ... and {len(items) - args.samples} more")

    # Space that quarantining would reclaim, once the quarantine is emptied.
    size_by_path = {r["path"]: (r.get("size") or 0) for r in inv}
    dupe_bytes = sum(size_by_path.get(a["src"], 0) for a in plan if a["kind"] == "quarantine-dupe")
    if dupe_bytes:
        print()
        print(
            f"  duplicate copies occupy {dupe_bytes / 2**30:.2f} GiB (reclaimed only after you delete the quarantine)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

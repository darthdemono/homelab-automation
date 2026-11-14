# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Fill a missing DISCNUMBER from the rest of the folder.

Players such as Symphonium group by disc, and a track with no DISCNUMBER (or 0)
beside tracks tagged 1 lands on its own phantom "disc 0". Folders where *no*
track has a disc are consistent and left alone.

    python -m homelab_musicfix.run discs                    # dry-run
    python -m homelab_musicfix.run discs --commit           # stop Navidrome first
    python -m homelab_musicfix.run discs --restore --commit
"""

import argparse
import collections
import json
import os
import re

from .settings import MUSIC_ROOT, REPORTS
from .editions import write_keys

JOURNAL = REPORTS / "discs_journal.jsonl"


def _disc(rec):
    v = (rec["tags"].get("discnumber") or [""])[0]
    m = re.match(r"\s*(\d+)", str(v))
    return int(m.group(1)) if m else 0


def plan(inventory):
    by_dir = collections.defaultdict(list)
    for line in open(inventory, encoding="utf-8"):
        r = json.loads(line)
        if not r.get("error"):
            by_dir[r["path"].rsplit("/", 1)[0]].append(r)
    out = []
    for rows in by_dir.values():
        have = collections.Counter(d for d in map(_disc, rows) if d)
        if not have:
            continue
        fill = have.most_common(1)[0][0]
        out += [(r["path"], (r["tags"].get("discnumber") or [None])[0], str(fill)) for r in rows if not _disc(r)]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args(argv)

    if args.restore:
        jobs = [json.loads(l) for l in open(JOURNAL, encoding="utf-8")] if JOURNAL.exists() else []
        todo = [(j["path"], j["before"]) for j in jobs if not j.get("error")]
    else:
        found = plan(args.inventory)
        todo = [(p, b) for p, b, _ in found]
        fills = {p: f for p, _, f in found}
    print(f"[discs] {'COMMIT' if args.commit else 'DRY-RUN'} {len(todo)} files")
    if not args.commit:
        for p, _ in todo[:20]:
            print("  ", p)
        return 0
    with open(JOURNAL, "a", encoding="utf-8") as jfh:
        for p, before in todo:
            if args.restore:
                err = write_keys(os.path.join(MUSIC_ROOT, p), {"discnumber": [before] if before else []})
            else:
                err = write_keys(os.path.join(MUSIC_ROOT, p), {"discnumber": [fills[p]]})
                jfh.write(json.dumps({"path": p, "before": before, "after": fills[p], "error": err}) + "\n")
            if err:
                print("  ERROR", p, err)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

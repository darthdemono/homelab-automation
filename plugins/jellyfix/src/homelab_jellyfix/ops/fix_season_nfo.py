# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Normalise season.nfo <title> tags that Jellyfin filled with the wrong names.

Jellyfin scraped Bleach's season titles from a provider whose season structure
does not match TVDB's flat numbering, writing names like
"BLEACH: Thousand-Year Blood War - The Conflict" into season.nfo for seasons that
are nothing of the sort. A local season.nfo beats every metadata provider, so
clearing the library's fetchers does not undo it — the file itself must be fixed.

Sets <title> to "Season N" (or "Specials" for season 0) wherever it disagrees.
Everything else in the file (plot, ids, artwork) is left untouched.

  ./fix_season_nfo.py --dry-run            # report only
  ./fix_season_nfo.py                      # fix every configured TV library
  ./fix_season_nfo.py --path "/mnt/.../BLEACH (2004) [tvdbid-74796]"
"""

import argparse
import os
import re
import shutil
import xml.etree.ElementTree as ET

from homelab_jellyfix import stack  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def expected_title(season_no, folder):
    """Prefer the folder's own label when it carries one.

    "Season 2 - Tournament Saga" is a deliberate choice by the library owner and
    is worth keeping; a bare "Season 2" folder just becomes "Season 2".
    """
    if season_no is None:
        return None
    base = os.path.basename(folder)
    if season_no == 0:
        return "Specials"
    m = re.match(r"(?i)^season\s*0*(\d+)\s*[-–—]\s*(.+)$", base)
    if m and m.group(2).strip():
        return f"Season {season_no} - {m.group(2).strip()}"
    return f"Season {season_no}"


def season_number(root, folder):
    n = root.find("seasonnumber")
    if n is not None and (n.text or "").strip().isdigit():
        return int(n.text.strip())
    m = re.search(r"(?i)season\s*(\d+)", os.path.basename(folder))
    if m:
        return int(m.group(1))
    if os.path.basename(folder).lower() in ("specials", "season 00"):
        return 0
    return None


def fix_one(nfo, dry_run=False):
    folder = os.path.dirname(nfo)
    try:
        tree = ET.parse(nfo)
    except ET.ParseError as ex:
        return ("unparseable", str(ex))
    root = tree.getroot()
    num = season_number(root, folder)
    want = expected_title(num, folder)
    if want is None:
        return ("skipped", "no season number")
    node = root.find("title")
    have = (node.text or "").strip() if node is not None else None
    if have == want:
        return ("ok", have)
    if not dry_run:
        shutil.copy2(nfo, nfo + ".bak")
        if node is None:
            node = ET.SubElement(root, "title")
        node.text = want
        # keep sorttitle consistent when present
        st = root.find("sorttitle")
        if st is not None:
            st.text = f"Season {num:02d}" if num else want
        tree.write(nfo, encoding="utf-8", xml_declaration=True)
    return ("fixed", f"{have!r} -> {want!r}")


def walk(path, dry_run):
    counts = {"fixed": 0, "ok": 0, "skipped": 0, "unparseable": 0}
    for dp, dns, fns in os.walk(path):
        if "season.nfo" not in fns:
            continue
        status, detail = fix_one(os.path.join(dp, "season.nfo"), dry_run)
        counts[status] = counts.get(status, 0) + 1
        if status in ("fixed", "unparseable"):
            show = os.path.basename(os.path.dirname(dp))
            print(f"   [{status}] {show[:44]} / {os.path.basename(dp)}: {detail}")
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--path", help="single show or library path")
    a = ap.parse_args()

    targets = (
        [("(explicit)", a.path)]
        if a.path
        else [(l["name"], l["path"]) for l in stack.LIBRARY_SPECS if l["kind"] == "series"]
    )
    total = {}
    for name, path in targets:
        if not os.path.isdir(path):
            print(f"=== {name}: MISSING {path}")
            continue
        print(f"=== {name}")
        c = walk(path, a.dry_run)
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
        print(
            f"    fixed={c.get('fixed', 0)} ok={c.get('ok', 0)} "
            f"skipped={c.get('skipped', 0)} unparseable={c.get('unparseable', 0)}"
        )
    print(f"\nTOTAL {total}" + ("  (dry run — nothing written)" if a.dry_run else ""))


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Set Jellyfin season display names from their folders, and lock them.

Jellyfin had scraped season titles from a provider whose season structure does
not match the library ("BLEACH: Thousand-Year Blood War - The Conflict" on
Bleach season 8; "Dragon Ball GT" on Dragon Ball Z seasons 2-9). Correcting the
season.nfo files on disk is not enough — Jellyfin does not re-read them on
refresh — so the name is written through the API and the Name field is locked so
a later scrape cannot overwrite it again.

  ./fix_season_names.py <keyfile> --dry-run
  ./fix_season_names.py <keyfile>
"""

import argparse
import json
import os
import re
import urllib.request

from homelab_jellyfix import stack

JF = stack.url("jellyfin")
LIBRARY_PREFIXES = ("/anime", "/shows", "/animation")


def req(path, key, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(
        f"{JF}{path}", data=data, method=method, headers={"X-Emby-Token": key, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        t = resp.read().decode().lstrip("﻿")
        return json.loads(t) if t.strip() else None


def expected_name(path, index):
    """Folder label wins when it carries one; otherwise 'Season N'/'Specials'."""
    base = os.path.basename(path or "")
    if index == 0:
        return "Specials"
    if index is None:
        return None
    m = re.match(r"(?i)^season\s*0*(\d+)\s*[-–—]\s*(.+)$", base)
    if m and m.group(2).strip():
        return f"Season {index} - {m.group(2).strip()}"
    return f"Season {index}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyfile")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    key = open(a.keyfile).read().strip()

    admin = next(u["Id"] for u in req("/Users", key) if u["Policy"]["IsAdministrator"])
    series = req(f"/Items?userId={admin}&IncludeItemTypes=Series&Recursive=true&Fields=Path&limit=5000", key)["Items"]
    series = [s for s in series if (s.get("Path") or "").startswith(LIBRARY_PREFIXES)]
    print(f"{len(series)} series in TV libraries")

    fixed = same = skipped = 0
    for s in series:
        try:
            seasons = req(f"/Shows/{s['Id']}/Seasons?userId={admin}&Fields=Path", key)["Items"]
        except Exception as ex:
            print(f"   ! {s['Name']}: {ex}")
            continue
        for se in seasons:
            want = expected_name(se.get("Path"), se.get("IndexNumber"))
            if want is None:
                skipped += 1
                continue
            if se["Name"] == want:
                same += 1
                continue
            print(f"   ~ {s['Name'][:34]:<36} {se['Name'][:46]!r} -> {want!r}")
            fixed += 1
            if a.dry_run:
                continue
            item = req(f"/Users/{admin}/Items/{se['Id']}", key)
            item["Name"] = want
            locked = set(item.get("LockedFields") or [])
            locked.add("Name")
            item["LockedFields"] = sorted(locked)
            req(f"/Items/{se['Id']}", key, "POST", item)

    print(f"\nrenamed={fixed} already-correct={same} skipped={skipped}" + ("  (dry run)" if a.dry_run else ""))


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Make the TV libraries use TheTVDB alone for series/season metadata.

Jellyfin was naming Bleach's seasons after TMDB's Thousand-Year Blood War cours
("BLEACH: Thousand-Year Blood War - The Conflict") while the files on disk follow
TVDB's flat 17-season numbering, so seasons displayed inconsistently. Dropping
TheMovieDb from the series/season fetchers keeps one authority.
"""

import json
import sys
import urllib.parse
import urllib.request

from homelab_jellyfix import stack

JF = stack.url("jellyfin")
KEY = open(sys.argv[1]).read().strip() if len(sys.argv) > 1 else None
LIBRARIES = ["Anime", "Shows", "Animated Shows"]
# Series keeps TVDB but loses TMDB/OMDb so one source decides the show identity.
# Season loses *every* fetcher: TVDB names Bleach's seasons after Japanese arcs,
# TMDB after the Thousand-Year Blood War cours, and the anime plugin after
# something else again — mixing them is what produced the inconsistent display.
# With no season fetcher, Jellyfin uses the folder name: "Season 1" ... "Season 17".
DROP = {"TheMovieDb", "The Open Movie Database"}
TYPES = {"Series"}
CLEAR_TYPES = {"Season"}


def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{JF}{path}", data=data, method=method, headers={"X-Emby-Token": KEY, "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else None


def main():
    folders = call("/Library/VirtualFolders")
    for v in folders:
        if v["Name"] not in LIBRARIES:
            continue
        opts = v["LibraryOptions"]
        changed = False
        for t in opts.get("TypeOptions", []):
            if t.get("Type") in CLEAR_TYPES:
                for field in ("MetadataFetchers", "MetadataFetcherOrder"):
                    if t.get(field):
                        t[field] = []
                        changed = True
                continue
            if t.get("Type") not in TYPES:
                continue
            for field in ("MetadataFetchers", "MetadataFetcherOrder"):
                before = list(t.get(field) or [])
                after = [x for x in before if x not in DROP]
                if after != before:
                    t[field] = after
                    changed = True
        if not changed:
            print(f"  = {v['Name']}: already configured")
            continue
        q = urllib.parse.urlencode({"id": v["ItemId"]})
        call(f"/Library/VirtualFolders/LibraryOptions?{q}", "POST", {"Id": v["ItemId"], "LibraryOptions": opts})
        print(f"  ~ {v['Name']}: series=TVDB-only, season fetchers cleared")


if __name__ == "__main__":
    main()

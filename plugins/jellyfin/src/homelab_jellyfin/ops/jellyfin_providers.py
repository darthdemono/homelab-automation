# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Give every Jellyfin video library its metadata sources again.

A rebuilt library starts with internet providers off and no per-type fetchers, so
episodes keep the series name instead of their real titles. TV libraries get
TheTVDB alone (it matches the tvdbid in the folder names, and the files use TVDB
numbering); season fetchers are TheTVDB too, because an empty list means "every provider" and lets the Jikan plugin back in.
Movie libraries get TMDb. The anime plugins are left out on purpose: their Jikan
calls time out for 100 s each and stall the whole library scan.

Key from JELLYFIN_KEY (the vault's JELLYFIN_AUTOMATION_KEY). Idempotent.
"""

import json
import os
import urllib.parse
import urllib.request

from homelab_jellyfix import stack

JF = stack.url("jellyfin")
KEY = os.environ["JELLYFIN_KEY"]
TV = ["Anime", "Shows", "Animated Shows"]
MOVIES = ["Movies", "Anime Movies", "Animated Movies"]


def call(path, method="GET", body=None):
    req = urllib.request.Request(
        JF + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f'MediaBrowser Token="{KEY}"', "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip() else None


def type_opt(kind, meta, images):
    return {
        "Type": kind,
        "MetadataFetchers": meta,
        "MetadataFetcherOrder": meta,
        "ImageFetchers": images,
        "ImageFetcherOrder": images,
        "ImageOptions": [],
    }


def main():
    for v in call("/Library/VirtualFolders"):
        name = v["Name"]
        if name in TV:
            src, types = "TheTVDB", [("Series", ["TheTVDB"]), ("Season", ["TheTVDB"]), ("Episode", ["TheTVDB"])]
        elif name in MOVIES:
            src, types = "TheMovieDb", [("Movie", ["TheMovieDb"])]
        else:
            continue
        o = v["LibraryOptions"]
        o["EnableInternetProviders"] = True
        o["PreferredMetadataLanguage"] = "en"
        o["MetadataCountryCode"] = "US"
        o["TypeOptions"] = [type_opt(k, m, [src]) for k, m in types]
        call(
            "/Library/VirtualFolders/LibraryOptions?" + urllib.parse.urlencode({"id": v["ItemId"]}),
            "POST",
            {"Id": v["ItemId"], "LibraryOptions": o},
        )
        print(f"{name}: internet providers on, fetchers {src}")


if __name__ == "__main__":
    main()

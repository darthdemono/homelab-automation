# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Pin one series to the exact release flavour its existing files already use.

Sonarr's default behaviour is "grab whatever scores highest", which for a series
already sitting on a consistent set of files means the next episode can arrive in
a different group, codec or audio layout than the twelve before it. jellyFix then
names it correctly and the folder still ends up mixed.

A release profile's "Must Contain" list fixes that — but note Sonarr's semantics:
the list is **OR**, a release passes if it matches *any one* term. Listing
`Judas`, `1080p` and `x265` separately therefore lets `[AnoZu] ... 1080p ...`
straight through, which is exactly how a wrong-source Bleach episode got grabbed.

So each target carries a *single* term: one regex whose lookaheads AND all the
conditions together. Sonarr treats /.../ as a regex and uses .NET, so `(?=...)`
works. The terms are deliberately loose about separators, because trackers write
the same thing several ways — `H.264`, `H 264` and `x264` all mean the same
release here.

The profile is bound to a tag carried only by this series, so it constrains
nothing else in the library.

Idempotent: re-running updates the existing profile rather than adding another.
"""

import argparse
import json
import re
import urllib.error
import urllib.request

from homelab_jellyfix import stack

CFG = stack.cfg("sonarr")

# tvdbId -> (tag to bind the profile to, required terms, terms to reject)
TARGETS = {
    452710: {
        "tag": "toonshub-webdl-dual",
        "name": "From Old Country Bumpkin - ToonsHub WEB-DL H.264 dual audio",
        # group ToonsHub + WEB-DL (not WEBRip, not Bluray) + H.264 + dual audio
        "required": [
            "/(?=.*ToonsHub)(?=.*\\bWEB[ ._-]?DL\\b)(?=.*(\\bH[ ._-]?264\\b|\\bx264\\b))(?=.*\\bDUAL\\b)/i",
        ],
        "ignored": [],
    },
    460501: {
        "tag": "judas-neko-to-ryuu",
        "name": "The Cat and the Dragon - Judas 1080p HEVC x265 10bit",
        # Judas weeklies are the only source on disk for this series; the tail
        # of the release name is [1080p][HEVC x265 10bit][Multi-Subs].
        "required": [
            "/(?=.*Judas)(?=.*\\b1080p\\b)(?=.*(\\bx265\\b|\\bHEVC\\b))(?=.*\\b10[ ._-]?bit\\b)/i",
        ],
        "ignored": [],
    },
    74796: {
        "tag": "judas-bleach",
        "name": "Bleach - Judas 1080p x265 10bit",
        # Judas is the group behind every Bleach file on disk, BD and WEB alike.
        # The library is E.N.D-style consistent: nothing else may be grabbed.
        "required": [
            "/(?=.*Judas)(?=.*\\b1080p\\b)(?=.*(\\bx265\\b|\\bHEVC\\b))/i",
        ],
        "ignored": [],
    },
}


def call(path, method="GET", body=None):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(CFG).read()).group(1)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{stack.url('sonarr')}/api/v3/{path}",
        data=data,
        method=method,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    series = {s["tvdbId"]: s for s in call("series")}
    tags = {t["label"]: t["id"] for t in call("tag")}
    profiles = {p["name"]: p for p in call("releaseprofile")}

    for tvdb, spec in TARGETS.items():
        s = series.get(tvdb)
        if not s:
            print(f"! tvdb {tvdb} is not in Sonarr")
            continue
        print(f"=== {s['title']}")
        for t in spec["required"]:
            print(f"    must contain  {t}")
        for t in spec["ignored"]:
            print(f"    must not have {t}")

        if not args.apply:
            continue

        tid = tags.get(spec["tag"])
        if tid is None:
            tid = call("tag", "POST", {"label": spec["tag"]})["id"]
            print(f"    + tag {spec['tag']!r}")
        if tid not in s["tags"]:
            s["tags"] = sorted(set(s["tags"]) | {tid})
            call(f"series/{s['id']}?moveFiles=false", "PUT", s)
            print("    tagged the series")

        body = {
            "name": spec["name"],
            "enabled": True,
            "required": spec["required"],
            "ignored": spec["ignored"],
            "indexerId": 0,
            "tags": [tid],
        }
        old = profiles.get(spec["name"])
        if old:
            body["id"] = old["id"]
            call(f"releaseprofile/{old['id']}", "PUT", body)
            print("    updated release profile")
        else:
            call("releaseprofile", "POST", body)
            print("    created release profile")

    if not args.apply:
        print("\n[plan only] re-run with --apply")


if __name__ == "__main__":
    main()

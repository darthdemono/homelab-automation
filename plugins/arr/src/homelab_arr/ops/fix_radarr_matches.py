# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Repoint Radarr at the folders jellyFix renamed, and import the ones it never saw.

The Sonarr twin of ops/fix_sonarr_matches.py, and Radarr was in the same state
Sonarr was: every movie entry still carries its original release-name path, so it
resolves to nothing, while every renamed `Title (Year) [tmdbid-N]` folder shows
up as unmapped.

Keyed on the tmdbid embedded in the folder name:

  * an existing **monitored** movie whose tmdbId matches a renamed folder is
    repointed to it, always with moveFiles=false — jellyFix owns naming and
    Radarr must never move or rename a file;
  * an **unmonitored** movie is left completely alone. Radarr holds a large set
    of unmonitored entries that exist only so they show up on the calendar; they
    are not library items, they must never be pointed at a file on disk, and
    matching one is a misfile. `--include-unmonitored` overrides this;
  * a renamed folder with no movie entry at all is added, monitored, with no
    search triggered;
  * a folder carrying no [tmdbid-N] tag is reported and skipped.

Then every movie is refreshed and rescanned so the files get linked.

Idempotent: a second run reports 0 repoints and 0 adds.
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.request

from homelab_jellyfix import stack

PORT = stack.port("radarr")
CFG = stack.cfg("radarr")

ROOTS = {
    "/movies": stack.lib("Movies"),
    "/anime_movies": stack.lib("Anime Movies"),
    "/animated_movies": stack.lib("Animated Movies"),
}
TMDBID_RE = re.compile(r"\[tmdbid-(\d+)\]\s*$")

KEY = None


def api_key():
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(CFG).read())
    if not m:
        raise SystemExit(f"no ApiKey in {CFG}")
    return m.group(1)


def call(path, method="GET", body=None):
    url = f"{stack.url_for_port(PORT)}/api/v3/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"X-Api-Key": KEY, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")


def tagged_folders():
    tagged, untagged = {}, []
    for rf in call("rootfolder"):
        root = rf["path"]
        if root not in ROOTS:
            continue
        for u in rf["unmappedFolders"]:
            m = TMDBID_RE.search(u["relativePath"])
            if m:
                tagged[int(m.group(1))] = f"{root}/{u['relativePath']}"
            else:
                untagged.append(f"{root}/{u['relativePath']}")
    return tagged, untagged


def root_of(path):
    for root in ROOTS:
        if path.startswith(root + "/"):
            return root
    raise SystemExit(f"path outside every known root: {path}")


def main():
    global KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", type=int, default=1, help="quality profile id for newly added movies")
    ap.add_argument("--no-rescan", action="store_true")
    ap.add_argument(
        "--include-unmonitored",
        action="store_true",
        help="also repoint unmonitored movies (calendar-only entries; off by default, see the module docstring)",
    )
    args = ap.parse_args()
    KEY = api_key()
    dry = args.dry_run
    tag = "[dry-run] " if dry else ""

    movies = call("movie")
    by_tmdb = {m["tmdbId"]: m for m in movies}
    tagged, untagged = tagged_folders()

    print(f"{len(movies)} movies known to Radarr, {len(tagged)} tagged folders unmapped, {len(untagged)} untagged")

    print("\n=== repoint existing movies onto their renamed folders")
    repointed, skipped_unmonitored = 0, []
    for tmdb, newpath in sorted(tagged.items()):
        m = by_tmdb.get(tmdb)
        if not m or m["path"] == newpath:
            continue
        if not m.get("monitored") and not args.include_unmonitored:
            skipped_unmonitored.append((m, newpath))
            continue
        print(f"  {tag}{m['title']}\n      {m['path']}\n   -> {newpath}")
        if not dry:
            m["path"] = newpath
            m["rootFolderPath"] = root_of(newpath)
            call(f"movie/{m['id']}?moveFiles=false", "PUT", m)
        repointed += 1
    print(f"  repointed: {repointed}")
    if skipped_unmonitored:
        print(f"  left alone — unmonitored (calendar-only): {len(skipped_unmonitored)}")
        for m, newpath in skipped_unmonitored:
            print(f"    - {m['title']} ({m.get('year')}) would have taken {newpath}")

    print("\n=== add renamed folders Radarr has no movie for")
    added, failed = 0, []
    for tmdb, path in sorted(tagged.items()):
        if tmdb in by_tmdb:
            continue
        root = root_of(path)
        print(f"  {tag}+ tmdb {tmdb}  {path}")
        if dry:
            added += 1
            continue
        # POST /movie needs the whole lookup payload, exactly as Sonarr does
        try:
            found = call(f"movie/lookup/tmdb?tmdbId={tmdb}")
        except SystemExit as e:
            print(f"      LOOKUP FAILED: {e}")
            failed.append((tmdb, path))
            continue
        body = found[0] if isinstance(found, list) else found
        if not body or body.get("tmdbId") != tmdb:
            print(f"      LOOKUP FAILED: tmdb {tmdb} not found")
            failed.append((tmdb, path))
            continue
        body.update(
            {
                "path": path,
                "rootFolderPath": root,
                "qualityProfileId": args.profile,
                "monitored": True,
                "minimumAvailability": "released",
                "addOptions": {"searchForMovie": False},
            }
        )
        try:
            call("movie", "POST", body)
        except SystemExit as e:
            print(f"      FAILED: {e}")
            failed.append((tmdb, path))
            continue
        print(f"      added as {body.get('title')!r}")
        added += 1
    print(f"  added: {added}" + (f", failed: {len(failed)}" if failed else ""))

    if untagged:
        print("\n=== skipped — no [tmdbid-N] tag")
        for p in untagged:
            print(f"  ? {p}")

    # Unmonitored calendar entries legitimately point at nothing on disk, so
    # they are not stale — listing them would bury the handful that are.
    stale = [m for m in movies if m.get("monitored") and m["tmdbId"] not in tagged and not TMDBID_RE.search(m["path"])]
    if stale:
        print("\n=== movies still on a pre-rename path")
        for m in stale:
            print(f"  ! {m['title']} -> {m['path']}")

    if not args.no_rescan and not dry:
        print("\n=== refresh + rescan")
        call("command", "POST", {"name": "RefreshMovie"})
        time.sleep(2)
        call("command", "POST", {"name": "RescanMovie"})
        print("  queued RefreshMovie + RescanMovie")


if __name__ == "__main__":
    main()

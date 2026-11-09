# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Repoint Sonarr at the folders jellyFix renamed, and import the ones it never saw.

jellyFix renames every series folder to `Title (Year) [tvdbid-N]`. Sonarr's series
entries still carry the original release-name paths, so they resolve to nothing:
0 episode files tracked, and every renamed folder shows up as "unmapped".

This script reconciles the two, keyed on the tvdbid embedded in the folder name:

  * existing **monitored** series whose tvdbid matches a renamed folder are
    repointed to it (moveFiles=false always — jellyFix owns naming, Sonarr must
    never move a file);
  * **unmonitored** series are left completely alone. Sonarr holds a large set of
    unmonitored entries added purely so they appear on the calendar; they are not
    library items, must never be pointed at files on disk, and matching one is a
    misfile. `--include-unmonitored` overrides this;
  * renamed folders with no series at all are added, monitored, without a search;
  * folders carrying no [tvdbid-N] tag are reported and skipped — those are the
    items jellyFix deliberately refused to match.

Finally every series is refreshed and rescanned so the episode files get linked.

Idempotent: a second run reports 0 repoints and 0 adds.
"""

import argparse
import json
import re
import time
import urllib.error
import urllib.request

from homelab_jellyfix import stack

PORT = stack.port("sonarr")
CFG = stack.cfg("sonarr")

# Sonarr's in-container root folders -> host paths
ROOTS = {
    "/anime": stack.lib("Anime"),
    "/shows": stack.lib("Shows"),
    "/animation": stack.lib("Animated Shows"),
}
ANIME_ROOTS = {"/anime"}

TVDBID_RE = re.compile(r"\[tvdbid-(\d+)\]\s*$")

# jellyFix writes zero-padded season folders; Sonarr must agree.
SEASON_FOLDER_FORMAT = "Season {season:00}"


def api_key():
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(CFG).read())
    if not m:
        raise SystemExit(f"no ApiKey in {CFG}")
    return m.group(1)


KEY = None


def call(path, method="GET", body=None):
    url = f"{stack.url_for_port(PORT)}/api/v3/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"X-Api-Key": KEY, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")


def tagged_folders():
    """{tvdbid: container_path} for every unmapped folder carrying a [tvdbid-N] tag,
    plus the list of untagged ones so they can be reported."""
    tagged, untagged = {}, []
    for rf in call("rootfolder"):
        root = rf["path"]
        if root not in ROOTS:
            continue
        for u in rf["unmappedFolders"]:
            m = TVDBID_RE.search(u["relativePath"])
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
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument(
        "--profile", type=int, default=1, help="quality profile id for newly added series (default 1 = Any)"
    )
    ap.add_argument("--no-rescan", action="store_true", help="skip the final refresh/rescan pass")
    ap.add_argument(
        "--include-unmonitored",
        action="store_true",
        help="also touch unmonitored series (calendar-only entries; off by default, see the module docstring)",
    )
    args = ap.parse_args()
    KEY = api_key()
    dry = args.dry_run
    tag = "[dry-run] " if dry else ""

    series = call("series")
    by_tvdb = {s["tvdbId"]: s for s in series}
    tagged, untagged = tagged_folders()

    print(f"{len(series)} series known to Sonarr, {len(tagged)} tagged folders unmapped, {len(untagged)} untagged")

    # ------------------------------------------------ repoint existing series
    print("\n=== repoint existing series onto their renamed folders")
    repointed, skipped_unmonitored = 0, []
    for tvdb, newpath in sorted(tagged.items()):
        s = by_tvdb.get(tvdb)
        if not s or s["path"] == newpath:
            continue
        if not s.get("monitored") and not args.include_unmonitored:
            skipped_unmonitored.append((s, newpath))
            continue
        print(f"  {tag}{s['title']}\n      {s['path']}\n   -> {newpath}")
        if not dry:
            s["path"] = newpath
            s["rootFolderPath"] = root_of(newpath)
            call(f"series/{s['id']}?moveFiles=false", "PUT", s)
        repointed += 1
    print(f"  repointed: {repointed}")
    if skipped_unmonitored:
        print(f"  left alone — unmonitored (calendar-only): {len(skipped_unmonitored)}")
        for s, newpath in skipped_unmonitored:
            print(f"    - {s['title']} would have taken {newpath}")

    # ------------------------------------------------ add the missing series
    print("\n=== add renamed folders Sonarr has no series for")
    added, failed = 0, []
    for tvdb, path in sorted(tagged.items()):
        if tvdb in by_tvdb:
            continue
        root = root_of(path)
        print(f"  {tag}+ tvdb {tvdb}  {path}")
        if not dry:
            # POST /series wants the whole lookup payload (Title, seasons, images...),
            # not a bare tvdbId — so fetch it and override only what we own.
            try:
                found = call(f"series/lookup?term=tvdb:{tvdb}")
            except SystemExit as e:
                print(f"      LOOKUP FAILED: {e}")
                failed.append((tvdb, path))
                continue
            body = next((c for c in (found or []) if c.get("tvdbId") == tvdb), None)
            if not body:
                print(f"      LOOKUP FAILED: tvdb {tvdb} not found")
                failed.append((tvdb, path))
                continue
            body.update(
                {
                    "path": path,
                    "rootFolderPath": root,
                    "qualityProfileId": args.profile,
                    "monitored": True,
                    "seasonFolder": True,
                    "seriesType": "anime" if root in ANIME_ROOTS else "standard",
                    "addOptions": {
                        "monitor": "existing",
                        "searchForMissingEpisodes": False,
                        "searchForCutoffUnmetEpisodes": False,
                    },
                }
            )
            try:
                call("series", "POST", body)
            except SystemExit as e:
                print(f"      FAILED: {e}")
                failed.append((tvdb, path))
                continue
            print(f"      added as {body.get('title')!r}")
        added += 1
    print(f"  added: {added}" + (f", failed: {len(failed)}" if failed else ""))

    # ------------------------------------------------ report what was skipped
    if untagged:
        print("\n=== skipped — no [tvdbid-N] tag (jellyFix refused these; fix by hand)")
        for p in untagged:
            print(f"  ? {p}")

    # A series already sitting on a [tvdbid-N] folder is mapped, so it never shows
    # up in `tagged` — that is not staleness. Only untagged paths are suspect.
    # Unmonitored calendar entries legitimately point at nothing on disk, so
    # they are not stale — listing them would bury the handful that are.
    stale = [s for s in series if s.get("monitored") and s["tvdbId"] not in tagged and not TVDBID_RE.search(s["path"])]
    if stale:
        print("\n=== series still on a pre-rename path (no renamed folder exists)")
        for s in stale:
            print(f"  ! {s['title']} -> {s['path']}")

    # ------------------------------------------------ stop it re-breaking
    # jellyFix lays episodes out in zero-padded `Season 01` folders. Sonarr's
    # default `Season {season}` would drop a future import into `Season 1`
    # alongside it, and a series added before this ran has seasonFolder off
    # entirely, which flattens imports into the series root. Either one
    # reintroduces exactly the mismatch this script just cleaned up.
    print("\n=== align season-folder layout with jellyFix")
    naming = call("config/naming")
    if naming["seasonFolderFormat"] != SEASON_FOLDER_FORMAT:
        print(f"  {tag}seasonFolderFormat {naming['seasonFolderFormat']!r} -> {SEASON_FOLDER_FORMAT!r}")
        if not dry:
            naming["seasonFolderFormat"] = SEASON_FOLDER_FORMAT
            call(f"config/naming/{naming['id']}", "PUT", naming)
    else:
        print(f"  seasonFolderFormat already {SEASON_FOLDER_FORMAT!r}")

    # Only series that can actually receive a download need this; an
    # unmonitored calendar entry never imports anything, and PUTting hundreds of
    # them is churn against records this script has no business editing.
    flat = [s for s in call("series") if not s["seasonFolder"] and (s.get("monitored") or args.include_unmonitored)]
    print(f"  {tag}seasonFolder=true on {len(flat)} series")
    for s in flat:
        print(f"    {s['title']}")
        if not dry:
            s["seasonFolder"] = True
            call(f"series/{s['id']}?moveFiles=false", "PUT", s)

    # ------------------------------------------------ relink the episode files
    if not args.no_rescan and not dry:
        ids = [s["id"] for s in call("series")]
        print(f"\n=== refresh + rescan {len(ids)} series")
        call("command", "POST", {"name": "RefreshSeries"})
        time.sleep(2)
        call("command", "POST", {"name": "RescanSeries"})
        print("  queued RefreshSeries + RescanSeries")


if __name__ == "__main__":
    main()

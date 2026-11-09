# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Move films out of series folders and into the matching movie library.

Anime series folders often carry a `Movies/` or `OVAs/` subfolder holding the
franchise's films. Jellyfin indexes the folder as part of the series, which is
why `Dragon Ball` kept appearing twice: once as the renamed series and once as
the old folder, still alive because it held films.

Each film is moved to its own folder in the movie library that matches the series
library — Anime -> Anime Movies, Animation -> Animated Movies, Shows -> Movies —
under a name TMDB can match. The `M01 -` / `OVA2 -` release markers are dropped
and the series name kept, so `Dragon Ball Z - M01 - Dead Zone.mkv` becomes a
folder `Dragon Ball Z - Dead Zone`, which Radarr resolves to
`Dragon Ball Z: Dead Zone`.

It does not name them. Run `ops/fix_movie_names.py` afterwards: that already
identifies untagged movie folders through Radarr and renames them to
`Title (Year) [tmdbid-N]` with a TRaSH filename.

A video that sits beside the feature inside its own folder is treated as bonus
content and lands in `Extras/`.

Journalled to plans/ and reversible with --undo.
"""

import argparse
import json
import os
import re
import shutil
import time

from homelab_jellyfix import stack

LIBRARIES = {
    stack.lib("Anime"): stack.lib("Anime Movies"),
    stack.lib("Animated Shows"): stack.lib("Animated Movies"),
    stack.lib("Shows"): stack.lib("Movies"),
}
PLANS = stack.PLANS
VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
FILM_DIRS = {"movies", "movie", "ovas", "ova", "films", "film"}

# "Dragon Ball Z - M01 - Dead Zone" -> series "Dragon Ball Z", title "Dead Zone"
MARKER = re.compile(r"^(?P<series>.+?)\s*-\s*(?:M|OVA|MOVIE)\s*\d+\s*-\s*(?P<title>.+)$", re.I)
# "Dragon Ball Z Side Story - OVA1 - ..." keeps its series prefix too
TRAILING_PART = re.compile(r"\s*-\s*Part\s*\d+\s*$", re.I)


def derive(stem):
    m = MARKER.match(stem)
    if not m:
        return stem.strip()
    return f"{m.group('series').strip()} - {m.group('title').strip()}"


def survey():
    plan, extras = [], []
    for lib, dest_lib in LIBRARIES.items():
        if not os.path.isdir(lib):
            continue
        for series in sorted(os.listdir(lib)):
            spath = os.path.join(lib, series)
            if not os.path.isdir(spath) or series.startswith("."):
                continue
            for sub in sorted(os.listdir(spath)):
                if sub.lower() not in FILM_DIRS:
                    continue
                subpath = os.path.join(spath, sub)
                if not os.path.isdir(subpath):
                    continue
                for entry in sorted(os.listdir(subpath)):
                    p = os.path.join(subpath, entry)
                    if os.path.isdir(p):
                        vids = sorted(f for f in os.listdir(p) if os.path.splitext(f)[1].lower() in VIDEO)
                        if not vids:
                            continue
                        # the film shares the folder's name; the rest are bonus
                        feature = next((v for v in vids if os.path.splitext(v)[0] == entry), vids[0])
                        name = derive(os.path.splitext(feature)[0])
                        dst_dir = os.path.join(dest_lib, name)
                        plan.append(
                            {"src": os.path.join(p, feature), "dst": os.path.join(dst_dir, feature), "name": name}
                        )
                        for v in vids:
                            if v != feature:
                                extras.append(
                                    {"src": os.path.join(p, v), "dst": os.path.join(dst_dir, "Extras", v), "name": name}
                                )
                    elif os.path.splitext(entry)[1].lower() in VIDEO:
                        name = derive(os.path.splitext(entry)[0])
                        plan.append({"src": p, "dst": os.path.join(dest_lib, name, entry), "name": name})
    return plan, extras


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        d = json.load(open(args.undo))
        n = 0
        for mv in reversed(d["moves"]):
            if os.path.exists(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                shutil.move(mv["dst"], mv["src"])
                n += 1
        print(f"restored {n}")
        return

    plan, extras = survey()
    print(f"=== {len(plan)} films to move out of the series libraries")
    for p in plan:
        print(f"  {os.path.relpath(p['src'], stack.ROOT)}")
        print(f"    -> {os.path.relpath(p['dst'], stack.ROOT)}")
    if extras:
        print(f"\n=== {len(extras)} bonus videos alongside a film")
        for e in extras:
            print(f"  {os.path.basename(e['src'])[:60]}  ->  {e['name']}/Extras")
    clash = [p for p in plan if os.path.exists(p["dst"]) or os.path.isdir(os.path.dirname(p["dst"]))]
    if clash:
        print(f"\n=== {len(clash)} destinations already exist — skipped")
        for c in clash:
            print(f"  {c['name']}")

    todo = [p for p in plan if p not in clash]
    if not args.apply:
        print("\n[plan only] re-run with --apply, then ops/fix_movie_names.py to name them")
        return

    moves = []
    for p in todo + extras:
        os.makedirs(os.path.dirname(p["dst"]), exist_ok=True)
        shutil.move(p["src"], p["dst"])
        moves.append({"src": p["src"], "dst": p["dst"]})
        print(f"  moved {p['name']}")
    j = os.path.join(PLANS, time.strftime("%Y%m%d-%H%M%S") + "-films.json")
    json.dump({"moves": moves}, open(j, "w"), indent=2)
    print(f"\n{len(moves)} moves\njournal: {j}")
    print(f"undo: ./ops/extract_films.py --undo {j}")
    print("next: ./ops/fix_movie_names.py --apply")


if __name__ == "__main__":
    main()

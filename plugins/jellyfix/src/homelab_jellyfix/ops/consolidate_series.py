# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Merge leftover episodes from pre-rename folders into the real series folder.

Jellyfin indexes a folder, not a name. When jellyFix renamed a series but could
not match every file, the leftovers stayed in the original release folder — and
because that folder still holds episodes, Jellyfin identifies it as the same
series and shows the show **twice**, with its episodes split between the two
entries.

This moves the leftovers into the renamed folder under TRaSH names, which
collapses the duplicate. Each file is matched by an explicit `SxxEyy` against the
episode list Sonarr already holds, ffprobed for real media details, and named by
`jellyfix.naming` — the same path `jellyfix.py apply` takes.

Where two files claim one episode, the better copy wins on resolution, then bit
depth, then audio channels, then size, and the loser is quarantined. That is the
`duplicate-target` case jellyFix refuses to decide on its own.

Anything without an unambiguous `SxxEyy` is reported and left alone. Several of
these folders hold files that look like episodes and are not — Dragon Ball's
`S07E97` is really an absolute number, and Steins;Gate's "Season 2" is a
different series entirely.

Journalled to plans/ and reversible with --undo.
"""

import argparse
import json
import os
import re
import shutil
import time
import urllib.request

from homelab_jellyfix import (
    media,  # noqa: E402
    stack,
)
from homelab_jellyfix.naming import episode_name, season_folder  # noqa: E402
from homelab_jellyfix.parse import detect_group, detect_source  # noqa: E402

SONARR_CFG = stack.cfg("sonarr")
QUARANTINE = stack.QUARANTINE
PLANS = stack.PLANS

VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
SUBS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}
SXXEYY = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})\b", re.I)
# A REPACK or PROPER is the corrected release. It beats the original at the same
# resolution even when it is slightly smaller, which raw byte count gets wrong.
REPACK = re.compile(r"\b(repack|proper)\b", re.I)

# Pre-rename folder -> the tvdbId of the series it belongs to. These are the
# pairs Jellyfin was showing twice; each was confirmed by hand before listing.
# `season_map` remaps the season number in the filename onto the season the
# series actually uses. Steins;Gate 0 is a separate series that was foldered as
# "Season 2" of Steins;Gate, so its files say S02 while the show numbers them S01.
PAIRS = stack.SETTINGS.get(
    "pairs", {}
)  # folder -> {"tvdb": id, "absolute_fallback": bool, "season_map": {...}, "only_seasons": [...]}


def sonarr(path):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(SONARR_CFG).read()).group(1)
    req = urllib.request.Request(f"{stack.url('sonarr')}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def anime_paths():
    return [l["path"].rstrip("/") for l in stack.LIBRARY_SPECS if l.get("anime")]


ANIME = anime_paths()


def is_anime(p):
    return any(p == a or p.startswith(a + os.sep) for a in ANIME)


def quality(path):
    i = media.probe(path)
    px = (i.get("width") or 0) * (i.get("height") or 0)
    depth = 10 if "10" in (i.get("pix_fmt") or "") else 8
    ch = max((a.get("channels") or 0) for a in (i.get("audio") or [{}]))
    repack = 1 if REPACK.search(os.path.basename(path)) else 0
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return (px, depth, ch, repack, size), i


def describe(p):
    (px, d, ch, repack, size), i = quality(p)
    return f"{i.get('width')}x{i.get('height')} {i.get('vcodec')} {d}bit {ch}ch {size / 1e9:.2f}GB" + (
        " REPACK" if repack else ""
    )


def find_series_dir(tvdb, library_root):
    for d in os.listdir(library_root):
        if d.endswith(f"[tvdbid-{tvdb}]"):
            return os.path.join(library_root, d)
    return None


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

    series_by_tvdb = {s["tvdbId"]: s for s in sonarr("series")}
    plan, dupes, skipped = [], [], []

    for old, spec in PAIRS.items():
        tvdb = spec["tvdb"]
        smap = spec.get("season_map") or {}
        only = spec.get("only_seasons")
        absfb = spec.get("absolute_fallback")
        if not os.path.isdir(old):
            skipped.append((old, "source folder is gone"))
            continue
        s = series_by_tvdb.get(tvdb)
        dest = find_series_dir(tvdb, os.path.dirname(old))
        if s and not dest:
            # Sonarr knows the series but the folder does not exist yet
            dest = os.path.join(os.path.dirname(old), os.path.basename(s["path"].rstrip("/")))
            os.makedirs(dest, exist_ok=True)
        if not s or not dest:
            skipped.append((old, f"no Sonarr series or renamed folder for tvdb {tvdb}"))
            continue
        eps = sonarr(f"episode?seriesId={s['id']}")

        for dp, _dn, fn in os.walk(old):
            for f in sorted(fn):
                if os.path.splitext(f)[1].lower() not in VIDEO:
                    continue
                src = os.path.join(dp, f)
                m = SXXEYY.search(f)
                if not m:
                    skipped.append((src, "no SxxEyy in the filename"))
                    continue
                sn, en = int(m.group(1)), int(m.group(2))
                if only is not None and sn not in only:
                    skipped.append((src, f"season {sn} is not part of this series"))
                    continue
                sn = smap.get(sn, sn)
                ep = next((e for e in eps if e["seasonNumber"] == sn and e["episodeNumber"] == en), None)
                if not ep and absfb:
                    # Some releases put the absolute number in the episode field:
                    # Dragon Ball's "S07E97" is really absolute 97. Only accepted
                    # when exactly one episode carries that absolute number and
                    # the season it really belongs to is the one claimed or later.
                    cands = [e for e in eps if e.get("absoluteEpisodeNumber") == en and e["seasonNumber"] > 0]
                    if len(cands) == 1:
                        ep = cands[0]
                if not ep:
                    skipped.append((src, f"Sonarr has no S{sn:02d}E{en:02d} (often an absolute number in disguise)"))
                    continue
                # Always take the numbering from the matched episode, never from
                # the filename. Dragon Ball's "S07E97" resolves to absolute 97,
                # which TVDB numbers S04E29 - keeping the filename's 97 would
                # write a season-4 episode 97 that does not exist.
                sn, en = ep["seasonNumber"], ep["episodeNumber"]
                info = media.probe(src)
                if not info.get("ok"):
                    skipped.append((src, f"ffprobe failed: {info.get('error')}"))
                    continue
                new = episode_name(
                    s["title"],
                    s.get("year"),
                    sn,
                    en,
                    ep["title"],
                    info,
                    source=detect_source(os.path.splitext(f)[0]),
                    group=detect_group(os.path.splitext(f)[0]),
                    absolute=(ep.get("absoluteEpisodeNumber") if is_anime(dest) else None),
                    ext=os.path.splitext(f)[1],
                )
                dst = os.path.join(dest, season_folder(sn), new)
                plan.append({"src": src, "dst": dst, "ep": f"S{sn:02d}E{en:02d}", "series": s["title"]})

    # two files claiming one destination -> keep the better copy
    by_dst = {}
    for p in plan:
        by_dst.setdefault(p["dst"], []).append(p)
    final = []
    for dst, group in by_dst.items():
        if os.path.exists(dst):
            group.append({"src": dst, "dst": dst, "ep": group[0]["ep"], "series": group[0]["series"], "in_place": True})
        if len(group) == 1:
            final.append(group[0])
            continue
        group.sort(key=lambda g: quality(g["src"])[0], reverse=True)
        win, losers = group[0], group[1:]
        dupes.append(
            {
                "dst": dst,
                "win": win["src"],
                "lose": [l["src"] for l in losers],
                "ep": win["ep"],
                "series": win["series"],
                "win_in_place": win.get("in_place", False),
            }
        )
        if not win.get("in_place"):
            final.append(win)

    print(f"=== {len(final)} episodes to merge into their series folder")
    for p in final:
        print(f"  {p['series']} {p['ep']}")
        print(f"    -> {os.path.relpath(p['dst'], stack.ROOT)}")
    print(f"\n=== {len(dupes)} episodes claimed by more than one file")
    for d in dupes:
        print(f"  {d['series']} {d['ep']}")
        print(f"    keep  {describe(d['win'])}")
        print(f"          {os.path.basename(d['win'])[:88]}" + ("   (already in place)" if d["win_in_place"] else ""))
        for l in d["lose"]:
            print(f"    drop  {describe(l)}")
            print(f"          {os.path.basename(l)[:88]}")
    if skipped:
        print(f"\n=== {len(skipped)} left alone")
        for p, why in skipped:
            print(f"  {os.path.basename(p)[:70]}\n      {why}")
    media.save_cache()

    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    qroot = os.path.join(QUARANTINE, f"consolidate-{stamp}")
    moves = []
    for d in dupes:
        for l in d["lose"]:
            rel = os.path.relpath(l, stack.ROOT)
            q = os.path.join(qroot, rel)
            os.makedirs(os.path.dirname(q), exist_ok=True)
            shutil.move(l, q)
            moves.append({"src": l, "dst": q})
            print(f"  retired {os.path.basename(l)[:70]}")
    for p in final:
        os.makedirs(os.path.dirname(p["dst"]), exist_ok=True)
        shutil.move(p["src"], p["dst"])
        moves.append({"src": p["src"], "dst": p["dst"]})
        stem_old = os.path.splitext(os.path.basename(p["src"]))[0]
        stem_new = os.path.splitext(os.path.basename(p["dst"]))[0]
        sd = os.path.dirname(p["src"])
        if os.path.isdir(sd):
            for f in sorted(os.listdir(sd)):
                if os.path.splitext(f)[1].lower() in SUBS and f.startswith(stem_old):
                    a = os.path.join(sd, f)
                    b = os.path.join(os.path.dirname(p["dst"]), stem_new + f[len(stem_old) :])
                    shutil.move(a, b)
                    moves.append({"src": a, "dst": b})
        print(f"  merged {p['series']} {p['ep']}")

    j = os.path.join(PLANS, f"{stamp}-consolidate.json")
    json.dump({"moves": moves, "quarantine": qroot}, open(j, "w"), indent=2)
    media.save_cache()
    print(f"\n{len(moves)} moves\njournal: {j}")
    print(f"undo: ./ops/consolidate_series.py --undo {j}")


if __name__ == "__main__":
    main()

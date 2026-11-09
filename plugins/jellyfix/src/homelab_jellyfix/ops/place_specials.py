# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Move specials that were left behind in pre-rename folders into the real series folder.

jellyFix renamed each series into `Title (Year) [tvdbid-N]` but refused the
specials it could not confidently number, so they stayed in the old release
folder — outside the series Jellyfin and Sonarr actually track.

Each file is matched against the season-0 episode list Sonarr already holds:
an explicit `S00Exx` in the filename wins outright, otherwise the title is
fuzzy-matched against the special's name and its alternate titles. Anything that
does not clear the match threshold is reported and left alone — a special filed
under the wrong number is worse than one still sitting in the old folder.

Names are built by jellyfix.naming from a real ffprobe of the file, so the result
is what `jellyfix.py apply` would have produced. Subtitle sidecars travel with
their video. Every move is journalled to plans/ and reversible with --undo.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import time

from homelab_jellyfix import (
    media,  # noqa: E402
    stack,
)
from homelab_jellyfix.naming import episode_name  # noqa: E402
from homelab_jellyfix.parse import detect_group, detect_source  # noqa: E402

ANIME = stack.lib("Anime")
PLANS = stack.PLANS
SONARR_CFG = stack.cfg("sonarr")

VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
SUBS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}

# old folder (relative to the Anime root) -> tvdbId of the series it belongs to
SOURCES = {
    "(CBB) Tengen Toppa Gurren Lagann - The Complete Series (BD 1080p)(Dual-Audio)(HEVC-x265-10bit)": 80096,
    "Dragon Ball": 76666,
    "Dragon Ball Z": 81472,
    "Dragon Ball GT": 79275,
    "Made in Abyss": 326109,
    "Steins;Gate 1080p BluRay AV1 Dual Opus 2.0 MSubs-Trix": 244061,
}

SXXEYY = re.compile(r"S0*0\s*E\s*(\d{1,3})", re.I)
THRESHOLD = 0.62


def sonarr(path):
    import urllib.request

    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(SONARR_CFG).read()).group(1)
    req = urllib.request.Request(f"{stack.url('sonarr')}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def norm(s):
    s = re.sub(r"\b(19|20)\d{2}\b", " ", s or "")
    s = re.sub(
        r"\b(bluray|bd|web-?dl|webrip|dual[- ]?audio|\d{3,4}p|x26[45]|hevc|"
        r"h\.?26[45]|10bit|8bit|aac|flac|opus|ova|movie|special|"
        r"credit\s*scroll)\b",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"[^a-z0-9]+", " ", s.lower())
    return " ".join(s.split())


def find_series_dir(tvdb):
    for d in os.listdir(ANIME):
        if d.endswith(f"[tvdbid-{tvdb}]"):
            return os.path.join(ANIME, d)
    return None


def match(fname, specials):
    """-> (episode dict, why) or (None, why-not)"""
    m = SXXEYY.search(fname)
    if m:
        n = int(m.group(1))
        hit = next((e for e in specials if e["episodeNumber"] == n), None)
        if hit:
            return hit, f"explicit S00E{n:02d}"
        return None, f"filename says S00E{n:02d}, Sonarr has no such special"

    stem = norm(os.path.splitext(fname)[0])
    best, score = None, 0.0
    for e in specials:
        cands = [e.get("title") or ""] + list(e.get("alternateTitles") or [])
        for c in cands:
            r = difflib.SequenceMatcher(None, stem, norm(c)).ratio()
            # a full containment of one title in the other is a strong signal
            if norm(c) and (norm(c) in stem or stem in norm(c)):
                r = max(r, 0.9)
            if r > score:
                best, score = e, r
    if best and score >= THRESHOLD:
        return best, f"title match {score:.2f} -> {best['title']!r}"
    return None, (f"best title match only {score:.2f}" + (f" ({best['title']!r})" if best else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        data = json.load(open(args.undo))
        n = 0
        for mv in reversed(data["moves"]):
            if os.path.exists(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                shutil.move(mv["dst"], mv["src"])
                n += 1
        print(f"restored {n}")
        return

    series = {s["tvdbId"]: s for s in sonarr("series")}
    plan, skipped = [], []

    for rel, tvdb in SOURCES.items():
        src_dir = os.path.join(ANIME, rel, "Specials")
        dest_series = find_series_dir(tvdb)
        s = series.get(tvdb)
        if not os.path.isdir(src_dir) or not dest_series or not s:
            skipped.append((rel, "source or destination missing"))
            continue
        eps = [e for e in sonarr(f"episode?seriesId={s['id']}") if e["seasonNumber"] == 0]
        dest_dir = os.path.join(dest_series, "Specials")

        for f in sorted(os.listdir(src_dir)):
            p = os.path.join(src_dir, f)
            if os.path.splitext(f)[1].lower() not in VIDEO:
                continue
            ep, why = match(f, eps)
            if not ep:
                skipped.append((p, why))
                continue
            info = media.probe(p)
            if not info.get("ok"):
                skipped.append((p, f"ffprobe failed: {info.get('error')}"))
                continue
            new = episode_name(
                s["title"],
                s.get("year"),
                0,
                ep["episodeNumber"],
                ep["title"],
                info,
                source=detect_source(f),
                group=detect_group(f),
                absolute=None,
                ext=os.path.splitext(f)[1],
            )
            dst = os.path.join(dest_dir, new)
            if os.path.exists(dst):
                skipped.append((p, f"destination already exists: {new}"))
                continue
            side = [
                os.path.join(src_dir, x)
                for x in os.listdir(src_dir)
                if os.path.splitext(x)[1].lower() in SUBS and x.startswith(os.path.splitext(f)[0])
            ]
            plan.append({"src": p, "dst": dst, "why": why, "subs": side})

    print(f"=== {len(plan)} specials to place\n")
    for x in plan:
        print(f"  {os.path.basename(x['src'])}")
        print(f"    [{x['why']}]")
        print(f"    -> {os.path.relpath(x['dst'], ANIME)}")
        for sfile in x["subs"]:
            print(f"       + {os.path.basename(sfile)}")
    if skipped:
        print(f"\n=== {len(skipped)} left alone")
        for p, why in skipped:
            print(f"  {os.path.basename(str(p))}\n      {why}")
    media.save_cache()

    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    moves = []
    for x in plan:
        os.makedirs(os.path.dirname(x["dst"]), exist_ok=True)
        shutil.move(x["src"], x["dst"])
        moves.append({"src": x["src"], "dst": x["dst"]})
        base = os.path.splitext(x["dst"])[0]
        for sfile in x["subs"]:
            tail = os.path.basename(sfile)[len(os.path.splitext(os.path.basename(x["src"]))[0]) :]
            sdst = base + tail
            shutil.move(sfile, sdst)
            moves.append({"src": sfile, "dst": sdst})
        print(f"  moved {os.path.basename(x['dst'])}")
    jpath = os.path.join(PLANS, f"{stamp}-specials.json")
    json.dump({"moves": moves}, open(jpath, "w"), indent=2)
    print(f"\nmoved {len(moves)} files\njournal: {jpath}")
    print(f"undo: ./ops/place_specials.py --undo {jpath}")


if __name__ == "__main__":
    main()

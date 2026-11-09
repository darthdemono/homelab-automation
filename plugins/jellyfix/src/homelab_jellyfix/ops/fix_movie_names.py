# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Bring the leftover movie folders up to jellyFix naming, and resolve duplicates.

Three kinds of folder are left in the movie libraries after the rename sweep:

1. **Films jellyFix refused to rename** — mostly Bengali-titled Satyajit Ray
   films TVDB/TMDB list under an English name, plus one leetspeak-obfuscated
   release. They are identified through Radarr's lookup and renamed to
   `Title (Year) [tmdbid-N]/Title (Year) [tags]-Group.ext`, so Radarr can match
   them and jellyFix will leave them alone from now on.
2. **Duplicates** — the same film present both as a renamed folder and as its
   original release folder. The better copy wins and the loser is quarantined,
   never deleted. "Better" is resolution first, then bit depth, then audio
   channels, then file size; the comparison is printed so it can be argued with.
3. **Bonus-content folders** — an old release folder whose feature already moved
   out, leaving only `Extras/` or `Featurettes/`. Those are moved *into* the
   film's real folder, which is where Jellyfin looks for them, rather than being
   left behind or thrown away.

Titles come from TMDB via Radarr, so `Apur Sansar` becomes `The World of Apu` and
`Mahapurush` becomes `The Coward` — that is what Radarr and Jellyfin will match
on, and it keeps one naming authority rather than two.

Journalled to plans/ and reversible with --undo.
"""

import argparse
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request

from homelab_jellyfix import (
    media,  # noqa: E402
    stack,
)
from homelab_jellyfix.naming import movie_folder, movie_name  # noqa: E402
from homelab_jellyfix.parse import detect_group, detect_source, parse  # noqa: E402

ROOTS = [stack.lib("Movies"), stack.lib("Anime Movies"), stack.lib("Animated Movies")]
RADARR_CFG = stack.cfg("radarr")
QUARANTINE = stack.QUARANTINE
PLANS = stack.PLANS

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}
SUBS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}
EXTRAS = {
    "extras",
    "featurettes",
    "behind the scenes",
    "deleted scenes",
    "interviews",
    "scenes",
    "shorts",
    "trailers",
    "other",
    "bonus",
    "making of",
    "samples",
    "sample",
}
TAG = re.compile(r"\[tmdbid-(\d+)\]\s*$")

# Lookups Radarr cannot make on its own. `Nayak The Hero 1996` carries the wrong
# year — the film is 1966 — so every search on the parsed year comes back empty.
OVERRIDES = {"Nayak The Hero 1996": 112999}  # matched as a substring of the folder name


def radarr(path):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(RADARR_CFG).read()).group(1)
    req = urllib.request.Request(f"{stack.url('radarr')}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=90) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt.strip() else None


def lookup(term):
    try:
        return radarr("movie/lookup?term=" + urllib.parse.quote(term)) or []
    except Exception:
        return []


def stem_of(path):
    """Filename without its extension. detect_group on a full filename returns
    the extension as part of the group — `x265-E.mkv` becomes group "E.mkv"."""
    return os.path.splitext(os.path.basename(path))[0]


def videos(d):
    out = []
    for r, _s, fs in os.walk(d, onerror=lambda e: None):
        if ".trickplay" in r:
            continue
        for f in fs:
            if os.path.splitext(f)[1].lower() in VIDEO:
                out.append(os.path.join(r, f))
    return out


def quality(path):
    """Sort key: bigger is better. Resolution, then bit depth, then channels,
    then bytes — the order a person would argue them in."""
    i = media.probe(path)
    px = (i.get("width") or 0) * (i.get("height") or 0)
    depth = 10 if "10" in (i.get("pix_fmt") or "") else 8
    ch = max((a.get("channels") or 0) for a in (i.get("audio") or [{}]))
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return (px, depth, ch, size), i


def describe(path):
    (px, depth, ch, size), i = quality(path)
    return f"{i.get('width')}x{i.get('height')} {i.get('vcodec')} {depth}bit {ch}ch {size / 1e9:.2f}GB"


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

    tagged = {}
    untagged = []
    for root in ROOTS:
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name)
            if not os.path.isdir(p) or name.startswith("."):
                continue
            m = TAG.search(name)
            if m:
                tagged[int(m.group(1))] = p
            elif videos(p):
                untagged.append(p)

    renames, dupes, extras, unresolved = [], [], [], []

    for p in untagged:
        root, name = os.path.dirname(p), os.path.basename(p)
        vids = videos(p)
        feature = [v for v in vids if os.path.basename(os.path.dirname(v)).lower() not in EXTRAS]

        info = parse(name, is_dir=True)
        forced = next((v for k, v in OVERRIDES.items() if k in name), None)
        hit = None
        if forced:
            found = lookup(f"tmdb:{forced}")
            hit = found[0] if found else None
        else:
            term = f"{info.get('title')} {info.get('year')}" if info.get("year") else (info.get("title") or name)
            res = lookup(term)
            hit = res[0] if res else None
        if not hit or not hit.get("tmdbId"):
            unresolved.append((p, "no TMDB match"))
            continue

        tid = hit["tmdbId"]
        if not feature:
            # only bonus content left; it belongs inside the real film's folder
            home = tagged.get(tid)
            if not home:
                unresolved.append((p, "only extras, and the film is not in the library"))
                continue
            for d in sorted(os.listdir(p)):
                sub = os.path.join(p, d)
                if os.path.isdir(sub) and d.lower() in EXTRAS:
                    extras.append({"src": sub, "dst": os.path.join(home, d), "film": os.path.basename(home)})
            continue

        if len(feature) > 1:
            unresolved.append((p, f"{len(feature)} feature files in one folder"))
            continue
        src = feature[0]

        home = tagged.get(tid)
        if home and os.path.abspath(home) != os.path.abspath(p):
            keep_existing = videos(home)
            if keep_existing:
                mine, _ = quality(src)
                theirs, _ = quality(keep_existing[0])
                dupes.append(
                    {
                        "tmdb": tid,
                        "title": hit.get("title"),
                        "new": src,
                        "old": keep_existing[0],
                        "home": home,
                        "folder": p,
                        "year": hit.get("year") or info.get("year"),
                        "new_wins": mine > theirs,
                        "new_desc": describe(src),
                        "old_desc": describe(keep_existing[0]),
                    }
                )
                continue

        folder = movie_folder(hit.get("title") or info.get("title"), hit.get("year") or info.get("year"), tmdb_id=tid)
        i = media.probe(src)
        if not i.get("ok"):
            unresolved.append((p, f"ffprobe failed: {i.get('error')}"))
            continue
        fname = movie_name(
            hit.get("title") or info.get("title"),
            hit.get("year") or info.get("year"),
            i,
            source=detect_source(name) or detect_source(stem_of(src)),
            group=detect_group(name) or detect_group(stem_of(src)),
            ext=os.path.splitext(src)[1],
        )
        renames.append(
            {
                "folder_src": p,
                "folder_dst": os.path.join(root, folder),
                "file_src": src,
                "file_name": fname,
                "title": hit.get("title"),
                "tmdb": tid,
            }
        )

    # Two folders resolving to one film is a multi-part release, not a rename.
    # Renaming the first would leave the second with nowhere to go, so both are
    # reported and left for a human to place under Jellyfin's part convention.
    by_dst = {}
    for r in renames:
        by_dst.setdefault(r["folder_dst"], []).append(r)
    for dst, group in by_dst.items():
        if len(group) > 1:
            for r in group:
                renames.remove(r)
                unresolved.append(
                    (r["folder_src"], f"{len(group)} folders resolve to {os.path.basename(dst)} — multi-part release")
                )

    # ---------------------------------------------------------------- report
    print(f"=== {len(renames)} folders to rename")
    for r in renames:
        print(f"  {os.path.basename(r['folder_src'])[:74]}")
        print(f"    -> {os.path.basename(r['folder_dst'])}")
        print(f"       {r['file_name'][:96]}")

    print(f"\n=== {len(dupes)} duplicates")
    for d in dupes:
        win, lose = (d["new"], d["old"]) if d["new_wins"] else (d["old"], d["new"])
        print(f"  {d['title']} (tmdb {d['tmdb']})")
        print(f"    keep  {describe(win)}")
        print(f"          {os.path.basename(win)[:92]}")
        print(f"    drop  {describe(lose)}")
        print(f"          {os.path.basename(lose)[:92]}")

    print(f"\n=== {len(extras)} bonus-content folders to move into their film")
    for e in extras:
        print(
            f"  {os.path.basename(os.path.dirname(e['src']))}/{os.path.basename(e['src'])}"
            f"  ->  {e['film']}/{os.path.basename(e['dst'])}"
        )

    if unresolved:
        print(f"\n=== {len(unresolved)} left alone")
        for p, why in unresolved:
            print(f"  {os.path.basename(p)[:74]}\n      {why}")
    media.save_cache()

    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    qroot = os.path.join(QUARANTINE, f"movies-{stamp}")
    moves = []

    for r in renames:
        os.rename(r["folder_src"], r["folder_dst"])
        moves.append({"src": r["folder_src"], "dst": r["folder_dst"]})
        newfile = os.path.join(r["folder_dst"], r["file_name"])
        cur = os.path.join(r["folder_dst"], os.path.relpath(r["file_src"], r["folder_src"]))
        if os.path.abspath(cur) != os.path.abspath(newfile):
            os.rename(cur, newfile)
            moves.append({"src": cur, "dst": newfile})
        # subtitles keep the video's stem
        stem_old = os.path.splitext(os.path.basename(cur))[0]
        stem_new = os.path.splitext(r["file_name"])[0]
        d = os.path.dirname(cur)
        for f in sorted(os.listdir(d)):
            if os.path.splitext(f)[1].lower() in SUBS and f.startswith(stem_old):
                s, t = os.path.join(d, f), os.path.join(d, stem_new + f[len(stem_old) :])
                os.rename(s, t)
                moves.append({"src": s, "dst": t})
        print(f"  renamed {os.path.basename(r['folder_dst'])}")

    for d in dupes:
        win, lose = (d["new"], d["old"]) if d["new_wins"] else (d["old"], d["new"])

        rel = os.path.relpath(lose, stack.ROOT)
        qdst = os.path.join(qroot, rel)
        os.makedirs(os.path.dirname(qdst), exist_ok=True)
        shutil.move(lose, qdst)
        moves.append({"src": lose, "dst": qdst})

        if d["new_wins"]:
            # the better copy was the loose one; install it in the film's folder
            i = media.probe(win)
            fname = movie_name(
                d["title"],
                d["year"],
                i,
                source=detect_source(stem_of(win)),
                group=detect_group(stem_of(win)),
                ext=os.path.splitext(win)[1],
            )
            dst = os.path.join(d["home"], fname)
            shutil.move(win, dst)
            moves.append({"src": win, "dst": dst})
            print(f"  {d['title']}: kept {describe(dst)}")
            print(f"      -> {fname[:88]}")
        else:
            print(f"  {d['title']}: kept the copy already in place ({describe(win)})")

    for e in extras:
        os.makedirs(os.path.dirname(e["dst"]), exist_ok=True)
        if os.path.exists(e["dst"]):
            print(f"  skipped {e['dst']} — already there")
            continue
        shutil.move(e["src"], e["dst"])
        moves.append({"src": e["src"], "dst": e["dst"]})
        print(f"  moved {os.path.basename(e['dst'])} into {e['film']}")

    j = os.path.join(PLANS, f"{stamp}-movienames.json")
    json.dump({"moves": moves, "quarantine": qroot}, open(j, "w"), indent=2)
    media.save_cache()
    print(f"\n{len(moves)} moves\njournal: {j}")
    print(f"undo: ./ops/fix_movie_names.py --undo {j}")


if __name__ == "__main__":
    main()

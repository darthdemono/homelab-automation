# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""File completed qBittorrent downloads into the library under TRaSH names.

Sonarr and Radarr have completed-download handling switched off, because their
importer renames files its own way and fights jellyFix. This does the import
instead, and names every file the way `jellyfix.py apply` would.

Files are not copied out of the torrent. qBittorrent is told to rename the
content and then move it, so the download ends up in the library under its final
name **and keeps seeding** — one set of bytes, no duplicate, no broken ratio.

Matching is deliberately conservative, because a confident wrong match misfiles
an episode silently:

  * Sonarr identifies the series, but never the episode numbers. Its parser reads
    `One Piece - 1175` as S11E75, so an explicit SxxEyy in the release name wins,
    a bare three-or-four digit number is treated as an absolute number, and
    anything else is reported rather than guessed.
  * A movie must match a Radarr entry on title and year.
  * Only **monitored** entries are candidates. Sonarr and Radarr both hold a
    large set of unmonitored entries added purely so they show up on the
    calendar — they are not library items, and letting a download match one
    files it against a series or movie that was never meant to have files.
    `--include-unmonitored` turns the gate off.
  * Media details come from ffprobe on the finished file, so the name carries the
    real codec, bit depth and audio layout rather than what the release claimed.

Anything ambiguous is left exactly where it is and printed. Credentials are read
from the local config files; nothing is passed in.

Run it on a timer:
    */10 * * * * homelab apply <planned run>   # or schedule the job with `homelab schedule`
or with --loop 600 to keep it resident.
"""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from homelab_jellyfix import (
    media,  # noqa: E402
    stack,  # noqa: E402
)
from homelab_jellyfix.naming import episode_name, movie_name, season_folder  # noqa: E402
from homelab_jellyfix.parse import detect_group, detect_source  # noqa: E402

# Set from --include-unmonitored; see the module docstring.
INCLUDE_UNMONITORED = False

ARR = {
    "sonarr": {"port": stack.port("sonarr"), "cfg": stack.cfg("sonarr")},
    "radarr": {"port": stack.port("radarr"), "cfg": stack.cfg("radarr")},
}
# qBittorrent categories worth looking at -> which app owns them
CATEGORIES = {
    "tv-sonarr": "sonarr",
    "anime-sonarr": "sonarr",
    "animation-sonarr": "sonarr",
    "Shows": "sonarr",
    "Anime": "sonarr",
    "Animation": "sonarr",
    "radarr": "radarr",
    "anime-radarr": "radarr",
    "animated-radarr": "radarr",
    "Movies": "radarr",
    "Anime Movies": "radarr",
    "Animation Movies": "radarr",
}
# host path <-> path inside the qbittorrent container. Longest prefix wins, so
# <downloads> must be tried before <root>.
HOST_TO_CT = stack.container_map() or {stack.DOWNLOADS: "/downloads", stack.ROOT: "/media"}
LIBRARY_ROOTS = [
    stack.lib("Anime"),
    stack.lib("Shows"),
    stack.lib("Animated Shows"),
    stack.lib("Movies"),
    stack.lib("Anime Movies"),
    stack.lib("Animated Movies"),
]
PLANS = stack.PLANS

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".ts"}
SXXEYY = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})(?:v\d+)?\b", re.I)
# a bare episode number, once resolution and year tokens are out of the way
BARE = re.compile(r"(?:^|[ ._-])(\d{2,4})(?:[ ._-]|$)")


def anime_libraries():
    """Library paths flagged `anime = true` in config.toml.

    Whether a name carries the absolute episode number is jellyFix's decision,
    taken from the library the file lands in — not Sonarr's seriesType, which is
    "standard" on several series that live in /anime and would silently drop the
    token.
    """
    return [l["path"].rstrip("/") for l in stack.LIBRARY_SPECS if l.get("anime")]


ANIME_PATHS = anime_libraries()


def is_anime(path):
    return any(path == p or path.startswith(p + os.sep) for p in ANIME_PATHS)


def api_key(cfg):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", open(cfg).read()).group(1)


def call(app, path):
    meta = ARR[app]
    req = urllib.request.Request(
        f"{stack.url_for_port(meta['port'])}/api/v3/{path}", headers={"X-Api-Key": api_key(meta["cfg"])}
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{app} {path} -> {e.code}: {e.read().decode()[:300]}")


def qbt(path, data=None):
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "60", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"qbt {path}: {r.stderr[:200]}")
    return r.stdout.strip()


def to_host(p):
    for host, ct in sorted(HOST_TO_CT.items(), key=lambda kv: -len(kv[1])):
        if p == ct or p.startswith(ct + "/"):
            return host + p[len(ct) :]
    return p


def to_ct(p):
    for host, ct in sorted(HOST_TO_CT.items(), key=lambda kv: -len(kv[0])):
        if p == host or p.startswith(host + "/"):
            return ct + p[len(host) :]
    return p


def tags_from(release, fallback):
    """Source and release group come from the torrent's release name; the file
    inside is often just `Backrooms 2026.mkv` and carries neither. The extension
    is stripped first, or detect_group returns "ToonsHub.mkv"."""
    for cand in (release, fallback):
        stem, ext = os.path.splitext(cand)
        if ext.lower() not in VIDEO:  # "H.264-EniaHD" is not an extension
            stem = cand
        src, grp = detect_source(stem), detect_group(stem)
        if src or grp:
            return src, grp
    return None, None


FOLDER_RE = re.compile(r"^(?P<title>.+?)\s*\((?P<year>\d{4})\)\s*\[tvdbid-\d+\]$")


def title_from_folder(dest, series):
    """Title and year as jellyFix writes them, read back off the library folder.

    Falls back to Sonarr's own fields for a folder that is not in jellyFix's
    "Title (Year) [tvdbid-N]" shape yet.
    """
    m = FOLDER_RE.match(os.path.basename(dest.rstrip("/")))
    if not m:
        return series["title"], series.get("year")
    return m.group("title"), int(m.group("year"))


def strip_noise(name):
    """Remove tokens a bare episode number could be confused with."""
    n = re.sub(r"\b(?:19|20)\d{2}\b", " ", name)  # years
    n = re.sub(r"\b\d{3,4}[pi]\b", " ", n, flags=re.I)  # 1080p, 480i
    n = re.sub(r"\b[xh][ .]?26[45]\b", " ", n, flags=re.I)  # x265 / h.264
    n = re.sub(r"\b(?:AAC|AC3|EAC3|DDP?|DTS|FLAC|OPUS)[ .]?\d(?:[ .]\d)?\b", " ", n, flags=re.I)  # AAC2.0, DDP5.1
    n = re.sub(r"\b\d{1,2}bit\b", " ", n, flags=re.I)
    return n


def resolve_episodes(fname, series):
    """-> (list of episode dicts, why) or ([], why-not). Never guesses."""
    eps = call("sonarr", f"episode?seriesId={series['id']}")
    m = SXXEYY.search(fname)
    if m:
        s, e = int(m.group(1)), int(m.group(2))
        # Judas weekly TYBW files are numbered S01Exx inside the cour, but Sonarr
        # holds them as season 17 (S01E43 is really S17E43, absolute 409)
        if s == 1 and re.search(r"Thousand[ ._-]Year[ ._-]Blood[ ._-]War", fname, re.I):
            s = 17
        hit = [x for x in eps if x["seasonNumber"] == s and x["episodeNumber"] == e]
        if hit:
            return hit, f"explicit S{s:02d}E{e:02d}"
        # some anime releases put the absolute number in the episode field
        alt = [x for x in eps if x.get("absoluteEpisodeNumber") == e]
        if alt and is_anime(series.get("path", "")):
            return alt, (f"S{s:02d}E{e:02d} matched no episode; {e} read as an absolute number")
        return [], f"release says S{s:02d}E{e:02d}, series has no such episode"

    nums = BARE.findall(strip_noise(os.path.splitext(fname)[0]))
    cands = {int(n) for n in nums}
    hits = [x for x in eps if x.get("absoluteEpisodeNumber") in cands]
    if len(hits) == 1:
        return hits, f"absolute {hits[0]['absoluteEpisodeNumber']}"
    if len(hits) > 1:
        return [], f"{len(hits)} episodes match the numbers {sorted(cands)}"
    return [], f"no season/episode found (numbers seen: {sorted(cands) or 'none'})"


def existing_episode(series_dir, ep):
    """The filename jellyFix would already have given this episode, if present.
    Filing a second copy of an episode is how a library ends up with a REPACK
    and its original both claiming one slot."""
    d = os.path.join(series_dir, season_folder(ep["seasonNumber"]))
    tag = f" - S{ep['seasonNumber']:02d}E{ep['episodeNumber']:02d} - "
    try:
        for f in os.listdir(d):
            if tag in f and os.path.splitext(f)[1].lower() in VIDEO:
                return f
    except OSError:
        pass
    return None


def find_dir(root_hint, name_suffix):
    for root in LIBRARY_ROOTS:
        p = os.path.join(root, name_suffix)
        if os.path.isdir(p):
            return p
    return root_hint


EXTRA_RE = re.compile(r"(^|/)extras?/|\bNC(OP|ED)\b|\bcreditless\b|\btrailer\b|\bPV\b|\bmenu\b", re.I)


def extra_name(base):
    """Non-episode video (creditless OP/ED, trailers): kept, never matched to an episode."""
    return "extras/" + re.sub(r"^\[[^\]]+\]\s*", "", base)


def provisional_info(release):
    """Quality, bit depth and codec read off the release name, for a file that is
    still downloading. Audio is unknown until it finishes; the name is redone then."""
    h = re.search(r"\b(2160|1080|720|480)p\b", release)
    height = int(h.group(1)) if h else None
    return {
        "ok": True,
        "height": height,
        "width": {2160: 3840, 1080: 1920, 720: 1280, 480: 854}.get(height),
        "pix_fmt": "yuv420p10le" if re.search(r"10[ ._-]?bit", release, re.I) else "yuv420p",
        "vcodec": "hevc"
        if re.search(r"x265|hevc|h[ ._-]?265", release, re.I)
        else "h264"
        if re.search(r"x264|avc|h[ ._-]?264", release, re.I)
        else None,
        "audio": [],
    }


def plan_torrent(t, partial=False):
    """-> dict describing what to do, or a (None, reason) skip.

    partial: the torrent is still downloading; plan only its finished files and
    rename them in place (no move), so a long batch is named as it arrives."""
    cat = t.get("category") or ""
    app = CATEGORIES.get(cat)
    if not app:
        return None, f"category {cat!r} is not watched"
    if t.get("progress", 0) < 1.0 and not partial:
        return None, "not finished"
    save = to_host(t.get("save_path", ""))
    if any(save.rstrip("/") == r or save.startswith(r + os.sep) for r in LIBRARY_ROOTS):
        return None, "already inside the library"

    files = json.loads(qbt("torrents/files", {"hash": t["hash"]}) or "[]")
    vids = [
        f for f in files if os.path.splitext(f["name"])[1].lower() in VIDEO and (partial or f.get("progress", 0) >= 1.0)
    ]
    if not vids:
        return None, "no completed video files"

    if app == "sonarr":
        parsed = call("sonarr", "parse?title=" + urllib.parse.quote(t["name"]))
        series = (parsed or {}).get("series")
        if not series:
            return None, "Sonarr could not identify the series"
        # /parse returns a thin series object whose seriesType is always
        # "standard"; the real record decides whether names carry the absolute
        # episode number, so take identification from parse and nothing else.
        series = call("sonarr", f"series/{series['id']}")
        if not series.get("monitored") and not INCLUDE_UNMONITORED:
            return None, (f"{series['title']} is unmonitored in Sonarr (calendar-only entry); not importing into it")
        dest = series["path"]
        dest = to_host(dest) if dest.startswith("/") and not os.path.isdir(dest) else dest
        if not os.path.isdir(dest):
            # Sonarr's path is container-side; map it through the library roots
            dest = find_dir(dest, os.path.basename(dest))
        if not os.path.isdir(dest):
            return None, f"series folder not found on disk: {dest}"

        # jellyFix owns the library folder name, and it comes from TVDB:
        # "BLEACH (2004) [tvdbid-74796]", where Sonarr's own title is "Bleach".
        # Name files after the folder so an import matches its siblings exactly
        # and `jellyfix.py plan` has nothing left to rename.
        show, year = title_from_folder(dest, series)

        out = []
        for f in vids:
            base = os.path.basename(f["name"])
            if EXTRA_RE.search(f["name"]):
                # creditless OP/ED, trailers: not wanted. Stop downloading, delete what is there.
                host = os.path.join(to_host(t["save_path"]), f["name"])
                if f.get("priority", 1) != 0 or os.path.exists(host):
                    out.append(
                        {"old": f["name"], "new": None, "why": "extra, dropped", "index": f["index"], "host": host}
                    )
                continue
            eps, why = resolve_episodes(base, series)
            if len(eps) != 1:
                return None, f"{base}: {why}"
            ep = eps[0]
            if f.get("progress", 0) < 1.0:
                info = provisional_info(t["name"])  # no probe on a half-written file
            else:
                info = media.probe(os.path.join(to_host(t["save_path"]), f["name"]))
                if not info.get("ok"):
                    return None, f"{base}: ffprobe failed ({info.get('error')})"
            src, grp = tags_from(t["name"], base)
            new = episode_name(
                show,
                year,
                ep["seasonNumber"],
                ep["episodeNumber"],
                ep["title"],
                info,
                source=src,
                group=grp,
                absolute=(ep.get("absoluteEpisodeNumber") if is_anime(dest) else None),
                ext=os.path.splitext(base)[1],
            )
            rel = os.path.join(season_folder(ep["seasonNumber"]), new)
            clash = existing_episode(dest, ep)
            if clash:
                return None, (f"{base}: S{ep['seasonNumber']:02d}E{ep['episodeNumber']:02d} already on disk as {clash}")
            if rel != f["name"]:  # already named by an earlier pass
                out.append({"old": f["name"], "new": rel, "why": why})
        if not out:
            return None, "files already named"
        return {
            "hash": t["hash"],
            "name": t["name"],
            "dest": dest,
            "files": out,
            "kind": "series",
            "partial": partial,
        }, None

    movies = call("radarr", "movie")
    # block_slop.py unmonitors a movie once it is grabbed, so "was grabbed" counts
    # as wanted too; a calendar-only entry has never been grabbed.
    grabbed = {r["movieId"] for r in call("radarr", "history?pageSize=1000&eventType=1")["records"]}
    wanted = lambda m: m.get("monitored") or m["id"] in grabbed
    by_id = {m["id"]: m for m in movies}
    parsed = call("radarr", "parse?title=" + urllib.parse.quote(t["name"]))
    mv = (parsed or {}).get("movie")
    if mv:
        # /parse returns a thin movie object; the real record carries monitored.
        mv = by_id.get(mv.get("id"), mv)
    else:
        pi = (parsed or {}).get("parsedMovieInfo") or {}
        title, year = (pi.get("primaryMovieTitle") or "").lower(), pi.get("year")
        # Title+year is a loose key, and the calendar-only entries are exactly
        # the ones with no file and a plausible title. Never match one here.
        mv = next(
            (
                m
                for m in movies
                if m["title"].lower() == title and m["year"] == year and (wanted(m) or INCLUDE_UNMONITORED)
            ),
            None,
        )
    if not mv:
        return None, "Radarr could not identify the movie"
    if not wanted(mv) and not INCLUDE_UNMONITORED:
        return None, (
            f"{mv['title']} ({mv.get('year')}) is unmonitored in Radarr (calendar-only entry); not importing into it"
        )
    dest = mv["path"]
    if not os.path.isdir(dest):
        dest = find_dir(dest, os.path.basename(dest))
    if not os.path.isdir(dest):
        return None, f"movie folder not found on disk: {dest}"
    if not re.search(r"\[(?:tmdb|imdb)id-[^\]]+\]$", os.path.basename(dest)):
        return None, (f"Radarr still points at a pre-rename folder ({os.path.basename(dest)}); reconcile Radarr first")
    if len(vids) != 1:
        return None, f"{len(vids)} video files in a movie torrent"
    have = [f for f in os.listdir(dest) if os.path.splitext(f)[1].lower() in VIDEO]
    if have:
        return None, f"a video is already in that folder: {have[0]}"
    base = os.path.basename(vids[0]["name"])
    info = media.probe(os.path.join(to_host(t["save_path"]), vids[0]["name"]))
    if not info.get("ok"):
        return None, f"{base}: ffprobe failed ({info.get('error')})"
    src, grp = tags_from(t["name"], base)
    new = movie_name(mv["title"], mv.get("year"), info, source=src, group=grp, ext=os.path.splitext(base)[1])
    return {
        "hash": t["hash"],
        "name": t["name"],
        "dest": dest,
        "files": [{"old": vids[0]["name"], "new": new, "why": "movie"}],
        "kind": "movie",
    }, None


def run_once(apply_it):
    torrents = json.loads(qbt("torrents/info") or "[]")
    plans, skips = [], []
    for t in torrents:
        if (t.get("category") or "") not in CATEGORIES:
            continue
        p, why = plan_torrent(t, partial=t.get("progress", 0) < 1.0)
        (plans.append(p) if p else skips.append((t["name"], why)))

    print(f"=== {len(plans)} torrents to file")
    for p in plans:
        print(f"  {p['name'][:78]}")
        print(f"    -> {p['dest']}")
        for f in p["files"]:
            print(f"       {f['new'] or f['old']}   [{f['why']}]")
    if skips:
        print(f"\n=== {len(skips)} left alone")
        for n, why in skips:
            print(f"  {n[:70]}\n      {why}")
    media.save_cache()

    if not plans or not apply_it:
        if plans:
            print("\n[dry run] pass --apply to file them")
        return 0

    done = []
    for p in plans:
        for f in p["files"]:
            if f["new"] is None:
                qbt("torrents/filePrio", {"hash": p["hash"], "id": f["index"], "priority": 0})
                if os.path.exists(f["host"]):
                    os.remove(f["host"])
                continue
            r = qbt("torrents/renameFile", {"hash": p["hash"], "oldPath": f["old"], "newPath": f["new"]})
            if r:
                print(f"  rename FAILED {f['new']}: {r[:150]}")
                break
        else:
            if not p.get("partial"):  # still downloading: name files, do not move
                qbt("torrents/setLocation", {"hashes": p["hash"], "location": to_ct(p["dest"])})
            done.append({"torrent": p["name"], "dest": p["dest"], "files": [f["new"] for f in p["files"] if f["new"]]})
            print(f"  filed {p['name'][:60]}")

    if done:
        os.makedirs(PLANS, exist_ok=True)
        j = os.path.join(PLANS, time.strftime("%Y%m%d-%H%M%S") + "-import.json")
        json.dump({"filed": done}, open(j, "w"), indent=2)
        print(f"\nfiled {len(done)} torrents; journal: {j}")
    return len(done)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually file them")
    ap.add_argument(
        "--include-unmonitored",
        action="store_true",
        help="allow unmonitored Sonarr/Radarr entries as match candidates (calendar-only entries; off by default)",
    )
    ap.add_argument("--loop", type=int, metavar="SECONDS", help="keep running, checking every SECONDS")
    args = ap.parse_args()
    global INCLUDE_UNMONITORED
    INCLUDE_UNMONITORED = args.include_unmonitored
    while True:
        run_once(args.apply)
        if args.apply:  # retitle the torrents just filed, so they seed under library names
            subprocess.run(
                [os.path.join(os.path.dirname(os.path.abspath(__file__)), "rename_torrents.py"), "--apply"], check=False
            )
            subprocess.run(
                [os.path.join(os.path.dirname(os.path.abspath(__file__)), "write_episode_nfo.py"), "--apply"],
                check=False,
            )
        if not args.loop:
            return
        time.sleep(args.loop)


if __name__ == "__main__":
    main()

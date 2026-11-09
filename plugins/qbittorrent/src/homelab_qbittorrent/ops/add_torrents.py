# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Add .torrent files to qBittorrent, placed and named the way jellyFix would.

These are hand-grabbed episodes for a series Sonarr already tracks, so they skip
the *arr download categories entirely and download straight into the library
season folder under their final TRaSH name. That is the convention the existing
manual grabs in this qBittorrent follow (category `Anime`, tag `Private`), and it means no import or rename step afterwards.

The filename is built by jellyfix.naming.episode_name — the same code path
`jellyfix.py apply` uses — so what lands on disk is byte-identical to what the
renamer would produce. Season, episode and title come from Sonarr, keyed on the
absolute episode number parsed out of the release name.

Media details (resolution, bit depth, codec, audio, language) normally come from
ffprobe, which cannot run on a file that has not been downloaded yet. They are
passed in via --media-spec instead, and the default matches this release series.
`--verify` checks a generated name against an episode already on disk before
anything is added; run it whenever the spec might not hold.

Nothing is added until the plan has been printed. Torrents are added stopped,
renamed, then started, so qBittorrent writes the final name from the first byte
and no partially-named file ever appears in the library.
"""

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request

from homelab_jellyfix import stack
from homelab_jellyfix.naming import episode_name, season_folder  # noqa: E402

SONARR_CFG = stack.cfg("sonarr")
SONARR_PORT = stack.port("sonarr")
# host path -> path inside the qbittorrent container
HOST_TO_CT = stack.container_map() or {stack.DOWNLOADS: "/downloads", stack.ROOT: "/media"}
STAGING = stack.DOWNLOADS + "/_torrent_staging"
STAGING_CT = stack.to_container(STAGING)

DEFAULT_SPEC = {
    "height": 1080,
    "width": 1920,
    "pix_fmt": "yuv420p",
    "vcodec": "hevc",
    "audio": [{"codec": "aac", "channels": 2, "lang": "jpn"}],
}


# ------------------------------------------------------------------ bencode
def bdecode(data, i=0):
    c = data[i : i + 1]
    if c == b"i":
        j = data.index(b"e", i)
        return int(data[i + 1 : j]), j + 1
    if c == b"l":
        out, i = [], i + 1
        while data[i : i + 1] != b"e":
            v, i = bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        out, i = {}, i + 1
        while data[i : i + 1] != b"e":
            k, i = bdecode(data, i)
            v, i = bdecode(data, i)
            out[k] = v
        return out, i + 1
    j = data.index(b":", i)
    n = int(data[i:j])
    return data[j + 1 : j + 1 + n], j + 1 + n


def torrent_meta(path):
    """-> (infohash_v1, name, single_file_name or None)"""
    raw = open(path, "rb").read()
    d, _ = bdecode(raw)
    # hash the info dictionary's raw bytes; re-encoding risks changing them
    key = raw.index(b"4:info") + len(b"4:info")
    _info, end = bdecode(raw, key)
    ih = hashlib.sha1(raw[key:end]).hexdigest()
    info = d[b"info"]
    name = info[b"name"].decode("utf-8", "replace")
    single = None if b"files" in info else name
    return ih, name, single


# -------------------------------------------------------------------- APIs
def sonarr(path):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(SONARR_CFG).read()).group(1)
    req = urllib.request.Request(f"{stack.url_for_port(SONARR_PORT)}/api/v3/{path}", headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def qbt(path, data=None, files=None):
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "60", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--form", f"{k}={v}"] if files else ["--data-urlencode", f"{k}={v}"]
    for k, v in (files or {}).items():
        cmd += ["--form", f"{k}=@{v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"qbt {path} failed: {r.stderr[:300]}")
    return r.stdout.strip()


def add_ok(out):
    """qBittorrent 5.x answers torrents/add with JSON; older builds say "Ok.".
    Treat both as success — an empty body is also a success on 4.x."""
    out = (out or "").strip()
    if not out or out.lower() in ("ok.", "ok"):
        return True
    try:
        return json.loads(out).get("success_count", 0) >= 1
    except ValueError:
        return False


def to_container(p):
    for host, ct in HOST_TO_CT.items():
        if p.startswith(host):
            return ct + p[len(host) :]
    raise SystemExit(f"{p} is not inside a path the qbittorrent container can see")


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="directory of .torrent files")
    ap.add_argument("--tvdbid", type=int, required=True, help="series to match against")
    ap.add_argument("--library", required=True, help="host path of the series folder")
    ap.add_argument("--category", default="Anime")
    ap.add_argument("--tags", default="Private")
    ap.add_argument("--group", default=None, help="release group for the name tail")
    ap.add_argument("--source", default=None, help="e.g. WEBRip, Bluray, WEBDL")
    ap.add_argument("--media-spec", default=None, help="JSON overriding resolution/codec/audio for the name tail")
    ap.add_argument(
        "--verify",
        type=int,
        metavar="ABS",
        help="regenerate this absolute episode's name and diff it against the file already on disk, then exit",
    )
    ap.add_argument("--apply", action="store_true", help="actually add them")
    args = ap.parse_args()

    spec = json.loads(args.media_spec) if args.media_spec else DEFAULT_SPEC
    series = next((s for s in sonarr("series") if s["tvdbId"] == args.tvdbid), None)
    if not series:
        raise SystemExit(f"no series in Sonarr with tvdbId {args.tvdbid}")
    eps = sonarr(f"episode?seriesId={series['id']}")
    by_abs = {e["absoluteEpisodeNumber"]: e for e in eps if e.get("absoluteEpisodeNumber")}
    by_se = {(e["seasonNumber"], e["episodeNumber"]): e for e in eps}

    def name_for(e, ext):
        return episode_name(
            series["title"],
            series.get("year"),
            e["seasonNumber"],
            e["episodeNumber"],
            e["title"],
            spec,
            source=args.source,
            group=args.group,
            absolute=e.get("absoluteEpisodeNumber"),
            ext=ext,
        )

    if args.verify:
        e = by_abs[args.verify]
        got = name_for(e, ".mkv")
        d = os.path.join(args.library, season_folder(e["seasonNumber"]))
        on_disk = [f for f in os.listdir(d) if f" - {args.verify:03d} - " in f]
        print(f"generated: {got}")
        print(f"on disk  : {on_disk[0] if on_disk else '(not found)'}")
        print("MATCH" if on_disk and on_disk[0] == got else "*** MISMATCH ***")
        return

    # ------------------------------------------------------------ plan
    plan, problems = [], []
    for p in sorted(glob.glob(os.path.join(args.dir, "*.torrent"))):
        ih, tname, single = torrent_meta(p)
        # An explicit SxxEyy is authoritative; only releases without one are read
        # as absolute numbering. Guessing absolute first misfires on codec tags
        # like `H.264`, which look exactly like a three-digit episode number.
        se = re.search(r"\bS(\d{1,2})E(\d{1,3})\b", tname, re.I)
        if se:
            sn, en = int(se.group(1)), int(se.group(2))
            e = by_se.get((sn, en))
            if not e:
                problems.append((p, f"Sonarr has no S{sn:02d}E{en:02d}"))
                continue
        else:
            m = re.search(r"\b(\d{3,4})\b", tname.replace("1080p", "").replace("720p", ""))
            if not m:
                problems.append((p, "no episode number in the release name"))
                continue
            absno = int(m.group(1))
            if absno not in by_abs:
                problems.append((p, f"Sonarr has no absolute episode {absno}"))
                continue
            e = by_abs[absno]
        if not single:
            problems.append((p, "multi-file torrent, not handled"))
            continue
        newname = name_for(e, os.path.splitext(single)[1] or ".mkv")
        dest = os.path.join(args.library, season_folder(e["seasonNumber"]))
        plan.append(
            {
                "torrent": p,
                "hash": ih,
                "abs": e.get("absoluteEpisodeNumber"),
                "season": e["seasonNumber"],
                "ep": f"S{e['seasonNumber']:02d}E{e['episodeNumber']:02d}",
                "old": single,
                "new": newname,
                "dest": dest,
                "have": e["hasFile"],
            }
        )

    for p, why in problems:
        print(f"SKIP {os.path.basename(p)}: {why}")
    if not plan:
        raise SystemExit("nothing to add")

    print(f"\n{len(plan)} torrents -> {args.category} / {args.tags}")
    for s in sorted(set(x["season"] for x in plan)):
        d = os.path.join(args.library, season_folder(s))
        n = sum(1 for x in plan if x["season"] == s)
        print(f"  {n:3} into {d}" + ("" if os.path.isdir(d) else "   (will be created)"))
    print()
    for x in plan:
        flag = "  ALREADY HAVE" if x["have"] else ""
        print(f"  {x['ep']} abs {x['abs']}{flag}\n      {x['new']}")

    dupes = [x for x in plan if x["have"]]
    if dupes:
        print(f"\n{len(dupes)} of these are episodes Sonarr already has a file for.")

    if not args.apply:
        print("\n[plan only] re-run with --apply to add them")
        return

    # ------------------------------------------------------------ apply
    # Anything already in the session is skipped straight to rename+start, so a
    # re-run after a partial failure finishes the job instead of erroring out.
    present = {t["hash"] for t in json.loads(qbt("torrents/info") or "[]")}

    os.makedirs(STAGING, exist_ok=True)
    added = renamed = 0
    for x in plan:
        os.makedirs(x["dest"], exist_ok=True)
        stage = os.path.join(STAGING, os.path.basename(x["torrent"]))
        shutil.copy2(x["torrent"], stage)
        stage_ct = os.path.join(STAGING_CT, os.path.basename(x["torrent"]))

        if x["hash"] in present:
            print(f"  = {x['ep']} already in qBittorrent, renaming only")
        else:
            out = qbt(
                "torrents/add",
                {
                    "savepath": to_container(x["dest"]),
                    "category": args.category,
                    "tags": args.tags,
                    "autoTMM": "false",
                    "stopped": "true",
                    "paused": "true",
                    "contentLayout": "Original",
                },
                files={"torrents": stage_ct},
            )
            if not add_ok(out):
                print(f"  add failed for {x['ep']}: {out[:200]}")
                os.remove(stage)
                continue
            added += 1
        # rename before starting, so the final name is written from byte zero
        r = qbt("torrents/renameFile", {"hash": x["hash"], "oldPath": x["old"], "newPath": x["new"]})
        if r:
            print(f"  rename FAILED {x['ep']}: {r[:200]}")
        else:
            renamed += 1
        qbt("torrents/start", {"hashes": x["hash"]})
        qbt("torrents/resume", {"hashes": x["hash"]})
        if os.path.exists(stage):
            os.remove(stage)
        print(f"  + {x['ep']}  {x['new'][:70]}")

    try:
        os.rmdir(STAGING)
    except OSError:
        pass
    print(f"\nadded {added} torrents, renamed {renamed}")


if __name__ == "__main__":
    main()

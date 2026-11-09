# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Find torrents for library files that no torrent seeds, and attach them.

Cross-seeding: a torrent whose file list holds a file of exactly the same byte
count as one already in the library is almost certainly the release it came from.
For each library video with no torrent, search Prowlarr, fetch the .torrent of
every plausible result, and look for a file of identical size. A hit is added to
qBittorrent paused, pointed at the library folder, the torrent's file renamed to
the library name, then force-rechecked. It only seeds if the recheck reaches
100%; anything less is removed again (no data is touched either way, files are
only read). Nothing is written to the library.

    ops/cross_seed.py "Pather Panchali (1955)"      # plan, one title (substring)
    ops/cross_seed.py --apply ...
"""

import argparse
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

from homelab_jellyfix import stack

LIBRARY = [stack.lib("Movies"), stack.lib("Anime Movies"), stack.lib("Animated Movies")]
CT = (stack.ROOT, "/bigchungus")
VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
PROWLARR = f"{stack.url('prowlarr')}/api/v1"


def prowlarr_key():
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", open(stack.cfg("prowlarr")).read()).group(1)


def qbt(path, data=None, raw=None):
    cmd = ["docker", "exec", "-i", stack.QBT_CONTAINER, "curl", "-s", "-m", "60", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def bdecode(b, i=0):
    c = b[i : i + 1]
    if c == b"i":
        e = b.index(b"e", i)
        return int(b[i + 1 : e]), e + 1
    if c == b"l":
        i += 1
        out = []
        while b[i : i + 1] != b"e":
            v, i = bdecode(b, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i += 1
        out = {}
        while b[i : i + 1] != b"e":
            k, i = bdecode(b, i)
            v, i = bdecode(b, i)
            out[k] = v
        return out, i + 1
    colon = b.index(b":", i)
    n = int(b[i:colon])
    return b[colon + 1 : colon + 1 + n], colon + 1 + n


def torrent_files(data):
    """-> (infohash-free list of (path, length)) from raw .torrent bytes."""
    meta, _ = bdecode(data)
    info = meta[b"info"]
    if b"files" in info:
        return [
            ("/".join(p.decode("utf8", "replace") for p in f[b"path"]), f[b"length"]) for f in info[b"files"]
        ], info[b"name"].decode("utf8", "replace")
    return [(info[b"name"].decode("utf8", "replace"), info[b"length"])], None


def orphans(titles):
    held = set()
    for t in json.loads(qbt("torrents/info") or "[]"):
        for f in json.loads(qbt("torrents/files", {"hash": t["hash"]}) or "[]"):
            held.add(f["size"])
    for root in LIBRARY:
        for d in sorted(os.listdir(root)):
            if titles and not any(s.lower() in d.lower() for s in titles):
                continue
            for fn in os.listdir(os.path.join(root, d)):
                p = os.path.join(root, d, fn)
                if os.path.splitext(fn)[1].lower() in VIDEO and os.path.getsize(p) not in held:
                    yield d, p, os.path.getsize(p)


def search(key, query):
    url = f"{PROWLARR}/search?" + urllib.parse.urlencode({"query": query, "type": "search"})
    req = urllib.request.Request(url, headers={"X-Api-Key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def fetch(url, key):
    """.torrent bytes, or the magnet link (str) when the indexer redirects to one."""
    req = urllib.request.Request(url, headers={"X-Api-Key": key})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc.startswith("magnet:"):
            return loc
        raise


def add(data, savepath):
    """Add paused to qBittorrent at savepath; -> (hash, [(path, size)]) once metadata is in."""
    before = {t["hash"] for t in json.loads(qbt("torrents/info") or "[]")}
    base = [
        "docker",
        "exec",
        stack.QBT_CONTAINER,
        "curl",
        "-s",
        f"{stack.QBT_INTERNAL}/api/v2/torrents/add",
        "-F",
        f"savepath={savepath}",
        "-F",
        "stopped=true",
        "-F",
        "paused=true",
        "-F",
        "category=radarr",
        "-F",
        "skip_checking=true",
    ]
    if isinstance(data, str):
        subprocess.run(base + ["-F", f"urls={data}"], capture_output=True)
    else:
        tmp = f"/tmp/xseed-{os.getpid()}.torrent"
        open(tmp, "wb").write(data)
        subprocess.run(["docker", "cp", tmp, stack.QBT_CONTAINER + ":" + tmp], check=True)
        subprocess.run(base + ["-F", f"torrents=@{tmp}"], capture_output=True)
        os.unlink(tmp)
    for _ in range(45):
        time.sleep(2)
        new = [t for t in json.loads(qbt("torrents/info") or "[]") if t["hash"] not in before]
        if new:
            files = json.loads(qbt("torrents/files", {"hash": new[0]["hash"]}) or "[]")
            if files:
                return new[0]["hash"], [(f["name"], f["size"]) for f in files]
    raise RuntimeError("no metadata")


def finish(h, tpath, path, size):
    """Point the one matching file at the library file, skip the rest, recheck."""
    files = json.loads(qbt("torrents/files", {"hash": h}))
    keep = next(f["index"] for f in files if f["name"] == tpath)
    skip = "|".join(str(f["index"]) for f in files if f["index"] != keep)
    if skip:
        qbt("torrents/filePrio", {"hash": h, "id": skip, "priority": 0})
    qbt("torrents/renameFile", {"hash": h, "oldPath": tpath, "newPath": os.path.basename(path)})
    qbt("torrents/recheck", {"hash": h})
    for _ in range(600):
        time.sleep(3)
        t = json.loads(qbt("torrents/info", {"hashes": h}))[0]
        if not t["state"].startswith("checking"):
            break
    f = next(f for f in json.loads(qbt("torrents/files", {"hash": h})) if f["index"] == keep)
    if f["progress"] >= 1:
        qbt("torrents/start", {"hashes": h})
        return True
    qbt("torrents/delete", {"hashes": h, "deleteFiles": "false"})
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("titles", nargs="*", help="substring(s) of library folder names")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    key = prowlarr_key()
    for folder, path, size in orphans(a.titles):
        query = re.sub(r"\s*\[.*", "", folder)
        print(f"\n== {folder}  ({size} bytes)")
        hit_seen = set()
        savepath = CT[1] + os.path.dirname(path)[len(CT[0]) :]
        hit = None
        for res in sorted(search(key, query), key=lambda r: -(r.get("seeders") or 0)):
            if not (size <= res["size"] <= size * 2.5) or res["title"] in hit_seen:
                continue
            hit_seen.add(res["title"])
            url = res.get("downloadUrl") or ""
            if not url.startswith("http"):
                continue
            try:
                data = fetch(url, key)
                if isinstance(data, str):  # magnet: metadata only via qBittorrent
                    h, files = add(data, savepath)
                else:
                    files = torrent_files(data)[0]
                    h = None
            except Exception as e:
                print(f"  skip {res['title'][:60]}: {str(e)[:60]}")
                continue
            m = [f for f in files if f[1] == size]
            print(f"  {'HIT ' if m else 'no  '}{res['indexer']}: {res['title'][:70]}")
            if h and not m:
                qbt("torrents/delete", {"hashes": h, "deleteFiles": "false"})
            if m:
                hit = (data, h, m[0][0])
                break
        if not hit:
            print("  no torrent holds a file of that exact size")
            continue
        if a.apply:
            data, h, tpath = hit
            if h is None:
                h, _ = add(data, savepath)
            print("  seeding" if finish(h, tpath, path, size) else "  recheck failed, removed")


if __name__ == "__main__":
    main()

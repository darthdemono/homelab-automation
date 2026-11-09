# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Make Sonarr/Radarr downloads land in their per-library folders instead of /downloads.

Two independent faults put everything in the root of <downloads>:

1. qBittorrent runs Manual Torrent Management (`auto_tmm_enabled=false`) with
   `use_category_paths_in_manual_mode=false`. In that combination a category's
   savePath is never consulted at all — every torrent lands in the global
   `save_path`, which is `/downloads`. The `/downloads/<category>` paths that
   ops/configure_arr.py wrote were correct and simply never read.

2. Sonarr and Radarr each have three qBittorrent clients with **no tags**, so
   every one of them is eligible for every item and selection falls to priority:
   `qbittorrent-tv` (priority 1) wins for all series and `qbittorrent-movies`
   for all movies. The anime/animation categories are unreachable, so fixing
   (1) alone would just funnel the whole library into `tv-sonarr` / `radarr`.

Fixing (2) needs tag routing: each client gets a distinct tag, and every series
or movie is tagged by which root folder it lives in. A tagged client only serves
matching items, so each item ends up with exactly one eligible client.

Turning on `use_category_paths_in_manual_mode` also makes the legacy Windows
category paths live (`E:/Games`, `Z:/OS` — leftovers from a Windows qBittorrent).
On Linux those are *relative*, so a new torrent would create `/downloads/E:/Games`.
They are blanked, which falls back to `/downloads` — exactly what happens today.

Idempotent: a second run reports nothing to change.
"""

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.request

import os
from homelab_jellyfix import stack

ARR = {
    "sonarr": {
        "port": stack.port("sonarr"),
        "cfg": stack.cfg("sonarr"),
        "item": "series",
        "editor": "series/editor",
        "ids": "seriesIds",
        # root folder -> (tag, download client name)
        "routes": {
            "/shows": ("shows", "qbittorrent-tv"),
            "/anime": ("anime", "qbittorrent-anime"),
            "/animation": ("animation", "qbittorrent-animation"),
        },
    },
    "radarr": {
        "port": stack.port("radarr"),
        "cfg": stack.cfg("radarr"),
        "item": "movie",
        "editor": "movie/editor",
        "ids": "movieIds",
        "routes": {
            "/movies": ("movies", "qbittorrent-movies"),
            "/anime_movies": ("anime", "qbittorrent-anime"),
            "/animated_movies": ("animated", "qbittorrent-animated"),
        },
    },
}

# Categories qBittorrent must route; anything else is the user's own.
ARR_CATEGORIES = {"tv-sonarr", "anime-sonarr", "animation-sonarr", "radarr", "anime-radarr", "animated-radarr"}

WINDOWS_PATH = re.compile(r"^[A-Za-z]:[\\/]")
BACKUP = os.path.join(stack.STATE, "qbt-category-paths.bak.json")


# ---------------------------------------------------------------- transports
def api_key(cfg):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(cfg).read())
    if not m:
        raise SystemExit(f"no ApiKey in {cfg}")
    return m.group(1)


def call(port, key, path, method="GET", body=None):
    url = f"{stack.url_for_port(port)}/api/v3/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"X-Api-Key": key, "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")


def qbt(path, data=None):
    """qBittorrent shares gluetun's netns, so from inside the container
    127.0.0.1:8080 is localhost and WebUI\\LocalHostAuth=false applies."""
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "30", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    for k, v in (data or {}).items():
        cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"qbt {path} failed: {r.stderr[:300]}")
    return r.stdout.strip()


def qbt_json(path, data=None):
    out = qbt(path, data)
    return json.loads(out) if out else None


# ------------------------------------------------------------------- phase 1
def fix_qbt_preferences(dry, tag):
    print("\n=== qBittorrent: honour category save paths")
    prefs = qbt_json("app/preferences")
    print(f"  auto_tmm_enabled                  = {prefs.get('auto_tmm_enabled')}")
    print(f"  use_category_paths_in_manual_mode = {prefs.get('use_category_paths_in_manual_mode')}")
    if prefs.get("use_category_paths_in_manual_mode"):
        print("  already on")
        return
    print(f"  {tag}set use_category_paths_in_manual_mode = true")
    if not dry:
        qbt("app/setPreferences", {"json": json.dumps({"use_category_paths_in_manual_mode": True})})


# ------------------------------------------------------------------- phase 2
def blank_windows_category_paths(dry, tag):
    print("\n=== qBittorrent: neutralise leftover Windows category paths")
    cats = qbt_json("torrents/categories") or {}
    bad = {n: c["savePath"] for n, c in cats.items() if WINDOWS_PATH.match(c.get("savePath") or "")}
    if not bad:
        print("  none")
        return
    if not dry:
        with open(BACKUP, "w") as f:
            json.dump(bad, f, indent=2)
        print(f"  originals saved to {BACKUP}")
    for name, path in sorted(bad.items()):
        print(f"  {tag}{name}: {path!r} -> '' (falls back to /downloads)")
        if not dry:
            qbt("torrents/editCategory", {"category": name, "savePath": ""})


# ------------------------------------------------------------------- phase 3
def relocate_stranded(dry, tag):
    print("\n=== qBittorrent: move arr torrents stranded in the download root")
    prefs = qbt_json("app/preferences")
    root = prefs["save_path"].rstrip("/")
    cats = qbt_json("torrents/categories") or {}
    moved = 0
    for t in qbt_json("torrents/info") or []:
        cat = t.get("category") or ""
        if cat not in ARR_CATEGORIES:
            continue
        if t.get("save_path", "").rstrip("/") != root:
            continue
        dest = (cats.get(cat, {}).get("savePath") or "").rstrip("/") or f"{root}/{cat}"
        print(f"  {tag}{t['name'][:70]}\n      {t['save_path']} -> {dest}")
        if not dry:
            qbt("torrents/setLocation", {"hashes": t["hash"], "location": dest})
        moved += 1
    print(f"  relocated: {moved}")


# ------------------------------------------------------------------- phase 4
def tag_routing(app, meta, dry, tag):
    print(f"\n=== {app}: tag-route each library to its own download client")
    port, key = meta["port"], api_key(meta["cfg"])
    routes = meta["routes"]

    # tags, created on demand
    existing = {t["label"]: t["id"] for t in call(port, key, "tag")}
    ids = {}
    for label, _client in routes.values():
        if label in existing:
            ids[label] = existing[label]
            continue
        print(f"  {tag}+ tag {label!r}")
        ids[label] = 0 if dry else call(port, key, "tag", "POST", {"label": label})["id"]
    routing_tags = set(ids.values()) - {0}

    # one tag per download client
    clients = {c["name"]: c for c in call(port, key, "downloadclient")}
    for label, cname in routes.values():
        c = clients.get(cname)
        if not c:
            print(f"  ! no download client named {cname!r} — skipped")
            continue
        want = [ids[label]]
        if c["tags"] == want:
            continue
        print(f"  {tag}client {cname} tags {c['tags']} -> {want}")
        if not dry:
            c["tags"] = want
            call(port, key, f"downloadclient/{c['id']}", "PUT", c)

    # one tag per item, decided by which root folder it lives in
    items = call(port, key, meta["item"])
    for root, (label, _c) in sorted(routes.items()):
        want = ids[label]
        mine = [i for i in items if i.get("rootFolderPath") == root]
        add = [i["id"] for i in mine if want not in i["tags"]]
        strip = [i["id"] for i in mine if routing_tags & (set(i["tags"]) - {want})]
        if add:
            print(f"  {tag}{root}: +{label} on {len(add)} {meta['item']}")
            if not dry:
                call(port, key, meta["editor"], "PUT", {meta["ids"]: add, "tags": [want], "applyTags": "add"})
        if strip:
            print(f"  {tag}{root}: strip other routing tags from {len(strip)}")
            if not dry:
                call(
                    port,
                    key,
                    meta["editor"],
                    "PUT",
                    {meta["ids"]: strip, "tags": sorted(routing_tags - {want}), "applyTags": "remove"},
                )
        if not add and not strip:
            print(f"  {root}: {len(mine)} {meta['item']} already tagged {label}")

    orphan = [i for i in items if i.get("rootFolderPath") not in routes]
    for i in orphan:
        print(f"  ! {i['title']} sits outside every known root: {i.get('rootFolderPath')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--skip-relocate", action="store_true", help="leave torrents already sitting in the download root")
    args = ap.parse_args()
    dry = args.dry_run
    tag = "[dry-run] " if dry else ""

    fix_qbt_preferences(dry, tag)
    blank_windows_category_paths(dry, tag)
    if not args.skip_relocate:
        relocate_stranded(dry, tag)
    for app, meta in ARR.items():
        tag_routing(app, meta, dry, tag)


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Wire Sonarr + Radarr to qBittorrent (through Gluetun) with per-library categories,
and hand naming authority to jellyFix by disabling the *arr renamers.

Idempotent: re-running updates existing entries instead of duplicating them.
"""

import json
import re
import subprocess
import urllib.error
import urllib.request

from homelab_jellyfix import stack

QBT_HOST, QBT_PORT = "gluetun", 8080  # qbittorrent shares gluetun's netns
DOCKER_SUBNETS = "172.16.0.0/12,127.0.0.1/32"

ARR = {
    "sonarr": {
        "port": stack.port("sonarr"),
        "cfg": stack.cfg("sonarr"),
        "clients": [
            ("qbittorrent-tv", "tv-sonarr"),
            ("qbittorrent-anime", "anime-sonarr"),
            ("qbittorrent-animation", "animation-sonarr"),
        ],
    },
    "radarr": {
        "port": stack.port("radarr"),
        "cfg": stack.cfg("radarr"),
        "clients": [
            ("qbittorrent-movies", "radarr"),
            ("qbittorrent-anime", "anime-radarr"),
            ("qbittorrent-animated", "animated-radarr"),
        ],
    },
}
CATEGORIES = ["tv-sonarr", "anime-sonarr", "animation-sonarr", "radarr", "anime-radarr", "animated-radarr"]


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
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:400]}")


def qbt(path, data=None):
    """Call the qBittorrent API from inside its own container (localhost auth bypass)."""
    cmd = ["docker", "exec", stack.QBT_CONTAINER, "curl", "-s", "-m", "15", f"{stack.QBT_INTERNAL}/api/v2/{path}"]
    if data:
        for k, v in data.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def step(msg):
    print(f"\n=== {msg}")


def main():
    # ---------------------------------------------------------- qBittorrent
    step("qBittorrent: allow docker subnets to bypass WebUI auth")
    prefs = {"bypass_auth_subnet_whitelist_enabled": True, "bypass_auth_subnet_whitelist": DOCKER_SUBNETS}
    print("  ", qbt("app/setPreferences", {"json": json.dumps(prefs)}) or "ok")

    step("qBittorrent: ensure categories exist")
    existing = json.loads(qbt("torrents/categories") or "{}")
    for c in CATEGORIES:
        if c in existing:
            print(f"   = {c} (exists)")
        else:
            qbt("torrents/createCategory", {"category": c, "savePath": f"/downloads/{c}"})
            print(f"   + {c}")

    # ---------------------------------------------------------- Sonarr/Radarr
    for app, meta in ARR.items():
        key, port = api_key(meta["cfg"]), meta["port"]
        step(f"{app}: download clients")
        current = {c["name"]: c for c in call(port, key, "downloadclient")}
        schema = next(s for s in call(port, key, "downloadclient/schema") if s["implementation"] == "QBittorrent")

        for idx, (name, category) in enumerate(meta["clients"]):
            fields = {f["name"]: f.get("value") for f in schema["fields"]}
            fields.update(
                {
                    "host": QBT_HOST,
                    "port": QBT_PORT,
                    "useSsl": False,
                    "urlBase": "",
                    "username": "",
                    "password": "",
                    "movieCategory" if app == "radarr" else "tvCategory": category,
                    "initialState": 0,
                    "recentTvPriority": 0,
                    "olderTvPriority": 0,
                }
            )
            body = {
                "enable": True,
                "protocol": "torrent",
                "priority": idx + 1,
                "removeCompletedDownloads": True,
                "removeFailedDownloads": True,
                "name": name,
                "implementation": "QBittorrent",
                "implementationName": "qBittorrent",
                "configContract": schema["configContract"],
                "infoLink": schema.get("infoLink", ""),
                # Keep whatever tags are already on the client. ops/fix_download_routing.py
                # tags each client so a series/movie routes to its own library category;
                # clobbering them here would silently funnel everything back into
                # tv-sonarr / radarr the next time this script runs.
                "tags": current.get(name, {}).get("tags", []),
                "fields": [{"name": k, "value": v} for k, v in fields.items()],
            }
            if name in current:
                body["id"] = current[name]["id"]
                call(port, key, f"downloadclient/{body['id']}", "PUT", body)
                print(f"   ~ updated {name:<22} category={category}")
            else:
                call(port, key, "downloadclient", "POST", body)
                print(f"   + created {name:<22} category={category}")

        step(f"{app}: hand naming to jellyFix (disable built-in renamer)")
        nc = call(port, key, "config/naming")
        nc["renameEpisodes" if app == "sonarr" else "renameMovies"] = False
        call(port, key, f"config/naming/{nc['id']}", "PUT", nc)
        print(f"   renaming disabled ({'renameEpisodes' if app == 'sonarr' else 'renameMovies'}=False)")

    # ---------------------------------------------------------- verify
    step("verification")
    for app, meta in ARR.items():
        key, port = api_key(meta["cfg"]), meta["port"]
        for c in call(port, key, "downloadclient"):
            t = call(port, key, "downloadclient/test", "POST", c)
            print(f"   {app}/{c['name']:<22} test={'OK' if t is None else t}")
    print("\ndone.")


if __name__ == "__main__":
    main()

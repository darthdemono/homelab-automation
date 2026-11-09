# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Expose Jackett's working indexers to Sonarr/Radarr by registering them in Prowlarr.

Sonarr and Radarr take every indexer from Prowlarr; nothing in the stack talks to
Jackett. Jackett nevertheless has trackers configured that Prowlarr does not, so
those results were invisible to the *arrs.

Each one is added to Prowlarr as a Generic Torznab feed pointing back at Jackett,
rather than reconfigured natively. That keeps the tracker credentials in Jackett,
where they already are, instead of copying them into a second application.

Indexers Prowlarr already has natively are skipped — a native definition is
better than a proxied one, and duplicates just mean two hits per search.
An indexer whose Jackett search currently errors is skipped and reported.

Idempotent: an already-registered feed is left alone.
"""

import argparse
import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from homelab_jellyfix import stack

JACKETT_CFG = stack.CONFIG_FILES.get("jackett") or str(
    Path(stack.DOCKER_DIR) / "jackett/config/Jackett/ServerConfig.json"
)
PROWLARR_CFG = stack.cfg("prowlarr")
JACKETT_FROM_PROWLARR = "http://jackett:9117"  # both sit on media_net
SUFFIX = " (Jackett)"


def jackett_key():
    return json.load(open(JACKETT_CFG))["APIKey"]


def prowlarr(path, method="GET", body=None):
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(PROWLARR_CFG).read()).group(1)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{stack.url('prowlarr')}/api/v1/{path}",
        data=data,
        method=method,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def jackett_indexers():
    """Configured Jackett indexers, with whether a search currently works.

    The admin endpoint needs a dashboard session, so the definitions are read off
    disk and probed one by one through the torznab API, which the key does open.
    """
    import os

    d = os.path.dirname(JACKETT_CFG) + "/Indexers"
    ids = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
    key = jackett_key()
    out = []
    for i in ids:
        url = f"{stack.url('jackett')}/api/v2.0/indexers/{i}/results/torznab/api?apikey={key}&t=search&q=test"
        r = subprocess.run(["curl", "-s", "-m", "90", url], capture_output=True, text=True)
        body = r.stdout
        err = re.search(r'<error code="\d+" description="([^"]{0,120})', body)
        out.append(
            {
                "id": i,
                "ok": err is None,
                "error": (err.group(1).replace("&quot;", '"') if err else None),
                "items": body.count("<item>"),
            }
        )
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    existing = prowlarr("indexer")
    have = {i["name"].replace(SUFFIX, "").strip().lower() for i in existing}

    schema = next(
        s
        for s in prowlarr("indexer/schema")
        if s.get("implementation") == "Torznab" and s.get("name") == "Generic Torznab"
    )

    print("=== Jackett indexers")
    plan, skip = [], []
    for ix in jackett_indexers():
        label = ix["id"]
        if not ix["ok"]:
            skip.append((label, f"Jackett search fails: {ix['error']}"))
            continue
        # 1337x -> "1337x", nyaasi -> "nyaa.si": compare loosely
        norm = re.sub(r"[^a-z0-9]", "", label)
        if any(
            re.sub(r"[^a-z0-9]", "", h).startswith(norm) or norm.startswith(re.sub(r"[^a-z0-9]", "", h))
            for h in have
            if h
        ):
            skip.append((label, "Prowlarr already has this indexer natively"))
            continue
        plan.append(ix)

    for ix in plan:
        print(f"  + {ix['id']:16} {ix['items']} results on a test search")
    for label, why in skip:
        print(f"  - {label:16} {why}")

    if not plan:
        print("\nnothing to add")
        return
    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    key = jackett_key()
    added = 0
    for ix in plan:
        body = json.loads(json.dumps(schema))  # deep copy per indexer
        vals = {
            "baseUrl": f"{JACKETT_FROM_PROWLARR}/api/v2.0/indexers/{ix['id']}/results/torznab/",
            "apiPath": "/api",
            "apiKey": key,
        }
        for f in body["fields"]:
            if f["name"] in vals:
                f["value"] = vals[f["name"]]
        body.update({"name": f"{ix['id']}{SUFFIX}", "enable": True, "appProfileId": 1, "priority": 25, "tags": []})
        body.pop("presets", None)
        prowlarr("indexer", "POST", body)
        added += 1
        print(f"  added {body['name']}")

    print(f"\nadded {added}. Prowlarr syncs them to Sonarr/Radarr on its next pass.")


if __name__ == "__main__":
    main()

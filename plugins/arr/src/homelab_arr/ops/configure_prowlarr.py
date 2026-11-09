# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Populate Prowlarr: FlareSolverr proxy, public indexers, and app sync to
Sonarr/Radarr. Idempotent — re-running updates rather than duplicating.

Sonarr/Radarr get their indexers *from* Prowlarr, so the two Sonarr health
warnings ("No indexers available with RSS sync / Automatic Search") are fixed by
syncing apps here rather than by adding indexers to Sonarr directly.
"""

import json
import re
import urllib.error
import urllib.request

from homelab_jellyfix import stack

PROWLARR = stack.url("prowlarr")
PROW_CFG = stack.cfg("prowlarr")

APPS = [
    ("Sonarr", "Sonarr", "http://sonarr:8989", stack.cfg("sonarr")),
    ("Radarr", "Radarr", "http://radarr:7878", stack.cfg("radarr")),
]
# public trackers only — no credentials required
INDEXERS = ["1337x", "The Pirate Bay", "YTS", "EZTV", "LimeTorrents", "Nyaa.si", "SubsPlease"]
PROXY_NAME, PROXY_URL = "byparr", "http://byparr:8191/"


def api_key(path):
    m = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(path).read())
    if not m:
        raise SystemExit(f"no ApiKey in {path}")
    return m.group(1)


PK = api_key(PROW_CFG)


def call(path, method="GET", body=None, base=PROWLARR, key=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}/api/v1/{path}",
        data=data,
        method=method,
        headers={"X-Api-Key": key or PK, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return json.loads(t) if t.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:500]}")


def set_field(fields, name, value):
    for f in fields:
        if f["name"] == name:
            f["value"] = value
            return True
    fields.append({"name": name, "value": value})
    return False


def main():
    print("=== proxy (FlareSolverr-compatible)")
    proxies = call("indexerproxy")
    if any(p["name"] == PROXY_NAME for p in proxies):
        print(f"   = {PROXY_NAME} exists")
        tag_id = None
    else:
        schema = next(s for s in call("indexerproxy/schema") if s["implementation"] == "FlareSolverr")
        set_field(schema["fields"], "host", PROXY_URL)
        schema.update(name=PROXY_NAME, tags=[])
        call("indexerproxy", "POST", schema)
        print(f"   + {PROXY_NAME} -> {PROXY_URL}")

    print("\n=== indexers")
    existing = {i["name"]: i for i in call("indexer")}
    defs = {d["name"]: d for d in call("indexer/schema")}
    for name in INDEXERS:
        d = defs.get(name)
        if d is None:
            print(f"   ! {name}: no definition in this Prowlarr build")
            continue
        body = dict(d)
        body.update(name=name, enable=True, tags=[], appProfileId=1, priority=25)
        if name in existing:
            body["id"] = existing[name]["id"]
            call(f"indexer/{body['id']}", "PUT", body)
            print(f"   ~ {name}")
        else:
            try:
                call("indexer", "POST", body)
                print(f"   + {name}")
            except SystemExit as e:
                print(f"   ! {name}: {str(e)[:160]}")

    print("\n=== applications (sync indexers into Sonarr/Radarr)")
    apps = {a["name"]: a for a in call("applications")}
    schemas = {s["implementation"]: s for s in call("applications/schema")}
    for name, impl, url, cfg in APPS:
        s = dict(schemas[impl])
        set_field(s["fields"], "prowlarrUrl", "http://prowlarr:9696")
        set_field(s["fields"], "baseUrl", url)
        set_field(s["fields"], "apiKey", api_key(cfg))
        s.update(name=name, syncLevel="fullSync", tags=[])
        if name in apps:
            s["id"] = apps[name]["id"]
            call(f"applications/{s['id']}", "PUT", s)
            print(f"   ~ {name} -> {url}")
        else:
            call("applications", "POST", s)
            print(f"   + {name} -> {url}")

    print("\n=== sync")
    call("command", "POST", {"name": "ApplicationIndexerSync"})
    print("   ApplicationIndexerSync queued")
    print(f"\nprowlarr now has {len(call('indexer'))} indexers, {len(call('applications'))} apps")


if __name__ == "__main__":
    main()

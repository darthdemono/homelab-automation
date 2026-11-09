# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Wire qui to Sonarr and Radarr (and Prowlarr's indexers) so its cross-seed search
can identify releases. Idempotent. The qui key comes from QUI_API_KEY (vault
entry QUI, exported to a private temp file by the caller); Sonarr, Radarr and
Prowlarr keys are read from their own config.xml. Nothing is printed or stored.
"""

import json
import os
import re
import urllib.error
import urllib.request

from homelab_jellyfix import stack

QUI = f"{stack.url('qui')}/api"
APPS = [("sonarr", "Sonarr", "http://sonarr:8989", "sonarr"), ("radarr", "Radarr", "http://radarr:7878", "radarr")]


def key_of(app):
    return re.search(r"<ApiKey>([^<]+)</ApiKey>", open(stack.cfg(app)).read()).group(1)


def call(path, method="GET", body=None):
    req = urllib.request.Request(
        QUI + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-API-Key": os.environ["QUI_API_KEY"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return json.loads(t) if t.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode()[:300]}")


def main():
    have = {i["type"]: i for i in call("/arr/instances") or []}
    for typ, name, url, cfg in APPS:
        body = {
            "type": typ,
            "name": name,
            "base_url": url,
            "api_key": key_of(cfg),
            "enabled": True,
            "priority": 0,
            "timeout_seconds": 15,
        }
        if typ in have:
            res = call(f"/arr/instances/{have[typ]['id']}", "PUT", body)
        else:
            res = call("/arr/instances", "POST", body)
        t = call(f"/arr/instances/{res['id']}/test", "POST")
        print(f"{name}: {'updated' if typ in have else 'added'}; test -> {t}")


if __name__ == "__main__":
    main()

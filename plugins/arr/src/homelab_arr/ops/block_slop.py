# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Stop Sonarr and Radarr grabbing slop and duplicates.

Why they did it: completed-download handling is off (jellyFix files the
downloads), so an episode or movie that was already grabbed still looks
"missing" and the next RSS sync grabs a second release of it. Nothing rejected
AI-enhanced, upscaled, open-matte or camera rips either.

  1. A "Slop" custom format (any one term matches) scored -10000 in every
     quality profile, with minFormatScore 0, so those releases are rejected.
  2. Everything already grabbed (per history) is unmonitored, so it cannot be
     grabbed again. Unmonitored entries stay in the app; import_watch.py still
     needs the series flag, so series are never unmonitored here.
  3. --unmonitor-season SERIES SEASON for a season grabbed by mistake.

Idempotent. Dry run unless --apply.
"""

import argparse
import json
import re
import urllib.error
import urllib.request

from homelab_jellyfix import stack

ARR = {
    "sonarr": {"port": stack.port("sonarr"), "cfg": stack.cfg("sonarr")},
    "radarr": {"port": stack.port("radarr"), "cfg": stack.cfg("radarr")},
}
CF_NAME = "Slop"
SCORE = -10000
SLOP = [
    r"\bai[ ._-]?(enhanced|upscal\w*|remaster\w*|restor\w*|generated|colou?ri[sz]ed)\b",
    r"\b(upscal(e|ed|ing)|up-?res|topaz|esrgan|autohdr|rtx[ ._-]?hdr)\b",
    r"\bopen[ ._-]?matte\b",
    r"\b(fan[ ._-]?(edit|cut|made)|handmade|re-?edit)\b",
    r"\b(30|48|50|60)[ ._-]?fps\b",
    r"\b(hd)?cam(rip)?\b|\b(hd)?ts\b|\btele(sync|cine)\b|\bscr(eener)?\b|\bpre[ ._-]?dvd\b",
]


def call(app, path, method="GET", body=None):
    m = ARR[app]
    key = re.search(r"<ApiKey>([^<]+)</ApiKey>", open(m["cfg"]).read()).group(1)
    req = urllib.request.Request(
        f"{stack.url_for_port(m['port'])}/api/v3/{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{app} {method} {path} -> {e.code}: {e.read().decode()[:300]}")


def ensure_format(app, apply):
    spec = [
        {
            "name": f"t{i}",
            "implementation": "ReleaseTitleSpecification",
            "negate": False,
            "required": False,
            "fields": [{"name": "value", "value": rx}],
        }
        for i, rx in enumerate(SLOP)
    ]
    cf = next((c for c in call(app, "customformat") if c["name"] == CF_NAME), None)
    body = {"name": CF_NAME, "includeCustomFormatWhenRenaming": False, "specifications": spec}
    if apply:
        cf = (
            call(app, f"customformat/{cf['id']}", "PUT", {**body, "id": cf["id"]})
            if cf
            else call(app, "customformat", "POST", body)
        )
    print(f"  {app}: custom format {CF_NAME} {'written' if apply else 'would be written'}")
    for p in call(app, "qualityprofile"):
        if not apply or not cf:
            continue
        items = [i for i in p["formatItems"] if i["format"] != cf["id"]]
        items.append({"format": cf["id"], "name": CF_NAME, "score": SCORE})
        p["formatItems"], p["minFormatScore"] = items, 0
        call(app, f"qualityprofile/{p['id']}", "PUT", p)
    print(f"  {app}: score {SCORE}, minFormatScore 0 on all profiles")


def unmonitor_grabbed(app, apply):
    page = call(app, "history?pageSize=1000&eventType=1&sortKey=date&sortDirection=descending")
    if app == "radarr":
        ids = {r["movieId"] for r in page["records"]}
        todo = [m for m in call(app, "movie") if m["id"] in ids and m["monitored"]]
        for m in todo:
            print(f"  radarr: unmonitor {m['title']} ({m['year']})")
            if apply:
                call(app, f"movie/{m['id']}", "PUT", {**m, "monitored": False})
    else:
        ids = {r["episodeId"] for r in page["records"]}
        eps = []
        for sid in {r["seriesId"] for r in page["records"]}:
            eps += [e for e in call(app, f"episode?seriesId={sid}") if e["id"] in ids and e["monitored"]]
        print(f"  sonarr: unmonitor {len(eps)} grabbed episodes")
        if apply and eps:
            call(app, "episode/monitor", "PUT", {"episodeIds": [e["id"] for e in eps], "monitored": False})


def unmonitor_season(series_id, season, apply):
    s = call("sonarr", f"series/{series_id}")
    for x in s["seasons"]:
        if x["seasonNumber"] == season:
            x["monitored"] = False
    eps = [
        e["id"]
        for e in call("sonarr", f"episode?seriesId={series_id}")
        if e["seasonNumber"] == season and e["monitored"]
    ]
    print(f"  sonarr: {s['title']} S{season:02d} unmonitored ({len(eps)} episodes)")
    if apply:
        call("sonarr", f"series/{series_id}", "PUT", s)
        if eps:
            call("sonarr", "episode/monitor", "PUT", {"episodeIds": eps, "monitored": False})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--unmonitor-season", nargs=2, type=int, metavar=("SERIES_ID", "SEASON"))
    a = ap.parse_args()
    for app in ARR:
        ensure_format(app, a.apply)
        unmonitor_grabbed(app, a.apply)
    if a.unmonitor_season:
        unmonitor_season(*a.unmonitor_season, a.apply)

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Build MusicBrainz "add release" seeding forms from library tags.

    python -m homelab_musicfix.run mbseed --artist "Ex 8" --artist Majhpoth --artist Umbra

Writes reports/mb_seeds/index.html: one form per album, each posting to
musicbrainz.org/release/add with the fields pre-filled. Nothing is submitted
for you; open the page, check the form MusicBrainz shows, then submit there.
ISRCs cannot be seeded, so they are also listed per album for a later ISRC
submission (Picard, or the track's ISRC edit page).
"""

import argparse
import html
import json
import re
from collections import defaultdict

import mutagen

from .settings import REPORTS

OUT = REPORTS / "mb_seeds"


def first(tags, key):
    v = tags.get(key) or tags.get(key.upper()) or []
    return str(v[0]) if v else ""


def join_credit(names):
    """[(name, join_phrase)] with the library's 'X, Y and Z' rule."""
    out = []
    for i, n in enumerate(names):
        jp = "" if i == len(names) - 1 else (" and " if i == len(names) - 2 else ", ")
        out.append((n, jp))
    return out


def release_type(n):
    return "single" if n <= 3 else "ep" if n <= 6 else "album"


def album_fields(tracks):
    t0 = tracks[0]["tags"]
    f = {
        "name": first(t0, "album"),
        "status": "official",
        "packaging": "none",
        "type": release_type(len(tracks)),
        "mediums.0.format": "Digital Media",
    }
    albumartist = [str(x) for x in (t0.get("albumartist") or t0.get("artist") or [])]
    for i, (n, jp) in enumerate(join_credit(albumartist)):
        f[f"artist_credit.names.{i}.artist.name"] = n
        f[f"artist_credit.names.{i}.name"] = n
        f[f"artist_credit.names.{i}.join_phrase"] = jp
    date = first(t0, "date") or first(t0, "releasedate")
    m = re.match(r"(\d{4})(?:-(\d\d))?(?:-(\d\d))?", date)
    if m:
        for k, v in zip(("year", "month", "day"), m.groups()):
            if v:
                f[f"events.0.date.{k}"] = str(int(v))
        f["events.0.country"] = "XW"
    if first(t0, "label"):
        f["labels.0.name"] = first(t0, "label")
        if first(t0, "catalognumber"):
            f["labels.0.catalog_number"] = first(t0, "catalognumber")
    code = first(t0, "upc") or first(t0, "barcode")
    if code:
        f["barcode"] = code
    for i, t in enumerate(tracks):
        tg = t["tags"]
        f[f"mediums.0.track.{i}.name"] = first(tg, "title")
        f[f"mediums.0.track.{i}.number"] = first(tg, "tracknumber").split("/")[0] or str(i + 1)
        f[f"mediums.0.track.{i}.length"] = str(int(float(t["quality"]["duration"]) * 1000))
        ta = [str(x) for x in tg.get("artist", [])]
        if ta != albumartist:
            for j, (n, jp) in enumerate(join_credit(ta)):
                f[f"mediums.0.track.{i}.artist_credit.names.{j}.artist.name"] = n
                f[f"mediums.0.track.{i}.artist_credit.names.{j}.name"] = n
                f[f"mediums.0.track.{i}.artist_credit.names.{j}.join_phrase"] = jp
    f["edit_note"] = "Seeded from the tagged files of a personal library; track URLs and ISRCs to follow."
    return f


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--artist", action="append", required=True)
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    a = ap.parse_args(argv)
    wanted = [x.casefold() for x in a.artist]
    albums = defaultdict(list)
    for line in open(a.inventory, encoding="utf-8"):
        r = json.loads(line)
        aa = first(r["tags"], "albumartist").casefold()
        if any(aa == w or aa.startswith(w) for w in wanted):
            mf = mutagen.File(r["abspath"])  # inventory lacks comment/label/upc
            r["tags"] = {k.lower(): [str(x) for x in v] for k, v in mf.tags.items()}
            albums[(first(r["tags"], "albumartist"), first(r["tags"], "album"))].append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    cards, isrcs = [], {}
    for (ar, al), tr in sorted(albums.items()):
        tr.sort(key=lambda r: int((re.findall(r"\d+", first(r["tags"], "tracknumber")) or ["0"])[0]))
        fields = album_fields(tr)
        inputs = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in fields.items()
        )
        rows = "".join(
            f"<li>{html.escape(first(t['tags'], 'title'))} <code>{html.escape(first(t['tags'], 'isrc'))}</code></li>"
            for t in tr
        )
        cards.append(
            f'<form method="post" action="https://musicbrainz.org/release/add" target="_blank">'
            f"{inputs}<h3>{html.escape(ar)} - {html.escape(al)} "
            f"<small>({fields['type']}, {len(tr)} tracks, {fields.get('events.0.date.year', 'no date')})</small></h3>"
            f"<ol>{rows}</ol><button>Open in MusicBrainz release editor</button></form>"
        )
        isrcs[f"{ar} - {al}"] = {first(t["tags"], "title"): first(t["tags"], "isrc") for t in tr}
    page = (
        "<!doctype html><meta charset=utf-8><title>MusicBrainz seeds</title>"
        "<style>body{font:15px sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem}"
        "form{border:1px solid #8886;border-radius:8px;padding:0 1rem 1rem;margin:1rem 0}</style>"
        "<h1>MusicBrainz seeds</h1><p>Each button opens the release editor pre-filled. "
        "Check for an existing release first (the editor warns), then submit there.</p>" + "".join(cards)
    )
    (OUT / "index.html").write_text(page, encoding="utf-8")
    (OUT / "isrcs.json").write_text(json.dumps(isrcs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[mbseed] {len(albums)} albums -> {OUT / 'index.html'}")


if __name__ == "__main__":
    main()

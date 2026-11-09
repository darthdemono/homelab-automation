# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Import a CSV track list into a Navidrome playlist.

Merged from navidrome-csv-playlist-importer-with-fuzzy-matching so it shares
musicFix's notion of what an artist and a title are, rather than keeping a
second, subtly different set of cleaning rules.

Two things change as a result of that sharing:

* Matching runs against the local inventory (reports/inventory.jsonl) instead of
  a Subsonic search call per row. It is offline, far faster, and sees the same
  tags the rest of the tooling does.
* Titles are normalised with plan.norm, which keeps version qualifiers. The
  original importer stripped every bracketed suffix, so a CSV row for "Song"
  could match "Song (Live)" -- the wrong recording.

Match order: ISRC, then MusicBrainz track id, then fuzzy on artist/title/album.
Song ids come from Navidrome's own database (read-only) so a matched file maps
to the exact track; the playlist itself is created through the Subsonic API.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sqlite3
import string
import sys
from collections import defaultdict

import requests
from rapidfuzz import fuzz

from .settings import MUSIC_ROOT, REPORTS
from .plan import load, norm
from .tags import join_artists, split_artists

DEFAULT_DB = os.environ.get("NAVIDROME_DB", "navidrome.db")


# --------------------------------------------------------------------------
# Subsonic API
# --------------------------------------------------------------------------
def auth_params(user, password, client):
    salt = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    token = hashlib.md5((password + salt).encode("utf-8")).hexdigest()
    return {"u": user, "t": token, "s": salt, "v": "1.16.1", "c": client, "f": "json"}


class Navidrome:
    def __init__(self, url, user, password, client="musicFix"):
        self.url = url.rstrip("/")
        self.user, self.password, self.client = user, password, client

    def call(self, endpoint, params=None, method="get"):
        p = dict(params or {})
        p.update(auth_params(self.user, self.password, self.client))
        fn = requests.post if method == "post" else requests.get
        resp = fn(f"{self.url}/rest/{endpoint}", params=p, timeout=30)
        resp.raise_for_status()
        body = resp.json().get("subsonic-response", {})
        if body.get("status") != "ok":
            raise RuntimeError(f"{endpoint}: {body.get('error', {}).get('message', body)}")
        return body

    def ping(self):
        return self.call("ping")

    def playlists(self):
        return self.call("getPlaylists").get("playlists", {}).get("playlist", [])

    def create_playlist(self, name, song_ids):
        # createPlaylist takes repeated songId params.
        return self.call("createPlaylist", {"name": name, "songId": song_ids})

    def update_playlist(self, playlist_id, song_ids):
        return self.call("updatePlaylist", {"playlistId": playlist_id, "songIdToAdd": song_ids})

    def rename_playlist(self, playlist_id, name):
        """Rename in place. The track list and cover are untouched -- the same
        playlist id keeps them, so this is not a delete-and-recreate."""
        return self.call("updatePlaylist", {"playlistId": playlist_id, "name": name})

    def find_playlist(self, name):
        """Exact match first, then case-insensitive. Emoji names are compared as
        the raw strings they are: ZWJ sequences must not be normalised away, or
        `👁️‍🗨️` stops matching itself."""
        pls = self.playlists()
        for p in pls:
            if p.get("name") == name:
                return p
        low = name.lower()
        hits = [p for p in pls if (p.get("name") or "").lower() == low]
        return hits[0] if len(hits) == 1 else None

    # -- native API (JWT) -- only playlist cover upload lives outside Subsonic --
    def login(self):
        r = requests.post(f"{self.url}/auth/login", json={"username": self.user, "password": self.password}, timeout=15)
        r.raise_for_status()
        return r.json()["token"]

    def upload_playlist_image(self, playlist_id, image_path, token):
        """POST an image to a playlist. The form field must be named 'image'."""
        with open(image_path, "rb") as fh:
            r = requests.post(
                f"{self.url}/api/playlist/{playlist_id}/image",
                headers={"x-nd-authorization": f"Bearer {token}"},
                files={"image": (os.path.basename(image_path), fh, "image/jpeg")},
                timeout=30,
            )
        return r.status_code == 200, (r.text or "")[:120]


# --------------------------------------------------------------------------
# Local index
# --------------------------------------------------------------------------
def build_index(records):
    """Index the inventory by ISRC, MBID and normalised artist/title."""
    idx = {"isrc": {}, "mbid": {}, "fuzzy": defaultdict(list)}
    for r in records:
        if r.get("error"):
            continue
        t = r.get("tags") or {}
        title = (t.get("title") or [""])[0]
        album = (t.get("album") or [""])[0]
        artists = split_artists(t.get("artist"))
        entry = {
            "path": r["path"],
            "title": title,
            "album": album,
            "artist": join_artists(artists),
            "n_title": norm(title),
            "n_album": norm(album),
            "n_artist": norm(join_artists(artists)),
        }
        for isrc in t.get("isrc") or []:
            idx["isrc"].setdefault(isrc.strip().upper(), entry)
        for mb in t.get("musicbrainz_trackid") or []:
            idx["mbid"].setdefault(mb.strip().lower(), entry)
        # Bucket by first token of the title to keep fuzzy comparisons cheap.
        key = entry["n_title"].split(" ")[0] if entry["n_title"] else ""
        idx["fuzzy"][key].append(entry)
        idx["fuzzy"][""].append(entry)
    return idx


def build_index_from_db(db_path):
    """Index Navidrome's own database, so a match carries the song id directly.

    This is the most reliable source once the library is indexed: paths and ids
    are exactly what Navidrome serves, and ISRC lives in the media_file.tags
    JSON blob. No separate path->id step is needed.
    """
    idx = {"isrc": {}, "mbid": {}, "fuzzy": defaultdict(list)}
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "select id, title, artist, album, tags, mbz_recording_id from media_file where missing = 0"
        ).fetchall()
    finally:
        con.close()
    for song_id, title, artist, album, tags_json, mbz in rows:
        entry = {
            "id": song_id,
            "title": title or "",
            "album": album or "",
            "artist": artist or "",
            "n_title": norm(title or ""),
            "n_album": norm(album or ""),
            "n_artist": norm(artist or ""),
        }
        for isrc in _isrc_from_tags(tags_json):
            idx["isrc"].setdefault(isrc.upper(), entry)
        if mbz:
            idx["mbid"].setdefault(mbz.strip().lower(), entry)
        key = entry["n_title"].split(" ")[0] if entry["n_title"] else ""
        idx["fuzzy"][key].append(entry)
        idx["fuzzy"][""].append(entry)
    return idx


def _isrc_from_tags(tags_json):
    """Pull ISRC values out of Navidrome's media_file.tags JSON."""
    if not tags_json:
        return []
    try:
        data = json.loads(tags_json)
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for key, vals in data.items() if isinstance(data, dict) else []:
        if key.lower() != "isrc":
            continue
        # Navidrome stores each value as {"id": ..., "value": "USxxx..."}.
        for v in vals if isinstance(vals, list) else [vals]:
            s = v.get("value") if isinstance(v, dict) else v
            if s and str(s).strip():
                out.append(str(s).strip())
    return out


def match_row(row, idx, thresholds):
    """Resolve one CSV row to an index entry, or None."""
    for isrc in _split_isrc(row.get("ISRC") or row.get("isrc") or ""):
        hit = idx["isrc"].get(isrc.upper())
        if hit:
            return hit, "isrc", 100.0

    mb = (row.get("MusicBrainz Id") or row.get("mbid") or "").strip().lower()
    if mb and mb in idx["mbid"]:
        return idx["mbid"][mb], "mbid", 100.0

    title = norm(row.get("Track") or row.get("Title") or "")
    artist = norm(row.get("Artist") or "")
    album = norm(row.get("Album") or "")
    if not title:
        return None, "no-title", 0.0

    candidates = idx["fuzzy"].get(title.split(" ")[0]) or idx["fuzzy"][""]
    best, best_score = None, 0.0
    for e in candidates:
        t_score = fuzz.ratio(title, e["n_title"])
        if t_score < thresholds["title"]:
            continue
        a_score = fuzz.partial_ratio(artist, e["n_artist"]) if artist else 100
        if a_score < thresholds["artist"]:
            continue
        al_score = fuzz.ratio(album, e["n_album"]) if album else 100
        # Album is the weakest signal (compilations, re-releases), so it
        # contributes to the score but does not by itself reject a match.
        score = t_score * 0.55 + a_score * 0.35 + al_score * 0.10
        if score > best_score:
            best, best_score = e, score
    if best and best_score >= thresholds["overall"]:
        return best, "fuzzy", best_score
    return None, "no-match", best_score


def _split_isrc(field):
    out = []
    for part in str(field).replace(";", ",").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def path_to_song_ids(db_path, paths, music_root):
    """Map library-relative paths to Navidrome song ids, read-only.

    Going through the database rather than the search API gives an exact
    mapping instead of a second round of fuzzy matching.
    """
    if not os.path.exists(db_path):
        return {}
    uri = f"file:{db_path}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute("select id, path from media_file where missing = 0").fetchall()
    except sqlite3.Error as exc:
        print(f"[playlist] cannot read navidrome db: {exc}", file=sys.stderr)
        return {}
    finally:
        con.close()
    by_path = {}
    for song_id, p in rows:
        by_path[p] = song_id
        by_path[os.path.normpath(p)] = song_id
    out = {}
    for p in paths:
        out[p] = by_path.get(p) or by_path.get(os.path.normpath(p))
    return out


# Column aliases: Spotify exports use "Track name"/"Artist name".
_COL_ALIASES = {
    "track name": "Track",
    "track": "Track",
    "title": "Track",
    "song": "Track",
    "artist name": "Artist",
    "artist": "Artist",
    "artists": "Artist",
    "album": "Album",
    "album name": "Album",
    "isrc": "ISRC",
    "playlist name": "Playlist name",
    "playlist": "Playlist name",
}


def read_csv(path):
    """Read a CSV, normalising header names to the canonical set."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for row in rows:
        norm_row = {}
        for k, v in row.items():
            canon = _COL_ALIASES.get((k or "").strip().lower())
            norm_row[canon or k] = v
        out.append(norm_row)
    return out


def playlist_name_for(rows, path):
    """Prefer the CSV's own 'Playlist name' column; fall back to the filename."""
    for row in rows:
        name = (row.get("Playlist name") or "").strip()
        if name:
            return name
    return os.path.splitext(os.path.basename(path))[0].strip()


def process_csv(path, idx, thresholds):
    """Match one CSV against the index. Returns (name, song_ids, matched, unmatched)."""
    rows = read_csv(path)
    name = playlist_name_for(rows, path)
    song_ids, seen = [], set()
    matched, unmatched = [], []
    by_method = defaultdict(int)
    for row in rows:
        hit, method, score = match_row(row, idx, thresholds)
        if hit:
            by_method[method] += 1
            matched.append((row, hit, method, score))
            if hit["id"] not in seen:
                seen.add(hit["id"])
                song_ids.append(hit["id"])
        else:
            unmatched.append((row, method, score))

    print(
        f"\n[playlist] {os.path.basename(path)} -> '{name}': "
        f"{len(rows)} rows, {len(matched)} matched, {len(unmatched)} unmatched"
    )
    for m, n in sorted(by_method.items()):
        print(f"[playlist]     by {m:6} {n}")
    for row, reason, score in unmatched:
        print(
            f"[playlist]     UNMATCHED {row.get('Artist', '?')} - {row.get('Track', '?')}  ({reason}, best {score:.0f})"
        )
    return name, song_ids, matched, unmatched


IMAGE_COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _cover_key(s):
    # Filenames substitute the reserved '<' with '_'; undo that for matching.
    return s.strip().lower().replace("_", "<")


def match_covers(folder, playlist_names, threshold=90):
    """Match image files to playlist names. Returns (matched, extras, uncovered).

    matched: [(filename, playlist_name, how, score)]. Exact (after '_'->'<')
    first, then a high-threshold fuzzy pass so a descriptive filename like
    "attack on titan ... endings (1-8).jpg" still finds the "endings (1-8)"
    playlist. Each playlist is claimed at most once.
    """
    by_key = {_cover_key(n): n for n in playlist_names}
    covers = [f for f in sorted(os.listdir(folder)) if os.path.splitext(f)[1].lower() in IMAGE_COVER_EXTS]
    matched, extras, used = [], [], set()
    for fn in covers:
        key = _cover_key(os.path.splitext(fn)[0])
        if key in by_key and by_key[key] not in used:
            matched.append((fn, by_key[key], "exact", 100))
            used.add(by_key[key])
            continue
        best, best_pl = 0, None
        for n in playlist_names:
            if n in used:
                continue
            s = max(
                fuzz.ratio(key, _cover_key(n)),
                fuzz.partial_ratio(key, _cover_key(n)),
                fuzz.token_set_ratio(key, _cover_key(n)),
            )
            if s > best:
                best, best_pl = s, n
        if best_pl and best >= threshold:
            matched.append((fn, best_pl, "fuzzy", round(best)))
            used.add(best_pl)
        else:
            extras.append((fn, best_pl, round(best)))
    uncovered = [n for n in playlist_names if n not in used]
    return matched, extras, uncovered


def run_covers(folder, nd, commit):
    """Match every image in `folder` to a playlist and upload it."""
    pls = {p["name"]: p["id"] for p in nd.playlists()}
    matched, extras, uncovered = match_covers(folder, list(pls))
    print(f"[covers] {len(matched)} matched, {len(extras)} extras, {len(uncovered)} playlists without a cover")
    for fn, pl, how, sc in matched:
        print(f"  {fn}  ->  '{pl}'  ({how} {sc})")
    for fn, near, sc in extras:
        print(f"  EXTRA {fn}  (no playlist yet; closest {near!r} @ {sc})")
    if not commit:
        print("[covers] DRY-RUN; re-run with --commit to upload")
        return 0
    token = nd.login()
    ok = fail = 0
    for fn, pl, _how, _sc in matched:
        good, msg = nd.upload_playlist_image(pls[pl], os.path.join(folder, fn), token)
        ok += good
        fail += not good
        if not good:
            print(f"  FAIL '{pl}': {msg}")
    print(f"[covers] {ok} uploaded, {fail} failed")
    return 0


def run_rename(nd, renames, covers, commit):
    """Rename playlists and/or set their covers. Dry-run unless commit."""
    rc = 0
    # In a dry run the renames have not happened, so a cover aimed at a new name
    # would look like a failure. Track what the renames *would* produce and
    # resolve covers against that too, or the dry run reports a false error.
    pending = {}
    for old, new in renames:
        p = nd.find_playlist(old)
        if p is None:
            print(f"[playlist] NOT FOUND {old!r} -- nothing renamed")
            rc = 1
            continue
        if nd.find_playlist(new) is not None and new != old:
            print(f"[playlist] REFUSING {old!r} -> {new!r}: a playlist called {new!r} already exists")
            rc = 1
            continue
        print(
            f"[playlist] {'RENAME' if commit else 'would rename'} "
            f"{p['name']!r} (id {p['id']}, {p.get('songCount')} tracks) -> {new!r}"
        )
        if commit:
            nd.rename_playlist(p["id"], new)
        else:
            pending[new] = p

    for name, image in covers:
        if not os.path.exists(image):
            print(f"[playlist] NOT FOUND image {image}")
            rc = 1
            continue
        p = nd.find_playlist(name) or pending.get(name)
        if p is None:
            print(f"[playlist] NOT FOUND playlist {name!r} for cover")
            rc = 1
            continue
        print(f"[playlist] {'COVER' if commit else 'would set cover'} {p['name']!r} <- {os.path.basename(image)}")
        if commit:
            token = nd.login()
            ok, body = nd.upload_playlist_image(p["id"], image, token)
            print(f"[playlist]   upload {'ok' if ok else 'FAILED: ' + body}")
            if not ok:
                rc = 1
    if not commit:
        print("[playlist] nothing changed; re-run with --commit")
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import CSV track lists into Navidrome playlists.")
    ap.add_argument("path", nargs="?", help="a CSV file, or a directory of CSVs (one playlist each)")
    ap.add_argument(
        "--rename",
        nargs=2,
        metavar=("OLD", "NEW"),
        action="append",
        default=[],
        help="rename an existing playlist. Repeatable.",
    )
    ap.add_argument(
        "--cover",
        nargs=2,
        metavar=("NAME", "IMAGE"),
        action="append",
        default=[],
        help="set one playlist's cover image. Repeatable.",
    )
    ap.add_argument(
        "--covers",
        action="store_true",
        help="instead of importing CSVs, match image files in `path` to "
        "existing playlists by name and upload them as covers",
    )
    ap.add_argument("--name", help="override the playlist name (single-file only)")
    ap.add_argument("--db", default=DEFAULT_DB, help="navidrome.db, read-only, matched against directly")
    ap.add_argument("--url", default=_env("NAVIDROME_URL"))
    ap.add_argument("--user", default=_env("NAVIDROME_USER"))
    ap.add_argument("--password", default=_env("NAVIDROME_TOKEN"))
    ap.add_argument("--title-threshold", type=int, default=78)
    ap.add_argument("--artist-threshold", type=int, default=70)
    ap.add_argument("--overall-threshold", type=int, default=78)
    ap.add_argument(
        "--replace", action="store_true", help="replace an existing playlist of the same name instead of adding to it"
    )
    ap.add_argument("--commit", action="store_true", help="actually create/update playlists on the server")
    args = ap.parse_args(argv)

    if args.rename or args.cover:
        if not (args.url and args.user and args.password):
            print("[playlist] need NAVIDROME_URL/USER/TOKEN (env) or --url/--user/--password", file=sys.stderr)
            return 2
        nd = Navidrome(args.url, args.user, args.password)
        nd.ping()
        return run_rename(nd, args.rename, args.cover, args.commit)

    if not args.path:
        ap.error("a CSV path is required unless --rename/--cover is used")

    if args.covers:
        if not (args.url and args.user and args.password):
            print("[covers] need NAVIDROME_URL/USER/TOKEN (env) or --url/--user/--password", file=sys.stderr)
            return 2
        nd = Navidrome(args.url, args.user, args.password)
        nd.ping()
        folder = args.path if os.path.isdir(args.path) else os.path.dirname(args.path)
        return run_covers(folder, nd, args.commit)

    if os.path.isdir(args.path):
        csvs = sorted(os.path.join(args.path, f) for f in os.listdir(args.path) if f.lower().endswith(".csv"))
    else:
        csvs = [args.path]
    if not csvs:
        print(f"[playlist] no CSVs found at {args.path}", file=sys.stderr)
        return 1

    print(f"[playlist] indexing {args.db} ...")
    idx = build_index_from_db(args.db)
    print(f"[playlist] {len(idx['isrc'])} ISRCs, {len(idx['fuzzy'][''])} tracks indexed")
    thresholds = {"title": args.title_threshold, "artist": args.artist_threshold, "overall": args.overall_threshold}

    plans = []
    for path in csvs:
        name, song_ids, matched, unmatched = process_csv(path, idx, thresholds)
        if args.name and len(csvs) == 1:
            name = args.name
        plans.append((name, song_ids, len(unmatched)))

    print(f"\n[playlist] {'COMMIT' if args.commit else 'DRY-RUN'}: {len(plans)} playlists")
    for name, song_ids, n_unmatched in plans:
        print(f"[playlist]   '{name}': {len(song_ids)} tracks" + (f", {n_unmatched} unmatched" if n_unmatched else ""))
    if not args.commit:
        print("[playlist] nothing written; re-run with --commit")
        return 0

    if not (args.url and args.user and args.password):
        print("[playlist] need NAVIDROME_URL/USER/TOKEN (env) or --url/--user/--password", file=sys.stderr)
        return 2
    nd = Navidrome(args.url, args.user, args.password)
    nd.ping()
    existing = {p["name"]: p["id"] for p in nd.playlists()}
    for name, song_ids, _n in plans:
        if not song_ids:
            print(f"[playlist] skip '{name}' (no matches)")
            continue
        if name in existing and not args.replace:
            nd.update_playlist(existing[name], song_ids)
            print(f"[playlist] added {len(song_ids)} tracks to existing '{name}'")
        else:
            if name in existing and args.replace:
                nd.call("deletePlaylist", {"id": existing[name]})
            nd.create_playlist(name, song_ids)
            print(f"[playlist] created '{name}' with {len(song_ids)} tracks")
    return 0


def _env(key):
    """Read a credential from the environment, or the importer's .env if present."""
    val = os.getenv(key)
    if val:
        return val
    for env_path in (".env",):
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return None


if __name__ == "__main__":
    raise SystemExit(main())

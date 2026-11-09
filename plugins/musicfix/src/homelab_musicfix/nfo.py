# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Generate album.nfo + artist.nfo for Emby / Jellyfin / Kodi.

Each album folder gets an album.nfo (with a nested <track> list) and each
album-artist folder an artist.nfo -- the two files those servers actually
import for music. Content is the file tags (authoritative for names, genre,
MusicBrainz ids, dates) merged with the live Navidrome database read
**read-only**: play counts, star ratings, loved flags and play dates from the
`annotation` table, joined through media_file/album/artist ids.

    python -m homelab_musicfix.run nfo                 # dry-run: what would be written
    python -m homelab_musicfix.run nfo --commit
    python -m homelab_musicfix.run nfo --restore --commit   # put back prior .nfo content

genres.py already maintained a handful of .nfo files; this regenerates them all
from one source of truth. Every write is journalled to reports/nfo_journal.jsonl
with the previous file content (or a "did not exist" marker), so --restore is
exact and nothing is silently destroyed.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections import Counter, defaultdict
from xml.etree import ElementTree as ET
from xml.dom import minidom

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS

DEFAULT_DB = os.environ.get("NAVIDROME_DB", "navidrome.db")
JOURNAL = REPORTS / "nfo_journal.jsonl"


# --------------------------------------------------------------------------- #
# navidrome (read-only)
# --------------------------------------------------------------------------- #
def load_navidrome(db_path):
    """Maps keyed for merging annotations into the .nfo files."""
    data = {"mf": {}, "ann_mf": {}, "ann_album": {}, "ann_artist": {}, "album": {}}
    if not os.path.exists(db_path):
        print(f"[nfo] navidrome db not found at {db_path}; writing tags only")
        return data
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        for path, mid, alb_id, aa_id, art_id, mbz_alb, mbz_art, dur, year in con.execute(
            "select path, id, album_id, album_artist_id, artist_id, "
            "mbz_album_id, mbz_artist_id, duration, year "
            "from media_file where missing = 0"
        ):
            data["mf"][path] = {
                "id": mid,
                "album_id": alb_id,
                "album_artist_id": aa_id,
                "artist_id": art_id,
                "mbz_album_id": mbz_alb,
                "mbz_artist_id": mbz_art,
                "duration": dur,
                "year": year,
            }
        for item_type, dest in (("media_file", "ann_mf"), ("album", "ann_album"), ("artist", "ann_artist")):
            for iid, pc, pd, rt, st, sa in con.execute(
                "select item_id, play_count, play_date, rating, starred, starred_at "
                "from annotation where item_type = ?",
                (item_type,),
            ):
                data[dest][iid] = {
                    "play_count": pc or 0,
                    "play_date": pd,
                    "rating": rt or 0,
                    "starred": bool(st),
                    "starred_at": sa,
                }
        for aid, name, mbz, date, min_year in con.execute("select id, name, mbz_album_id, date, min_year from album"):
            data["album"][aid] = {"name": name, "mbz_album_id": mbz, "date": date, "min_year": min_year}
    finally:
        con.close()
    print(
        f"[nfo] navidrome: {len(data['mf'])} tracks, "
        f"{len(data['ann_mf'])} track / {len(data['ann_album'])} album / "
        f"{len(data['ann_artist'])} artist annotations"
    )
    return data


# --------------------------------------------------------------------------- #
# tag helpers
# --------------------------------------------------------------------------- #
def _first(tags, key):
    v = tags.get(key)
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _all(tags, key):
    v = tags.get(key)
    if isinstance(v, list):
        return [x for x in v if str(x).strip()]
    return [v] if v else []


def _int(x):
    try:
        return int(str(x).split("/")[0].strip())
    except (ValueError, AttributeError):
        return None


def _sub(parent, tag, text):
    if text is None or str(text) == "":
        return None
    e = ET.SubElement(parent, tag)
    e.text = str(text)
    return e


def _rating_to_10(navrating):
    """Navidrome stores 0-5 stars; Kodi/Jellyfin .nfo userrating is 0-10."""
    return navrating * 2 if navrating else None


def pretty(elem):
    raw = ET.tostring(elem, encoding="utf-8")
    return minidom.parseString(raw).toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


# --------------------------------------------------------------------------- #
# nfo builders
# --------------------------------------------------------------------------- #
def build_album_nfo(tracks, nav):
    """tracks: list of (rel, tags). Returns XML string."""
    root = ET.Element("album")
    tags0 = tracks[0][1]

    album = _first(tags0, "album") or ""
    _sub(root, "title", album)

    # album artist (may be multi-valued)
    aas = _all(tags0, "albumartist") or _all(tags0, "artist")
    if aas:
        _sub(root, "artistdesc", ", ".join(aas))
        for a in aas:
            _sub(root, "albumartist", a)

    # genres: union across the album's tracks, most-common first
    genre_counts = Counter()
    for _, t in tracks:
        for g in _all(t, "genre"):
            genre_counts[g] += 1
    for g, _ in genre_counts.most_common():
        _sub(root, "genre", g)

    # navidrome album row + annotation
    alb_ids = Counter(nav["mf"].get(rel, {}).get("album_id") for rel, _ in tracks)
    alb_ids.pop(None, None)
    alb_id = alb_ids.most_common(1)[0][0] if alb_ids else None
    alb_row = nav["album"].get(alb_id, {}) if alb_id else {}
    alb_ann = nav["ann_album"].get(alb_id, {}) if alb_id else {}

    mbz = _first(tags0, "musicbrainz_albumid") or alb_row.get("mbz_album_id")
    _sub(root, "musicbrainzalbumid", mbz)

    date = _first(tags0, "date") or alb_row.get("date")
    year = _int(date) or alb_row.get("min_year")
    _sub(root, "year", year)
    _sub(root, "releasedate", date)

    if alb_ann:
        _sub(root, "userrating", _rating_to_10(alb_ann.get("rating")))
        _sub(root, "playcount", alb_ann.get("play_count") or None)
        _sub(root, "lastplayed", alb_ann.get("play_date"))
        if alb_ann.get("starred"):
            _sub(root, "loved", "true")

    # tracks, ordered by (disc, track)
    def order(item):
        rel, t = item
        return (_int(_first(t, "discnumber")) or 1, _int(_first(t, "tracknumber")) or 0, rel)

    for rel, t in sorted(tracks, key=order):
        te = ET.SubElement(root, "track")
        _sub(te, "position", _int(_first(t, "tracknumber")))
        _sub(te, "title", _first(t, "title"))
        mf = nav["mf"].get(rel, {})
        dur = mf.get("duration") or t.get("_length")
        if dur:
            _sub(te, "duration", int(float(dur)))
        _sub(te, "musicbrainztrackid", _first(t, "musicbrainz_trackid") or mf.get("mbz_album_id") and None)
        ann = nav["ann_mf"].get(mf.get("id"), {}) if mf else {}
        if ann.get("play_count"):
            _sub(te, "playcount", ann["play_count"])
        if ann.get("rating"):
            _sub(te, "userrating", _rating_to_10(ann["rating"]))
    return pretty(root)


def build_artist_nfo(artist_name, tracks, nav):
    root = ET.Element("artist")
    _sub(root, "name", artist_name)

    genre_counts = Counter()
    mbz_art = None
    art_ids = Counter()
    for rel, t in tracks:
        for g in _all(t, "genre"):
            genre_counts[g] += 1
        mbz_art = mbz_art or _first(t, "musicbrainz_albumartistid") or _first(t, "musicbrainz_artistid")
        aid = nav["mf"].get(rel, {}).get("album_artist_id")
        if aid:
            art_ids[aid] += 1
    art_id = art_ids.most_common(1)[0][0] if art_ids else None
    mbz_art = mbz_art or nav["mf"].get(tracks[0][0], {}).get("mbz_artist_id")
    _sub(root, "musicbrainzartistid", mbz_art)
    for g, _ in genre_counts.most_common():
        _sub(root, "genre", g)

    ann = nav["ann_artist"].get(art_id, {}) if art_id else {}
    if ann:
        _sub(root, "userrating", _rating_to_10(ann.get("rating")))
        _sub(root, "playcount", ann.get("play_count") or None)
        if ann.get("starred"):
            _sub(root, "loved", "true")
    return pretty(root)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def load_inventory(inv):
    return [json.loads(l) for l in open(inv, encoding="utf-8") if l.strip()]


def write_journalled(root, rel_nfo, content, jf):
    """Write one .nfo, journalling prior content for restore.

    Returns "atomic" or "in-place", so the caller can report how many files
    needed the fallback below.

    The write is normally `<name>.part` + `os.replace`, which is atomic: a
    crash leaves either the old file or the new one, never half of one. On this
    library's NTFS volume that is not always possible. Some directory entries
    are damaged such that the file is *readable but not unlinkable* --

        stat album.nfo  -> inode 132742, 545 bytes   (resolves)
        head album.nfo  -> valid XML                 (reads)
        rm   album.nfo  -> ENOENT                    (cannot unlink)

    -- because the $I30 index and the MFT record disagree. `os.replace` has to
    unlink the target, so it fails, and the first version of this function let
    the exception escape with the `.part` file still on disk. One run stranded
    **183** of them across the library, which then had to be cleaned up by hand.

    Two things fix that. The `.part` file is always removed on failure, and an
    in-place rewrite is tried instead: opening the damaged path "w" truncates
    through the existing directory entry without ever unlinking it, and that
    does succeed. It is not atomic -- an interruption mid-write leaves a
    truncated .nfo -- but a .nfo is regenerated from tags on every run, so the
    cost of that is one more run, and the alternative is not writing at all.
    """
    path = os.path.join(root, rel_nfo)
    prior = None
    existed = os.path.exists(path)
    if existed:
        try:
            prior = open(path, encoding="utf-8").read()
        except Exception:  # noqa: BLE001
            prior = None
    os.makedirs(os.path.dirname(path), exist_ok=True)

    tmp = path + ".part"
    how = "atomic"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except OSError as exc:
        # Never leave the temp file behind. It may itself be unlinkable, in
        # which case there is nothing more to be done about it.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            how = "in-place"
        except OSError as inner:
            raise OSError(
                f"atomic replace failed ({type(exc).__name__}: {exc}) and the "
                f"in-place rewrite failed too ({type(inner).__name__}: {inner})"
            ) from inner

    jf.write(
        json.dumps({"ts": time.time(), "nfo": rel_nfo, "existed": existed, "prior": prior}, ensure_ascii=False) + "\n"
    )
    return how


def restore(root, *, commit):
    if not JOURNAL.exists():
        print(f"[nfo] no journal at {JOURNAL}")
        return 1
    # earliest record per file = its state before we first touched it
    state = {}
    for line in open(JOURNAL, encoding="utf-8"):
        if not line.strip():
            continue
        e = json.loads(line)
        state.setdefault(e["nfo"], e)
    print(f"[nfo] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(state)} .nfo files")
    if not commit:
        return 0
    restored = removed = 0
    for rel, e in state.items():
        path = os.path.join(root, rel)
        if e["existed"] and e.get("prior") is not None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(e["prior"])
            restored += 1
        elif not e["existed"] and os.path.exists(path):
            os.remove(path)
            removed += 1
    print(f"[nfo] {restored} restored, {removed} removed (were newly created)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate album.nfo + artist.nfo (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--no-album", action="store_true", help="skip album.nfo")
    ap.add_argument("--no-artist", action="store_true", help="skip artist.nfo")
    ap.add_argument("--limit", type=int, default=0, help="limit albums (for testing)")
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.root, commit=args.commit)

    records = load_inventory(args.inventory)
    nav = load_navidrome(args.db)

    by_album = defaultdict(list)  # album folder -> [(rel, tags)]
    by_artist = defaultdict(list)  # artist folder -> [(rel, tags)]
    for r in records:
        if r.get("error"):
            continue
        rel = r["path"]
        if os.path.splitext(rel)[1].lower() not in AUDIO_EXTS:
            continue
        tags = r.get("tags") or {}
        if tags.get("_error"):
            continue
        parts = rel.split("/")
        if len(parts) < 2:
            continue
        album_dir = os.path.dirname(rel)
        artist_dir = parts[0]
        by_album[album_dir].append((rel, tags))
        by_artist[artist_dir].append((rel, tags))

    album_dirs = sorted(by_album)
    artist_dirs = sorted(by_artist)
    if args.limit:
        album_dirs = album_dirs[: args.limit]
        keep = {d.split("/")[0] for d in album_dirs}
        artist_dirs = [a for a in artist_dirs if a in keep]

    n_album = 0 if args.no_album else len(album_dirs)
    n_artist = 0 if args.no_artist else len(artist_dirs)
    print(f"[nfo] {'COMMIT' if args.commit else 'DRY-RUN'}: {n_album} album.nfo + {n_artist} artist.nfo to write")

    if not args.commit:
        for d in album_dirs[:5]:
            if not args.no_album:
                print(f"  album  {d}/album.nfo  ({len(by_album[d])} tracks)")
        for a in artist_dirs[:5]:
            if not args.no_artist:
                print(f"  artist {a}/artist.nfo")
        # show one rendered sample so the schema is reviewable
        if album_dirs and not args.no_album:
            print("\n--- sample album.nfo ---")
            print(build_album_nfo(by_album[album_dirs[0]], nav)[:1400])
        print("[nfo] nothing written; re-run with --commit")
        return 0

    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    ok = failed = fallback = 0
    with open(JOURNAL, "a", encoding="utf-8") as jf:
        if not args.no_album:
            for d in album_dirs:
                try:
                    content = build_album_nfo(by_album[d], nav)
                    how = write_journalled(args.root, os.path.join(d, "album.nfo"), content, jf)
                    ok += 1
                    fallback += how == "in-place"
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  ERROR {d}/album.nfo: {type(exc).__name__}: {exc}")
        if not args.no_artist:
            for a in artist_dirs:
                try:
                    content = build_artist_nfo(a, by_artist[a], nav)
                    how = write_journalled(args.root, os.path.join(a, "artist.nfo"), content, jf)
                    ok += 1
                    fallback += how == "in-place"
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    print(f"  ERROR {a}/artist.nfo: {type(exc).__name__}: {exc}")
    print(f"[nfo] {ok} written, {failed} failed")
    if fallback:
        # Not a warning about this run -- a count of how many directory entries
        # on the volume can no longer be unlinked. Expect it to grow until the
        # filesystem is repaired with chkdsk.
        print(
            f"[nfo] {fallback} written in place: their directory entries are "
            f"damaged and cannot be replaced atomically (filesystem needs chkdsk)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

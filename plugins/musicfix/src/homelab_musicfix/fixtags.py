# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Rewrite Artist / Album Artist tags into clean multi-valued form.

The rule the library should follow, and which Navidrome understands natively:

  * ARTIST and ALBUMARTIST hold *one artist per value*, as a genuine
    multi-valued tag. They never contain ";", "•", " & " or "feat." used as a
    packing separator.
  * ALBUM carries no "(feat. X)" suffix.

Only files whose tags actually change are written. Every write is journalled to
reports/tag_journal.jsonl with the exact before and after values, so `--restore`
can put the previous tags back even after the file was rewritten in place.

Unlike the move phases, this edits files inside the library rather than staging
them, because rewriting a tag does not move the file and so cannot collide.
Stop Navidrome first anyway: it will otherwise re-index files mid-rewrite.
"""

from __future__ import annotations

import argparse
import json
import os
import time

import re

import mutagen
from mutagen.id3 import ID3, TALB, TIT2, TPE1, TPE2
from mutagen.mp4 import MP4

from .settings import MUSIC_ROOT, REPORTS
from .naming import clean_album, recover_from_path
from .tags import augment_with_guests, build_vocabulary, split_artists, strip_feat

_WS_RE = re.compile(r"\s+")

# ID3v2.4 stores multiple values as a NUL-separated list in one frame; mutagen
# handles that when given a Python list. ID3v2.3 has no such concept, so files
# written as v2.3 get upgraded to v2.4 on save.
ID3_FRAMES = {"artist": (TPE1, "TPE1"), "albumartist": (TPE2, "TPE2"), "album": (TALB, "TALB")}

VORBIS_KEYS = {"artist": "artist", "albumartist": "albumartist", "album": "album"}
MP4_KEYS = {"artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb"}


def desired_tags(rec, vocab):
    """What the artist/albumartist/album tags should become for one file."""
    tags = dict(rec.get("tags") or {})

    # A file with no tags at all still has its path, which in this library
    # encodes "<Artist>/<Album>/<Artist> - <Title>". Recovering from it means
    # those files get fixed rather than quarantined as unnameable.
    if not (tags.get("title") or [""])[0].strip():
        for key, value in recover_from_path(rec.get("path", "")).items():
            have = tags.get(key)
            have_first = (have[0] if isinstance(have, list) and have else have) or ""
            if not str(have_first).strip():
                tags[key] = value

    artist = split_artists(tags.get("artist"), vocab)
    albumartist = split_artists(tags.get("albumartist"), vocab) or artist
    album_vals = tags.get("album") or []
    album = clean_album(album_vals[0] if album_vals else "")

    # Strip "(feat. X)" from the title, but never lose X: if the guest is not
    # already credited on the track, add them to the artist tag. The album
    # artist is untouched -- a featured guest does not headline the album.
    title_vals = tags.get("title") or []
    title_raw = title_vals[0] if title_vals else ""
    title, guests = strip_feat(title_raw, vocab)
    title = _WS_RE.sub(" ", title or "").strip()
    sort_names = list(tags.get("artistsort") or []) + list(tags.get("albumartistsort") or [])
    artist = augment_with_guests(artist, guests, sort_names)

    return {"artist": artist, "albumartist": albumartist, "album": album, "title": title, "title_raw": title_raw}


def current_tags(rec):
    tags = rec.get("tags") or {}
    return {
        "artist": list(tags.get("artist") or []),
        "albumartist": list(tags.get("albumartist") or []),
        "album": (tags.get("album") or [""])[0],
        "title": (tags.get("title") or [""])[0],
    }


def needs_change(cur, want):
    return (
        cur["artist"] != want["artist"]
        or cur["albumartist"] != want["albumartist"]
        or (cur["album"] or "") != (want["album"] or "")
        or (cur.get("title") or "") != (want.get("title") or "")
    )


def write_tags(path, want) -> str | None:
    """Apply the desired tags to one file. Returns an error string or None."""
    try:
        mf = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001
        return f"open: {type(exc).__name__}: {exc}"
    if mf is None:
        return "unrecognised format"

    try:
        if mf.tags is None:
            mf.add_tags()
    except Exception as exc:  # noqa: BLE001
        return f"add_tags: {type(exc).__name__}: {exc}"

    try:
        if isinstance(mf.tags, ID3):
            frames = mf.tags
            frames.setall("TPE1", [TPE1(encoding=3, text=want["artist"])])
            frames.setall("TPE2", [TPE2(encoding=3, text=want["albumartist"])])
            if want["album"]:
                frames.setall("TALB", [TALB(encoding=3, text=[want["album"]])])
            if want.get("title"):
                frames.setall("TIT2", [TIT2(encoding=3, text=[want["title"]])])
            # Multi-value text frames only survive in ID3v2.4, so force it.
            mf.save(v2_version=4)
        elif isinstance(mf, MP4):
            mf["\xa9ART"] = want["artist"]
            mf["aART"] = want["albumartist"]
            if want["album"]:
                mf["\xa9alb"] = [want["album"]]
            if want.get("title"):
                mf["\xa9nam"] = [want["title"]]
            mf.save()
        else:
            # Vorbis comments (FLAC, Ogg, Opus) store repeated keys natively,
            # which is the cleanest representation of a multi-artist tag.
            mf["artist"] = want["artist"]
            mf["albumartist"] = want["albumartist"]
            if want["album"]:
                mf["album"] = [want["album"]]
            if want.get("title"):
                mf["title"] = [want["title"]]
            mf.save()
    except Exception as exc:  # noqa: BLE001
        return f"save: {type(exc).__name__}: {exc}"
    return None


def restore(journal_path, root, *, commit):
    """Reverse tag writes using the journal, newest first.

    The journal stores the complete prior value of every field this tool
    touches, so replaying it backwards restores the original state exactly.
    """
    if not os.path.exists(journal_path):
        print(f"[fixtags] no journal at {journal_path}")
        return 1
    entries = []
    with open(journal_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                e = json.loads(line)
                if not e.get("error"):
                    entries.append(e)

    # Keep only the earliest record per file: that is the original state.
    original = {}
    for e in entries:
        original.setdefault(e["path"], e["before"])

    print(f"[fixtags] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(original)} files to revert")
    ok = failed = 0
    for path, before in original.items():
        if not commit:
            ok += 1
            continue
        err = write_tags(os.path.join(root, path), before)
        if err:
            failed += 1
            print(f"  ERROR {path}: {err}")
        else:
            ok += 1
    print(f"[fixtags] {ok} restored, {failed} failed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalise artist/albumartist/album tags (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT), help="directory the inventory paths are relative to")
    ap.add_argument(
        "--vocab-inventory",
        action="append",
        default=[],
        help="extra inventory to fold into the artist vocabulary but "
        "never write to. Needed when --inventory covers only a "
        "subset (e.g. a Downloads batch): a weak artist split is "
        "only accepted when every fragment is corroborated, so a "
        "254-file vocabulary would leave splits the library "
        "already makes elsewhere unmade.",
    )
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--journal", default=str(REPORTS / "tag_journal.jsonl"))
    ap.add_argument("--report", default=str(REPORTS / "tag_changes.jsonl"))
    ap.add_argument(
        "--plan", default=str(REPORTS / "plan.jsonl"), help="used only to pick up sync-tags actions for WAV/FLAC pairs"
    )
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--restore", action="store_true", help="put back the tags recorded in the journal, newest entry first"
    )
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.journal, args.root, commit=args.commit)

    records = []
    with open(args.inventory, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))
    vocab = build_vocabulary(records)
    for extra in args.vocab_inventory:
        if not os.path.exists(extra):
            continue
        with open(extra, encoding="utf-8") as fh:
            vocab |= build_vocabulary([json.loads(l) for l in fh if l.strip()])
    by_path = {r["path"]: r for r in records}

    # A track kept in two formats (WAV alongside FLAC) must end up with the
    # same tags in both, or the two copies derive different canonical paths and
    # get filed into different folders. The plan names the richer file as the
    # source of truth for each such pair.
    sync_source = {}
    if os.path.exists(args.plan):
        with open(args.plan, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                a = json.loads(line)
                if a.get("kind") == "sync-tags":
                    sync_source[a["src"]] = a["from"]

    changes = []
    for rec in records:
        if rec.get("error"):
            continue
        cur = current_tags(rec)
        src_path = sync_source.get(rec["path"])
        basis = by_path.get(src_path, rec) if src_path else rec
        want = desired_tags(basis, vocab)
        if needs_change(cur, want):
            changes.append((rec, cur, want))
    if args.limit:
        changes = changes[: args.limit]

    print(
        f"[fixtags] {'COMMIT' if args.commit else 'DRY-RUN'}  "
        f"{len(changes)} of {len(records)} files need tag changes  (vocabulary={len(vocab)})"
    )

    with open(args.report, "w", encoding="utf-8") as rfh:
        for rec, cur, want in changes:
            rfh.write(json.dumps({"path": rec["path"], "from": cur, "to": want}, ensure_ascii=False) + "\n")

    for rec, cur, want in changes[:15]:
        print(f"  {rec['path']}")
        for k in ("artist", "albumartist", "album"):
            if cur[k] != want[k]:
                print(f"      {k:12} {cur[k]!r}  ->  {want[k]!r}")
    if len(changes) > 15:
        print(f"  ... and {len(changes) - 15} more (full list in {args.report})")

    if not args.commit:
        print("[fixtags] nothing written; re-run with --commit to apply")
        return 0

    os.makedirs(os.path.dirname(args.journal), exist_ok=True)
    ok = failed = 0
    with open(args.journal, "a", encoding="utf-8") as jfh:
        for rec, cur, want in changes:
            path = os.path.join(args.root, rec["path"])
            err = write_tags(path, want)
            jfh.write(
                json.dumps(
                    {"ts": time.time(), "path": rec["path"], "before": cur, "after": want, "error": err},
                    ensure_ascii=False,
                )
                + "\n"
            )
            if err:
                failed += 1
                print(f"  ERROR {rec['path']}: {err}")
            else:
                ok += 1
    print(f"[fixtags] {ok} files updated, {failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

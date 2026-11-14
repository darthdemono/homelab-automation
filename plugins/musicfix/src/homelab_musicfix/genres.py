# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Normalise genres across the library: consistent casing, de-duplicated.

This absorbs the standalone fix_genres.py that lived in the music root. That
script did two useful things to Kodi/Jellyfin .nfo files:

  * collapsed genre variants that differ only in case ("Pop"/"pop") to one form,
  * removed duplicate <genre> entries.

Its casing choice was local to a single file, so the same genre could still end
up cased differently in two different albums. Here the canonical form is decided
**once, library-wide**, from the audio tags themselves, and applied to both the
audio tags (which is what Navidrome actually reads) and the .nfo files (kept in
sync for Kodi/Jellyfin).

Casing rule per genre: the spelling that appears most often wins; ties break
toward the more title-cased variant, matching the original script's intent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter, defaultdict

import mutagen
from mutagen.id3 import ID3, TCON

from .settings import MUSIC_ROOT, REPORTS

# Album/artist .nfo only; these are the files the original script targeted.
NFO_NAMES = {"album.nfo", "artist.nfo"}

# A single genre string may itself pack several genres. Split on ; or , but not
# on the ; of an HTML entity like &amp; (carried over from the original).
_GENRE_SPLIT = re.compile(r"(?<!&amp);|,")


def split_genre_value(value: str):
    """One raw genre string -> list of individual, whitespace-collapsed genres."""
    return [re.sub(r"\s+", " ", p).strip() for p in _GENRE_SPLIT.split(value or "") if p.strip()]


def canonical_casing(counter: Counter) -> str:
    """Pick the winning spelling for one genre from all observed spellings.

    Frequency first (the spelling the library already prefers), then the more
    title-cased variant as a tiebreak.
    """
    return max(counter.items(), key=lambda kv: (kv[1], sum(c.isupper() for c in kv[0])))[0]


def build_canonical_map(records) -> dict:
    """lowercase genre -> canonical display form, decided library-wide."""
    spellings = defaultdict(Counter)
    for r in records:
        for g in (r.get("tags") or {}).get("genre") or []:
            for part in split_genre_value(g):
                spellings[part.lower()][part] += 1
    return {key: canonical_casing(counter) for key, counter in spellings.items()}


def normalise_list(genres, canon):
    """Apply canonical casing and drop case-insensitive duplicates, in order."""
    out, seen = [], set()
    for g in genres:
        for part in split_genre_value(g):
            key = part.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(canon.get(key, part))
    return out


# --------------------------------------------------------------------------
# Audio tags
# --------------------------------------------------------------------------
def write_audio_genres(path, genres) -> str | None:
    try:
        mf = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001
        return f"open: {type(exc).__name__}: {exc}"
    if mf is None:
        return "unrecognised format"
    try:
        if mf.tags is None:
            mf.add_tags()
        if isinstance(mf.tags, ID3):
            mf.tags.setall("TCON", [TCON(encoding=3, text=genres)])
            mf.save(v2_version=4)
        elif path.lower().endswith((".m4a", ".mp4", ".alac")):
            mf["\xa9gen"] = genres
            mf.save()
        else:
            mf["genre"] = genres
            mf.save()
    except Exception as exc:  # noqa: BLE001
        return f"save: {type(exc).__name__}: {exc}"
    return None


# --------------------------------------------------------------------------
# .nfo files (ported from fix_genres.py, using the library-wide casing map)
# --------------------------------------------------------------------------
def process_nfo(path, canon, *, commit):
    """Rewrite the <genre> tags in one .nfo. Returns (changed, old, new)."""
    try:
        text = path if isinstance(path, str) else None
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except UnicodeDecodeError:
        return None, [], []
    except OSError as exc:
        return None, [], [str(exc)]

    found = []
    for m in re.finditer(r"<genre>(.*?)</genre>", text, re.DOTALL):
        found.extend(split_genre_value(m.group(1)))
    if not found:
        return False, [], []

    old = normalise_list(found, {g.lower(): g for g in found})  # unique, original casing
    new = normalise_list(found, canon)

    first = True

    def repl(m):
        nonlocal first
        leading = m.group(1)
        if first:
            first = False
            tags = [f"<genre>{new[0]}</genre>"] + [f"  <genre>{g}</genre>" for g in new[1:]]
            return leading + "\n".join(tags)
        return ""

    new_text = re.sub(r"(\n?\s*)<genre>(.*?)</genre>", repl, text, flags=re.DOTALL)
    new_text = re.sub(r"\n{3,}", "\n\n", new_text)
    changed = new_text != text
    if changed and commit:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new_text)
    return changed, old, new


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalise genres in audio tags and .nfo files (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--audio", dest="audio", action="store_true", default=True)
    ap.add_argument("--no-audio", dest="audio", action="store_false", help="skip audio tags, only touch .nfo files")
    ap.add_argument("--nfo", dest="nfo", action="store_true", default=True)
    ap.add_argument("--no-nfo", dest="nfo", action="store_false", help="skip .nfo files, only touch audio tags")
    ap.add_argument("--journal", default=str(REPORTS / "genre_journal.jsonl"))
    ap.add_argument("--report", default=str(REPORTS / "genre_changes.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--restore", action="store_true", help="put genres back to the values recorded in the journal")
    args = ap.parse_args(argv)

    if args.restore:
        return _restore(args.journal, args.root, commit=args.commit)

    records = []
    with open(args.inventory, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))

    canon = build_canonical_map(records)
    collisions = _casing_collisions(records)
    print(f"[genres] {len(canon)} distinct genres; {len(collisions)} had inconsistent casing across the library")
    for low, forms in list(collisions.items())[:15]:
        print(f"[genres]   {sorted(forms)}  ->  {canon[low]!r}")

    # --- audio tags ---
    audio_changes = []
    if args.audio:
        for r in records:
            if r.get("error"):
                continue
            cur = (r.get("tags") or {}).get("genre") or []
            new = normalise_list(cur, canon)
            if new != cur:
                audio_changes.append((r["path"], cur, new))
    if args.limit:
        audio_changes = audio_changes[: args.limit]

    # --- nfo files ---
    nfo_files = []
    if args.nfo:
        for r in _iter_nfo(args.root):
            nfo_files.append(r)

    with open(args.report, "w", encoding="utf-8") as rfh:
        for path, cur, new in audio_changes:
            rfh.write(json.dumps({"kind": "audio", "path": path, "from": cur, "to": new}, ensure_ascii=False) + "\n")

    print(
        f"[genres] {'COMMIT' if args.commit else 'DRY-RUN'}: "
        f"{len(audio_changes)} audio files, up to {len(nfo_files)} .nfo files"
    )
    for path, cur, new in audio_changes[:10]:
        print(f"  {path}")
        print(f"      {cur}\n   -> {new}")

    if not args.commit:
        print("[genres] nothing written; re-run with --commit")
        return 0

    os.makedirs(os.path.dirname(args.journal), exist_ok=True)
    ok = failed = 0
    with open(args.journal, "a", encoding="utf-8") as jfh:
        for path, cur, new in audio_changes:
            err = write_audio_genres(os.path.join(args.root, path), new)
            jfh.write(
                json.dumps(
                    {"ts": time.time(), "kind": "audio", "path": path, "before": cur, "after": new, "error": err},
                    ensure_ascii=False,
                )
                + "\n"
            )
            ok += not err
            failed += bool(err)
            if err:
                print(f"  ERROR {path}: {err}")
        nfo_changed = 0
        for nfo in nfo_files:
            changed, old, new = process_nfo(nfo, canon, commit=True)
            if changed:
                nfo_changed += 1
                jfh.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "kind": "nfo",
                            "path": os.path.relpath(nfo, args.root),
                            "before": old,
                            "after": new,
                            "error": None,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    print(f"[genres] audio: {ok} updated, {failed} failed; nfo: {nfo_changed} updated")
    return 0


def _restore(journal_path, root, *, commit):
    """Reverse genre writes from the journal (audio tags only; nfo left as-is)."""
    if not os.path.exists(journal_path):
        print(f"[genres] no journal at {journal_path}")
        return 1
    original = {}
    with open(journal_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("kind") == "audio" and not e.get("error"):
                original.setdefault(e["path"], e["before"])
    print(f"[genres] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(original)} audio files")
    ok = failed = 0
    for path, before in original.items():
        if not commit:
            ok += 1
            continue
        err = write_audio_genres(os.path.join(root, path), before)
        failed += bool(err)
        ok += not err
        if err:
            print(f"  ERROR {path}: {err}")
    print(f"[genres] {ok} restored, {failed} failed")
    return 0


def _iter_nfo(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".Trash")]
        for fn in filenames:
            if fn.lower() in NFO_NAMES:
                yield os.path.join(dirpath, fn)


def _casing_collisions(records):
    forms = defaultdict(set)
    for r in records:
        for g in (r.get("tags") or {}).get("genre") or []:
            for part in split_genre_value(g):
                forms[part.lower()].add(part)
    return {k: v for k, v in forms.items() if len(v) > 1}


if __name__ == "__main__":
    raise SystemExit(main())

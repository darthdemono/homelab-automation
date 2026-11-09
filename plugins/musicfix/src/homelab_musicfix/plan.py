# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Turn an inventory into a reviewable plan of moves.

Nothing here touches the filesystem. It reads reports/inventory.jsonl and emits
reports/plan.jsonl, one action per line, which `apply` then executes. Keeping
the decision and the execution in separate passes means the whole plan can be
inspected, diffed and signed off before a single byte moves.

Action kinds:
  quarantine-junk    OS/player turds (AlbumArt_{GUID}, Thumbs.db, desktop.ini)
  quarantine-badtag  metadata too broken to name a file from
  quarantine-dupe    the losing copy of a same-album duplicate
  rename             move a track (and its sidecars) to its canonical path
  rename-sidecar     move an album-level sidecar alongside its album
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from collections import defaultdict

from .settings import IMAGE_EXTS, JUNK_PATTERNS, LYRIC_EXTS, MUSIC_ROOT, REPORTS
from . import settings
from .naming import canonical_paths, sanitize_component
from .quality import quality_key
from .tags import build_vocabulary

JUNK_RES = [re.compile(p) for p in JUNK_PATTERNS]


# A bracketed suffix naming guest artists does not change which recording this
# is, so it can be ignored when matching duplicates.
_FEAT_PARENS_RE = re.compile(r"[\(\[\{]\s*(?:feat|ft|featuring|with|w)\b\.?[^\)\]\}]*[\)\]\}]", re.IGNORECASE)

# Everything else in brackets very much does change it. "GUMMO (remix)" is not
# "GUMMO", and "Mary (Instrumental)" is not "Mary" -- treating those as
# duplicates would quarantine a recording the library has no other copy of. So
# version qualifiers are deliberately left in the match key.


def norm(s: str) -> str:
    """Normalisation for duplicate matching only.

    Deliberately conservative: it folds case, accents and punctuation, and drops
    featured-artist suffixes, but preserves version qualifiers so that distinct
    recordings never collide.
    """
    s = unicodedata.normalize("NFKD", (s or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _FEAT_PARENS_RE.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def metadata_richness(rec: dict) -> tuple:
    """How complete a file's tags are, used to break duplicate ties.

    A file that carries MusicBrainz IDs, genres, a date and embedded lyrics is
    more useful to Navidrome than a bare one, even at identical audio quality.
    """
    t = rec.get("tags") or {}
    weighted = 0
    for key, weight in (
        ("musicbrainz_trackid", 3),
        ("musicbrainz_albumid", 3),
        ("musicbrainz_artistid", 2),
        ("isrc", 2),
        ("date", 1),
        ("genre", 1),
        ("tracknumber", 1),
        ("discnumber", 1),
        ("albumartist", 1),
        ("lyrics", 1),
    ):
        if t.get(key):
            weighted += weight
    return (weighted, 1 if t.get("_has_embedded_art") else 0, len(t))


def is_junk(name: str) -> bool:
    return any(r.match(name) for r in JUNK_RES)


def is_leftover(rel_path: str) -> bool:
    """True for a player application leftover, by location or by filename."""
    parts = rel_path.replace("\\", "/").split("/")
    if parts and parts[0] in settings.LEFTOVER_DIRS:
        return True
    name = parts[-1].lower()
    stem, ext = os.path.splitext(name)
    return (
        ext in settings.LEFTOVER_EXTS
        or name in settings.LEFTOVER_EXACT_NAMES
        or (bool(settings.LEFTOVER_NAME_PREFIXES) and stem.startswith(settings.LEFTOVER_NAME_PREFIXES))
    )


# Artwork that belongs to a folder rather than to any one track. Navidrome and
# Jellyfin both write artist-level images (backdrop, clearlogo) into the artist
# directory, which contains no audio at all -- so matching them to a track by
# name will always fail and must not be read as "orphaned".
FOLDER_ART_STEMS = {
    "folder",
    "cover",
    "front",
    "back",
    "album",
    "albumart",
    "discart",
    "cdart",
    "artist",
    "banner",
    "fanart",
    "logo",
    "thumb",
    "poster",
    "backdrop",
    "clearlogo",
    "clearart",
    "landscape",
    "characterart",
    "background",
    "disc",
    "booklet",
}

# Some taggers write per-track art as "<track stem>.cover.jpg".
_ART_INFIX_RE = re.compile(r"\.(cover|thumb|art|front|back)$", re.IGNORECASE)
# Lyrics are often downloaded with a track-number prefix instead of the artist
# prefix the audio uses: "6. Demons and Angels.lrc" beside "A Boogie Wit da
# Hoodie - Demons and Angels.flac".
_TRACKNUM_PREFIX_RE = re.compile(r"^\s*\d{1,3}\s*[\.\-_)]+\s*")
# Copy artefacts: "cover (1).jpg", "folder (2).jpg".
_COPY_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")
# Artist images written as "<Artist>_Avatar.jpg" / "<Artist>_Header.jpg".
_ARTIST_IMAGE_RE = re.compile(r"_(avatar|header|banner|背景|profile|icon)$", re.IGNORECASE)


def sidecar_stem_variants(stem: str):
    """Plausible track stems a sidecar filename could be referring to."""
    out = [stem]
    a = _ART_INFIX_RE.sub("", stem)
    if a != stem:
        out.append(a)
    for base in list(out):
        b = _TRACKNUM_PREFIX_RE.sub("", base)
        if b and b != base:
            out.append(b)
    return out


def find_sidecar_owner(name, dir_tracks):
    """Match a sidecar to a track in the same folder.

    Exact stem first, then the common filename variations, then a comparison of
    normalised titles. Falling back to the title is what keeps ~1200 .lrc files
    attached to their tracks: the lyrics were fetched with a different naming
    convention than the audio.

    Returns None when the match would be ambiguous, so nothing is guessed.
    """
    stem = os.path.splitext(name)[0]
    variants = sidecar_stem_variants(stem)

    by_stem = {t["stem"].lower(): t for t in dir_tracks}
    for v in variants:
        hit = by_stem.get(v.lower())
        if hit:
            return hit

    # Candidate title strings. The filename usually carries an artist prefix
    # that the title tag does not, so the part after " - " is tried as well.
    titles = []
    for v in variants:
        titles.append(norm(v))
        if " - " in v:
            titles.append(norm(v.split(" - ", 1)[1]))
    titles = [t for t in dict.fromkeys(titles) if t]

    for n in titles:
        matches = [t for t in dir_tracks if t["n_title"] and t["n_title"] == n]
        if len(matches) == 1:
            return matches[0]

    # Last resort: one name carries a version qualifier the other lacks, e.g.
    # "The Smiths - Ask - 2008 Remaster.jpg" beside "The Smiths - Ask.flac".
    # Only a prefix relationship counts, the longest match wins, and it must be
    # unique at that length -- otherwise the sidecar is left orphaned rather
    # than attached to a guess.
    best, best_len, ambiguous = None, -1, False
    for n in titles:
        for t in dir_tracks:
            nt = t["n_title"]
            if not nt:
                continue
            if n.startswith(nt + " ") or nt.startswith(n + " "):
                length = len(nt)
                if length > best_len:
                    best, best_len, ambiguous = t, length, False
                elif length == best_len and t is not best:
                    ambiguous = True
    return None if (best is None or ambiguous) else best


def clean_sidecar_name(name, vocab):
    """Sanitise a sidecar filename that has no track to inherit a name from.

    A sidecar like "BTS; Halsey - Boy With Luv (feat. Halsey).jpg" that never
    matched a track would otherwise keep its ";" when moved. When the name looks
    like "<artist> - <rest>", the artist half is re-parsed with the library
    vocabulary and rejoined the same way a track filename is, so ";" becomes
    "and"/"," and reserved characters are substituted. Names that are not in
    that shape (folder.jpg, cover.png) are only NTFS-sanitised.
    """
    from .tags import join_artists, split_artists

    stem, ext = os.path.splitext(name)
    if " - " in stem:
        artist_part, rest = stem.split(" - ", 1)
        artists = split_artists([artist_part], vocab)
        if artists:
            stem = f"{join_artists(artists)} - {rest}"
    return sanitize_component(stem) + ext.lower()


def format_class(path: str) -> str:
    """Which duplicate bucket a file competes in.

    WAV gets its own bucket so it is never weighed against a FLAC of the same
    track -- both are wanted. Everything else competes normally, so a FLAC and
    an MP3 of one song still resolve to the better copy.
    """
    return "wav" if path.lower().endswith(".wav") else "other"


def load(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def build(inventory, others, *, strip_title_features=True, spectral=None):
    records = list(inventory)
    vocab = build_vocabulary(records)
    spectral = spectral or {}
    actions = []
    stats = defaultdict(int)

    # --- pass 1: canonical target for every playable file -------------------
    usable = []
    for rec in records:
        if rec.get("error") or (rec.get("quality") or {}).get("error"):
            actions.append(
                {
                    "kind": "quarantine-badtag",
                    "src": rec["path"],
                    "reason": rec.get("error") or rec["quality"]["error"],
                }
            )
            stats["unreadable"] += 1
            continue
        # canonical_paths does the full resolution -- vocab-gated artist splits,
        # feat/EP stripping, and moving a title's featured artist into the
        # artist credit -- so no second pass is needed.
        rec["canon"] = canonical_paths(rec, vocabulary=vocab, strip_title_features=strip_title_features)
        if not rec["canon"]["rel_path"]:
            actions.append(
                {
                    "kind": "quarantine-badtag",
                    "src": rec["path"],
                    "reason": "no usable title tag; cannot build a filename",
                    "problems": rec["canon"]["problems"],
                }
            )
            stats["untitled"] += 1
            continue
        usable.append(rec)

    # --- pass 2: duplicates, scoped to a single album -----------------------
    # Per the agreed rule, the same song on two different albums is NOT a
    # duplicate; only two copies within one album are.
    #
    # WAV is deliberately kept in its own class so it never competes with a
    # FLAC of the same track: both formats are wanted side by side. Their tags
    # are synced below, which is what makes them land in the same folder.
    groups = defaultdict(list)
    song_groups = defaultdict(list)
    for rec in usable:
        c = rec["canon"]
        song = (norm(c["album_artist_display"]), norm(c["album"]), norm(c["title"]))
        song_groups[song].append(rec)
        groups[song + (format_class(rec["path"]),)].append(rec)

    # Tracks held in more than one format must carry identical metadata,
    # otherwise they derive different canonical paths and get separated.
    sibling_actions = []
    for song, members in song_groups.items():
        classes = {format_class(r["path"]) for r in members}
        if len(classes) < 2 or not any(c == "wav" for c in classes):
            continue
        source = max(members, key=lambda r: (metadata_richness(r), quality_key(r)))
        for r in members:
            if r is source:
                continue
            sibling_actions.append(
                {
                    "kind": "sync-tags",
                    "src": r["path"],
                    "from": source["path"],
                    "reason": "keep WAV and FLAC of the same track tagged identically",
                }
            )

    losers = set()
    winner_of = {}  # losing track path -> the copy that survives
    dupe_groups = []
    for key, members in groups.items():
        if len(members) < 2:
            continue

        def rank(r):
            # A lossless file whose spectrum betrays a lossy source loses to a
            # genuine lossless copy -- but only *within* its own tier.
            #
            # Ranking "genuine" ahead of everything made a suspect 943 kbps FLAC
            # lose to a 230 kbps MP3, which is strictly worse: the FLAC stores
            # that same audio without discarding anything further. Lossy files
            # are never "suspect" (a cutoff is expected of them), so putting the
            # flag first handed them an undeserved win. It belongs immediately
            # after the lossless flag, where it separates real lossless from
            # upscaled lossless and nothing else.
            lossless, *rest = quality_key(r)
            genuine = 0 if _is_suspect(r, spectral) else 1
            return (lossless, genuine, tuple(rest), metadata_richness(r))

        ranked = sorted(members, key=rank, reverse=True)
        winner, rest = ranked[0], ranked[1:]
        dupe_groups.append({"key": key, "winner": winner["path"], "losers": [r["path"] for r in rest]})
        for r in rest:
            losers.add(r["path"])
            winner_of[r["path"]] = winner["path"]
            reason = "same album + title, lower quality copy"
            if _is_suspect(r, spectral) and not _is_suspect(winner, spectral):
                reason = "same album + title; this copy looks transcoded from a lossy source"
            actions.append(
                {
                    "kind": "quarantine-dupe",
                    "src": r["path"],
                    "reason": reason,
                    "kept": winner["path"],
                    "this": _qsummary(r, spectral),
                    "kept_quality": _qsummary(winner, spectral),
                }
            )
            stats["dupes"] += 1

    # --- pass 3: renames for everything we are keeping ----------------------
    # Reserve target paths case-insensitively: the volume is NTFS, so two names
    # differing only in case collide.
    taken = {}
    for rec in sorted(usable, key=lambda r: r["path"]):
        if rec["path"] in losers:
            continue
        c = rec["canon"]
        target = _dedupe_target(c["rel_path"], taken, rec["path"])
        if target != rec["path"]:
            actions.append(
                {
                    "kind": "rename",
                    "src": rec["path"],
                    "dst": target,
                    "problems": c["problems"],
                    "album_artist": c["album_artist_display"],
                    "track_artist": c["track_artist_display"],
                    "album": c["album"],
                    "title": c["title"],
                }
            )
            stats["renames"] += 1
        else:
            stats["already-correct"] += 1
        rec["target"] = target

    # --- pass 4: non-audio files -------------------------------------------
    # Sidecars follow their track; album art stays with the album; junk goes.
    tracks_by_dir = defaultdict(list)
    losers_by_dir = defaultdict(list)
    # Where each source album folder is heading, precomputed. Deriving this per
    # sidecar would be O(sidecars x tracks) -- 22k x 10.7k on this library.
    dir_targets = defaultdict(set)
    for rec in usable:
        d, fn = os.path.split(rec["path"])
        stem = os.path.splitext(fn)[0]
        entry = {"stem": stem, "n_title": norm(rec["canon"]["title"]), "rec": rec}
        if rec["path"] in losers:
            # Kept separately so a quarantined duplicate takes its own artwork
            # and lyrics with it instead of leaving them behind as orphans.
            losers_by_dir[d.lower()].append(entry)
            continue
        if "target" not in rec:
            continue
        tracks_by_dir[d.lower()].append(entry)
        dir_targets[d].add(os.path.dirname(rec["target"]))

    # Per-track art is sometimes filed one level up, in the artist folder rather
    # than the album folder. Allow a sidecar to reach tracks in subdirectories
    # when its own directory holds none.
    tracks_below = defaultdict(list)
    for d, entries in tracks_by_dir.items():
        parent = os.path.dirname(d)
        while parent:
            tracks_below[parent].extend(entries)
            nxt = os.path.dirname(parent)
            if nxt == parent:
                break
            parent = nxt
    # Only unambiguous folders move: if tracks in one folder disagree about
    # where they are going, album-level art has no single correct destination.
    dir_move = {d: t.pop() for d, t in dir_targets.items() if len(t) == 1}

    for o in others:
        name = os.path.basename(o["path"])
        d = os.path.dirname(o["path"])
        ext = o.get("ext", "").lower()
        if is_leftover(o["path"]):
            actions.append({"kind": "quarantine-leftover", "src": o["path"], "reason": "player application leftover"})
            stats["leftover"] += 1
            continue
        if is_junk(name):
            actions.append({"kind": "quarantine-junk", "src": o["path"], "reason": "player/OS generated file"})
            stats["junk"] += 1
            continue
        if ext == ".py":
            # Stray scripts do not belong in a music library. fix_genres.py in
            # particular is superseded by musicfix.genres.
            actions.append({"kind": "quarantine-junk", "src": o["path"], "reason": "stray script (not music)"})
            stats["junk"] += 1
            continue
        stem = os.path.splitext(name)[0]
        owner = None
        if ext in (LYRIC_EXTS | IMAGE_EXTS):
            owner = find_sidecar_owner(name, tracks_by_dir.get(d.lower(), []))
            if owner is None:
                # A duplicate that is being quarantined takes its sidecars along.
                gone = find_sidecar_owner(name, losers_by_dir.get(d.lower(), []))
                if gone is not None:
                    # "follows" is the losing track this sidecar belongs to;
                    # "kept" must stay the surviving copy, or the report reads
                    # as though a kept track is losing its lyrics.
                    loser_path = gone["rec"]["path"]
                    actions.append(
                        {
                            "kind": "quarantine-dupe",
                            "src": o["path"],
                            "reason": "sidecar of a quarantined duplicate",
                            "follows": loser_path,
                            "kept": winner_of.get(loser_path),
                        }
                    )
                    stats["dupe-sidecars"] += 1
                    continue
            if owner is None and not tracks_by_dir.get(d.lower()):
                # Per-track art filed in the artist folder instead of the album.
                owner = find_sidecar_owner(name, tracks_below.get(d.lower(), []))
        if owner is not None:
            # Per-track sidecar: rename in lockstep with its track. Reserve the
            # path so two art files for one track do not silently target the
            # same name; the second gets a numbered suffix instead.
            tgt_dir, tgt_fn = os.path.split(owner["rec"]["target"])
            tgt_stem = os.path.splitext(tgt_fn)[0]
            dst = _dedupe_target(f"{tgt_dir}/{tgt_stem}{ext}", taken, o["path"])
            if dst != o["path"]:
                actions.append(
                    {"kind": "rename-sidecar", "src": o["path"], "dst": dst, "follows": owner["rec"]["target"]}
                )
                stats["sidecars"] += 1
            continue

        # Folder-level artwork keeps its name and follows its folder.
        bare = _COPY_SUFFIX_RE.sub("", _ART_INFIX_RE.sub("", stem)).lower()
        is_folder_art = bare in FOLDER_ART_STEMS or bool(_ARTIST_IMAGE_RE.search(bare))
        moved_dir = dir_move.get(d)
        if moved_dir and moved_dir != d:
            # Not matched to a track, but the album folder moved. Folder art
            # keeps its name; a stray "Artist - Title" sidecar gets its artist
            # cleaned so no ";" survives in the new path.
            fn = name if is_folder_art else clean_sidecar_name(name, vocab)
            dst = _dedupe_target(f"{moved_dir}/{fn}", taken, o["path"])
            actions.append({"kind": "rename-sidecar", "src": o["path"], "dst": dst, "follows": moved_dir})
            stats["album-sidecars"] += 1
        elif is_folder_art and not tracks_by_dir.get(d.lower()) and losers_by_dir.get(d.lower()):
            # Every track in this album folder was quarantined as a duplicate,
            # so its album-level art (folder.jpg, album.nfo) is redundant -- the
            # surviving copy has its own. Send it after the tracks instead of
            # leaving it stranded in what is often a ";"-named folder.
            gone = losers_by_dir[d.lower()][0]["rec"]["path"]
            actions.append(
                {
                    "kind": "quarantine-dupe",
                    "src": o["path"],
                    "reason": "album art of a fully-quarantined album",
                    "follows": gone,
                    "kept": winner_of.get(gone),
                }
            )
            stats["dupe-sidecars"] += 1
        elif not is_folder_art and clean_sidecar_name(name, vocab) != name:
            # Folder did not move, but the filename itself carries a ";" or a
            # reserved character that must still be fixed in place.
            dst = _dedupe_target(
                f"{d}/{clean_sidecar_name(name, vocab)}" if d else clean_sidecar_name(name, vocab), taken, o["path"]
            )
            if dst != o["path"]:
                actions.append({"kind": "rename-sidecar", "src": o["path"], "dst": dst, "follows": None})
                stats["sidecars"] += 1
        elif is_folder_art:
            # Artist-level art (backdrop.jpg, clearlogo.png) lives in a folder
            # with no audio in it at all. Staying put is correct.
            stats["folder-art"] += 1
        elif ext in (LYRIC_EXTS | IMAGE_EXTS):
            actions.append(
                {
                    "kind": "orphan-sidecar",
                    "src": o["path"],
                    "reason": "no track in this folder matches, by name or title",
                }
            )
            stats["orphan-sidecars"] += 1

    # Sibling tag syncs only apply to copies we are actually keeping.
    for a in sibling_actions:
        if a["src"] not in losers and a["from"] not in losers:
            actions.append(a)
            stats["sync-tags"] += 1

    return actions, dict(stats), dupe_groups, vocab


def _dedupe_target(path, taken, src):
    """Guarantee a unique target, comparing case-insensitively for NTFS."""
    key = path.lower()
    if key not in taken:
        taken[key] = src
        return path
    if taken[key] == src:
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while f"{base} ({n}){ext}".lower() in taken:
        n += 1
    out = f"{base} ({n}){ext}"
    taken[out.lower()] = src
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build a move plan from the inventory (no filesystem writes).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--others", default=str(REPORTS / "non_audio.jsonl"))
    ap.add_argument("--out", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument(
        "--keep-title-features",
        dest="strip_title_features",
        action="store_false",
        default=True,
        help='keep "(feat. X)" in track titles (default strips it and moves the guest into the artist tag)',
    )
    ap.add_argument(
        "--spectral",
        default=str(REPORTS / "spectral.jsonl"),
        help="optional; if present, suspected transcodes lose duplicate ties",
    )
    args = ap.parse_args(argv)

    inv = list(load(args.inventory))
    others = list(load(args.others))
    spectral = {r["path"]: r for r in load(args.spectral)}
    if spectral:
        n = sum(1 for r in spectral.values() if r.get("verdict") == "SUSPECT")
        print(f"[plan] spectral data for {len(spectral)} files, {n} suspected transcodes")
    actions, stats, dupe_groups, vocab = build(
        inv, others, strip_title_features=args.strip_title_features, spectral=spectral
    )

    with open(args.out, "w", encoding="utf-8") as fh:
        for a in actions:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")
    with open(str(REPORTS / "dupe_groups.json"), "w", encoding="utf-8") as fh:
        json.dump(dupe_groups, fh, ensure_ascii=False, indent=1)

    print(f"[plan] {len(inv)} audio, {len(others)} non-audio, vocabulary={len(vocab)} artists")
    for k in sorted(stats):
        print(f"[plan]   {k:20} {stats[k]}")
    print(f"[plan] -> {args.out}")
    return 0


def _is_suspect(rec, spectral):
    s = spectral.get(rec["path"])
    return bool(s and s.get("verdict") == "SUSPECT")


def _qsummary(rec, spectral=None):
    q = rec.get("quality") or {}
    out = {
        "codec": q.get("codec"),
        "bit_rate": q.get("bit_rate"),
        "sample_rate": q.get("sample_rate"),
        "bit_depth": q.get("bit_depth"),
        "lossless": q.get("lossless"),
        "richness": metadata_richness(rec)[0],
    }
    s = (spectral or {}).get(rec["path"])
    if s and s.get("cutoff_hz"):
        out["cutoff_hz"] = s["cutoff_hz"]
        out["spectral_verdict"] = s.get("verdict")
    return out


if __name__ == "__main__":
    raise SystemExit(main())

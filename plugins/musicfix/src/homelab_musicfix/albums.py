# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Why one album shows up twice in Navidrome, and what to do about it.

Navidrome does not key an album on the folder. It keys it on the *release
identity* carried by the tags -- album artist, album name, MusicBrainz album id,
the dates, catalogue number. Two consequences, and they pull in opposite
directions:

  * **One folder can become several albums.** If 30 files in a folder carry
    MBID `d1dec788` and one carries none, that folder shows up twice. Measured
    here: 62 of 71 duplicate album rows are a single folder whose files disagree.
  * **Several folders can become one album.** `Thriller` and `Thriller 25` both
    tag ALBUM="Thriller", so Navidrome folds them into a single 31-track album.

So a "duplicate album" is never a duplicate *file* problem, and none of the
existing passes see it: `plan` compares tracks inside one album, `casing`
compares spellings, `merge` compares canonical target paths. Nothing compares
release identity. That is this module.

Four findings come out, and only the first is safe to fix mechanically:

  fill    one release, some files missing or under-specifying its identity
          tags. The minority adopts the majority's values. Journalled,
          reversible, moves nothing.
  split   two *genuinely different releases* sharing a folder. `Slipknot/`
          holds the 1999 Roadrunner album (25 tracks) and the 1989 Revelation
          7" EP by the other band of that name (6 tracks) -- different MBID,
          different release group, different album-artist id, different label.
          Merging those tags would destroy real information, so this is only
          ever reported.
  variant same album, different spelling of the name across folders
          ("M A N I A" / "Mania", "Let's" / "Let’s"). Fixing means agreeing a
          canonical ALBUM tag and renaming the folder -- a `casing`-style job,
          reported here with the evidence.
  edition base album plus a Deluxe/Remaster/Anniversary folder. These carry
          real duplicate *tracks*, so the report counts the title overlap and
          leaves the keep-or-merge decision to a human.

> **Do not read the answer out of navidrome.db.** It only shows the *effect*.
> The DB also keeps a soft-deleted row per moved file, and its album rows lag a
> rescan. Identity lives in the tags; that is what is read here.

> **The identity tags are not all in `reports/inventory.jsonl`.** The scan keeps
> a whitelist (`tags.TAG_KEYS`) that has `date` and `musicbrainz_albumid` but
> not `catalognumber`, `originaldate`, `label` or `releasetype` -- and those are
> exactly what tells a genuine second release apart from a file with a missing
> tag. So the inventory is used only to *pick candidate folders* (cheap), and
> the candidates are then re-read with mutagen (~150 dirs, not 3157).

Dry-run by default. `--commit` applies `fill` and nothing else.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

import mutagen
from mutagen.id3 import ID3, TDRC, TPUB, TXXX
from mutagen.mp4 import MP4

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS
from .plan import norm

JOURNAL = REPORTS / "album_journal.jsonl"
REPORT = REPORTS / "album_dupes.jsonl"

# --------------------------------------------------------------------------- #
# release identity
# --------------------------------------------------------------------------- #
# IDENTITY is what Navidrome splits an album row on, and the only thing `fill`
# is ever allowed to write.
IDENTITY = [
    "musicbrainz_albumid",
    "musicbrainz_releasegroupid",
    "date",
    "originaldate",
    "originalyear",
    "releasedate",
    "catalognumber",
]

# CORROBORATING is never written. It exists to answer one question: when two
# clusters in a folder disagree, are they one release tagged sloppily, or two
# real releases? A second pressing of the same record shares the label, the
# release group and the year; the 1989 Slipknot EP shares none of them.
CORROBORATING = [
    "label",
    "media",
    "releasetype",
    "releasecountry",
    "musicbrainz_albumartistid",
    "totaltracks",
]

# Per-format key for each concept: (vorbis key, id3 frame, mp4 atom).
# Vorbis comments are case-insensitive and stored lowercased; ID3 puts most of
# these in a TXXX whose *description* is the Picard spelling, which is not the
# vorbis spelling -- "MusicBrainz Album Id", not "musicbrainz_albumid".
_KEYMAP = {
    "musicbrainz_albumid": ("musicbrainz_albumid", "TXXX:MusicBrainz Album Id", "MusicBrainz Album Id"),
    "musicbrainz_releasegroupid": (
        "musicbrainz_releasegroupid",
        "TXXX:MusicBrainz Release Group Id",
        "MusicBrainz Release Group Id",
    ),
    "musicbrainz_albumartistid": (
        "musicbrainz_albumartistid",
        "TXXX:MusicBrainz Album Artist Id",
        "MusicBrainz Album Artist Id",
    ),
    "date": ("date", "TDRC", "\xa9day"),
    "originaldate": ("originaldate", "TXXX:originaldate", "originaldate"),
    "originalyear": ("originalyear", "TXXX:originalyear", "originalyear"),
    "releasedate": ("releasedate", "TXXX:releasedate", "releasedate"),
    "catalognumber": ("catalognumber", "TXXX:CATALOGNUMBER", "CATALOGNUMBER"),
    "label": ("label", "TPUB", "LABEL"),
    "media": ("media", "TXXX:MEDIA", "MEDIA"),
    "releasetype": ("releasetype", "TXXX:MusicBrainz Album Type", "MusicBrainz Album Type"),
    "releasecountry": ("releasecountry", "TXXX:MusicBrainz Album Release Country", "MusicBrainz Album Release Country"),
    "totaltracks": ("totaltracks", None, None),
}

# Vorbis spells this one two ways depending on the tagger. They are genuine
# synonyms, so reading either is correct.
#
# RELEASEDATE deliberately does *not* fall back to DATE, nor ORIGINALYEAR to
# ORIGINALDATE, even though a reader could. A fallback invents a value that is
# not in the file, and the consensus is computed per file: filling DATE on one
# track then silently changed what RELEASEDATE *read* as on that track, so a
# second pass found new work every time and the folder never converged. An
# absent tag reads as absent.
_VORBIS_ALIASES = {
    "totaltracks": ["totaltracks", "tracktotal"],
}

_ID3_NATIVE = {"date": ("TDRC", TDRC), "label": ("TPUB", TPUB)}


def read_release_tags(path: str) -> dict:
    """Read every identity + corroborating tag from one file. Absent -> ''."""
    try:
        mf = mutagen.File(path)
    except Exception:  # noqa: BLE001 -- an unreadable file is just evidence-free
        return {}
    if mf is None or mf.tags is None:
        return {}
    tags = mf.tags
    out = {}
    for concept in IDENTITY + CORROBORATING:
        vorbis, id3, mp4 = _KEYMAP[concept]
        val = ""
        if isinstance(tags, ID3):
            if id3:
                fr = tags.get(id3)
                if fr is not None and getattr(fr, "text", None):
                    val = str(fr.text[0])
        elif isinstance(mf, MP4):
            if mp4:
                atom = mp4 if mp4.startswith("\xa9") else f"----:com.apple.iTunes:{mp4}"
                if atom in tags and tags[atom]:
                    raw = tags[atom][0]
                    val = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        else:
            for key in _VORBIS_ALIASES.get(concept, [vorbis]):
                if key in tags and tags[key]:
                    val = str(tags[key][0])
                    break
        out[concept] = val.strip()
    return out


def write_release_tags(path: str, want: dict) -> tuple[dict, str | None]:
    """Set `want` (concept -> value) on one file. Returns (before, error)."""
    for concept in want:
        if concept not in IDENTITY:
            return {}, f"refusing to write non-identity tag {concept!r}"
    try:
        mf = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001
        return {}, f"open: {type(exc).__name__}: {exc}"
    if mf is None:
        return {}, "unrecognised format"
    try:
        if mf.tags is None:
            mf.add_tags()
    except Exception as exc:  # noqa: BLE001
        return {}, f"add_tags: {type(exc).__name__}: {exc}"

    before = {c: v for c, v in read_release_tags(path).items() if c in want}
    tags = mf.tags
    try:
        if isinstance(tags, ID3):
            for concept, value in want.items():
                if concept in _ID3_NATIVE:
                    name, cls = _ID3_NATIVE[concept]
                    tags.setall(name, [cls(encoding=3, text=[value])])
                else:
                    frame = _KEYMAP[concept][1]
                    desc = frame.split(":", 1)[1]
                    tags.setall(frame, [TXXX(encoding=3, desc=desc, text=[value])])
            mf.save(v2_version=4)
        elif isinstance(mf, MP4):
            for concept, value in want.items():
                atom = _KEYMAP[concept][2]
                if atom.startswith("\xa9"):
                    tags[atom] = [value]
                else:
                    tags[f"----:com.apple.iTunes:{atom}"] = [value.encode("utf-8")]
            mf.save()
        else:
            for concept, value in want.items():
                # Vorbis keys are matched case-insensitively but stored as
                # written; assigning the canonical lowercase spelling while an
                # uppercase one survives leaves the file with both. Drop every
                # case variant of *this key only*.
                #
                # Never touch the aliases. `_VORBIS_ALIASES` is a read-side
                # fallback chain -- RELEASEDATE falls back to DATE when absent
                # -- not a set of equivalent names. Deleting them on write made
                # writing RELEASEDATE erase DATE, which is the exact field the
                # album split on in the first place.
                for existing in [k for k in tags.keys() if k.lower() == concept]:
                    del tags[existing]
                tags[concept] = [value]
            mf.save()
    except Exception as exc:  # noqa: BLE001
        return before, f"save: {type(exc).__name__}: {exc}"
    return before, None


def _delete_release_tags(path: str, concepts) -> str | None:
    """Remove identity tags entirely -- what restore needs when the tag was
    absent before the fill. Vorbis lookup folds case, so the delete must too."""
    try:
        mf = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001
        return f"open: {type(exc).__name__}: {exc}"
    if mf is None or mf.tags is None:
        return None
    tags = mf.tags
    try:
        for concept in concepts:
            if isinstance(tags, ID3):
                tags.delall(_ID3_NATIVE[concept][0] if concept in _ID3_NATIVE else _KEYMAP[concept][1])
            elif isinstance(mf, MP4):
                atom = _KEYMAP[concept][2]
                atom = atom if atom.startswith("\xa9") else f"----:com.apple.iTunes:{atom}"
                tags.pop(atom, None)
            else:
                # Only this key. Following the read-side alias chain here would
                # delete DATE while restoring RELEASEDATE.
                for existing in [k for k in list(tags.keys()) if k.lower() == concept]:
                    del tags[existing]
        mf.save(v2_version=4) if isinstance(tags, ID3) else mf.save()
    except Exception as exc:  # noqa: BLE001
        return f"save: {type(exc).__name__}: {exc}"
    return None


# --------------------------------------------------------------------------- #
# name folding and edition suffixes
# --------------------------------------------------------------------------- #
def fold(s: str) -> str:
    """Case/accent/punctuation-insensitive album or artist key.

    Keeps every alphanumeric, including CJK and Cyrillic. Stripping to ASCII
    folds every Japanese album name onto the empty string, which reports the
    whole J-pop shelf as one duplicate group.
    """
    s = unicodedata.normalize("NFKD", s or "").casefold()
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'")
    return "".join(c for c in s if c.isalnum())


_EDITION_RE = re.compile(
    r"""\s*[\(\[\-–—]?\s*(?:
          deluxe(?:\s+edition)?
        | expanded(?:\s+edition)?
        | (?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?
        | \d+(?:st|nd|rd|th)\s+anniversary(?:\s+edition)?
        | (?:\d+\s+year\s+)?anniversary(?:\s+edition)?
        | \d\.0
        | \d+\s+year\s+anniversary\s+edition
        | special\s+edition
        | complete\s+edition
        | collector'?s\s+edition
        | bonus\s+track\s+version
        | reissue
        | explicit | clean
        | the\s+paradise\s+edition
        )\s*[\)\]]?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def base_album(name: str) -> str:
    """Album name with a trailing edition qualifier removed, repeatedly.

    Only *trailing* and only from a closed list. A greedy version that dropped
    any bracketed tail would collapse "You Want It Darker (Paul Kalkbrenner
    Remix)" onto the original -- the same mistake `plan.norm` is written to
    avoid.
    """
    prev = None
    out = name or ""
    while prev != out:
        prev = out
        out = _EDITION_RE.sub("", out).strip(" -–—,")
    return out or (name or "")


# --------------------------------------------------------------------------- #
# date handling
# --------------------------------------------------------------------------- #
def _year(value: str) -> str:
    m = re.match(r"\s*(\d{4})", value or "")
    return m.group(1) if m else ""


def plausible_date(value: str) -> bool:
    """Is this a date a record could have been released on?

    12 of the 16 files in `Michael Jackson/Invincible` carry `0001-01-01` --
    what a tagger writes when it has no date and the field is mandatory. Left
    to count as a real year it looks exactly like a second pressing, and the
    album splits in two on nothing at all.
    """
    year = _year(value)
    return bool(year) and 1900 <= int(year) <= 2100


def dates_compatible(a: str, b: str) -> bool:
    """`2020` and `2020-03-27` describe one release; `1989` and `2009` do not.

    A date that is not plausible contradicts nothing -- it is missing data
    wearing a number.
    """
    if not a or not b or not plausible_date(a) or not plausible_date(b):
        return True
    return _year(a) == _year(b)


def more_specific(a: str, b: str) -> str:
    """The date that says more, given the two are compatible."""
    if plausible_date(a) != plausible_date(b):
        return a if plausible_date(a) else b
    return a if len(a or "") >= len(b or "") else b


# --------------------------------------------------------------------------- #
# per-folder classification
# --------------------------------------------------------------------------- #
def _evidence(groups: dict) -> int:
    """How many independent fields say these clusters are different releases.

    A second pressing of one record keeps its release group, label, album-artist
    id, type and track count; only the album id and the pressing date move. So
    one differing field is noise and two is a different release. Empty values
    never count -- an absent label is not evidence of anything.
    """
    evidence = 0
    for field in (
        "musicbrainz_releasegroupid",
        "label",
        "musicbrainz_albumartistid",
        "releasetype",
        "totaltracks",
        "media",
    ):
        seen = {g[0].get(field, "") for g in groups.values()}
        if len(seen) > 1 and "" not in seen:
            evidence += 1
    years = {_year(g[0].get("date", "")) for g in groups.values()}
    if len(years) > 1 and "" not in years:
        evidence += 1
    return evidence


def classify_folder(files: dict) -> dict:
    """`files` maps relative path -> release tag dict. Returns a verdict.

    verdict is one of:
      clean  -- one coherent release
      fill   -- one release; some files under-specify it. `fills` says what to
                write where.
      split  -- two or more real releases sharing a folder. Reported only.
      check  -- disagreement that is neither obviously one release nor two.
    """
    if len(files) < 2:
        return {"verdict": "clean", "fills": {}}

    mbids = collections.Counter(t.get("musicbrainz_albumid", "") for t in files.values())
    distinct = [m for m in mbids if m]

    if len(distinct) > 1:
        # Two MBIDs. Either one release tagged from two sources, or two
        # releases. Corroboration decides: a second pressing keeps the label,
        # the release group and the year.
        groups = collections.defaultdict(list)
        for path, tags in files.items():
            if tags.get("musicbrainz_albumid"):
                groups[tags["musicbrainz_albumid"]].append(tags)
        if _evidence(groups) >= 2:
            return {"verdict": "split", "fills": {}, "releases": {m: len(g) for m, g in groups.items()}}
        # One release, two ids -- a second pressing, or two taggers. The id the
        # most files carry wins, and the rest of the identity is still decided
        # field by field across the whole folder rather than copied wholesale
        # off one file.
        winner = max((m for m in distinct), key=lambda m: (mbids[m], m))
        return _fills_towards(
            files, _consensus(files, anchor_mbid=winner), "variant-mbid", allow_mbid=mbid_propagation_ok(files, winner)
        )

    anchor_mbid = distinct[0] if distinct else None
    canon = _consensus(files, anchor_mbid=anchor_mbid)
    allow_mbid = not anchor_mbid or mbid_propagation_ok(files, anchor_mbid)

    # A year that contradicts the folder's release is the one signal that
    # survives when the odd file carries no MusicBrainz id at all -- which is
    # the usual shape, because the odd file came from somewhere that does not
    # tag ids. `Nirvana/In Utero` holds 41 files of the 2013 Geffen Records
    # anniversary edition and 2 of the 1993 Geffen original.
    years = collections.defaultdict(list)
    for tags in files.values():
        if plausible_date(tags.get("date", "")):
            years[_year(tags["date"])].append(tags)
    # If every file already agrees on a MusicBrainz album id they are one
    # release, full stop; a year that disagrees is a bad tag to fill, not
    # evidence of a second pressing.
    unanimous = len(distinct) == 1 and mbids[distinct[0]] == len(files)
    if len(years) > 1 and not unanimous:
        if _evidence(years) >= 1:
            return {
                "verdict": "split",
                "fills": {},
                "reason": "different years and a corroborating label/type/track count",
                "releases": {y: len(g) for y, g in years.items()},
            }
        return {
            "verdict": "check",
            "fills": {},
            "reason": "the dates disagree on the year with nothing else to corroborate",
            "releases": {y: len(g) for y, g in years.items()},
        }
    return _fills_towards(files, canon, "missing-identity", allow_mbid=allow_mbid)


def _consensus(files: dict, anchor_mbid: str | None = None) -> dict:
    """The identity the folder as a whole asserts.

    Anchored to the MBID-bearing files when there are any: a file that carries
    a MusicBrainz id was tagged from MusicBrainz and its dates come from the
    same place, so it is better evidence than a majority vote across files a
    scraper touched.
    """
    anchor = [t for t in files.values() if anchor_mbid and t.get("musicbrainz_albumid") == anchor_mbid]
    canon = {}
    for concept in IDENTITY:
        counts = collections.Counter(t[concept] for t in files.values() if t.get(concept))
        if not counts:
            canon[concept] = ""
            continue
        if concept in ("date", "originaldate", "originalyear", "releasedate"):
            # Dates are not a popularity contest: "2020" and "2020-03-27" are
            # the same release stated with different precision, and the precise
            # one is the answer even if only one file carries it.
            preferred = [t[concept] for t in anchor if t.get(concept)] or list(counts)
            best = preferred[0]
            for v in list(counts):
                if dates_compatible(best, v):
                    best = more_specific(best, v)
            # ORIGINALYEAR is a year by definition, but the vorbis reader falls
            # back to ORIGINALDATE when it is absent, so the winner can be a
            # full date. Truncate rather than write a date into a year field.
            canon[concept] = _year(best) if concept == "originalyear" else best
        else:
            # Frequency across the whole folder, with the MusicBrainz-tagged
            # cluster breaking ties: a value one file asserts should not
            # overwrite the same field on ten others.
            anchor_vals = {t[concept] for t in anchor if t.get(concept)}
            canon[concept] = max(counts, key=lambda v: (counts[v], v in anchor_vals, v))
    return canon


_MBID_FIELDS = ("musicbrainz_albumid", "musicbrainz_releasegroupid")


def mbid_propagation_ok(files: dict, winner: str) -> bool:
    """May a MusicBrainz album id be copied onto the files that lack one?

    Writing an album id claims *every file in the folder is that release*, and
    in a big folder a handful of tagged tracks are outliers rather than the
    folder's identity. `Juice WRLD/Unreleased` is a 1160-track leak dump with
    exactly three properly-tagged singles in it; without this guard the
    consensus picked one of the three and proposed stamping it onto the other
    1159 files.

    Two refusals, both about the claim being implausible on its face:
      * the tagged files are a rounding error of a large folder, or
      * the winning release is a *single* and the folder is clearly an album.

    Deliberately permissive for small folders: one MusicBrainz-tagged track
    among eleven is good evidence for the other ten, and that is the common
    and correct case (`Conclusion/Mohakashchari`).
    """
    tagged = [t for t in files.values() if t.get("musicbrainz_albumid")]
    if len(files) >= 20 and len(tagged) < 0.1 * len(files):
        return False
    types = {t.get("releasetype", "").lower() for t in tagged if t.get("musicbrainz_albumid") == winner}
    if types == {"single"} and len(files) > 5:
        return False
    return True


def _fills_towards(files: dict, canon: dict, kind: str, *, allow_mbid: bool = True) -> dict:
    fills = {}
    for path, tags in files.items():
        want = {}
        for concept in IDENTITY:
            # When the folder is not plausibly one release, its MusicBrainz ids
            # are left alone in *both* directions. Skipping only the empty ones
            # still unified the three distinct singles sitting in
            # `Juice WRLD/Unreleased` onto whichever id happened to win.
            if concept in _MBID_FIELDS and not allow_mbid:
                continue
            target = canon.get(concept, "")
            if not target or tags.get(concept, "") == target:
                continue
            if concept in ("date", "originaldate", "releasedate"):
                # Never overwrite a date that says *more* than the consensus.
                if tags.get(concept) and more_specific(tags[concept], target) == tags[concept]:
                    continue
            want[concept] = target
        if want:
            fills[path] = want
    # `fill_kind`, not `kind`: the finding dict is keyed on `kind` for the
    # verdict, and reusing the name here silently overwrote every "fill" with
    # "missing-identity", so --commit found nothing to do.
    return {"verdict": "fill" if fills else "clean", "fills": fills, "fill_kind": kind}


# --------------------------------------------------------------------------- #
# cross-folder classification
# --------------------------------------------------------------------------- #
def cross_folder_groups(by_dir: dict) -> list:
    """Folders that Navidrome will show as (or fold into) one album.

    Grouped on album artist + the album name with its edition suffix removed,
    so `Slipknot` and `Slipknot (10th Anniversary Edition)` land together and
    the report can count how many tracks they actually share.
    """
    groups = collections.defaultdict(list)
    for rel_dir, rows in by_dir.items():
        albums = collections.Counter((r["tags"].get("album") or [""])[0] for r in rows if r.get("tags"))
        artists = collections.Counter(v for r in rows for v in (r["tags"].get("albumartist") or []))
        if not albums:
            continue
        album = albums.most_common(1)[0][0]
        artist = artists.most_common(1)[0][0] if artists else ""
        if not fold(album):
            continue
        groups[(fold(artist), fold(base_album(album)))].append(
            {
                "dir": rel_dir,
                "album": album,
                "albumartist": artist,
                "tracks": len(rows),
                "titles": {norm((r["tags"].get("title") or [""])[0]) for r in rows},
            }
        )

    findings = []
    for (_artist_key, _base_key), members in groups.items():
        if len(members) < 2:
            continue
        literal = {m["album"] for m in members}
        folded = {fold(m["album"]) for m in members}
        if len(folded) == 1:
            # Identical album tag in two folders: Navidrome shows one album
            # spanning both. Sometimes right (a 2-disc rip), usually a folder
            # that was never retagged -- `Crystal Castles (II)` still tagged
            # "Crystal Castles".
            kind = "same-tag-multi-folder" if len(literal) == 1 else "variant"
        else:
            kind = "edition"
        overlap = collections.Counter()
        for m in members:
            for t in m["titles"]:
                if t:
                    overlap[t] += 1
        shared = sum(1 for t, n in overlap.items() if n > 1)
        findings.append(
            {
                "kind": kind,
                "albumartist": members[0]["albumartist"],
                "members": [{k: m[k] for k in ("dir", "album", "tracks")} for m in members],
                "shared_titles": shared,
            }
        )
    return findings


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def load_inventory(path: str) -> dict:
    by_dir = collections.defaultdict(list)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rec = json.loads(line)
                by_dir[os.path.dirname(rec["path"])].append(rec)
    return by_dir


def candidate_dirs(by_dir: dict, *, everything: bool = False) -> list:
    """Folders worth re-reading with mutagen.

    The inventory carries only `date` and `musicbrainz_albumid` of the identity
    set. Nominating on those two is fast (94 folders, not 3157) and catches the
    common shape, but it is **not** sufficient, and assuming it was left 10
    duplicate albums standing after the first commit:

        Barney Sku/DHK   3 tracks + 15 tracks, one album
        same date, same (absent) MusicBrainz id -- split purely on RELEASEDATE,
        which the inventory does not carry, so the folder was never read.

    `--all` reads every multi-file folder instead. That is ~11.6k mutagen opens
    over FUSE, some 20 minutes, and it is the honest default when the question
    is "which albums are duplicated" rather than "did my last batch land".
    """
    out = []
    for rel_dir, rows in by_dir.items():
        if len(rows) < 2:
            continue
        if everything:
            out.append(rel_dir)
            continue
        for concept in ("musicbrainz_albumid", "date"):
            values = {tuple(r["tags"].get(concept) or ()) for r in rows if r.get("tags")}
            if len(values) > 1:
                out.append(rel_dir)
                break
    return sorted(out)


def read_dirs(root: str, dirs: list, workers: int) -> dict:
    """Read release tags for every audio file in each folder, in parallel.

    FUSE round-trip latency dominates, exactly as in `scan.walk`, so this is
    thread-pooled rather than serial.
    """

    def one(rel_dir):
        abs_dir = os.path.join(root, rel_dir)
        files = {}
        try:
            names = sorted(os.listdir(abs_dir))
        except OSError:
            return rel_dir, files
        for name in names:
            if os.path.splitext(name)[1].lower() not in AUDIO_EXTS:
                continue
            rel = os.path.join(rel_dir, name)
            tags = read_release_tags(os.path.join(root, rel))
            if tags:
                files[rel] = tags
        return rel_dir, files

    result = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rel_dir, files in pool.map(one, dirs):
            result[rel_dir] = files
    return result


def analyse(inventory: str, root: str, workers: int, cache: str | None = None, everything: bool = False) -> list:
    by_dir = load_inventory(inventory)
    cands = candidate_dirs(by_dir, everything=everything)
    print(f"[albums] {len(by_dir)} folders in inventory, {len(cands)} nominated for a deep tag read")
    # The deep read is ~2 minutes of FUSE round-trips for ~800 files. Cache it,
    # so re-reading the report or re-running after a rule change is instant.
    if cache and os.path.exists(cache):
        with open(cache, encoding="utf-8") as fh:
            deep = json.load(fh)
        print(
            f"[albums] release tags from cache {cache} "
            f"({sum(len(v) for v in deep.values())} files) -- delete it to re-read"
        )
    else:
        t0 = time.time()
        deep = read_dirs(root, cands, workers)
        print(f"[albums] read release tags for {sum(len(v) for v in deep.values())} files in {time.time() - t0:.1f}s")
        if cache:
            with open(cache, "w", encoding="utf-8") as fh:
                json.dump(deep, fh, ensure_ascii=False)

    findings = []
    for rel_dir in cands:
        files = deep.get(rel_dir) or {}
        verdict = classify_folder(files)
        if verdict["verdict"] == "clean":
            continue
        findings.append(
            {
                "kind": verdict["verdict"],
                "dir": rel_dir,
                "files": len(files),
                **{k: v for k, v in verdict.items() if k != "verdict"},
            }
        )
    findings.extend(cross_folder_groups(by_dir))
    return findings


def report(findings: list, out_path) -> None:
    with open(out_path, "w", encoding="utf-8") as fh:
        for f in findings:
            fh.write(json.dumps(f, ensure_ascii=False, default=list) + "\n")
    counts = collections.Counter(f["kind"] for f in findings)
    print(f"[albums] wrote {len(findings)} findings -> {out_path}")
    for kind, n in counts.most_common():
        print(f"    {kind:24} {n}")
    tracks = sum(len(f.get("fills") or {}) for f in findings if f["kind"] == "fill")
    print(f"    {'(files fill would write)':24} {tracks}")


def apply_fills(findings: list, root: str, *, commit: bool) -> int:
    fills = [(f["dir"], f["fills"]) for f in findings if f["kind"] == "fill"]
    total = sum(len(v) for _, v in fills)
    print(f"[albums] {'COMMIT' if commit else 'DRY-RUN'}: {total} files in {len(fills)} folders")
    ok = failed = 0
    jf = open(JOURNAL, "a", encoding="utf-8") if commit else None
    try:
        for rel_dir, per_file in fills:
            for rel, want in sorted(per_file.items()):
                if not commit:
                    print(f"  {rel}")
                    for concept, value in sorted(want.items()):
                        print(f"      {concept} -> {value!r}")
                    ok += 1
                    continue
                before, err = write_release_tags(os.path.join(root, rel), want)
                entry = {"ts": time.time(), "path": rel, "dir": rel_dir, "before": before, "after": want, "error": err}
                jf.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if err:
                    failed += 1
                    print(f"  ERROR {rel}: {err}")
                else:
                    ok += 1
    finally:
        if jf:
            jf.close()
    print(f"[albums] {ok} {'written' if commit else 'would change'}, {failed} failed")
    return 1 if failed else 0


def restore(journal_path, root: str, *, commit: bool) -> int:
    if not os.path.exists(journal_path):
        print(f"[albums] no journal at {journal_path}")
        return 1
    original = {}
    with open(journal_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            e = json.loads(line)
            if not e.get("error"):
                original.setdefault(e["path"], e["before"])

    print(f"[albums] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(original)} files")
    ok = failed = 0
    for rel, before in original.items():
        # A tag that was absent before the fill has to be deleted, not set to
        # the empty string -- an empty vorbis field is still a field, and
        # Navidrome would key on it.
        restore_set = {c: v for c, v in before.items() if v}
        drop = [c for c, v in before.items() if not v]
        if not commit:
            ok += 1
            continue
        path = os.path.join(root, rel)
        err = None
        if restore_set:
            _b, err = write_release_tags(path, restore_set)
        if not err and drop:
            err = _delete_release_tags(path, drop)
        if err:
            failed += 1
            print(f"  ERROR {rel}: {err}")
        else:
            ok += 1
    print(f"[albums] {ok} restored, {failed} failed")
    return 1 if failed else 0


def print_summary(findings: list) -> None:
    """The part a human reads. Splits and editions need a decision; fills do not."""
    fills = [f for f in findings if f["kind"] == "fill"]
    if fills:
        print(
            f"\n== One release, files under-specifying it -- `--commit` fixes these "
            f"({len(fills)} folders, {sum(len(f['fills']) for f in fills)} files)"
        )
        for f in fills[:60]:
            concepts = sorted({c for w in f["fills"].values() for c in w})
            print(f"  {f['dir']}  ({len(f['fills'])}/{f['files']} files) {f['fill_kind']}: {', '.join(concepts)}")

    for kind, header in (
        ("split", "Two real releases sharing one folder (report only)"),
        ("check", "Disagreement that is neither clearly one release nor two"),
        ("variant", "Same album, different spelling of the name"),
        ("same-tag-multi-folder", "Different folders carrying an identical ALBUM tag"),
        ("edition", "Base album beside a deluxe/remaster/anniversary edition"),
    ):
        rows = [f for f in findings if f["kind"] == kind]
        if not rows:
            continue
        print(f"\n== {header} ({len(rows)})")
        for f in rows[:60]:
            if "members" in f:
                shared = f.get("shared_titles", 0)
                print(f"  {f['albumartist']}  (shared titles: {shared})")
                for m in f["members"]:
                    print(f"      {m['tracks']:3} tracks  {m['dir']}   ALBUM={m['album']!r}")
            else:
                extra = f.get("releases") or f.get("reason") or ""
                print(f"  {f['dir']}  ({f['files']} files)  {extra}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find and repair duplicate albums (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument(
        "--tag-cache",
        default=str(REPORTS / "album_release_tags.json"),
        help="cache of the deep tag read; delete it to force a re-read",
    )
    ap.add_argument(
        "--workers", type=int, default=16, help="parallel directory readers; FUSE latency, not CPU, is the limit"
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="deep-read every multi-file folder, not just the ones the "
        "inventory nominates. Slower (~20 min) and complete.",
    )
    ap.add_argument("--commit", action="store_true", help="apply the `fill` findings. Nothing else is ever applied.")
    ap.add_argument("--restore", action="store_true", help="reverse the journal")
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.journal, args.root, commit=args.commit)

    findings = analyse(args.inventory, args.root, args.workers, args.tag_cache, everything=args.all)
    report(findings, args.report)
    print_summary(findings)
    return apply_fills(findings, args.root, commit=args.commit)


if __name__ == "__main__":
    raise SystemExit(main())

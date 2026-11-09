# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Raw tag reading and multi-value artist normalisation.

Navidrome's own database is *not* a reliable source here: it collapses a
multi-valued ARTIST tag down to a single display string, so a file tagged
ARTIST=["akiaura", "Olya Holiday"] and one tagged ARTIST="akiaura • Olya Holiday"
look identical in the DB. Everything in this module works off the real tags.
"""

from __future__ import annotations

import re
import unicodedata

import mutagen

# Keys to try, in priority order, per tag concept. Covers Vorbis (FLAC/Opus),
# ID3 (MP3), MP4 atoms (m4a) and the ASF/WMA names.
TAG_KEYS = {
    "artist": ["artist", "ARTIST", "TPE1", "\xa9ART", "Author"],
    # Sort names are alternate spellings of the artists already credited --
    # "ARTISTSORT=Marasy feat. nomico" beside "ARTIST=まらしぃ feat. のみこ" is the
    # only thing in the file that ties a romanisation to its CJK original.
    # augment_with_guests uses them to refuse a guest that is already credited
    # under a different script.
    "artistsort": ["artistsort", "ARTISTSORT", "TSOP", "soar", "WM/ArtistSortOrder"],
    "albumartistsort": ["albumartistsort", "ALBUMARTISTSORT", "TSO2", "soaa", "WM/AlbumArtistSortOrder"],
    "albumartist": [
        "albumartist",
        "ALBUMARTIST",
        "album artist",
        "ALBUM ARTIST",
        "TPE2",
        "aART",
        "WM/AlbumArtist",
        "ensemble",
    ],
    "album": ["album", "ALBUM", "TALB", "\xa9alb", "WM/AlbumTitle"],
    "title": ["title", "TITLE", "TIT2", "\xa9nam", "Title"],
    "tracknumber": ["tracknumber", "TRACKNUMBER", "TRCK", "trkn", "WM/TrackNumber"],
    "discnumber": ["discnumber", "DISCNUMBER", "TPOS", "disk", "WM/PartOfSet"],
    "date": ["date", "DATE", "year", "YEAR", "TDRC", "TYER", "\xa9day", "WM/Year"],
    "genre": ["genre", "GENRE", "TCON", "\xa9gen", "WM/Genre"],
    "musicbrainz_trackid": [
        "musicbrainz_trackid",
        "MUSICBRAINZ_TRACKID",
        "MUSICBRAINZ_RELEASETRACKID",
        "musicbrainz_releasetrackid",
    ],
    "musicbrainz_albumid": ["musicbrainz_albumid", "MUSICBRAINZ_ALBUMID"],
    "musicbrainz_artistid": ["musicbrainz_artistid", "MUSICBRAINZ_ARTISTID"],
    "isrc": ["isrc", "ISRC", "TSRC"],
    "lyrics": ["lyrics", "LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"],
}

# Separators fall into two classes, and conflating them corrupts data.
#
# STRONG separators only ever appear when a tagger packed several artists into
# one string. No real artist name contains them, so we split on sight.
# Measured in this library: "•" 587, ";" 3, "/" 1.
_STRONG_RE = re.compile(
    r"""\s*(?:
          ;                    # semicolon
        | • | · | ・ # bullet, middot, katakana middot
        | ／                    # fullwidth solidus
        | \s/\s                # slash surrounded by spaces
        )\s*""",
    re.VERBOSE,
)

# ...with one exception. An interpunct with NO space around it is the standard
# way to write a transliterated personal name in Chinese and Japanese --
# "希林娜依•高" is one singer, not 希林娜依 and 高, exactly as "Jean-Luc" is one
# name. A tagger packing two artists writes " • " with spaces (the measured 587
# in this library all do). So an unspaced interpunct is only split when the
# vocabulary independently confirms every fragment is a standalone artist.
_TIGHT_INTERPUNCT_RE = re.compile(r"(?<!\s)[•·・](?!\s)")

# WEAK separators are ambiguous: they occur inside genuine artist names just as
# often as they separate artists. Splitting on them blindly destroys data --
# "Malcolm X" -> ["Malcolm"], "X Ambassadors" -> ["Ambassadors"],
# "Now, Now" -> ["Now"], "Tyler, The Creator" -> ["Tyler", "The Creator"].
# These are only applied when every resulting part is corroborated by the
# library's own vocabulary of known standalone artists (see split_artists).
_WEAK_RE = re.compile(
    r"""\s*(?:
          ,                    # comma
        | ＆ | &           # fullwidth ampersand, ampersand
        | \+                   # plus
        | \bx\b                # collab "x"
        | \bvs\.?\b            # versus
        | \band\b              # the word "and"
        )\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# "feat." and friends introduce guest artists rather than separating equals.
# Two shapes, because the markers differ in how safe they are:
#
#   * Bracketed "(feat. X)", "[ft. X]", "(with X)", "(w/ X)" -- unambiguous, the
#     bracket makes intent clear, so "with"/"w" are allowed here.
#   * Bare "feat."/"ft."/"featuring" running to the end -- only the strong
#     markers. Bare "with"/"w" are NOT matched: they wreck ordinary titles like
#     "Boy With Luv" -> "Boy" and "break up with your girlfriend" -> "break up".
FEAT_RE = re.compile(
    r"""\s*(?:
          [\(\[\{]\s*(?:feat|ft|featuring|with|w)\b\.?/?\s+(?P<who1>[^\)\]\}]*?)\s*[\)\]\}]
        | \b(?:feat|ft|featuring)\b\.?\s+(?P<who2>.*?)\s*(?=[\(\[\{]|$)
        )\s*""",
    re.IGNORECASE | re.VERBOSE,
)

# Artist names that legitimately contain a separator character and must never
# be split. Extend this list as the library grows.
DO_NOT_SPLIT = {
    "ac/dc",
    "acdc",
    "above & beyond",
    "simon & garfunkel",
    "hall & oates",
    "florence + the machine",
    "mumford & sons",
    "earth, wind & fire",
    "blood, sweat & tears",
    "crosby, stills, nash & young",
    "crosby, stills & nash",
    "kool & the gang",
    "huey lewis & the news",
    "derek & the dominos",
    "emerson, lake & palmer",
    "peter, paul and mary",
    "tom petty and the heartbreakers",
    "iron & wine",
    "angus & julia stone",
    "sam & dave",
    "ike & tina turner",
    "captain & tennille",
    "hootie & the blowfish",
    "echo & the bunnymen",
    "siouxsie and the banshees",
    "kc and the sunshine band",
    "sly and the family stone",
    "booker t. & the m.g.'s",
    "big brother and the holding company",
    "nick cave & the bad seeds",
    "edward sharpe & the magnetic zeros",
    "the mamas & the papas",
    "jay & the americans",
    "panic! at the disco",
    "godspeed you! black emperor",
    "sam & max",
    "you me & everyone we know",
    "matt & kim",
    "chad & jeremy",
    "she & him",
    "gnarls barkley",
    "m|o|o|n",
    "!!!",
    "7!!",
    "3oh!3",
    "$not",
    "a$ap ferg",
    "a$ap rocky",
}


def _norm_key(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip()).lower()


def _values(tags, keys):
    """Return every value stored under the first matching key, as a flat list."""
    if not tags:
        return []
    for k in keys:
        try:
            if k not in tags:
                continue
            v = tags[k]
        except (TypeError, ValueError, KeyError):
            continue
        out = []
        if isinstance(v, (list, tuple)):
            items = v
        else:
            items = [v]
        for item in items:
            # ID3 frames, MP4FreeForm, ASF values all stringify sensibly.
            if hasattr(item, "text"):
                sub = item.text
                out.extend(str(x) for x in (sub if isinstance(sub, list) else [sub]))
            elif isinstance(item, tuple):  # MP4 trkn/disk -> (num, total)
                out.append(str(item[0]))
            elif isinstance(item, bytes):
                out.append(item.decode("utf-8", "replace"))
            else:
                out.append(str(item))
        out = [x.strip() for x in out if x and str(x).strip()]
        if out:
            return out
    return []


def read_tags(path) -> dict:
    """Read raw tags. Returns {} for files mutagen cannot parse."""
    try:
        mf = mutagen.File(str(path))
    except Exception as exc:  # noqa: BLE001 - want the message, not a traceback
        return {"_error": f"{type(exc).__name__}: {exc}"}
    if mf is None:
        return {"_error": "unrecognised audio format"}

    out = {}
    for concept, keys in TAG_KEYS.items():
        vals = _values(mf.tags, keys)
        if vals:
            out[concept] = vals

    info = getattr(mf, "info", None)
    if info is not None:
        for attr in ("length", "bitrate", "sample_rate", "channels", "bits_per_sample", "bitrate_mode"):
            v = getattr(info, attr, None)
            if v is not None:
                out[f"_{attr}"] = str(v) if not isinstance(v, (int, float)) else v

    # Embedded cover art presence, without pulling the bytes into memory.
    has_pic = False
    try:
        if getattr(mf, "pictures", None):
            has_pic = True
        elif mf.tags is not None:
            for k in mf.tags.keys():
                if str(k).startswith("APIC") or str(k) in ("covr", "WM/Picture"):
                    has_pic = True
                    break
    except Exception:  # noqa: BLE001
        pass
    out["_has_embedded_art"] = has_pic
    return out


def strip_feat(text: str, vocabulary=None) -> tuple[str, list[str]]:
    """Split "The Thrill (feat. Empire of the Sun)" -> ("The Thrill", ["Empire of the Sun"]).

    Returns the cleaned text and the guest artists that were removed. The
    vocabulary is threaded through so a guest list can itself be split: without
    it, "ft. A Boogie Wit da Hoodie & Fetty Wap" stays glued together as one
    bogus artist even when both names are known to the library.
    """
    if not text:
        return "", []
    guests: list[str] = []

    def _grab(m):
        who = (m.group("who1") or m.group("who2") or "").strip()
        if who:
            guests.extend(split_artists(who, vocabulary))
        return " "

    cleaned = FEAT_RE.sub(_grab, text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—,")
    return cleaned, guests


def _weak_split(text: str, vocabulary) -> list[str]:
    """Try to split on an ambiguous separator, but only on evidence.

    The split is accepted only when every resulting fragment is a name the
    library already knows as a standalone artist. Without that corroboration the
    string is left whole, because a wrong split silently loses part of a name and
    nothing downstream can detect it.
    """
    if not vocabulary:
        return [text]
    parts = [p.strip(" ,;&+·•-") for p in _WEAK_RE.split(text)]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return [text]
    if all(_norm_key(p) in vocabulary for p in parts):
        return parts
    return [text]


def _spaced_strong(text: str) -> bool:
    """True if the string carries a separator that is unambiguously a packer."""
    return bool(re.search(r";|／|\s[•·・/]\s", text))


def _interpunct_corroborated(text: str, vocabulary) -> bool:
    """Only split a tight interpunct when every fragment is a known artist."""
    if not vocabulary:
        return False
    parts = [p.strip() for p in _TIGHT_INTERPUNCT_RE.split(text)]
    parts = [p for p in parts if p]
    return len(parts) > 1 and all(_norm_key(p) in vocabulary for p in parts)


def split_artists(value, vocabulary=None) -> list[str]:
    """Turn a tag value into a list of individual artist names.

    Accepts either a real multi-valued tag (already a list, the reliable case)
    or a single packed string. Strong separators are always honoured; ambiguous
    ones need support from `vocabulary`, a set of normalised names known to
    stand alone elsewhere in the library. Build it with build_vocabulary().
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = [value]

    out: list[str] = []
    for item in raw:
        item = (item or "").strip()
        if not item:
            continue
        if _norm_key(item) in DO_NOT_SPLIT:
            out.append(item)
            continue
        # Pull guests out first so "A feat. B" yields both A and B.
        base, guests = strip_feat(item, vocabulary)
        # A name whose only separator is a tight interpunct is one name unless
        # the library says otherwise -- see _TIGHT_INTERPUNCT_RE.
        if _TIGHT_INTERPUNCT_RE.search(base) and not _spaced_strong(base):
            if not _interpunct_corroborated(base, vocabulary):
                out.append(base)
                out.extend(guests)
                continue
        for chunk in _STRONG_RE.split(base):
            chunk = chunk.strip(" ,;&·•-")
            if not chunk:
                continue
            if _norm_key(chunk) in DO_NOT_SPLIT:
                candidates = [chunk]
            else:
                candidates = _weak_split(chunk, vocabulary)
            out.extend(candidates)
        out.extend(guests)

    # Drop exact-duplicate names while preserving order.
    seen, uniq = set(), []
    for a in out:
        a = (a or "").strip()
        k = _norm_key(a)
        if k and k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq


def _harvest_split(value: str):
    """Split for vocabulary harvesting: strong separators, but never a tight
    interpunct (see build_vocabulary for why that would be circular)."""
    if _TIGHT_INTERPUNCT_RE.search(value or "") and not _spaced_strong(value or ""):
        return [value]
    return _STRONG_RE.split(value or "")


def build_vocabulary(records) -> set[str]:
    """Collect artist names the library treats as standalone.

    Only unambiguous evidence counts: a name that appears as its own element of
    a genuinely multi-valued tag, or as an entire artist tag containing no weak
    separator at all. Those are names we know exist on their own, so seeing one
    as a fragment of some other string is real evidence of a packed field.

    Harvesting deliberately does NOT break a tight interpunct. If it did, the
    single tag "希林娜依•高" would enter the vocabulary as both 希林娜依 and 高,
    and `split_artists` would then cite that as independent corroboration for
    splitting the very string it came from. Evidence a name produces about
    itself is not evidence.
    """
    vocab: set[str] = set()
    for rec in records:
        tags = rec.get("tags") or {}
        for key in ("artist", "albumartist"):
            vals = tags.get(key)
            if not vals:
                continue
            if isinstance(vals, str):
                vals = [vals]
            if len(vals) > 1:
                # A real multi-valued tag: every element stands alone.
                for v in vals:
                    for piece in _harvest_split(v):
                        piece = piece.strip()
                        if piece:
                            vocab.add(_norm_key(piece))
            else:
                v = (vals[0] or "").strip()
                if not v:
                    continue
                for piece in _harvest_split(v):
                    piece = piece.strip()
                    # Only trust it if nothing ambiguous is going on inside.
                    if piece and not _WEAK_RE.search(piece) and not FEAT_RE.search(piece):
                        vocab.add(_norm_key(piece))
    vocab.discard("")
    return vocab


# A guest whose text contains one of these is a production/version note, not a
# performer: "(with instrumental by Ghost Loft)", "(with SUGA of BTS)". The
# title still loses the parenthetical, but nothing bogus is added to the artist.
_NON_ARTIST_GUEST = re.compile(
    r"\b(?:instrumental|prod|produced|version|remix|edit|bootleg|mashup|mix|by|of"
    r"|intro|outro|interlude|skit|reprise|snippet|acapella|acappella)\b",
    re.IGNORECASE,
)
_GUEST_SPLIT = re.compile(r"\s*(?:&|,|;|/|\band\b|\bx\b)\s*", re.IGNORECASE)


def _artist_key(s: str) -> str:
    """Punctuation- and accent-insensitive key, so 'Lil' Wayne' == 'Lil Wayne'."""
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s.lower() if c.isalnum())


def _sort_name_keys(sort_names) -> set:
    """Artist keys named by ARTISTSORT / ALBUMARTISTSORT values.

    A sort tag is written from the ARTIST tag, so every name in it is a name of
    someone the file already credits -- usually the same string, sometimes a
    romanisation ("Marasy feat. nomico" for "まらしぃ feat. のみこ").

    Split only on the feature marker and the strong separators. Weak separators
    are not applied: "Beatles, The" and "Tyler, The Creator" are one name each,
    and splitting them would let an unrelated guest "Tyler" be discarded.
    """
    keys = set()
    for raw in sort_names or ():
        raw = str(raw or "").strip()
        if not raw:
            continue
        base, guests = strip_feat(raw)
        for name in [base, *guests]:
            for piece in _STRONG_RE.split(name):
                piece = piece.strip()
                if piece:
                    keys.add(_artist_key(piece))
    keys.discard("")
    return keys


def augment_with_guests(artists, guests, sort_names=()) -> list[str]:
    """Append featured artists that are not already credited, in order.

    Used when a "(feat. X)" is removed from a title: X must not vanish, so if it
    is not already credited it is added. Care is taken not to introduce junk:

      * a compound guest ("Lil' Wayne & Busta Rhymes") is split so each name is
        matched on its own, which also stops the whole string being re-added when
        both names are already present;
      * matching ignores punctuation and accents, so "Lil' Wayne" collapses onto
        an existing "Lil Wayne";
      * production/version notes ("instrumental by Ghost Loft") are dropped;
      * a guest named by the file's own sort tags is refused, because a sort tag
        only ever spells an artist the file already credits. "TITLE=Bad Apple!!
        feat. nomico" beside "ARTIST=まらしぃ feat. のみこ" and
        "ARTISTSORT=Marasy feat. nomico" is two artists, not three -- nomico is
        のみこ romanised, and nothing else in the file says so.
    """
    out = list(artists)
    have = {_artist_key(a) for a in out}
    sort_keys = _sort_name_keys(sort_names) - have
    for g in guests:
        g = (g or "").strip()
        if not g or _NON_ARTIST_GUEST.search(g):
            continue
        parts = [p.strip() for p in _GUEST_SPLIT.split(g) if p.strip()]
        for p in parts if len(parts) > 1 else [g]:
            k = _artist_key(p)
            if not k or k in have or k in sort_keys:
                continue
            if not _is_short_form_of_credited(p, out):
                out.append(p)
                have.add(k)
    return out


def _is_short_form_of_credited(guest, credited) -> bool:
    """True when the guest is a shorter way of naming someone already credited.

    A title's "(feat. Waka Flocka)" beside an ARTIST tag of "Waka Flocka Flame"
    is one person, and adding both credits the same artist twice and derives a
    filename naming them twice. Requires the guest to be at least two words: a
    single token is far too weak, since "Drake" must still be added alongside an
    unrelated "Drake Bell".
    """
    gset = _artist_tokens(guest)
    if len(gset) < 2:
        return False
    return any(gset < _artist_tokens(name) for name in credited)


def _artist_tokens(s: str) -> set:
    """Word tokens of a name, punctuation- and accent-insensitive.

    Distinct from `_artist_key`, which drops spaces as well and so collapses a
    name to one blob -- fine for equality, useless for asking whether one name
    is contained in another.
    """
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c if c.isalnum() else " " for c in s.lower())
    return set(s.split())


def join_artists(artists) -> str:
    """Human-facing join used for folder and file names.

    One artist  -> "X"
    Two         -> "X and Y"
    Three plus  -> "X, Y and Z"
    """
    artists = [a.strip() for a in artists if a and a.strip()]
    if not artists:
        return ""
    if len(artists) == 1:
        return artists[0]
    if len(artists) == 2:
        return f"{artists[0]} and {artists[1]}"
    return ", ".join(artists[:-1]) + f" and {artists[-1]}"

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Canonical path construction, tuned for Navidrome on an NTFS volume.

Target layout (matches what the library already mostly uses):

    <Album Artist>/<Album>/<Track Artist> - <Title>.<ext>
    <Album Artist>/<Album>/<Track Artist> - <Title>.lrc     (lyrics sidecar)
    <Album Artist>/<Album>/<Track Artist> - <Title>.jpg     (per-track art)
    <Album Artist>/<Album>/folder.jpg                        (album art)

The artist *folder* comes from Album Artist, so every track of an album lands in
one place even when individual tracks have guests. The track artist stays in the
filename. Track numbers are deliberately absent from filenames: Navidrome orders
by tag, and the existing library follows that convention.
"""

from __future__ import annotations

import os
import re
import unicodedata

from .settings import MAX_COMPONENT_LEN, NTFS_FORBIDDEN, NTFS_RESERVED_NAMES, NTFS_SUBSTITUTIONS
from .tags import augment_with_guests, join_artists, split_artists, strip_feat

# Control characters are illegal in NTFS names and invisible in reports.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WS_RE = re.compile(r"\s+")


def sanitize_component(name: str, *, substitute: bool = True) -> str:
    """Make one path component safe for NTFS without destroying information.

    The previous tooling replaced reserved characters with spaces, which turned
    "Re:Re:" into "Re Re " and "AC/DC" into "AC DC". We map them to Unicode
    look-alikes instead, so the name still reads correctly.
    """
    if not name:
        return ""
    s = unicodedata.normalize("NFC", str(name))
    s = _CTRL_RE.sub("", s)

    if substitute:
        for bad, good in NTFS_SUBSTITUTIONS.items():
            s = s.replace(bad, good)
    else:
        for bad in NTFS_FORBIDDEN:
            s = s.replace(bad, "")

    # Collapse the whitespace damage left behind by earlier passes.
    s = _WS_RE.sub(" ", s).strip()

    # NTFS silently drops trailing dots and spaces, which makes a path that
    # cannot be looked up by the name we think we wrote.
    s = s.rstrip(" .")
    if not s:
        return ""

    # Device names are unusable as components, even with an extension.
    if s.upper().split(".")[0] in NTFS_RESERVED_NAMES:
        s = f"_{s}"

    if len(s) > MAX_COMPONENT_LEN:
        s = s[:MAX_COMPONENT_LEN].rstrip(" .")
    return s


# Release-format tags that get appended to an album name and add no information
# once the album is in a library: "(single)", " - Single", "EP", "(EP)". Anchored
# to the end so "You Want It Darker (Paul Kalkbrenner Remix) (single)" loses only
# the trailing "(single)" and "Lesser Man (extended version) EP" keeps its
# qualifier. Applied repeatedly for the rare "... (single) EP".
_ALBUM_FORMAT_SUFFIX = re.compile(
    r"""\s*(?:
          [\(\[]\s*(?:single|ep)\s*[\)\]]   # (single) [EP]
        | [-–—]\s*(?:single|ep)             # - Single   — EP
        | \s(?:single|ep)                   # trailing bare " EP" / " Single"
        )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def strip_album_format(album: str) -> str:
    """Remove trailing (single)/EP release-format tags from an album name.

    Never returns empty: an album literally named "EP" or "Single" keeps it,
    because there is nothing else to fall back to.
    """
    prev = album
    while True:
        stripped = _ALBUM_FORMAT_SUFFIX.sub("", prev).strip(" -–—,")
        if stripped == prev or not stripped:
            return stripped if stripped else prev
        prev = stripped


def clean_album(album: str) -> str:
    """Album title with featured-artist and release-format suffixes removed.

    "The Thrill (feat. Empire of the Sun)"      -> "The Thrill"
    "Watercolor Eyes (single)"                  -> "Watercolor Eyes"
    "Vroom Vroom EP"                            -> "Vroom Vroom"
    "Lesser Man (extended version) EP"          -> "Lesser Man (extended version)"
    """
    if not album:
        return ""
    cleaned, _guests = strip_feat(album)
    # Trailing bracket junk left by the removal, e.g. "Title () [Radio Edit]".
    cleaned = re.sub(r"[\(\[\{]\s*[\)\]\}]", "", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—,")
    return strip_album_format(cleaned)


def clean_title(title: str, *, strip_features: bool = False) -> str:
    """Track title. Featured artists are preserved unless explicitly stripped,
    because for a single they are often part of the canonical title."""
    if not title:
        return ""
    if strip_features:
        title, _ = strip_feat(title)
    return _WS_RE.sub(" ", title).strip()


def album_artist_folder(tags: dict) -> tuple[str, list[str]]:
    """Folder name for the album artist, plus the parsed artist list.

    Falls back to the track artist when Album Artist is missing or empty.
    """
    artists = split_artists(tags.get("albumartist"))
    if not artists:
        artists = split_artists(tags.get("artist"))
    if not artists:
        return "Unknown Artist", []
    return join_artists(artists), artists


def track_artist_name(tags: dict) -> tuple[str, list[str]]:
    """Display name for the track artist, plus the parsed artist list."""
    artists = split_artists(tags.get("artist"))
    if not artists:
        artists = split_artists(tags.get("albumartist"))
    if not artists:
        return "Unknown Artist", []
    return join_artists(artists), artists


def _first(tags: dict, key: str) -> str:
    v = tags.get(key)
    if isinstance(v, (list, tuple)):
        return (v[0] if v else "") or ""
    return v or ""


def recover_from_path(rel_path: str) -> dict:
    """Reconstruct artist/album/title from a file's location.

    Some files carry no tags at all, yet sit at
    "<Artist>/<Album>/<Artist> - <Title>.flac" -- the metadata is right there in
    the path. Quarantining those would throw away real music over a missing tag,
    so the path is used as a fallback source.

    Only fields the caller is missing should be taken from here: a real tag is
    always better evidence than a filename.
    """
    parts = [p for p in rel_path.replace("\\", "/").split("/") if p]
    if not parts:
        return {}
    filename = os.path.splitext(parts[-1])[0]
    out = {}

    # "<Artist> - <Title>" is the convention this library already uses.
    if " - " in filename:
        artist_part, title_part = filename.split(" - ", 1)
        out["artist"] = [artist_part.strip()]
        out["title"] = [title_part.strip()]
    else:
        out["title"] = [filename.strip()]

    if len(parts) >= 2:
        out["album"] = [parts[-2].strip()]
    if len(parts) >= 3:
        out["albumartist"] = [parts[0].strip()]
    elif "artist" in out:
        out["albumartist"] = list(out["artist"])
    return out


def canonical_paths(rec: dict, *, vocabulary=None, strip_title_features: bool = True) -> dict:
    """Compute the target location for one scanned file.

    This is the single source of truth for how tags become a path. Pass the
    library `vocabulary` (from tags.build_vocabulary) so ambiguous artist splits
    are corroborated; without it, only unambiguous separators are split.

    Returns a dict with the individual components plus the assembled relative
    path, and a list of the reasons the current path is wrong (empty when the
    file is already where it belongs).
    """
    tags = dict(rec.get("tags") or {})
    ext = "." + (rec.get("path", "").rsplit(".", 1)[-1].lower())

    # Fill gaps from the path before deciding anything. A file with no title tag
    # is otherwise unnameable and would be quarantined despite being fine.
    recovered = []
    if not _first(tags, "title").strip():
        fallback = recover_from_path(rec.get("path", ""))
        for key in ("title", "artist", "album", "albumartist"):
            have = tags.get(key)
            have_first = (have[0] if isinstance(have, list) and have else have) or ""
            if not str(have_first).strip() and fallback.get(key):
                tags[key] = fallback[key]
                recovered.append(key)

    aa_list = split_artists(tags.get("albumartist"), vocabulary) or split_artists(tags.get("artist"), vocabulary)
    ta_base = split_artists(tags.get("artist"), vocabulary) or list(aa_list)
    album_raw = _first(tags, "album")
    title_raw = _first(tags, "title")

    album = clean_album(album_raw)

    # A featured artist named in the title must not be lost when the "(feat. X)"
    # is stripped: X is added to the TRACK artist if it is not already credited.
    # The album artist is left untouched -- a guest does not headline the album.
    title, guests = strip_feat(title_raw, vocabulary) if strip_title_features else (title_raw, [])
    title = _WS_RE.sub(" ", title or "").strip()
    sort_names = list(tags.get("artistsort") or []) + list(tags.get("albumartistsort") or [])
    ta_list = augment_with_guests(ta_base, guests, sort_names)
    guests_added = [g for g in ta_list if g not in ta_base]

    aa_display = join_artists(aa_list) or "Unknown Artist"
    ta_display = join_artists(ta_list) or "Unknown Artist"

    # A file with no usable title cannot be named from metadata; the caller
    # quarantines these rather than inventing something.
    if not title:
        title = ""
    if not album:
        album = "Unknown Album"

    artist_dir = sanitize_component(aa_display) or "Unknown Artist"
    album_dir = sanitize_component(album) or "Unknown Album"
    stem = sanitize_component(f"{ta_display} - {title}") if title else ""

    result = {
        "album_artist_display": aa_display,
        "album_artists": aa_list,
        "track_artist_display": ta_display,
        "track_artists": ta_list,
        "album": album,
        "album_raw": album_raw,
        "title": title,
        "title_raw": title_raw,
        "title_guests_added": guests_added,
        "artist_dir": artist_dir,
        "album_dir": album_dir,
        "stem": stem,
        "ext": ext,
        "rel_path": f"{artist_dir}/{album_dir}/{stem}{ext}" if stem else None,
        "recovered_from_path": recovered,
        "recovered_tags": {k: tags[k] for k in recovered} if recovered else {},
        "problems": [],
    }

    p = result["problems"]
    if recovered:
        p.append("recovered-from-path")
    orig = rec.get("tags") or {}
    if not _first(orig, "title").strip():
        p.append("missing-title-tag")
    if not _first(orig, "album").strip():
        p.append("missing-album-tag")
    if not split_artists(orig.get("artist")):
        p.append("missing-artist-tag")
    if not split_artists(orig.get("albumartist")):
        p.append("missing-albumartist-tag")
    if album_raw != album:
        p.append("album-has-feat")
    if title_raw.strip() and title_raw.strip() != title:
        p.append("title-has-feat")
    if guests_added:
        p.append("title-guest-added-to-artist")
    if any(ch in NTFS_FORBIDDEN for ch in f"{aa_display}{album}{title}"):
        p.append("tag-has-reserved-char")
    if len(aa_list) > 1:
        p.append("multi-artist-albumartist")
    return result


def sidecar_stems(stem: str) -> dict:
    """Sidecar filenames that travel with a track."""
    return {"lyrics": f"{stem}.lrc", "art": f"{stem}.jpg"}

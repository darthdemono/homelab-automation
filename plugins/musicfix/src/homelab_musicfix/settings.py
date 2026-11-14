# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Paths and name rules shared by every stage. `configure()` points them at the user's library for one run."""

from __future__ import annotations

from pathlib import Path

MUSIC_ROOT = Path("/music")
TMP_ROOT = Path("/music-tmp")
REPORTS = Path("reports")  # where inventory and caches live; becomes the plugin state folder
REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # the plugin folder, which holds docker/


def configure(music, tmp, reports) -> None:
    """Set the library, the staging and quarantine area, and the cache folder, and rebuild the quarantine paths."""
    global MUSIC_ROOT, TMP_ROOT, REPORTS, Q_DUPES, Q_JUNK, Q_BADTAGS, Q_EMPTY, Q_UNKNOWN, Q_LEFTOVERS, Q_ARTWORK
    MUSIC_ROOT, TMP_ROOT, REPORTS = Path(music), Path(tmp), Path(reports)
    Q_DUPES = TMP_ROOT / "quarantine/dupes"
    Q_JUNK = TMP_ROOT / "quarantine/junk"
    Q_BADTAGS = TMP_ROOT / "quarantine/badtags"
    Q_EMPTY = TMP_ROOT / "quarantine/empty-dirs"
    Q_UNKNOWN = TMP_ROOT / "quarantine/unknown"
    Q_LEFTOVERS = TMP_ROOT / "quarantine/player-leftovers"
    Q_ARTWORK = TMP_ROOT / "quarantine/navidrome-artwork"


Q_DUPES = Q_JUNK = Q_BADTAGS = Q_EMPTY = Q_UNKNOWN = Q_LEFTOVERS = Q_ARTWORK = TMP_ROOT
configure(MUSIC_ROOT, TMP_ROOT, REPORTS)
# Files another player or tagger leaves in the library (its database, settings, playlists, caches). None of it is
# music and it clutters the tree. Nothing is assumed: the rules come from the plugin options (`leftovers`), so a
# library that has such files lists their extensions, name prefixes, exact names and top-level folders there.
LEFTOVER_EXTS: set[str] = set()
LEFTOVER_NAME_PREFIXES: tuple[str, ...] = ()
LEFTOVER_EXACT_NAMES: set[str] = set()
LEFTOVER_DIRS: set[str] = set()


def set_leftovers(rules: dict) -> None:
    """Install the user's leftover rules (keys: exts, name_prefixes, exact_names, dirs)."""
    global LEFTOVER_EXTS, LEFTOVER_NAME_PREFIXES, LEFTOVER_EXACT_NAMES, LEFTOVER_DIRS
    LEFTOVER_EXTS = {e.lower() for e in rules.get("exts", [])}
    LEFTOVER_NAME_PREFIXES = tuple(p.lower() for p in rules.get("name_prefixes", []))
    LEFTOVER_EXACT_NAMES = {n.lower() for n in rules.get("exact_names", [])}
    LEFTOVER_DIRS = set(rules.get("dirs", []))


AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".wav", ".opus", ".ogg", ".aac", ".wma", ".alac", ".aiff"}
LOSSLESS_EXTS = {".flac", ".wav", ".alac", ".aiff"}
LYRIC_EXTS = {".lrc", ".txt"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# Album-level sidecars that belong to the folder, not to one track.
ALBUM_SIDECAR_STEMS = {
    "folder",
    "cover",
    "front",
    "album",
    "albumart",
    "discart",
    "cdart",
    "back",
    "banner",
    "fanart",
    "logo",
    "thumb",
    "artist",
    "poster",
}
ALBUM_SIDECAR_NAMES = {"album.nfo", "artist.nfo", "desktop.ini", "folder.jpg", "cover.jpg"}

# Windows Media Player / OS turds - safe to quarantine as junk.
JUNK_PATTERNS = [
    r"^AlbumArt_\{[0-9A-Fa-f-]+\}_(Large|Small)\.jpg$",
    r"^AlbumArtSmall\.jpg$",
    r"^Folder\.ico$",
    r"^desktop\.ini$",
    r"^Thumbs\.db$",
    r"^\.DS_Store$",
    r"^__MACOSX$",
    r"^\._.*$",
]

# NTFS/exFAT reserved characters. The volume is fuseblk (ntfs-3g), so these
# cannot appear literally in a name even though Linux itself would allow most.
NTFS_FORBIDDEN = '<>:"/\\|?*'
NTFS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Substitutions used when a tag value must become a path component.
# Unicode look-alikes preserve the information instead of destroying it the way
# the previous tooling did (it turned "Re:Re:" into "Re Re " and "AC/DC" into "AC DC").
NTFS_SUBSTITUTIONS = {
    ":": "꞉",  # ꞉ MODIFIER LETTER COLON
    "/": "⁄",  # ⁄ FRACTION SLASH
    "\\": "⧵",  # ⧵ REVERSE SOLIDUS OPERATOR
    "?": "？",  # ？ FULLWIDTH QUESTION MARK
    "*": "∗",  # ∗ ASTERISK OPERATOR
    '"': "”",  # ” RIGHT DOUBLE QUOTATION MARK
    "<": "‹",  # ‹ SINGLE LEFT-POINTING ANGLE QUOTATION MARK
    ">": "›",  # › SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
    "|": "｜",  # ｜ FULLWIDTH VERTICAL LINE
}

MAX_COMPONENT_LEN = 120  # keep well under NTFS 255 and give room for sidecar suffixes


def prepare(ctx, options=None) -> None:
    """Point every stage at this run's library, staging area and cache folder (a plugin job calls this first)."""
    reports = ctx.path("state")
    reports.mkdir(parents=True, exist_ok=True)
    configure(ctx.path("music"), ctx.path("tmp"), reports)
    if options is not None and getattr(options, "leftovers", None):
        set_leftovers(options.leftovers)

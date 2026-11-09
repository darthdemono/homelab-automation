# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""One place that reads and writes audio tags, whatever the container.

Keys are neutral names. A few are mapped to the right field for each format (FLAC and Ogg Vorbis comments, MP3
ID3, MP4 atoms); any other key is stored as a custom field with that name. Values are always lists of strings, so a
multi-artist tag stays one artist per value. `write` changes only the keys it is given; `None` removes a key.
"""

from __future__ import annotations

import mutagen
from mutagen.id3 import COMM, ID3, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, TPE2, TPOS, TRCK, TXXX, USLT
from mutagen.mp4 import MP4, MP4FreeForm

# neutral key -> (vorbis key, id3 frame class or name, mp4 atom)
KNOWN = {
    "artist": ("artist", TPE1, "\xa9ART"),
    "albumartist": ("albumartist", TPE2, "aART"),
    "album": ("album", TALB, "\xa9alb"),
    "title": ("title", TIT2, "\xa9nam"),
    "genre": ("genre", TCON, "\xa9gen"),
    "date": ("date", TDRC, "\xa9day"),
    "tracknumber": ("tracknumber", TRCK, "trkn"),
    "discnumber": ("discnumber", TPOS, "disk"),
    "bpm": ("bpm", TBPM, "tmpo"),
    "initialkey": ("initialkey", TKEY, "----:com.apple.iTunes:initialkey"),
    "comment": ("comment", COMM, "\xa9cmt"),
    "lyrics": ("lyrics", USLT, "\xa9lyr"),
}
FREEFORM = "----:com.apple.iTunes:"


class TagError(Exception):
    pass


def _open(path):
    try:
        mf = mutagen.File(str(path))
    except Exception as err:
        raise TagError(f"open {path}: {type(err).__name__}: {err}") from err
    if mf is None:
        raise TagError(f"unrecognised audio format: {path}")
    return mf


def _strings(items) -> list[str]:
    out = []
    for item in items if isinstance(items, (list, tuple)) else [items]:
        if hasattr(item, "text"):
            out += [str(x) for x in (item.text if isinstance(item.text, list) else [item.text])]
        elif isinstance(item, MP4FreeForm | bytes):
            out.append(bytes(item).decode("utf-8", "replace"))
        elif isinstance(item, tuple):
            out.append("/".join(str(x) for x in item if x))
        else:
            out.append(str(item))
    return [x for x in (s.strip() for s in out) if x]


def read(path, keys: list[str]) -> dict[str, list[str]]:
    """The values of the given neutral keys; a key with no value is absent from the result."""
    mf = _open(path)
    tags, out = mf.tags, {}
    if tags is None:
        return out
    for key in keys:
        found: list[str] = []
        if isinstance(tags, ID3):
            if key in KNOWN:
                frame = KNOWN[key][1]
                frames = tags.getall(frame.__name__)
                found = _strings([f for f in frames]) if frames else []
            else:
                found = _strings(tags.getall(f"TXXX:{key}"))
        elif isinstance(mf, MP4):
            atom = KNOWN[key][2] if key in KNOWN else FREEFORM + key
            found = _strings(tags[atom]) if atom in tags else []
        else:
            vkey = KNOWN[key][0] if key in KNOWN else key
            found = _strings(tags[vkey]) if vkey in tags else []
        if found:
            out[key] = found
    return out


def write(path, changes: dict[str, list[str] | None]) -> None:
    """Apply changes: a list sets the key, None removes it. Saves once; raises TagError on failure."""
    mf = _open(path)
    try:
        if mf.tags is None:
            mf.add_tags()
        tags = mf.tags
        if isinstance(tags, ID3):
            for key, values in changes.items():
                name = KNOWN[key][1].__name__ if key in KNOWN else f"TXXX:{key}"
                tags.delall(name)
                if values:
                    if key in KNOWN:
                        frame = KNOWN[key][1]
                        if frame is COMM:
                            tags.add(COMM(encoding=3, lang="eng", desc="", text=values))
                        elif frame is USLT:
                            tags.add(USLT(encoding=3, lang="eng", desc="", text="\n".join(values)))
                        else:
                            tags.add(frame(encoding=3, text=values))
                    else:
                        tags.add(TXXX(encoding=3, desc=key, text=values))
            mf.save(v2_version=4)  # multi-value text frames only exist in ID3v2.4
        elif isinstance(mf, MP4):
            for key, values in changes.items():
                atom = KNOWN[key][2] if key in KNOWN else FREEFORM + key
                if not values:
                    tags.pop(atom, None)
                elif atom.startswith(FREEFORM):
                    tags[atom] = [MP4FreeForm(v.encode("utf-8")) for v in values]
                elif atom in ("trkn", "disk"):
                    num, _, total = values[0].partition("/")
                    tags[atom] = [(int(num or 0), int(total or 0))]
                elif atom == "tmpo":
                    tags[atom] = [int(float(values[0]))]
                else:
                    tags[atom] = values
            mf.save()
        else:
            for key, values in changes.items():
                vkey = KNOWN[key][0] if key in KNOWN else key
                if not values:
                    if vkey in tags:
                        del tags[vkey]
                else:
                    tags[vkey] = values
            mf.save()
    except TagError:
        raise
    except Exception as err:
        raise TagError(f"write {path}: {type(err).__name__}: {err}") from err

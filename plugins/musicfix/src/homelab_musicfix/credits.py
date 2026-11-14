# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Embed hand-sourced credits (Genius pastes) into tracks MusicBrainz lacks.

    python -m homelab_musicfix.run credits reports/credits.json            # dry-run
    python -m homelab_musicfix.run credits reports/credits.json --commit
    python -m homelab_musicfix.run credits --restore --commit

credits.json maps a file path (absolute, or relative to DOWNLOADS_ROOT) to
``{"role": ["Name", ...]}``. Roles become multi-valued Vorbis keys. A role that
the file already carries is merged, not replaced, and a name that is the same
after folding case/punctuation, or whose words are a subset of a longer
spelling already present ("Richard Gomes" / "Nobin Richard Gomes"), is kept once.
Nothing is deleted; every write is journalled for ``--restore``.
"""

import argparse
import json
import re
import unicodedata
from pathlib import Path

import mutagen

from .settings import REPORTS
from .merge import DOWNLOADS_ROOT

JOURNAL = REPORTS / "credits_journal.jsonl"
# role -> Vorbis key. "performer" values are written as "Name (instrument)".
KEYS = {
    "composer": "composer",
    "writer": "writer",
    "lyricist": "lyricist",
    "producer": "producer",
    "coproducer": "producer",
    "engineer": "engineer",
    "mixer": "mixer",
    "mastering": "mastering",
    "arranger": "arranger",
    "performer": "performer",
    "genre": "genre",
    "label": "label",
}


def _words(s):
    s = unicodedata.normalize("NFKC", s).casefold().replace(".", "")
    return frozenset(re.sub(r"[^\w]+", " ", s).split())


def merge(existing, new, strict=False):
    out = []
    for n in list(existing) + list(new):
        w = _words(n)
        if not w:
            continue
        hit = next(
            (i for i, o in enumerate(out) if _words(o) == w or (not strict and (_words(o) <= w or w <= _words(o)))),
            None,
        )
        if hit is None:
            out.append(n)
        elif len(_words(n)) > len(_words(out[hit])):
            out[hit] = n
    return out


def _split(vals):
    return [p.strip() for v in vals for p in re.split(r"\s*;\s*", str(v)) if p.strip()]


def plan(path, credits):
    mf = mutagen.File(path)
    pooled = {}
    for role, names in credits.items():
        pooled.setdefault(KEYS[role], []).extend(names)
    changes = {}
    for key, names in pooled.items():
        raw = [str(v) for v in mf.tags.get(key, [])]
        after = merge(_split(raw), names, strict=key in ("genre", "performer"))
        if after != raw:
            changes[key] = (raw, after)
    return changes


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("credits", nargs="?")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args(argv)
    if a.restore:
        for line in reversed(JOURNAL.read_text(encoding="utf-8").splitlines()):
            j = json.loads(line)
            mf = mutagen.File(j["path"])
            for k, (before, _) in j["changes"].items():
                if before:
                    mf[k] = before
                else:
                    mf.pop(k, None)
            print("restore" if a.commit else "would restore", j["path"])
            if a.commit:
                mf.save()
        return
    data = json.loads(Path(a.credits).read_text(encoding="utf-8"))
    for rel, credits in data.items():
        p = Path(rel) if rel.startswith("/") else Path(DOWNLOADS_ROOT) / rel
        ch = plan(p, credits)
        label = "same  " if not ch else ("WRITE " if a.commit else "dry   ")
        print(label + p.name, {k: v[1] for k, v in ch.items()})
        if a.commit and ch:
            mf = mutagen.File(p)
            for k, (_, after) in ch.items():
                mf[k] = after
            mf.save()
            with open(JOURNAL, "a", encoding="utf-8") as f:
                f.write(json.dumps({"path": str(p), "changes": ch}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

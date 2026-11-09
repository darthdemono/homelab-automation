# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Strip the Picard Classical Extras settings dump out of COMMENT tags.

The plugin appended its own option tables (a 3 KB Python-repr dict, 7 distinct
variants across the library) to the comment of ~3900 files. It holds plugin
settings only, nothing about the track, so it is clutter that Navidrome shows as
the comment. Every other comment value (URLs, release notes) is kept.

Dry-run by default; every write is journalled to reports/comment_journal.jsonl
with the removed text, so `--restore` puts it back. One copy of each distinct
dump is also saved to reports/classical_extras_settings.json.
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

import mutagen
from mutagen.id3 import COMM
from mutagen.mp4 import MP4

from .settings import MUSIC_ROOT, REPORTS

AUDIO = {".flac", ".mp3", ".m4a", ".ogg", ".opus"}
JUNK = re.compile(r"^\s*\{\s*'classical extras'", re.I)


def is_junk(value: str) -> bool:
    return bool(JUNK.match(value))


def _vorbis_key(tags):
    return next((k for k in tags.keys() if k.lower() == "comment"), None)


def read_comments(f) -> list[str]:
    """All comment values on an opened mutagen file, whatever the container."""
    if f is None or f.tags is None:
        return []
    if isinstance(f, MP4):
        return [str(v) for v in f.tags.get("\xa9cmt", [])]
    if hasattr(f.tags, "getall"):
        return [t for c in f.tags.getall("COMM") for t in c.text]
    k = _vorbis_key(f.tags)
    return [str(v) for v in f.tags[k]] if k else []


def write_comments(f, values: list[str]) -> None:
    if isinstance(f, MP4):
        if values:
            f.tags["\xa9cmt"] = values
        else:
            f.tags.pop("\xa9cmt", None)
    elif hasattr(f.tags, "getall"):
        # edit frames in place so other COMM frames keep their desc/lang
        for fr in f.tags.getall("COMM"):
            fr.text = [t for t in fr.text if t in values]
            if not fr.text:
                f.tags.delall(fr.HashKey)
        if values and not f.tags.getall("COMM"):
            f.tags.add(COMM(encoding=3, lang="eng", desc="", text=values))
    else:
        k = _vorbis_key(f.tags)
        if k:
            del f.tags[k]
        if values:
            f.tags["COMMENT"] = values
    f.save()


def _scan(path):
    try:
        f = mutagen.File(path)
        cur = read_comments(f)
    except Exception:
        return None
    bad = [v for v in cur if is_junk(v)]
    return (path, cur, bad) if bad else None


def restore(journal, *, commit) -> int:
    rows = [json.loads(line) for line in open(journal, encoding="utf-8")] if os.path.exists(journal) else []
    print(f"[comments] {len(rows)} journalled files")
    if not commit:
        print("[comments] dry-run; re-run with --commit")
        return 0
    fail = 0
    for r in reversed(rows):
        try:
            f = mutagen.File(r["path"])
            write_comments(f, r["before"])
        except Exception as e:
            fail += 1
            print(f"  FAIL {r['path']}: {e}")
    print(f"[comments] restored {len(rows) - fail}, failed {fail}")
    return 1 if fail else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--journal", default=str(REPORTS / "comment_journal.jsonl"))
    ap.add_argument("--paths", help="file listing paths to check instead of walking root")
    a = ap.parse_args(argv)
    if a.restore:
        return restore(a.journal, commit=a.commit)

    if a.paths:
        paths = [p.strip() for p in open(a.paths, encoding="utf-8") if p.strip()]
    else:
        paths = [
            os.path.join(d, n)
            for d, ds, fs in os.walk(a.root)
            if "/." not in d
            for n in fs
            if os.path.splitext(n)[1].lower() in AUDIO
        ]
    with ThreadPoolExecutor(16) as ex:
        hits = [h for h in ex.map(_scan, paths) if h]
    variants = {re.sub(r"\s+", "", b): b for _, _, bad in hits for b in bad}
    print(f"[comments] {len(paths)} files, {len(hits)} carry a settings dump ({len(variants)} variants)")
    if not a.commit:
        print("[comments] dry-run; re-run with --commit")
        return 0

    with open(REPORTS / "classical_extras_settings.json", "w", encoding="utf-8") as fh:
        json.dump(list(variants.values()), fh, ensure_ascii=False, indent=1)
    done = failed = 0
    with open(a.journal, "a", encoding="utf-8") as jfh:
        for path, cur, _ in hits:
            keep = [v for v in cur if not is_junk(v)]
            try:
                write_comments(mutagen.File(path), keep)
            except Exception as e:
                failed += 1
                print(f"  FAIL {path}: {e}")
                continue
            jfh.write(json.dumps({"path": path, "before": cur, "after": keep}, ensure_ascii=False) + "\n")
            done += 1
    print(f"[comments] cleaned {done}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

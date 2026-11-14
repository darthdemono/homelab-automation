# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Quarantine directories that hold no music.

After the rename/standardisation passes, the library is left with orphan
directories that contain only leftover metadata -- an old `artist.nfo`, some
Jellyfin artist art -- but no audio anywhere beneath them. They come from two
places, both harmless residue:

- **old artist-join spellings**: the audio moved to the canonical `X, Y and Z`
  folder, leaving `X, Y, Z` / `X & Y` behind (e.g. `Drake, 21 Savage` after the
  audio went to `Drake and 21 Savage`);
- **old-case spellings**: ntfs-3g is case-sensitive here, so `System Of A Down`
  and `System of a Down` are two real directories; the audio is under the tag's
  casing and the other keeps only stray metadata.

This finds every directory with no audio beneath it and moves it wholesale to
`tmp/quarantine/no-music-dirs/`, journalled so `--restore` puts it back. Nothing
is deleted. Dot-directories (`.Trash-1000`, `.claude`, …) are never touched.

    python -m homelab_musicfix.run nomusic                 # dry-run: list them
    python -m homelab_musicfix.run nomusic --commit
    python -m homelab_musicfix.run nomusic --restore --commit
"""

from __future__ import annotations

import argparse
import json
import os
import time

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS, TMP_ROOT
from . import scan

Q_NOMUSIC = TMP_ROOT / "quarantine/no-music-dirs"
JOURNAL = REPORTS / "nomusic_journal.jsonl"


def find_nomusic(root):
    """Top-most directories with no audio anywhere beneath them.

    'Top-most' means a no-music dir whose parent is also no-music is not listed
    on its own -- moving the parent takes it along.
    """
    root = os.path.abspath(root)
    all_dirs, direct_audio = [], {}
    for dp, _subdirs, files in scan.walk(root, workers=16):
        all_dirs.append(dp)
        direct_audio[dp] = any(os.path.splitext(f)[1].lower() in AUDIO_EXTS for f in files)

    has_music = set()
    for dp, has in direct_audio.items():
        if not has:
            continue
        d = dp
        while True:
            has_music.add(d)
            if d == root:
                break
            d = os.path.dirname(d)

    nomusic = set()
    for d in all_dirs:
        if d == root or d in has_music:
            continue
        rel = os.path.relpath(d, root)
        # never touch dot-directories anywhere in the path
        if any(part.startswith(".") for part in rel.split(os.sep)):
            continue
        nomusic.add(d)

    tops = sorted(d for d in nomusic if os.path.dirname(d) not in nomusic)
    return tops


def _dir_files(d):
    out = []
    for r, _sub, fs in os.walk(d):
        out.extend(fs)
    return out


def restore(root, *, commit):
    if not JOURNAL.exists():
        print(f"[nomusic] no journal at {JOURNAL}")
        return 1
    entries = [json.loads(l) for l in open(JOURNAL, encoding="utf-8") if l.strip()]
    print(f"[nomusic] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(entries)} directories")
    if not commit:
        return 0
    ok = failed = 0
    for e in reversed(entries):
        src = os.path.join(root, e["rel"])  # original location
        dst = e["dst"]  # quarantine location
        if os.path.exists(dst) and not os.path.exists(src):
            os.makedirs(os.path.dirname(src), exist_ok=True)
            os.rename(dst, src)
            ok += 1
        else:
            failed += 1
            print(f"  skip {e['rel']}: src exists or quarantine copy gone")
    print(f"[nomusic] {ok} restored, {failed} skipped")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Quarantine no-music directories (dry-run by default).")
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.root, commit=args.commit)

    tops = find_nomusic(args.root)
    if args.limit:
        tops = tops[: args.limit]
    print(f"[nomusic] {'COMMIT' if args.commit else 'DRY-RUN'}: {len(tops)} no-music directories")
    for d in tops:
        rel = os.path.relpath(d, args.root)
        exts = sorted({os.path.splitext(f)[1].lower() for f in _dir_files(d)})
        print(f"  {rel}   [{','.join(exts) or 'empty'}]")

    if not args.commit:
        print("[nomusic] nothing moved; re-run with --commit")
        return 0

    Q_NOMUSIC.mkdir(parents=True, exist_ok=True)
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    with open(JOURNAL, "a", encoding="utf-8") as jf:
        for d in tops:
            rel = os.path.relpath(d, args.root)
            dst = str(Q_NOMUSIC / rel)
            if os.path.exists(dst):  # ntfs-3g case-sensitive safety
                dst = dst + f".dup{int(time.time())}"
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(d, dst)
                jf.write(json.dumps({"ts": time.time(), "rel": rel, "dst": dst}, ensure_ascii=False) + "\n")
                ok += 1
            except OSError as exc:
                failed += 1
                print(f"  ERROR {rel}: {exc}")
    print(f"[nomusic] {ok} moved to {Q_NOMUSIC}, {failed} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

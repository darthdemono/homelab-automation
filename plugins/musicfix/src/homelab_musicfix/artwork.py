# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Reduce a music library to one Navidrome album cover per audio directory.

Navidrome resolves album art from cover.*, folder.* and front.*. Spotiflac's
per-track image sidecars and Jellyfin's banner/backdrop collection add work but
do not improve that lookup. This command keeps or promotes one ``folder.*``
image per audio directory, and moves everything else that is in scope to the
same-volume quarantine. It never deletes.

    python -m homelab_musicfix.run artwork
    python -m homelab_musicfix.run artwork --commit
    python -m homelab_musicfix.run artwork --restore --commit
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .settings import AUDIO_EXTS, IMAGE_EXTS, MUSIC_ROOT, Q_ARTWORK, REPORTS

JOURNAL = REPORTS / "artwork_journal.jsonl"
STATUS = REPORTS / "artwork_cleanup.status"

# Jellyfin/Kodi artwork names. ``artist.*`` is deliberately not included:
# Navidrome supports it for artist artwork.
JELLYFIN_STEMS = {
    "back",
    "backdrop",
    "banner",
    "cdart",
    "clearart",
    "clearlogo",
    "discart",
    "fanart",
    "landscape",
    "logo",
    "poster",
    "thumb",
}


def _is_audio(name: str) -> bool:
    return Path(name).suffix.lower() in AUDIO_EXTS


def _is_image(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_EXTS


def _is_jellyfin_art(name: str) -> bool:
    stem = Path(name).stem.casefold()
    suffixes = ("avatar", "header", *JELLYFIN_STEMS)
    return (
        stem in JELLYFIN_STEMS
        or any(stem.endswith(f"_{suffix}") for suffix in suffixes)
        or any(token in stem for token in ("backdrop", "banner", "fanart", "clearlogo", "clearart"))
    )


def _cover_rank(path: Path) -> tuple[int, int, str]:
    """Prefer the project-standard folder.* image, then Navidrome's fallbacks."""
    stem = path.stem.casefold()
    kind = {"folder": 0, "cover": 1, "front": 2}.get(stem, 3)
    jpg = 0 if path.suffix.casefold() in {".jpg", ".jpeg"} else 1
    return kind, jpg, path.name.casefold()


def plan(root: str) -> list[dict]:
    """Return the deterministic, non-mutating cleanup plan for ``root``."""
    root_path = Path(root)
    actions: list[dict] = []
    for directory, subdirs, files in os.walk(root_path):
        # Mounted volumes can carry .Trash-1000 or tool state. They are not
        # part of the live library and must never be touched here.
        subdirs[:] = [subdir for subdir in subdirs if not subdir.startswith(".")]
        directory_path = Path(directory)
        names = sorted(files, key=str.casefold)
        audio_here = any(_is_audio(name) for name in names)

        for name in names:
            if Path(name).suffix.casefold() == ".nfo":
                actions.append({"op": "quarantine", "src": directory_path / name, "reason": "jellyfin-nfo"})

        images = [directory_path / name for name in names if _is_image(name)]
        for image in images:
            # An artist folder's own fanart.* is the artist background that
            # merge files there on purpose; only album-level Jellyfin art goes.
            if directory_path.parent == root_path and image.stem.casefold() == "fanart":
                continue
            if _is_jellyfin_art(image.name):
                actions.append({"op": "quarantine", "src": image, "reason": "jellyfin-art"})

        if not audio_here:
            continue

        candidates = [image for image in images if not _is_jellyfin_art(image.name)]
        if not candidates:
            continue
        keep = min(candidates, key=_cover_rank)
        canonical = directory_path / f"folder{keep.suffix.lower()}"
        if keep != canonical:
            actions.append({"op": "promote", "src": keep, "dst": canonical, "reason": "album-cover"})
        for image in candidates:
            if image != keep:
                actions.append({"op": "quarantine", "src": image, "reason": "extra-cover"})
    return actions


def _quarantine_path(source: Path, root: Path) -> Path:
    return Q_ARTWORK / source.relative_to(root)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    return path.with_name(f"{path.name}.dup{int(time.time())}")


def _show(actions: list[dict], root: Path) -> None:
    counts: dict[str, int] = {}
    for action in actions:
        counts[action["reason"]] = counts.get(action["reason"], 0) + 1
    print("[artwork] " + ", ".join(f"{count} {reason}" for reason, count in sorted(counts.items())))
    for action in actions[:10]:
        src = action["src"].relative_to(root)
        if action["op"] == "promote":
            print(f"  promote {src} -> {action['dst'].name}")
        else:
            print(f"  quarantine {src} ({action['reason']})")
    if len(actions) > 10:
        print(f"  ... and {len(actions) - 10} more")


def restore(*, commit: bool) -> int:
    if not JOURNAL.exists():
        print(f"[artwork] no journal at {JOURNAL}")
        return 1
    entries = [json.loads(line) for line in JOURNAL.read_text(encoding="utf-8").splitlines() if line]
    print(f"[artwork] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(entries)} moves")
    if not commit:
        return 0
    restored = skipped = 0
    for entry in reversed(entries):
        src, dst = Path(entry["src"]), Path(entry["dst"])
        if dst.exists() and not src.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            os.rename(dst, src)
            restored += 1
        else:
            skipped += 1
    print(f"[artwork] {restored} restored, {skipped} skipped")
    return 0


def apply(actions: list[dict], root: Path) -> int:
    REPORTS.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(f"RUNNING {time.strftime('%Y-%m-%dT%H:%M:%S%z')}: artwork cleanup\n", encoding="utf-8")
    Q_ARTWORK.mkdir(parents=True, exist_ok=True)
    moved = failed = 0
    with JOURNAL.open("a", encoding="utf-8") as journal:
        for action in actions:
            src = action["src"]
            dst = action.get("dst") if action["op"] == "promote" else _quarantine_path(src, root)
            dst = _unique(dst)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
                journal.write(
                    json.dumps(
                        {
                            "ts": time.time(),
                            "op": action["op"],
                            "reason": action["reason"],
                            "src": str(src),
                            "dst": str(dst),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                moved += 1
            except OSError as exc:
                failed += 1
                print(f"  ERROR {src}: {exc}")
    state = "SUCCESS" if failed == 0 else "FAILED"
    STATUS.write_text(
        f"{state} {time.strftime('%Y-%m-%dT%H:%M:%S%z')}: {moved} moved, {failed} failed; journal: {JOURNAL}\n",
        encoding="utf-8",
    )
    print(f"[artwork] {moved} moved, {failed} failed; status: {STATUS}")
    return 0 if failed == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Keep one Navidrome cover per album (dry-run by default).")
    parser.add_argument("--root", default=str(MUSIC_ROOT))
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args(argv)
    if args.restore:
        return restore(commit=args.commit)
    root = Path(args.root)
    actions = plan(str(root))
    print(f"[artwork] {'COMMIT' if args.commit else 'DRY-RUN'}: {len(actions)} moves")
    _show(actions, root)
    if not args.commit:
        print("[artwork] nothing moved; re-run with --commit")
        return 0
    return apply(actions, root)


if __name__ == "__main__":
    raise SystemExit(main())

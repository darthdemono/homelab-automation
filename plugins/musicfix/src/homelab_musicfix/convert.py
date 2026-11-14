# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Convert ALAC/m4a to FLAC without losing audio or metadata.

ALAC and FLAC are both lossless, so this re-containers the same samples into the
format the rest of the library uses. Three things make that safe:

* Tags are re-applied from a mutagen read of the source rather than relying on
  ffmpeg's MP4->FLAC mapping, which silently drops freeform atoms and collapses
  multi-valued fields.
* Embedded artwork is carried across explicitly as a FLAC picture block.
* Every conversion is **verified bit-exact** by comparing the MD5 of the decoded
  PCM on both sides. A file that does not verify is not accepted.

Lossy sources (plain AAC) are skipped by default: wrapping lossy audio in FLAC
adds size and recovers nothing. The original file is moved to quarantine, never
deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time

import mutagen
from mutagen.flac import FLAC, Picture

from .settings import MUSIC_ROOT, REPORTS, TMP_ROOT
from .tags import read_tags

Q_CONVERTED = TMP_ROOT / "quarantine/converted-originals"

LOSSLESS_CODECS = {"alac", "flac", "wavpack", "tta", "ape", "pcm_s16le", "pcm_s24le", "pcm_s32le"}

# MP4 atom -> Vorbis comment field.
MP4_TO_VORBIS = {
    "\xa9ART": "ARTIST",
    "aART": "ALBUMARTIST",
    "\xa9alb": "ALBUM",
    "\xa9nam": "TITLE",
    "\xa9day": "DATE",
    "\xa9gen": "GENRE",
    "\xa9wrt": "COMPOSER",
    "\xa9cmt": "COMMENT",
    "\xa9lyr": "LYRICS",
    "\xa9grp": "GROUPING",
    "cpil": "COMPILATION",
    "tmpo": "BPM",
    "soar": "ARTISTSORT",
    "soal": "ALBUMSORT",
    "sonm": "TITLESORT",
    "soaa": "ALBUMARTISTSORT",
}


def probe_codec(path):
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        return (json.loads(out.stdout or "{}").get("streams") or [{}])[0].get("codec_name")
    except (json.JSONDecodeError, IndexError):
        return None


def pcm_md5(path):
    """MD5 of the decoded audio stream, for proving the conversion is exact.

    Decoding to a canonical raw format means container and codec differences
    drop out and only the actual samples are compared.
    """
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-f", "s32le", "-acodec", "pcm_s32le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    h = hashlib.md5()
    assert proc.stdout is not None
    for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
        h.update(chunk)
    proc.stdout.close()
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if proc.wait() != 0:
        return None, err.strip()[:200]
    return h.hexdigest(), None


def extract_tags(path):
    """Every tag on the source, as Vorbis field -> list of values."""
    out = {}
    try:
        mf = mutagen.File(str(path))
    except Exception as exc:  # noqa: BLE001
        return out, f"{type(exc).__name__}: {exc}"
    if mf is None or mf.tags is None:
        return out, None

    for key, value in mf.tags.items():
        key = str(key)
        if key == "covr":
            continue  # artwork handled separately
        if key.startswith("----"):
            # Freeform atom: "----:com.apple.iTunes:ISRC" -> "ISRC"
            field = key.split(":")[-1].upper()
        else:
            field = MP4_TO_VORBIS.get(key)
            if field is None:
                continue
        values = []
        for v in value if isinstance(value, list) else [value]:
            if isinstance(v, tuple):  # trkn / disk -> (num, total)
                values.append(str(v[0]))
            elif isinstance(v, bytes):
                values.append(v.decode("utf-8", "replace"))
            elif isinstance(v, bool):
                values.append("1" if v else "0")
            else:
                values.append(str(v))
        values = [x for x in values if x.strip()]
        if values:
            out.setdefault(field, []).extend(values)

    # trkn/disk also carry totals, which Vorbis stores separately.
    for atom, num_field, total_field in (("trkn", "TRACKNUMBER", "TRACKTOTAL"), ("disk", "DISCNUMBER", "DISCTOTAL")):
        try:
            pair = mf.tags.get(atom)
        except Exception:  # noqa: BLE001
            pair = None
        if pair and isinstance(pair[0], tuple) and len(pair[0]) > 1 and pair[0][1]:
            out[num_field] = [str(pair[0][0])]
            out[total_field] = [str(pair[0][1])]
    return out, None


def extract_art(path):
    """Embedded cover art as a list of (mime, bytes)."""
    try:
        mf = mutagen.File(str(path))
    except Exception:  # noqa: BLE001
        return []
    if mf is None or mf.tags is None:
        return []
    out = []
    try:
        for cover in mf.tags.get("covr", []) or []:
            fmt = getattr(cover, "imageformat", None)
            mime = "image/png" if fmt == 14 else "image/jpeg"
            out.append((mime, bytes(cover)))
    except Exception:  # noqa: BLE001
        pass
    return out


def convert_one(src, dst, *, compression=8):
    """ALAC -> FLAC, tags and art carried over, verified bit-exact."""
    result = {"src": str(src), "dst": str(dst), "ok": False, "error": None, "verified": False}

    before, err = pcm_md5(src)
    if err or not before:
        result["error"] = f"could not decode source: {err}"
        return result

    tmp = f"{dst}.part"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-i",
        str(src),
        "-map",
        "0:a:0",  # audio only; art is re-attached below
        "-c:a",
        "flac",
        "-compression_level",
        str(compression),
        "-map_metadata",
        "-1",  # tags come from mutagen, not ffmpeg
        # The temp file ends in ".part", so ffmpeg cannot infer the container
        # from the extension and must be told explicitly.
        "-f",
        "flac",
        tmp,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        result["error"] = (proc.stderr or "ffmpeg failed").strip()[:300]
        _rm(tmp)
        return result

    after, err = pcm_md5(tmp)
    if err or not after:
        result["error"] = f"could not decode output: {err}"
        _rm(tmp)
        return result
    if before != after:
        # Refuse to accept a conversion that changed a single sample.
        result["error"] = f"audio mismatch: source {before} != output {after}"
        _rm(tmp)
        return result
    result["verified"] = True

    tags, terr = extract_tags(src)
    try:
        flac = FLAC(tmp)
        for field, values in tags.items():
            flac[field] = values
        for mime, data in extract_art(src):
            pic = Picture()
            pic.type = 3  # front cover
            pic.mime = mime
            pic.data = data
            flac.add_picture(pic)
        flac.save()
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"tagging failed: {type(exc).__name__}: {exc}"
        _rm(tmp)
        return result

    os.replace(tmp, dst)
    result["ok"] = True
    result["tag_fields"] = sorted(tags)
    if terr:
        result["tag_warning"] = terr
    return result


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert ALAC/m4a to FLAC, preserving metadata (dry-run by default).")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--plan", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--ext", default=".m4a,.mp4,.alac", help="comma-separated source extensions to consider")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument(
        "--allow-lossy", action="store_true", help="also convert lossy sources (AAC); off because it only adds size"
    )
    ap.add_argument(
        "--convert-planned-dupes",
        action="store_true",
        help="also convert files already scheduled for duplicate quarantine",
    )
    ap.add_argument("--compression", type=int, default=8)
    ap.add_argument("--journal", default=str(REPORTS / "convert_journal.jsonl"))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    exts = tuple(e.strip().lower() for e in args.ext.split(",") if e.strip())
    records = []
    with open(args.inventory, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))

    planned_dupes = set()
    if os.path.exists(args.plan) and not args.convert_planned_dupes:
        with open(args.plan, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                a = json.loads(line)
                if a.get("kind") == "quarantine-dupe":
                    planned_dupes.add(a["src"])

    todo, skipped = [], []
    for r in records:
        if not r["path"].lower().endswith(exts):
            continue
        q = r.get("quality") or {}
        codec = q.get("codec")
        if r.get("error") or q.get("error"):
            skipped.append((r["path"], r.get("error") or q.get("error")))
            continue
        if codec not in LOSSLESS_CODECS and not args.allow_lossy:
            skipped.append((r["path"], f"lossy source ({codec}); FLAC would only add size"))
            continue
        if r["path"] in planned_dupes:
            skipped.append((r["path"], "already scheduled for duplicate quarantine"))
            continue
        dst_rel = os.path.splitext(r["path"])[0] + ".flac"
        if any(o["path"].lower() == dst_rel.lower() for o in records):
            skipped.append((r["path"], "a FLAC of this track already exists"))
            continue
        todo.append((r["path"], dst_rel))
    if args.limit:
        todo = todo[: args.limit]

    print(f"[convert] {'COMMIT' if args.commit else 'DRY-RUN'}: {len(todo)} to convert, {len(skipped)} skipped")
    for path, why in skipped:
        print(f"  SKIP {path}\n       {why}")
    for src_rel, dst_rel in todo:
        print(f"  {src_rel}\n    -> {dst_rel}")
    if not args.commit:
        print("[convert] nothing written; re-run with --commit")
        return 0

    ok = failed = 0
    os.makedirs(os.path.dirname(args.journal), exist_ok=True)
    with open(args.journal, "a", encoding="utf-8") as jfh:
        for src_rel, dst_rel in todo:
            src = os.path.join(args.root, src_rel)
            dst = os.path.join(args.root, dst_rel)
            res = convert_one(src, dst, compression=args.compression)
            res["ts"] = time.time()
            if res["ok"]:
                # Original goes to quarantine only once the FLAC is verified.
                qdst = os.path.join(str(Q_CONVERTED), src_rel)
                os.makedirs(os.path.dirname(qdst), exist_ok=True)
                try:
                    os.rename(src, qdst)
                except OSError:
                    shutil.move(src, qdst)
                res["original_moved_to"] = qdst
                ok += 1
                print(f"  OK  {dst_rel}  (bit-exact, {len(res.get('tag_fields', []))} tag fields)")
            else:
                failed += 1
                print(f"  FAIL {src_rel}: {res['error']}")
            jfh.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(f"[convert] {ok} converted and verified, {failed} failed")
    print("[convert] originals in", Q_CONVERTED)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

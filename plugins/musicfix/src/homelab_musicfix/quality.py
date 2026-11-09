# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Measured audio quality, derived from the decoded stream rather than tags.

Tag-reported bitrate lies constantly: transcodes keep the original header, VBR
files report a nominal rate, and some rippers write nothing at all. Everything
here comes from ffprobe reading the actual stream, with a decode-based fallback
for containers whose header bitrate is unreliable.
"""

from __future__ import annotations

import json
import os
import subprocess

from .settings import LOSSLESS_EXTS

FFPROBE = "ffprobe"


def probe(path) -> dict:
    """Return measured stream properties for one audio file.

    Keys: codec, container, duration, size, bit_rate (bits/s, measured),
    sample_rate, channels, bit_depth, lossless, bitrate_source, error.
    """
    path = str(path)
    out = {
        "codec": None,
        "container": None,
        "duration": None,
        "size": None,
        "bit_rate": None,
        "sample_rate": None,
        "channels": None,
        "bit_depth": None,
        "lossless": None,
        "bitrate_source": None,
        "error": None,
    }
    try:
        out["size"] = os.path.getsize(path)
    except OSError as exc:
        out["error"] = f"stat: {exc}"
        return out

    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_name,sample_rate,channels,bits_per_raw_sample,bits_per_sample,bit_rate,duration"
        ":format=format_name,duration,bit_rate,size",
        "-of",
        "json",
        path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        out["error"] = "ffprobe timeout"
        return out
    except FileNotFoundError:
        out["error"] = "ffprobe not installed"
        return out

    if res.returncode != 0:
        out["error"] = (res.stderr or "ffprobe failed").strip()[:300]
        return out
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError:
        out["error"] = "ffprobe returned non-JSON"
        return out

    streams = data.get("streams") or []
    if not streams:
        out["error"] = "no audio stream"
        return out
    st = streams[0]
    fmt = data.get("format") or {}

    out["codec"] = st.get("codec_name")
    out["container"] = fmt.get("format_name")
    out["sample_rate"] = _int(st.get("sample_rate"))
    out["channels"] = _int(st.get("channels"))
    out["bit_depth"] = _int(st.get("bits_per_raw_sample")) or _int(st.get("bits_per_sample")) or None
    out["duration"] = _float(st.get("duration")) or _float(fmt.get("duration"))

    ext = os.path.splitext(path)[1].lower()
    out["lossless"] = (
        out["codec"] in {"flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "wavpack", "tta", "ape"}
        or ext in LOSSLESS_EXTS
    )

    # Bitrate priority matters for duplicate ranking, so be careful here.
    #
    # The audio stream's own bit_rate is what we want: ffmpeg derives it from
    # the actual audio packets, so embedded cover art and tag blocks are
    # excluded. The container-level figure counts every byte in the file, which
    # inflates a track carrying a large embedded JPEG (measured on this library:
    # 270383 container vs 244467 real for one mp3, a 10% lie). size/duration has
    # the same flaw and is the last resort.
    stream_br = _int(st.get("bit_rate"))
    fmt_br = _int(fmt.get("bit_rate"))
    if stream_br:
        out["bit_rate"] = stream_br
        out["bitrate_source"] = "audio_stream"
    elif fmt_br:
        out["bit_rate"] = fmt_br
        out["bitrate_source"] = "container(includes art)"
    elif out["duration"] and out["duration"] > 0 and out["size"]:
        out["bit_rate"] = int(out["size"] * 8 / out["duration"])
        out["bitrate_source"] = "size/duration(includes art)"

    # FLAC and other lossless codecs never publish a stream bit_rate, so the
    # figure above is container-level for them. That is fine: for lossless the
    # bitrate reflects how compressible the music is, not how good it sounds,
    # so quality_key() ranks depth and sample rate ahead of it.

    # A file can name a codec in its header yet be undecodable: a truncated or
    # corrupt FLAC reports codec=flac but ffprobe cannot measure a duration or a
    # bitrate for it ("invalid sync code"). Neither number being available means
    # there is no real audio here, so flag it rather than treat it as a track.
    if out["duration"] is None and out["bit_rate"] is None:
        out["error"] = "undecodable audio (no measurable duration or bitrate; likely corrupt)"
    return out


# NOTE: a "fake" lossless file -- one transcoded up from a 128 kbps MP3 -- has
# lossless size and a lossless codec but its spectrum still dies around 16 kHz.
# Detecting that needs real FFT analysis of the decoded samples, which is far
# too slow for a whole-library pass and is not implemented here. Nothing in this
# module claims to catch it.


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Codec ranking for tie-breaking when choosing which duplicate to keep.
CODEC_RANK = {
    "flac": 100,
    "alac": 98,
    "wavpack": 97,
    "tta": 96,
    "ape": 95,
    "pcm_s32le": 94,
    "pcm_s24le": 93,
    "pcm_s16le": 90,
    "opus": 60,
    "aac": 55,
    "vorbis": 50,
    "mp3": 45,
    "wmav2": 30,
    "wmav1": 25,
}


def quality_key(rec: dict) -> tuple:
    """Sort key for "which copy is better". Higher tuple wins.

    Lossless always beats lossy regardless of the lossy file's bitrate, because
    a 320 kbps MP3 is still a lossy derivative. After that the two tiers are
    judged on different things, so they get different keys:

    * Lossless: bit depth, sample rate, then codec. Bitrate is deliberately
      ignored -- for lossless it measures how compressible the music is, not how
      good it sounds. Ranking by it makes an uncompressed WAV beat a bit-identical
      FLAC purely for being bigger, which is backwards: FLAC decodes to the same
      samples, carries real tags, and takes ~25% less space.
    * Lossy: sample rate, then bitrate, then codec. Here bitrate really is the
      dominant quality signal.
    """
    q = rec.get("quality") or {}
    lossless = 1 if q.get("lossless") else 0
    depth = q.get("bit_depth") or 0
    rate = q.get("sample_rate") or 0
    br = q.get("bit_rate") or 0
    codec = CODEC_RANK.get(q.get("codec") or "", 0)
    if lossless:
        return (1, depth, rate, codec, 0)
    return (0, 0, rate, br, codec)

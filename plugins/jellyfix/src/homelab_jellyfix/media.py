# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""ffprobe-backed media inspection with an on-disk cache keyed by path+size+mtime."""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".flv", ".mpg", ".mpeg"}
SUB_EXT = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt"}

CACHE = os.path.join(os.getcwd(), "cache", "media.json")


def set_cache_dir(path):
    global CACHE, _cache
    CACHE = os.path.join(str(path), "media.json")
    _cache = None


_cache = None


def _load():
    global _cache
    if _cache is None:
        try:
            _cache = json.load(open(CACHE))
        except Exception:
            _cache = {}
    return _cache


def save_cache():
    if _cache is not None:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        json.dump(_cache, open(CACHE, "w"))


def _key(path):
    try:
        st = os.stat(path)
        return f"{path}|{st.st_size}|{int(st.st_mtime)}"
    except OSError as e:
        return f"{path}|ERR|{e.errno}"


def probe(path, use_cache=True):
    """Return {ok, duration, vcodec, width, height, pix_fmt, audio[], subs[], error}."""
    c, k = _load(), _key(path)
    if use_cache and k in c:
        return c[k]
    out = {
        "ok": False,
        "error": None,
        "duration": None,
        "vcodec": None,
        "width": None,
        "height": None,
        "pix_fmt": None,
        "audio": [],
        "subs": [],
    }
    try:
        r = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,codec_name,width,height,pix_fmt,channels:stream_tags=language",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if r.returncode != 0 or not r.stdout.strip():
            out["error"] = (r.stderr or "ffprobe failed").strip().split("\n")[0][:200]
        else:
            d = json.loads(r.stdout)
            streams = d.get("streams", [])
            vs = [s for s in streams if s.get("codec_type") == "video"]
            dur = d.get("format", {}).get("duration")
            out["duration"] = float(dur) if dur else None
            if not vs:
                out["error"] = "no video stream"
            else:
                v = vs[0]
                out.update(
                    vcodec=v.get("codec_name"), width=v.get("width"), height=v.get("height"), pix_fmt=v.get("pix_fmt")
                )
                out["audio"] = [
                    {
                        "codec": a.get("codec_name"),
                        "channels": a.get("channels"),
                        "lang": (a.get("tags") or {}).get("language", ""),
                    }
                    for a in streams
                    if a.get("codec_type") == "audio"
                ]
                out["subs"] = [
                    (s.get("tags") or {}).get("language", "") for s in streams if s.get("codec_type") == "subtitle"
                ]
                out["ok"] = out["duration"] is not None
                if not out["ok"]:
                    out["error"] = "no duration"
    except subprocess.TimeoutExpired:
        out["error"] = "ffprobe timeout"
    except Exception as ex:
        out["error"] = str(ex)[:200]
    c[k] = out
    return out


def probe_many(paths, workers=6, use_cache=True):
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(lambda p: probe(p, use_cache), paths))
    save_cache()
    return dict(zip(paths, res))

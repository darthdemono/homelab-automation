# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""TRaSH-guides style naming for Jellyfin + Sonarr/Radarr compatible layouts."""

import re

LANG3TO2 = {
    "jpn": "JA",
    "eng": "EN",
    "fre": "FR",
    "fra": "FR",
    "ger": "DE",
    "deu": "DE",
    "ita": "IT",
    "spa": "ES",
    "por": "PT",
    "rus": "RU",
    "kor": "KO",
    "chi": "ZH",
    "zho": "ZH",
    "ara": "AR",
    "hin": "HI",
    "ben": "BN",
    "dan": "DA",
    "swe": "SV",
    "nor": "NO",
    "fin": "FI",
    "dut": "NL",
    "nld": "NL",
    "pol": "PL",
    "tur": "TR",
}

VCODEC = {
    "hevc": "x265",
    "h264": "x264",
    "av1": "AV1",
    "mpeg4": "XviD",
    "vc1": "VC1",
    "mpeg2video": "MPEG2",
    "vp9": "VP9",
}
ACODEC = {
    "opus": "OPUS",
    "aac": "AAC",
    "ac3": "AC3",
    "eac3": "EAC3",
    "dts": "DTS",
    "flac": "FLAC",
    "truehd": "TrueHD",
    "mp3": "MP3",
    "vorbis": "Vorbis",
    "pcm_s16le": "PCM",
    "pcm_s24le": "PCM",
}
CHANNELS = {1: "1.0", 2: "2.0", 3: "2.1", 6: "5.1", 7: "6.1", 8: "7.1"}

# characters NTFS/exFAT reject, plus control chars
ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(text, replace_colon=" -"):
    if not text:
        return ""
    t = text.replace(":", replace_colon)
    t = ILLEGAL.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t.rstrip(". ")  # NTFS forbids trailing dot/space


def res_tag(height, width=None):
    """Standard resolution class. Width is the reliable signal: letterboxed films
    keep a 1920 width but can be 1038, 960, 800 px tall."""
    h, w = height or 0, width or 0
    if not h and not w:
        return None
    if w >= 3000 or h >= 1700:
        return "2160p"
    if w >= 1700 or h >= 1000:
        return "1080p"
    if w >= 1100 or h >= 680:
        return "720p"
    if w >= 900 or h >= 550:
        return "576p"
    if w >= 600 or h >= 380:
        return "480p"
    return f"{h}p" if h else f"{w}w"


def bitdepth(pix_fmt):
    m = re.search(r"p(\d{2})", pix_fmt or "")
    return int(m.group(1)) if m else 8


def media_tail(info, source=None, group=None):
    """[Quality][10bit][x265][OPUS 2.0][JA+EN]-Group"""
    parts = []
    res = res_tag(info.get("height"), info.get("width"))
    if source and res:
        parts.append(f"{source}-{res}")
    elif res:
        parts.append(res)
    parts.append(f"{bitdepth(info.get('pix_fmt'))}bit")
    vc = info.get("vcodec")
    if vc:
        parts.append(VCODEC.get(vc, vc))
    au = info.get("audio") or []
    if au:
        ac = ACODEC.get(au[0].get("codec"), (au[0].get("codec") or "").upper())
        ch = CHANNELS.get(au[0].get("channels"), f"{au[0].get('channels') or 0}.0")
        parts.append(f"{ac} {ch}".strip())
        langs = "+".join(dict.fromkeys(LANG3TO2.get(a.get("lang"), (a.get("lang") or "UND").upper()) for a in au))
        if langs:
            parts.append(langs)
    tail = "".join(f"[{p}]" for p in parts if p)
    if group:
        tail += f"-{sanitize(group)}"
    return tail


def series_folder(title, year, tvdb_id=None):
    base = f"{sanitize(title)}" + (f" ({year})" if year else "")
    return base + (f" [tvdbid-{tvdb_id}]" if tvdb_id else "")


def season_folder(season):
    return "Specials" if season == 0 else f"Season {season:02d}"


def episode_name(
    title, year, season, episode, ep_title, info, source=None, group=None, absolute=None, episode_end=None, ext=".mkv"
):
    show = f"{sanitize(title)}" + (f" ({year})" if year else "")
    ep = f"S{season:02d}E{episode:02d}"
    if episode_end and episode_end != episode:
        ep += f"-E{episode_end:02d}"
    bits = [show, ep]
    if absolute is not None:
        bits.append(f"{absolute:03d}")
    bits.append(sanitize(ep_title) or f"Episode {episode}")
    tail = media_tail(info, source, group)
    name = " - ".join(bits) + (f" {tail}" if tail else "")
    return _cap(name) + ext


def movie_folder(title, year, tmdb_id=None, imdb_id=None):
    base = f"{sanitize(title)}" + (f" ({year})" if year else "")
    if tmdb_id:
        base += f" [tmdbid-{tmdb_id}]"
    elif imdb_id:
        base += f" [imdbid-{imdb_id}]"
    return base


def movie_name(title, year, info, source=None, group=None, edition=None, ext=".mkv"):
    base = f"{sanitize(title)}" + (f" ({year})" if year else "")
    if edition:
        base += f" {{edition-{sanitize(edition)}}}"
    tail = media_tail(info, source, group)
    return _cap(base + (f" {tail}" if tail else "")) + ext


def _cap(name, limit=240):
    """Keep filenames inside filesystem limits without cutting the tag block."""
    if len(name.encode("utf-8")) <= limit:
        return name
    m = re.search(r"\s*(\[[^\]]+\].*)$", name)
    tail = m.group(1) if m else ""
    head = name[: m.start()] if m else name
    while len((head + " " + tail).encode("utf-8")) > limit and len(head) > 10:
        head = head[:-1].rstrip()
    return (head + " " + tail).strip()

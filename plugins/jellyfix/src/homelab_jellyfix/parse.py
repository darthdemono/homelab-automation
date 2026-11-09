# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Parse scene / fansub release names into structured metadata.

Handles the shapes actually present in this library:
  Show.Name.S01E02.1080p.WEBRip.x265-GRP
  [Group] Show Name - 001 [BD 1080p][HEVC x265 10bit]
  Show Name - S01E02 - Episode Title.mkv
  Movie.Name.2007.DC.1080p.BluRay.x265-GRP
  Movie Name (1995) (1080p BluRay x265 HEVC 10bit AAC 5.1 Tigole)
  [group] Movie Name (Native Title) [BD-1080p] [DUAL-AUDIO] [x265]
"""

import os
import re

# ---------------------------------------------------------------- vocabulary

SOURCE_MAP = [
    (r"\b(?:uhd\s?)?blu-?ray\b|\bbdrip\b|\bbrrip\b|\bbd\b|\bbd[\s._-]?\d{3,4}[pi]\b", "Bluray"),
    (r"\bweb-?dl\b", "WEBDL"),
    (r"\bweb-?rip\b|\bwebrip\b", "WEBRip"),
    (r"\bhdtv\b", "HDTV"),
    (r"\bdvd-?rip\b|\bdvd\b", "DVD"),
    (r"\bremux\b", "Remux"),
    # Last resort, after the explicit forms above have had their turn: a bare
    # "WEB", and a fansub batch tagged "(Weekly)" — the weekly simulcast rip is
    # a WEB-DL, and the same groups mark their disc batches "[BD ...]", which
    # the Bluray pattern already catches first. Judas names Bleach this way and
    # states no source anywhere else in the title.
    (r"\bweb\b|\(weekly\)", "WEBDL"),
]

# tokens that are never part of a title
JUNK = r"""
 1080p|720p|2160p|4k|480p|576p|uhd|hd|sd
|1080|720|2160|480|576|1440
|x264|x265|h\.?264|h\.?265|hevc|avc|av1|xvid|divx|10bit|8bit|12bit|hi10p?|main10p?
|bluray|blu-ray|bdrip|brrip|bd|webrip|web-dl|webdl|web|hdtv|dvdrip|dvd|remux|hdrip
|aac|aac2|ac3|eac3|dd|ddp|dts|dts-hd|truehd|flac|opus|mp3|lpcm|atmos|dtshd|ma
|5\.1|7\.1|2\.0|6ch|2ch|8ch|dual-?audio|dual|multi-?subs?|eng-?subs?|esubs?|msubs?|subs?
|hdr10\+?|hdr|dovi|dv|sdr|repack|proper|extended|remastered|uncut|dc|imax
|complete|batch|season|nc(?:op|ed)|ova|oad|ncop|nced
"""
JUNK_RE = re.compile(r"(?ix)^(?:" + JUNK + r")$")

# Words like "complete", "extended" or "season" are junk in a *release folder*
# name but perfectly ordinary inside an episode title ("Complete Hollowification!"),
# so episode titles stop only at unmistakably technical tokens.
EPTITLE_STOP = re.compile(r"""(?ix)^(?:
 1080p|720p|2160p|4k|480p|576p|uhd
|x264|x265|h\.?264|h\.?265|hevc|avc|av1|xvid|10bit|8bit|hi10p?
|bluray|blu-ray|bdrip|brrip|webrip|web-dl|webdl|hdtv|dvdrip|remux
|aac|aac2|ac3|eac3|ddp?|dts|truehd|flac|opus|mp3|atmos
|5\.1|7\.1|2\.0|6ch|2ch|dual-?audio|multi-?subs?|eng-?subs?|esubs?|msubs?
|hdr10\+?|hdr|dovi|repack|proper
)$""")

RES_RE = re.compile(r"(?i)\b(2160|1080|720|576|480)[pi]\b")
YEAR_RE = re.compile(r"(?:\(|\[|\.|\s|^)((?:19|20)\d{2})(?:\)|\]|\.|\s|$)")

# season/episode patterns, most specific first
SXXEYY = re.compile(r"(?i)\bS(\d{1,3})[\s._-]?E(\d{1,4})(?:[\s._-]?-?E(\d{1,4}))?\b")
XxY = re.compile(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?![a-z0-9])")
SEASON_ONLY = re.compile(r"(?i)\bS(?:eason)?[\s._-]?(\d{1,2})\b")
SEASON_RANGE = re.compile(r"(?i)\bS(\d{1,2})[\s._-]*-[\s._-]*S(\d{1,2})\b")
EP_WORD = re.compile(r"(?i)\b(?:e|ep|episode)[\s._-]?(\d{1,4})\b")
# bare absolute number: " - 001 " or " - 001." typical of fansub anime
# fansub absolute episode: " - 01 - Title", "_-_01_(...)", " - 001.mkv"
ABSOLUTE = re.compile(r"(?:^|[\s_])-[\s_](\d{1,4})(?:v\d)?(?=[\s_.]|$)")

GROUP_BRACKET = re.compile(r"^\[([^\]]+)\]")
GROUP_TRAIL = re.compile(r"-([A-Za-z0-9_.]{2,20})$")

VERSION_RE = re.compile(r"(?i)(v\d)$")


def _strip_ext(name):
    base, ext = os.path.splitext(name)
    return base, ext.lower()


def detect_source(text):
    low = text.lower()
    if re.search(r"\bremux\b", low):
        return "Remux"
    for pat, label in SOURCE_MAP:
        if re.search(pat, low):
            return label
    return None


def detect_resolution(text):
    m = RES_RE.search(text)
    return f"{m.group(1)}p" if m else None


def detect_group(name):
    """Release group: leading [Group] (fansub) or trailing -GROUP (scene)."""
    m = GROUP_BRACKET.match(name)
    if m and not re.fullmatch(r"(?i)[\d\s.\-]+", m.group(1)):
        return m.group(1).strip()
    stripped = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name).strip()
    m = GROUP_TRAIL.search(stripped)
    if m and not JUNK_RE.match(m.group(1)) and not m.group(1).isdigit():
        return m.group(1)
    return None


def _clean_title(raw, dotted=None, stop_at_junk=True):
    """Turn a raw title fragment into a readable title."""
    raw = re.sub(r"\[[^\]]*\]", " ", raw)
    raw = re.sub(r"\([^)]*\)", " ", raw)
    # dots act as separators when the release name is dot-delimited overall
    if dotted is None:
        dotted = raw.count(".") >= 2 and " " not in raw.strip()
    if dotted and re.search(r"\w\.\w", raw):
        raw = raw.replace(".", " ")
    raw = raw.replace("_", " ")
    tokens = [t for t in re.split(r"\s+", raw) if t]
    # A scene/fansub title ends where the first quality-or-source token begins:
    # "Cyberpunk Edgerunners 1080p NF WEB-DL ..." -> "Cyberpunk Edgerunners".
    if stop_at_junk:
        for i, t in enumerate(tokens):
            bare = t.strip("[]()-_.")
            if JUNK_RE.match(bare) or RES_RE.fullmatch(bare) or re.fullmatch(r"(?i)S\d{1,2}(-S?\d{1,2})?", bare):
                tokens = tokens[:i]
                break
    while tokens and (
        (stop_at_junk and JUNK_RE.match(tokens[-1]))
        or tokens[-1] in "-–—"
        or (stop_at_junk and tokens[-1].lower() in ("the", "a", "an", "of", "and", "&"))
    ):
        tokens.pop()
    out = " ".join(tokens)
    out = re.sub(r"\s*-\s*$", "", out).strip(" -._([{")
    return re.sub(r"\s{2,}", " ", out)


def parse(name, is_dir=False):
    """Parse one release name. Returns a dict of best-effort fields."""
    base, ext = (name, "") if is_dir else _strip_ext(name)
    original = base

    r = {
        "original": name,
        "ext": ext,
        "title": None,
        "year": None,
        "season": None,
        "episode": None,
        "episode_end": None,
        "absolute": None,
        "season_range": None,
        "resolution": detect_resolution(base),
        "source": detect_source(base),
        "group": detect_group(base),
        "version": None,
        "episode_title": None,
        "kind": None,
    }

    m = VERSION_RE.search(base)
    if m:
        r["version"] = m.group(1)
        base = base[: m.start()]

    work = base.replace("_", " ") if base.count("_") >= 2 else base
    # some groups prefix the name: "flame-the.dog.days.of.winter.2018..."
    lead = re.match(r"^([A-Za-z0-9]{2,12})-(?=[a-z0-9])", work)
    if lead and "." in work[lead.end() :]:
        r["group"] = r["group"] or lead.group(1)
        work = work[lead.end() :]
    # remove a leading [Group] so it never pollutes the title
    if GROUP_BRACKET.match(work) and r["group"] and work.startswith("[" + r["group"]):
        work = work[len(r["group"]) + 2 :].strip()

    sr = SEASON_RANGE.search(work)
    if sr:
        r["season_range"] = (int(sr.group(1)), int(sr.group(2)))

    cut = len(work)
    if sr:
        cut = min(cut, sr.start())

    m = SXXEYY.search(work)
    if m:
        r["season"], r["episode"] = int(m.group(1)), int(m.group(2))
        if m.group(3):
            r["episode_end"] = int(m.group(3))
        cut = min(cut, m.start())
    else:
        m = XxY.search(work)
        if m:
            r["season"], r["episode"] = int(m.group(1)), int(m.group(2))
            cut = min(cut, m.start())
        else:
            m = ABSOLUTE.search(work)
            if m:
                r["absolute"] = int(m.group(1))
                cut = min(cut, m.start())
            else:
                m = EP_WORD.search(work)
                if m:
                    r["episode"] = int(m.group(1))
                    cut = min(cut, m.start())
                    s = SEASON_ONLY.search(work)
                    if s:
                        r["season"] = int(s.group(1))

    head = work[:cut]

    # year: prefer the last year in the head that is not the leading token
    # ("2001.A.Space.Odyssey.1968" -> 1968, title "2001 A Space Odyssey")
    def pick_year(text):
        ms = [m for m in YEAR_RE.finditer(text)]
        if not ms:
            return None
        non_leading = [m for m in ms if text[: m.start(1)].strip(" .[(_-") != ""]
        return (non_leading or ms)[-1]

    ym = pick_year(head)
    if ym:
        r["year"] = int(ym.group(1))
        head = head[: ym.start(1)]
    else:
        ym2 = pick_year(original)
        if ym2:
            r["year"] = int(ym2.group(1))

    # for episode files, text after SxxEyy is often the episode title
    if r["season"] is not None or r["absolute"] is not None:
        tail = work[cut:]
        tail = re.sub(r"(?i)^[\s._-]*S\d{1,3}[\s._-]?E\d{1,4}(?:[\s._-]?-?E\d{1,4})?", "", tail)
        tail = re.sub(r"^[\s._-]*\d{1,4}(?=[\s._-]|$)[\s._-]*", "", tail)
        tail = re.sub(r"^[\s._-]+", "", tail)
        # cut at the first junk token
        parts = [x for x in re.split(r"[\s._]+", tail) if x]
        keep = []
        for p in parts:
            # the media-tag block always opens with '[' — the title ends there
            if "[" in p or EPTITLE_STOP.match(p.strip("[]()")) or RES_RE.match(p) or re.match(r"^-\w+$", p):
                break
            keep.append(p)
        et = _clean_title(" ".join(keep), original.count(".") >= 3, stop_at_junk=False)
        r["episode_title"] = et or None

    dotted = (original.count(".") >= 3 and original.count(" ") <= 1) or (
        original.count(".") >= 2 and " " not in original
    )
    r["title"] = _clean_title(head, dotted) or None

    if r["season"] is not None or r["episode"] is not None or r["absolute"] is not None:
        r["kind"] = "episode"
    elif r["season_range"]:
        r["kind"] = "series_pack"
    else:
        r["kind"] = "movie"
    return r

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Build rename plans for a library by matching files against TVDB / TMDB."""

import os
import re

from . import media, naming
from .parse import parse
from .providers import TMDB, TVDB, score_match

MIN_SCORE = 70  # below this we refuse to guess and flag for review
EXISTING_TVDB = re.compile(r"\[tvdbid-(\d+)\]")
EXISTING_TMDB = re.compile(r"\[tmdbid-(\d+)\]")

SKIP_DIRS = {
    "extras",
    "featurettes",
    "behind the scenes",
    "trailers",
    "interviews",
    "scenes",
    "shorts",
    "other",
    "sample",
    "samples",
    "_broken",
    "_review",
}


def video_files(root, min_mb=50):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d.lower() not in SKIP_DIRS]
        for f in fns:
            if os.path.splitext(f)[1].lower() not in media.VIDEO_EXT:
                continue
            p = os.path.join(dp, f)
            try:
                if os.path.getsize(p) < min_mb * 1024 * 1024:
                    continue
            except OSError:
                continue
            out.append(p)
    return sorted(out)


def _issue(kind, path, detail):
    return {"kind": kind, "path": path, "detail": detail}


def _library_source(text):
    """Fall back to the folder name for the source tag, using the same
    regexes the filename parser uses (handles '(BD 1080p)', 'WEB-DL', ...)."""
    from .parse import detect_source

    return detect_source(text or "")


# --------------------------------------------------------------------- series


def english_title(cand):
    """TVDB often returns the native name; prefer the English translation."""
    strip = lambda n: re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", n or "").strip()
    tr = cand.get("translations") or {}
    for k in ("eng", "en"):
        if tr.get(k):
            return strip(tr[k])
    name = cand.get("name") or ""
    if re.search(r"[^\x00-\x7f]", name):
        for a in cand.get("aliases") or []:
            if a and not re.search(r"[^\x00-\x7f]", a):
                name = a
                break
    return re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", name).strip()


def _norm_eq(a, b):
    from .providers import _norm

    return _norm(a) == _norm(b)


def _best_series(guess, year, tvdb, prefer_jp=False, window=50, n_files=0):
    """Score candidates against name, aliases and translations.

    Returns (best, score, runner_up, exact, rank) where `rank` is the position in
    TVDB's own relevance ordering and `exact` means one of the winning series'
    names equals the parsed title after normalisation.
    """
    scored = []
    for rank, c in enumerate(tvdb.search_series(guess, year)[:window]):
        cy = c.get("year")
        names = [c.get("name", "")]
        names += [x for x in (c.get("aliases") or []) if x]
        names += [v for v in (c.get("translations") or {}).values() if v]
        names = [n for n in names if n]
        sc = max((score_match(guess, n, year, cy) for n in names), default=0)
        jp = c.get("country") == "jpn" or c.get("primary_language") == "jpn" or "jpn" in (c.get("translations") or {})
        if prefer_jp:
            sc += 8 if jp else -8
        scored.append((sc, any(_norm_eq(guess, n) for n in names), rank, c, jp))
    if not scored:
        return None, -1, -1, False, -1
    scored.sort(key=lambda t: (-t[0], t[2]))  # score desc, then relevance
    top = scored[0]
    runner = scored[1][0] if len(scored) > 1 else -1
    exacts = [t for t in scored if t[1]]
    # exactly one exact title match is decisive, whatever the fuzzy scores say
    if len(exacts) == 1:
        top = exacts[0]
    elif len(exacts) > 1 and n_files:
        # Several shows share the title ("One Piece": 1999 anime vs 2023
        # live-action). The library itself disambiguates: pick the candidate whose
        # episode count can actually account for the files on disk.
        def fits(cand):
            try:
                n = len(tvdb.episodes(int(cand["tvdb_id"])))
            except Exception:
                return (0, 0)  # unknown ranks last
            if n >= n_files:
                return (2, -(n - n_files))  # can account for every file
            return (1, -(n_files - n))  # too few episodes

        top = max(exacts[:4], key=lambda t: (fits(t[3]), t[0]))
    winner, score, exact, rank = top[3], top[0], top[1], top[2]
    # In an anime library a non-Japanese winner shadowed by a close Japanese
    # rival usually means the real show is missing from TVDB's results
    # ("Monster" returns the 2022 US drama, never the 2004 anime).
    if prefer_jp and not top[4]:
        rival = max((t[0] for t in scored if t[4]), default=-999)
        if rival >= score - 15:
            return winner, score, max(runner, rival), False, rank
    return winner, score, runner, exact, rank


def _identify_series(entry, path, tvdb, issues, prefer_jp=False, n_files=0):
    """Resolve a top-level folder to (tvdb_id, title, year) or None.

    Refuses to guess when the title is ambiguous: a generic name like "Monster"
    returns a crowd of similar shows, and picking the wrong one silently
    misfiles an entire series.
    """
    m = EXISTING_TVDB.search(entry)
    if m:
        sid = int(m.group(1))
        info = tvdb.series(sid)
        return sid, english_title(info), (info.get("firstAired") or "")[:4] or None

    p = parse(entry, is_dir=True)
    guess = p["title"] or entry

    attempts = [(guess, p["year"])]
    if p["year"]:
        attempts.append((guess, None))
    words = guess.split()
    attempts += [(" ".join(words[:c]), None) for c in (4, 3, 2) if len(words) > c]

    chosen = None
    for q, y in attempts:
        c, sc, runner, exact, rank = _best_series(q, y, tvdb, prefer_jp, n_files=n_files)
        if not c:
            continue
        margin = sc - runner
        # accept when the title is unmistakable: either TVDB's own top hit with an
        # exact title match, or a decisive lead over every other candidate
        if exact or (margin >= 10 and sc >= 85):
            return int(c["tvdb_id"]), english_title(c), c.get("year")
        if chosen is None:
            chosen = (c, sc, runner, exact)

    if chosen:
        c, sc, runner, exact = chosen
        issues.append(
            _issue(
                "ambiguous-series",
                path,
                f"title={guess!r} best={c.get('name')!r} score={sc:.0f} runner-up={runner:.0f} exact={exact}",
            )
        )
    else:
        issues.append(_issue("unmatched-series", path, f"title={guess!r} no candidates"))
    return None


def _title_score(parsed_title, name):
    """Best score across the whole parsed title and its ' -- ' separated parts.
    Releases often carry both the literal and broadcast title."""
    sc = score_match(parsed_title, name)
    for part in re.split(r"\s+--\s+", parsed_title):
        part = part.strip()
        if len(part) > 6:
            sc = max(sc, score_match(part, name))
    return sc


def _best_by_title(parsed_title, by_title):
    cand, best = None, 0
    for name, e in by_title:
        sc = _title_score(parsed_title, name)
        if sc > best:
            cand, best = e, sc
    return cand, best


def _folder_season(f):
    m = re.search(r"(?i)^\s*season\s*(\d+)", os.path.basename(os.path.dirname(f)))
    return int(m.group(1)) if m else None


def _match_episode(f, fp, by_se, by_abs, by_title=None):
    """Resolve a parsed filename to a TVDB episode.

    Order matters. An explicit SxxEyy is trusted, but validated against the
    episode title when the release carries one, because some releases put the
    ABSOLUTE number in the episode field ("Dragon Ball - S02E27" is S02E14).
    An explicit season never falls back to absolute numbering: that is what makes
    a "Season 2" folder of a *different* series collide with season 1.
    """
    et = fp.get("episode_title")
    fseason = _folder_season(f)

    if fp["season"] is not None and fp["episode"] is not None:
        se_hit = by_se.get((fp["season"], fp["episode"]))
        if se_hit is not None:
            if not et:
                return se_hit
            own = _title_score(et, se_hit.get("name") or "")
            if own >= 70:
                return se_hit
            alt, alt_sc = _best_by_title(et, by_title or [])
            if alt is not None and alt_sc >= 88 and alt_sc - own >= 15:
                return alt
            return se_hit
        if et and by_title:
            alt, alt_sc = _best_by_title(et, by_title)
            if alt is not None and alt_sc >= 90:
                return alt
        return None

    num = fp["absolute"] if fp["absolute"] is not None else fp["episode"]
    if num is not None:
        # a "Season N" folder means the number is per-season, not absolute
        if fseason is not None:
            e = by_se.get((fseason, num))
            if e:
                return e
        e = by_abs.get(num)
        if e:
            return e
        if fseason is None:
            e = by_se.get((1, num))
            if e:
                return e
    if et and by_title:
        alt, alt_sc = _best_by_title(et, by_title)
        if alt is not None and alt_sc >= 90:
            return alt
    return None


def plan_series(lib, tvdb=None, tmdb=None, anime=None):
    tvdb = tvdb or TVDB()
    anime = lib.get("anime", False) if anime is None else anime
    root = lib["path"]
    moves, issues = [], []

    for entry in sorted(os.listdir(root)):
        d = os.path.join(root, entry)
        if not os.path.isdir(d) or entry.startswith("."):
            continue
        files = video_files(d)
        if not files:
            continue

        ident = _identify_series(entry, d, tvdb, issues, prefer_jp=anime, n_files=len(files))
        if not ident:
            continue
        sid, stitle, syear = ident

        eps = tvdb.episodes(sid)
        if not eps:
            issues.append(_issue("no-episodes", d, f"tvdb {sid} returned no episodes"))
            continue
        by_se = {(e["seasonNumber"], e["number"]): e for e in eps}
        by_abs = {e["absoluteNumber"]: e for e in eps if e.get("absoluteNumber")}
        by_title = [(e["name"], e) for e in eps if e.get("name")]

        dest_root = os.path.join(root, naming.series_folder(stitle, syear, sid))
        infos = media.probe_many(files)

        for f in files:
            info = infos[f]
            if not info["ok"]:
                issues.append(_issue("unreadable", f, info.get("error")))
                continue
            fp = parse(os.path.basename(f))
            ep = _match_episode(f, fp, by_se, by_abs, by_title)
            if ep is None:
                issues.append(
                    _issue("unmatched-episode", f, f"S={fp['season']} E={fp['episode']} abs={fp['absolute']}")
                )
                continue
            name = naming.episode_name(
                stitle,
                syear,
                ep["seasonNumber"],
                ep["number"],
                ep.get("name") or "",
                info,
                fp["source"] or _library_source(entry),
                fp["group"],
                absolute=(ep.get("absoluteNumber") if anime else None),
                episode_end=fp["episode_end"],
                ext=os.path.splitext(f)[1].lower(),
            )
            dst = os.path.join(dest_root, naming.season_folder(ep["seasonNumber"]), name)
            if os.path.normpath(f) != os.path.normpath(dst):
                moves.append((f, dst))
    return _drop_collisions(moves, issues)


def _drop_collisions(moves, issues):
    """Two files claiming one episode is a real duplicate. Move neither, and say so."""
    seen = {}
    for src, dst in moves:
        seen.setdefault(dst, []).append(src)
    clean = []
    for dst, srcs in seen.items():
        if len(srcs) == 1:
            clean.append((srcs[0], dst))
        else:
            for s in srcs:
                issues.append(
                    _issue(
                        "duplicate-target",
                        s,
                        f"also claimed by {len(srcs) - 1} other file(s) -> {os.path.basename(dst)}",
                    )
                )
    clean.sort(key=lambda t: t[1])
    return clean, issues


# --------------------------------------------------------------------- movies


def plan_movies(lib, tmdb=None):
    tmdb = tmdb or TMDB()
    root = lib["path"]
    moves, issues = [], []

    entries = []
    for entry in sorted(os.listdir(root)):
        p = os.path.join(root, entry)
        if entry.startswith("."):
            continue
        if os.path.isdir(p):
            for f in video_files(p):
                entries.append((f, entry))
        elif os.path.splitext(entry)[1].lower() in media.VIDEO_EXT:
            entries.append((p, None))

    files = [f for f, _ in entries]
    infos = media.probe_many(files)

    for f, parent in entries:
        info = infos[f]
        if not info["ok"]:
            issues.append(_issue("unreadable", f, info.get("error")))
            continue

        cand_names = [os.path.basename(f)] + ([parent] if parent else [])
        # the parent folder usually carries the cleanest title AND the year
        parsed = [(cn, parse(cn, is_dir=(cn == parent))) for cn in cand_names]
        known_year = next((fp["year"] for _, fp in parsed if fp["year"]), None)

        best, bestsc, bestparse = None, -1, None
        for cn, fp in parsed:
            title = fp["title"]
            if not title:
                continue
            qyear = fp["year"] or known_year
            for c in tmdb.search_movie(title, qyear)[:8]:
                cy = (c.get("release_date") or "")[:4]
                sc = max(
                    score_match(title, c.get("title", ""), qyear, cy or None),
                    score_match(title, c.get("original_title", ""), qyear, cy or None),
                )
                # a release year contradicting the folder is almost always a
                # different film with a similar name
                if known_year and cy and abs(int(cy) - int(known_year)) > 1:
                    sc -= 40
                if cn == parent:
                    sc += 3
                if sc > bestsc:
                    best, bestsc, bestparse = c, sc, fp

        if not best or bestsc < MIN_SCORE:
            issues.append(
                _issue("unmatched-movie", f, f"best={best.get('title') if best else None!r} score={bestsc:.0f}")
            )
            continue

        mt = best["title"]
        my = (best.get("release_date") or "")[:4] or known_year
        folder = naming.movie_folder(mt, my, best["id"])
        name = naming.movie_name(
            mt,
            my,
            info,
            bestparse["source"] or _library_source(parent or ""),
            bestparse["group"],
            ext=os.path.splitext(f)[1].lower(),
        )
        dst = os.path.join(root, folder, name)
        if os.path.normpath(f) != os.path.normpath(dst):
            moves.append((f, dst))
    return moves, issues

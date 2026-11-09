# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Metadata providers: TVDB v4 (series) and TMDB v3/v4 (movies + series fallback).

All lookups are cached on disk under cache/ so repeated runs cost no API calls.
Credentials are passed in by the plugin (engine secrets) and are never logged.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request

MISSING_HINT = "is not set (declare it under the plugin secrets and provide it with unv or a .env file)"

CACHE_DIR = os.path.join(os.getcwd(), "cache")


def set_cache_dir(path):
    global CACHE_DIR
    CACHE_DIR = str(path)


def _cache_path(*parts):
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe = "_".join(re.sub(r"[^A-Za-z0-9._-]", "-", str(p))[:80] for p in parts)
    return os.path.join(CACHE_DIR, safe + ".json")


def _cached(key_parts, fn, ttl=None):
    p = _cache_path(*key_parts)
    if os.path.exists(p) and (ttl is None or time.time() - os.path.getmtime(p) < ttl):
        try:
            return json.load(open(p))
        except Exception:
            pass
    val = fn()
    json.dump(val, open(p, "w"), ensure_ascii=False)
    return val


def _get(url, headers=None, retries=3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as ex:
            last = ex
            code = getattr(ex, "code", None)
            if code in (401, 403, 404):
                raise
            time.sleep(1.5 * (i + 1))
    raise last


# ------------------------------------------------------------------ TVDB v4


class TVDB:
    BASE = "https://api4.thetvdb.com/v4"

    def __init__(self, env=None):
        env = env or {}
        self.key = env.get("TVDB_API_KEY")
        self._token = None

    @property
    def token(self):
        if self._token:
            return self._token
        if not self.key:
            raise RuntimeError("TVDB_API_KEY " + MISSING_HINT)
        body = json.dumps({"apikey": self.key}).encode()
        req = urllib.request.Request(f"{self.BASE}/login", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            self._token = json.load(r)["data"]["token"]
        return self._token

    def _auth_get(self, path):
        return _get(self.BASE + path, {"Authorization": "Bearer " + self.token})

    def search_series(self, title, year=None):
        def go():
            q = urllib.parse.quote(title)
            url = f"/search?query={q}&type=series"
            if year:
                url += f"&year={year}"
            return self._auth_get(url).get("data", [])

        return _cached(("tvdb", "search", title, year or ""), go, ttl=86400 * 30)

    def episodes(self, series_id, order="official", lang="eng"):
        """All episodes for a series, following pagination."""

        def go():
            eps, page = [], 0
            while True:
                d = self._auth_get(f"/series/{series_id}/episodes/{order}/{lang}?page={page}")
                batch = d["data"]["episodes"]
                if not batch:
                    break
                eps += batch
                if not d.get("links", {}).get("next"):
                    break
                page += 1
            return eps

        return _cached(("tvdb", "eps", series_id, order), go, ttl=86400 * 7)

    def series(self, series_id):
        return _cached(
            ("tvdb", "series", series_id), lambda: self._auth_get(f"/series/{series_id}")["data"], ttl=86400 * 30
        )


# ------------------------------------------------------------------ TMDB


class TMDB:
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, env=None):
        env = env or {}
        self.v4 = env.get("TMDB_V4")
        self.v3 = env.get("TMDB_V3")
        if not (self.v4 or self.v3):
            raise RuntimeError("TMDB_V4 / TMDB_V3 " + MISSING_HINT)

    def _req(self, path, **params):
        if self.v4:
            url = self.BASE + path
            if params:
                url += "?" + urllib.parse.urlencode(params)
            return _get(url, {"Authorization": "Bearer " + self.v4})
        params["api_key"] = self.v3
        return _get(self.BASE + path + "?" + urllib.parse.urlencode(params))

    def search_movie(self, title, year=None):
        def go():
            p = {"query": title}
            if year:
                p["year"] = year
            return self._req("/search/movie", **p).get("results", [])

        return _cached(("tmdb", "movie", title, year or ""), go, ttl=86400 * 30)

    def movie(self, movie_id):
        return _cached(("tmdb", "movieinfo", movie_id), lambda: self._req(f"/movie/{movie_id}"), ttl=86400 * 30)

    def search_tv(self, title, year=None):
        def go():
            p = {"query": title}
            if year:
                p["first_air_date_year"] = year
            return self._req("/search/tv", **p).get("results", [])

        return _cached(("tmdb", "tv", title, year or ""), go, ttl=86400 * 30)

    def tv_external_ids(self, tv_id):
        return _cached(("tmdb", "tvext", tv_id), lambda: self._req(f"/tv/{tv_id}/external_ids"), ttl=86400 * 30)


# ------------------------------------------------------------------ matching


def _norm(s):
    s = (s or "").lower()
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def score_match(query, candidate, qyear=None, cyear=None):
    """0..100 similarity, with a strong bonus for an exact year match."""
    import difflib

    a, b = _norm(query), _norm(candidate)
    if not a or not b:
        return 0
    s = difflib.SequenceMatcher(None, a, b).ratio() * 100
    if a == b:
        s = 100
    elif b.startswith(a) or a.startswith(b):
        s = max(s, 88)
    if qyear and cyear:
        if int(qyear) == int(cyear):
            s += 12
        elif abs(int(qyear) - int(cyear)) > 1:
            s -= 25
    return max(0, min(100, s))

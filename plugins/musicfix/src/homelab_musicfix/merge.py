# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Move new music from Downloads into the library, canonically named.

For each audio file under Downloads:

  1. It is probed and tagged exactly like a library file, so the same rules
     apply -- artist splitting, feat/EP/single stripping, NTFS-safe names.
  2. Its target is the canonical library path
     Music/<Album Artist>/<Album>/<Artist> - <Title>.<ext>.
  3. If nothing is there, it is moved in (a rename: Downloads and Music share
     the library volume, so this is instant and atomic).
  4. If a file is already there -- or two Downloads files want the same slot --
     the higher-quality copy wins (quality.quality_key). The loser goes to
     quarantine; nothing is deleted.

Per-track sidecars (.lrc, .jpg) travel with their track. Dry-run by default;
every move is journalled so it can be reversed.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import time
import unicodedata
from collections import defaultdict

from .settings import AUDIO_EXTS, IMAGE_EXTS, LYRIC_EXTS, MUSIC_ROOT, REPORTS, TMP_ROOT
from .naming import canonical_paths
from .fixtags import current_tags, desired_tags, needs_change, write_tags
from .quality import probe, quality_key
from .scan import SKIP_DIRS, walk
from .tags import build_vocabulary, join_artists, split_artists
from .plan import norm

DOWNLOADS_ROOT = os.environ.get("MUSICFIX_DOWNLOADS", os.path.join(os.path.expanduser("~"), "Downloads", "Music"))
Q_MERGE = TMP_ROOT / "quarantine/merge-losers"

# Playlist cover art arrives in the Downloads batch, not with the music: the
# exporter has nowhere else to put it, so it lands under a placeholder artist as
#     Unknown Artist/Unknown Album/<Playlist Name>.jpg
# It is not a track sidecar -- there is no audio in that directory and never will
# be -- so the orphan rule would quarantine it and the playlist would silently
# import with no cover. Route it to where playlist.py --cover looks instead.
# Seen: "LARP FEST, Noakhali.jpg" beside a "LARP FEST, Noakkhali.csv" (the names
# do not always agree, so matching stays fuzzy and is playlist.py's problem).
PLAYLIST_COVER_DIRS = {("unknown artist", "unknown album")}
PLAYLIST_COVER_DEST = os.environ.get(
    "MUSICFIX_PLAYLIST_COVERS", os.path.join(os.path.expanduser("~"), "playlist-covers")
)


def is_playlist_cover(rel):
    """True for an image sitting in the exporter's placeholder directory."""
    parts = rel.replace("\\", "/").split("/")
    if len(parts) != 3:
        return False
    if os.path.splitext(parts[2])[1].lower() not in IMAGE_EXTS:
        return False
    return (parts[0].lower(), parts[1].lower()) in PLAYLIST_COVER_DIRS


def index_music(music_root, inventory_path):
    """Map every existing library audio path (lowercased) to its quality.

    Uses reports/inventory.jsonl when it is present and current; otherwise the
    caller should re-scan first. Values are the quality dicts used for ranking.
    """
    idx = {}
    if os.path.exists(inventory_path):
        with open(inventory_path, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("error"):
                    continue
                idx[r["path"].lower()] = {"path": r["path"], "quality": r.get("quality") or {}}
    return idx


def scan_downloads(root, workers=12):
    """Probe + tag every audio file and collect sidecars under Downloads."""
    audio, sidecars = [], []
    for dirpath, _dirs, files in walk(root, workers=workers):
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if ext in AUDIO_EXTS:
                from .tags import read_tags

                audio.append({"path": rel, "abspath": full, "quality": probe(full), "tags": read_tags(full)})
            elif ext in (LYRIC_EXTS | IMAGE_EXTS):
                sidecars.append({"path": rel, "abspath": full, "ext": ext})
    return audio, sidecars


def same_recording(a_dur, b_dur) -> bool:
    """Could these two files hold the same recording at different quality?

    Everything else in the collision check is name-derived, and two *different*
    recordings of one song under one album derive the same canonical path. A
    Bauhaus batch shipped the album version of "Spirit" (track 5, 334.1s) and
    the single version (track 13, 225.6s), both tagged title="Spirit"; the
    quality comparison duly quarantined the album version as a "lower-quality
    duplicate". Nothing in the summary said otherwise.

    Duration settles it without decoding anything: the same take re-encoded
    varies by encoder padding, not by 108 seconds. Missing durations mean no
    evidence, so the old behaviour stands.
    """
    if not a_dur or not b_dur:
        return True
    return abs(a_dur - b_dur) <= max(2.0, 0.02 * max(a_dur, b_dur))


def _duration(rec) -> float:
    return float((rec.get("quality") or {}).get("duration") or 0.0)


def _leave_in_downloads(rec, down_sidecars, actions, stats, claimed, reason):
    """Take no action on a track, and hold on to its sidecars.

    Used when two files legitimately want one path. Claiming the sidecars stops
    the leftover pass from quarantining them as orphans -- the track is staying
    in Downloads, so its lyrics and art stay with it.
    """
    actions.append(
        {
            "kind": "collision-distinct",
            "src": rec["path"],
            "reason": reason,
            "duration": round(_duration(rec), 1),
            "dst_wanted": rec["canon"]["rel_path"],
        }
    )
    stats["collision-distinct"] += 1
    stem = os.path.splitext(os.path.basename(rec["path"]))[0]
    src_dir = os.path.dirname(rec["path"])
    for sc in down_sidecars:
        if os.path.dirname(sc["path"]) == src_dir and os.path.splitext(os.path.basename(sc["path"]))[0] == stem:
            claimed.add(sc["path"])


def build_merge_plan(down_audio, down_sidecars, music_idx, music_root, *, vocab):
    """Decide every move. Returns (actions, stats)."""
    actions, stats = [], defaultdict(int)
    # Sidecars matched by exact stem, and which extensions each track has
    # already taken -- a track owns at most one .jpg and one .lrc, since both
    # are filed as "<track stem>.<ext>" and a second would collide.
    claimed: set[str] = set()
    used_ext: dict[str, set[str]] = defaultdict(set)
    # dir -> the tracks resolved in it, for the leftover-sidecar pass
    by_dir: dict[str, list[dict]] = defaultdict(list)

    # Pull playlist covers out before anything treats them as track sidecars.
    for sc in list(down_sidecars):
        if is_playlist_cover(sc["path"]):
            actions.append({"kind": "playlist-cover", "src": sc["path"], "dst": os.path.basename(sc["path"])})
            stats["playlist-covers"] += 1
            claimed.add(sc["path"])

    # Resolve each Downloads track to a canonical library target.
    resolved = []
    for rec in down_audio:
        q = rec.get("quality") or {}
        if rec.get("error") or q.get("error"):
            actions.append({"kind": "skip-bad", "src": rec["path"], "reason": q.get("error") or rec.get("error")})
            stats["bad"] += 1
            continue
        rec["canon"] = _recanon(rec, vocab)
        if not rec["canon"]["rel_path"]:
            actions.append({"kind": "skip-bad", "src": rec["path"], "reason": "no usable title tag"})
            stats["bad"] += 1
            continue
        resolved.append(rec)

    # Group Downloads tracks that resolve to the same target so two incoming
    # copies of one song are compared to each other, not both moved in.
    by_target = defaultdict(list)
    for rec in resolved:
        by_target[rec["canon"]["rel_path"].lower()].append(rec)

    winners = {}  # target_lower -> chosen Downloads rec
    for tgt_lower, recs in by_target.items():
        # Two files wanting one path are only rivals if they are the same
        # recording. If their durations say otherwise they are different songs
        # that happen to derive one name, and picking a winner would delete
        # music. Refuse rather than guess -- both stay in Downloads.
        if any(not same_recording(_duration(a), _duration(b)) for a in recs for b in recs):
            for r in recs:
                others = ", ".join(f"{_duration(o):.0f}s" for o in recs if o is not r)
                _leave_in_downloads(
                    r,
                    down_sidecars,
                    actions,
                    stats,
                    claimed,
                    f"another Downloads file wants this same path but is a "
                    f"different length ({others}) -- different recordings",
                )
            continue
        best = max(recs, key=lambda r: quality_key(r))
        winners[tgt_lower] = best
        for r in recs:
            if r is not best:
                actions.append(
                    {
                        "kind": "quarantine-loser",
                        "src": r["path"],
                        "reason": "lower-quality duplicate within Downloads",
                        "kept": best["path"],
                    }
                )
                stats["down-internal-dupe"] += 1

    for rec in resolved:
        by_dir[os.path.dirname(rec["path"])].append(rec)

    for tgt_lower, rec in winners.items():
        target = rec["canon"]["rel_path"]
        existing = music_idx.get(tgt_lower)
        if existing is None:
            actions.append(
                {"kind": "move-in", "src": rec["path"], "dst": target, "quality": _q(rec), "retag": _retag(rec, vocab)}
            )
            stats["move-in"] += 1
            rec["_target"] = target
            _plan_sidecars(rec, down_sidecars, target, actions, stats, claimed, used_ext, replace=False)
        elif not same_recording(_duration(rec), float((existing.get("quality") or {}).get("duration") or 0.0)):
            # Same derived path, different length: the library's copy is a
            # different recording, so neither replacing it nor quarantining
            # this one is right.
            _leave_in_downloads(
                rec,
                down_sidecars,
                actions,
                stats,
                claimed,
                f"the library file at this path is a different length "
                f"({float((existing.get('quality') or {}).get('duration') or 0):.0f}s) "
                f"-- different recordings",
            )
        else:
            new_k = quality_key(rec)
            old_k = quality_key(existing)
            if new_k > old_k:
                # Incoming copy is better: existing library file steps aside.
                actions.append(
                    {
                        "kind": "replace",
                        "src": rec["path"],
                        "dst": target,
                        "demoted": existing["path"],
                        "new_quality": _q(rec),
                        "old_quality": existing["quality"],
                        "retag": _retag(rec, vocab),
                    }
                )
                stats["replace"] += 1
                rec["_target"] = target
                _plan_sidecars(rec, down_sidecars, target, actions, stats, claimed, used_ext, replace=True)
            else:
                actions.append(
                    {
                        "kind": "quarantine-loser",
                        "src": rec["path"],
                        "reason": "library already has an equal-or-better copy",
                        "kept": existing["path"],
                        "new_quality": _q(rec),
                        "kept_quality": existing["quality"],
                    }
                )
                stats["keep-existing"] += 1
                rec["_target"] = None
                _plan_sidecars(rec, down_sidecars, None, actions, stats, claimed, used_ext, quarantine=True)

    _plan_artist_art(down_sidecars, claimed, resolved, music_root, actions, stats)
    _plan_album_covers(down_sidecars, claimed, by_dir, music_root, actions, stats)
    _plan_leftover_sidecars(down_sidecars, claimed, used_ext, by_dir, actions, stats)
    return actions, dict(stats)


# Artist-level images arrive as "<Artist>/<Artist>_Avatar.jpg" (the photo) and
# "<Artist>/<Artist>_Header.jpg" (the background), two levels up from any
# audio, so the orphan rule would quarantine them. Navidrome reads artist.*;
# Jellyfin/Emby read fanart.* for the background.
_ARTIST_ART = {"avatar": "artist", "header": "fanart"}


def _plan_artist_art(down_sidecars, claimed, resolved, music_root, actions, stats):
    """File Avatar/Header images into the canonical artist folder, never over one."""
    artist_dir = {}
    for rec in resolved:
        if rec.get("_target"):
            artist_dir.setdefault(rec["path"].split("/")[0], rec["_target"].split("/")[0])
    for sc in down_sidecars:
        parts = sc["path"].replace("\\", "/").split("/")
        stem, ext = os.path.splitext(parts[-1])
        kind = _ARTIST_ART.get(stem.rsplit("_", 1)[-1].lower())
        if len(parts) != 2 or not kind or ext.lower() not in IMAGE_EXTS or parts[0] not in artist_dir:
            continue
        dst = f"{artist_dir[parts[0]]}/{kind}{ext.lower()}"
        if os.path.exists(os.path.join(music_root, dst)):
            continue  # leftover rule quarantines it; an existing image wins
        actions.append({"kind": "move-in-sidecar", "src": sc["path"], "dst": dst})
        stats["artist-art"] += 1
        claimed.add(sc["path"])


# Filenames an exporter uses for album art when it does not name it after the
# album itself. Compared folded, so "AlbumArt_Large" and "Folder" both land.
_COVER_STEMS = {"cover", "folder", "front", "album", "albumart", "albumartlarge", "albumartsmall", "artwork", "thumb"}


def _plan_album_covers(down_sidecars, claimed, by_dir, music_root, actions, stats):
    """File the album's own cover as <album dir>/folder.jpg.

    Album art arrives named after the *album*, not after a track, so nothing in
    the sidecar matcher had a slot for it and both outcomes were wrong:

      One-X.jpg      fuzzy-matched at 1.00 onto the track "Three Days Grace -
                     One-X" (the album and the title song share a name), took
                     that track's .jpg slot, and the real track art
                     "Three Days Grace - One X.jpg" was quarantined as an orphan.
      must-haves.jpg no track of that name existed, so it was quarantined --
                     and the album imported with no cover and no error.

    Only directories holding two or more tracks are considered. In a
    single-track directory the album name and the track title are routinely the
    same string, and the existing sole-track-in-dir rule already files that
    image against the track without losing it.
    """
    for d, tracks in sorted(by_dir.items()):
        targets = [t["_target"] for t in tracks if t.get("_target")]
        if len(tracks) < 2 or not targets:
            continue
        album_dir = os.path.dirname(targets[0])
        names = {norm(os.path.basename(album_dir)), norm(os.path.basename(d)), norm(tracks[0]["canon"]["album"])} - {""}
        # A track's own art is never album art, even if the names fold together.
        track_keys = {norm(os.path.splitext(os.path.basename(t["path"]))[0]) for t in tracks}
        for sc in sorted(down_sidecars, key=lambda x: x["path"]):
            if sc["path"] in claimed or os.path.dirname(sc["path"]) != d:
                continue
            if sc["ext"] not in IMAGE_EXTS:
                continue
            key = norm(os.path.splitext(os.path.basename(sc["path"]))[0])
            if key in track_keys or (key not in names and key not in _COVER_STEMS):
                continue
            claimed.add(sc["path"])
            dst = f"{album_dir}/folder{sc['ext']}"
            if os.path.exists(os.path.join(music_root, dst)):
                actions.append(
                    {"kind": "quarantine-loser", "src": sc["path"], "reason": f"album art, but {dst} already exists"}
                )
                stats["album-cover-losers"] += 1
            else:
                actions.append({"kind": "move-in-sidecar", "src": sc["path"], "dst": dst, "matched_by": "album-cover"})
                stats["album-covers"] += 1
            break  # one cover per album folder; anything else stays a sidecar


def _plan_sidecars(rec, down_sidecars, target, actions, stats, claimed, used_ext, *, replace=False, quarantine=False):
    """Move a track's .lrc/.jpg alongside it (or quarantine with a loser)."""
    d, fn = os.path.split(rec["path"])
    stem = os.path.splitext(fn)[0]
    for sc in down_sidecars:
        sd, sfn = os.path.split(sc["path"])
        if sd != d:
            continue
        sstem = os.path.splitext(sfn)[0]
        # Match the sidecar to this track by stem, allowing a ".cover" infix.
        if sstem.lower() != stem.lower() and sstem.lower() != f"{stem}.cover".lower():
            continue
        _emit_sidecar(
            rec,
            sc,
            target,
            actions,
            stats,
            claimed,
            used_ext,
            quarantine=quarantine or target is None,
            reason="sidecar of a Downloads loser",
        )


def _emit_sidecar(
    rec,
    sc,
    target,
    actions,
    stats,
    claimed,
    used_ext,
    *,
    quarantine,
    reason,
    matched_by=None,
    stat_key="sidecar-losers",
):
    """Record one sidecar decision and mark it (and the track's ext slot) taken."""
    claimed.add(sc["path"])
    if quarantine or target is None:
        actions.append({"kind": "quarantine-loser", "src": sc["path"], "reason": reason})
        stats[stat_key] += 1
        return
    used_ext[rec["path"]].add(sc["ext"])
    tdir, tfn = os.path.split(target)
    tstem = os.path.splitext(tfn)[0]
    a = {"kind": "move-in-sidecar", "src": sc["path"], "dst": f"{tdir}/{tstem}{sc['ext']}"}
    if matched_by:
        a["matched_by"] = matched_by
        a["track"] = rec["path"]
    actions.append(a)
    stats["sidecars"] += 1


# A sidecar is only paired with a track on a similarity guess when the
# directory holds more than one track. Below this the pair is rejected and the
# sidecar is left as an orphan rather than filed against the wrong song.
_SC_MIN_SCORE = 0.55
_SC_MIN_MARGIN = 0.05


def _sc_variants(rec):
    """The strings a sidecar's stem might plausibly have been built from.

    Taggers name the audio file from the tags but the sidecar from whatever
    listing they scraped, so the two disagree in ways an exact-stem match can
    never see: "Artist - Title (Feat. X)" beside "Artist feat. X - Title",
    U+2010 HYPHEN against "-", "i-dle" against "(G)I-DLE", "Bomb - Korean ver."
    against "Bomb". Scoring against title and "artist - title" as well as the
    filename stem catches those without loosening the exact match.
    """
    stem = os.path.splitext(os.path.basename(rec["path"]))[0]
    out = {norm(stem)}
    tags = rec.get("tags") or {}

    def _first(key):
        v = tags.get(key)
        if isinstance(v, list):
            v = v[0] if v else ""
        return str(v or "").strip()

    title, artist = _first("title"), _first("artist")
    if title:
        out.add(norm(title))
        if artist:
            out.add(norm(f"{artist} - {title}"))
    return {v for v in out if v}


def _script_set(text):
    """The writing systems the letters of `text` belong to.

    Digits, punctuation and spaces carry no script and are ignored, so "2020"
    and " - " never make two strings look related.
    """
    out = set()
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            name = unicodedata.name(ch)
        except ValueError:
            continue
        out.add(name.split()[0])
    return out


def _sc_title(rec):
    """The track's own title -- the part a sidecar stem is named after.

    The artist is deliberately excluded: a Latin-script artist in front of a
    Korean title ("LE SSERAFIM - 이브, 프시케 ...") would make every comparison
    look same-script and say nothing about whether the titles could ever match.
    """
    tags = rec.get("tags") or {}
    v = tags.get("title")
    if isinstance(v, list):
        v = v[0] if v else ""
    title = str(v or "").strip()
    return title or os.path.splitext(os.path.basename(rec["path"]))[0]


_LP_MIN_CONTAINED = 5


def _last_pair_ok(sidecar, track):
    """Whether the one sidecar and one track left in a directory may be paired.

    The rule exists for titles scoring cannot reach: "Eve, Psyche & The
    Bluebeard's wife" sits beside "이브, 프시케 그리고 푸른 수염의 아내", the same
    song with zero characters in common. Taking *every* leftover pair on that
    reasoning is too much, though -- a Volta batch shipped the sidecars for
    "Earth Intruders (Spike) - Extended Mark; Stent Mix" with no such track and
    they were filed against "I See Who You Are (Mark Bell mix)", putting the
    wrong lyrics on the song permanently. So the pair is taken only when the low
    score is explained:

      * different writing systems -- similarity could never have worked; or
      * one title contains the other as whole words, the version-qualifier drift
        that leaves "It Went" scoring 0.48 against "S3RL - It Went - Original
        Mix" purely on length.

    Two same-script titles with nothing in common stay an orphan.
    """
    stem = os.path.splitext(os.path.basename(sidecar["path"]))[0]
    a, b = _script_set(stem), _script_set(_sc_title(track))
    if not a or not b or a.isdisjoint(b):
        return True

    key = norm(stem)
    for v in _sc_variants(track):
        short, long = sorted((key, v), key=len)
        if len(short) >= _LP_MIN_CONTAINED and f" {short} " in f" {long} ":
            return True
    return False


def _plan_leftover_sidecars(down_sidecars, claimed, used_ext, by_dir, actions, stats):
    """Pair up sidecars whose stem does not match their track's filename.

    Runs after exact-stem matching and only over what it left behind. The
    dominant case is a directory holding exactly one track, where the pairing is
    unambiguous by position and needs no similarity guess at all; only genuinely
    multi-track directories fall through to scoring. A track takes at most one
    sidecar per extension, because both are filed as "<track stem>.<ext>".
    """
    leftovers = defaultdict(list)
    for sc in down_sidecars:
        if sc["path"] not in claimed:
            leftovers[os.path.dirname(sc["path"])].append(sc)

    for d, scs in leftovers.items():
        tracks = by_dir.get(d, [])
        by_ext = defaultdict(list)
        for sc in scs:
            by_ext[sc["ext"]].append(sc)

        for ext, group in by_ext.items():
            free = [t for t in tracks if ext not in used_ext[t["path"]]]
            if not free:
                for sc in group:
                    _emit_sidecar(
                        None,
                        sc,
                        None,
                        actions,
                        stats,
                        claimed,
                        used_ext,
                        quarantine=True,
                        reason="no track in this directory left to claim it",
                        stat_key="sidecar-orphans",
                    )
                continue

            # One track, one sidecar: the pairing is forced, no scoring needed.
            if len(free) == 1 and len(group) == 1:
                t = free[0]
                _emit_sidecar(
                    t,
                    group[0],
                    t.get("_target"),
                    actions,
                    stats,
                    claimed,
                    used_ext,
                    quarantine=t.get("_target") is None,
                    reason="sidecar of a Downloads loser",
                    matched_by="sole-track-in-dir",
                )
                continue

            # Otherwise score every pair and take the confident ones first, so a
            # clear match claims its track before a marginal one competes for it.
            scored = []
            for sc in group:
                key = norm(os.path.splitext(os.path.basename(sc["path"]))[0])
                for t in free:
                    best = max(difflib.SequenceMatcher(None, key, v).ratio() for v in _sc_variants(t))
                    scored.append((best, sc, t))
            scored.sort(key=lambda x: -x[0])

            taken_sc, taken_t = set(), set()
            for score, sc, t in scored:
                if sc["path"] in taken_sc or t["path"] in taken_t:
                    continue
                if score < _SC_MIN_SCORE:
                    continue
                rivals = [s for s, sc2, t2 in scored if sc2 is sc and t2 is not t and t2["path"] not in taken_t]
                if rivals and score - max(rivals) < _SC_MIN_MARGIN:
                    continue  # two tracks fit equally well; refuse to guess
                taken_sc.add(sc["path"])
                taken_t.add(t["path"])
                _emit_sidecar(
                    t,
                    sc,
                    t.get("_target"),
                    actions,
                    stats,
                    claimed,
                    used_ext,
                    quarantine=t.get("_target") is None,
                    reason="sidecar of a Downloads loser",
                    matched_by=f"fuzzy:{score:.2f}",
                )
            rest_sc = [sc for sc in group if sc["path"] not in taken_sc]
            rest_t = [t for t in free if ext not in used_ext[t["path"]]]

            # Scoring cannot bridge a translated title -- the sidecars for
            # "Eve, Psyche & The Bluebeard's wife" sit beside an audio file named
            # "이브, 프시케 그리고 푸른 수염의 아내", the same song in Korean. When one
            # sidecar and one track are all that is left in the directory, that
            # is the same forced pairing as the single-track case above, so take
            # it -- but only when the low score is explained (see
            # _last_pair_ok). Two same-script titles with nothing in common
            # stay orphans rather than being guessed at.
            if len(rest_sc) == 1 and len(rest_t) == 1 and _last_pair_ok(rest_sc[0], rest_t[0]):
                t = rest_t[0]
                _emit_sidecar(
                    t,
                    rest_sc[0],
                    t.get("_target"),
                    actions,
                    stats,
                    claimed,
                    used_ext,
                    quarantine=t.get("_target") is None,
                    reason="sidecar of a Downloads loser",
                    matched_by="last-pair-in-dir",
                )
                continue

            for sc in rest_sc:
                _emit_sidecar(
                    None,
                    sc,
                    None,
                    actions,
                    stats,
                    claimed,
                    used_ext,
                    quarantine=True,
                    reason="no confident track match in this directory",
                    stat_key="sidecar-orphans",
                )


def _recanon(rec, vocab):
    """Canonical library path for a Downloads file, using the shared resolver."""
    return canonical_paths(rec, vocabulary=vocab)


def _retag(rec, vocab):
    """The tag rewrite a moved-in file needs, or None if its tags are already right.

    The path is derived from *cleaned* tags -- clean_album strips "(single)"
    and a trailing "EP", split_artists unpacks a packed ARTIST, strip_feat
    pulls "(feat. X)" out of the title -- but until now merge only used the
    derived path. The file itself kept the raw tags, so a Bad Wolves single
    landed in Music/Bad Wolves/Zombie/ still tagged ALBUM="Zombie (single)"
    and Navidrome, which keys the album on the tag and not the folder, showed
    it under the uncleaned name. Same for "The Bela Session EP".

    Anything moved in is therefore retagged to what its path already claims,
    using the same desired_tags() the library-wide fixtags pass uses so the two
    can never disagree. Written after the move and journalled to
    tag_journal.jsonl, so `fixtags --restore` reverses it.
    """
    cur = current_tags(rec)
    want = desired_tags(rec, vocab)
    return {"from": cur, "to": want} if needs_change(cur, want) else None


def _q(rec):
    q = rec.get("quality") or {}
    return {k: q.get(k) for k in ("codec", "bit_rate", "sample_rate", "bit_depth", "lossless")}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Merge Downloads into the library (dry-run by default).")
    ap.add_argument("--downloads", default=DOWNLOADS_ROOT)
    ap.add_argument("--music", default=str(MUSIC_ROOT))
    ap.add_argument(
        "--inventory",
        default=str(REPORTS / "inventory.jsonl"),
        help="library inventory for collision quality; must be current",
    )
    ap.add_argument("--out", default=str(REPORTS / "merge_plan.jsonl"))
    ap.add_argument("--journal", default=str(REPORTS / "merge_journal.jsonl"))
    ap.add_argument(
        "--tag-journal",
        default=str(REPORTS / "tag_journal.jsonl"),
        help="where retags of moved-in files are journalled; shared with fixtags so `fixtags --restore` reverses them",
    )
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    print(f"[merge] scanning {args.downloads} ...", flush=True)
    down_audio, down_sidecars = scan_downloads(args.downloads, workers=args.workers)
    if args.limit:
        down_audio = down_audio[: args.limit]
    print(f"[merge] {len(down_audio)} audio, {len(down_sidecars)} sidecars in Downloads")

    music_idx = index_music(args.music, args.inventory)
    print(f"[merge] library index: {len(music_idx)} tracks (from {args.inventory})")

    # Vocabulary spans both libraries so artist splitting is consistent.
    vocab = build_vocabulary(down_audio)
    if os.path.exists(args.inventory):
        with open(args.inventory, encoding="utf-8") as fh:
            lib = [json.loads(l) for l in fh if l.strip()]
        vocab |= build_vocabulary(lib)

    actions, stats = build_merge_plan(down_audio, down_sidecars, music_idx, args.music, vocab=vocab)
    with open(args.out, "w", encoding="utf-8") as fh:
        for a in actions:
            fh.write(json.dumps(a, ensure_ascii=False) + "\n")

    print(f"[merge] {'COMMIT' if args.commit else 'DRY-RUN'} plan -> {args.out}")
    for k in sorted(stats):
        print(f"[merge]   {k:22} {stats[k]}")
    _preview(actions)

    if not args.commit:
        print("[merge] nothing moved; re-run with --commit")
        return 0

    return _apply(actions, args.downloads, args.music, args.journal, args.tag_journal)


def _preview(actions, n=8):
    retags = [a for a in actions if a.get("retag")]
    if retags:
        print(f"\n  RETAG ON MOVE-IN ({len(retags)}) -- tags cleaned to match the derived path")
        for a in retags[:n]:
            print(f"    {a['dst']}")
            for k in ("artist", "albumartist", "album", "title"):
                if a["retag"]["from"].get(k) != a["retag"]["to"].get(k):
                    print(f"        {k:12} {a['retag']['from'].get(k)!r}  ->  {a['retag']['to'].get(k)!r}")
        if len(retags) > n:
            print(f"    ... and {len(retags) - n} more")
    for kind, title in (
        ("replace", "REPLACE (Downloads better than library)"),
        ("quarantine-loser", "KEPT EXISTING / DUP (loser -> quarantine)"),
        ("skip-bad", "SKIPPED (unreadable)"),
    ):
        items = [a for a in actions if a["kind"] == kind]
        if not items:
            continue
        print(f"\n  {title} ({len(items)})")
        for a in items[:n]:
            line = f"    {a['src']}"
            if a.get("kept"):
                line += f"\n        kept: {a['kept']}"
            if a.get("demoted"):
                line += f"\n        demoted: {a['demoted']}"
            print(line)
        if len(items) > n:
            print(f"    ... and {len(items) - n} more")


def _apply(actions, downloads, music, journal_path, tag_journal_path):
    os.makedirs(os.path.dirname(journal_path), exist_ok=True)
    jf = open(journal_path, "a", encoding="utf-8")
    tjf = open(tag_journal_path, "a", encoding="utf-8")
    moved = quarantined = demoted = failed = covers = retagged = retag_failed = 0

    def _retag_file(a, dst):
        """Rewrite the moved file's tags to match the path it was named from."""
        nonlocal retagged, retag_failed
        rt = a.get("retag")
        if not rt or not dst:
            return
        err = write_tags(dst, rt["to"])
        rel = os.path.relpath(dst, music)
        tjf.write(
            json.dumps(
                {
                    "ts": time.time(),
                    "path": rel,
                    "before": rt["from"],
                    "after": rt["to"],
                    "error": err,
                    "source": "merge",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        if err:
            retag_failed += 1
            print(f"    RETAG ERROR {rel}: {err}")
        else:
            retagged += 1

    def _move(src, dst, op):
        nonlocal failed
        if not os.path.exists(src):
            print(f"    MISSING {src}")
            failed += 1
            return None
        if os.path.exists(dst):
            base, ext = os.path.splitext(dst)
            n = 2
            while os.path.exists(f"{base} ({n}){ext}"):
                n += 1
            dst = f"{base} ({n}){ext}"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            shutil.move(src, dst)
        jf.write(json.dumps({"ts": time.time(), "op": op, "src": src, "dst": dst}, ensure_ascii=False) + "\n")
        return dst

    for a in actions:
        kind = a["kind"]
        if kind == "replace":
            # Library copy steps aside first, then the better file moves in.
            old = os.path.join(music, a["demoted"])
            qdst = os.path.join(str(Q_MERGE), "demoted", a["demoted"])
            if _move(old, qdst, "merge-demote"):
                demoted += 1
            dst = _move(os.path.join(downloads, a["src"]), os.path.join(music, a["dst"]), "merge-in")
            if dst:
                moved += 1
                _retag_file(a, dst)
        elif kind in ("move-in", "move-in-sidecar"):
            dst = _move(os.path.join(downloads, a["src"]), os.path.join(music, a["dst"]), "merge-in")
            if dst:
                moved += 1
                _retag_file(a, dst)
        elif kind == "playlist-cover":
            if _move(
                os.path.join(downloads, a["src"]), os.path.join(PLAYLIST_COVER_DEST, a["dst"]), "merge-playlist-cover"
            ):
                covers += 1
        elif kind == "quarantine-loser":
            if _move(os.path.join(downloads, a["src"]), os.path.join(str(Q_MERGE), "losers", a["src"]), "merge-loser"):
                quarantined += 1
    jf.close()
    tjf.close()
    print(
        f"\n[merge] moved in {moved}, demoted {demoted}, quarantined {quarantined}, "
        f"playlist covers {covers}, failed {failed}"
    )
    print(f"[merge] retagged {retagged} files ({retag_failed} failed) -> {tag_journal_path}")
    print(f"[merge] losers/demoted under {Q_MERGE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

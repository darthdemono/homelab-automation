# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""One canonical casing per artist, decided library-wide, then made true on disk.

The library had 14 artists living in two folders at once -- `System Of A Down`
beside `System of a Down`, `DREAMCATCHER` beside `Dreamcatcher`. Two things
produce that, and both have to be fixed or it comes straight back:

  * **the tags**, because `naming.canonical_paths` derives the folder from the
    ALBUMARTIST tag. Two files tagged with two spellings of one artist derive two
    folders, and every later merge re-creates the split.
  * **the mount**, because ntfs-3g here is case-*sensitive* at the POSIX layer,
    so those really are two directories with two inodes and `rename(2)` will
    happily make a third.

So this decides the winning spelling **once, from the whole library**, writes it
into the tags, and then merges the stray directories into the winner. Same shape
as `genres.py`, which does exactly this for genre strings.

Deciding the winner, in order:

  1. **Spellings attested on files that carry a MusicBrainz artist id.** Those
     came out of a real tagger rather than a filename guess, and this tier alone
     is what picks `blackbear` over `Blackbear` (4 files say `Blackbear`, but
     only the 2 `blackbear` ones have an MBID), and `OutKast` over `Outkast`.
  2. **Frequency**, for artists no MBID vouches for.
  3. **More upper-case characters**, as a deterministic tiebreak, matching
     `genres.canonical_casing`.

A genuine 1-vs-1 tie with no MBID on either side is a coin flip, so those are
printed separately in the dry run -- they are the ones worth eyeballing, and
`CASING_OVERRIDES` is the place to pin one.

Dry-run by default. Tag writes and moves both go to
`reports/casing_journal.jsonl`; `--restore` reverses both, newest first.
Nothing is deleted -- a file that would land on an occupied path goes to
`tmp/quarantine/case-merge-losers/`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections import Counter, defaultdict

import mutagen
from mutagen.id3 import ID3, TALB, TPE1, TPE2
from mutagen.mp4 import MP4

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS, TMP_ROOT

JOURNAL = REPORTS / "casing_journal.jsonl"
Q_CASE = TMP_ROOT / "quarantine/case-merge-losers"

# Pin a spelling the evidence gets wrong. Keyed by the lowercased name, so it
# wins regardless of what the tags say. Keep this short -- every entry is a
# statement that the library's own metadata is not to be trusted for that name.
CASING_OVERRIDES: dict[str, str] = {}

ID3_FRAMES = {"artist": (TPE1, "TPE1"), "albumartist": (TPE2, "TPE2"), "album": (TALB, "TALB")}
MP4_KEYS = {"artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb"}
FIELDS = ("artist", "albumartist")


def _values(tags, key):
    v = tags.get(key) or []
    if isinstance(v, str):
        v = [v]
    return [str(x) for x in v]


def build_casing_map(records) -> tuple[dict, dict]:
    """lowercased artist -> canonical spelling, decided over the whole library.

    Returns (canon, evidence) where evidence keeps the per-tier counts so the
    dry run can show *why* each name resolved the way it did.
    """
    seen = defaultdict(Counter)  # lower -> {spelling: n}
    vouched = defaultdict(Counter)  # same, but only files carrying an MBID
    for r in records:
        if r.get("error"):
            continue
        tags = r.get("tags") or {}
        has_mbid = bool(tags.get("musicbrainz_artistid"))
        for field in FIELDS:
            for name in _values(tags, field):
                name = name.strip()
                if not name:
                    continue
                seen[name.lower()][name] += 1
                if has_mbid:
                    vouched[name.lower()][name] += 1

    canon, evidence = {}, {}
    for low, counter in seen.items():
        if low in CASING_OVERRIDES:
            canon[low] = CASING_OVERRIDES[low]
            evidence[low] = {"tier": "override", "all": counter, "mbid": vouched.get(low, Counter())}
            continue
        # An MBID-backed spelling outranks a merely popular one.
        tier = "mbid" if vouched.get(low) else "frequency"
        canon[low] = _rank_spellings(counter, vouched.get(low))
        evidence[low] = {"tier": tier, "all": counter, "mbid": vouched.get(low, Counter())}
    return canon, evidence


def _rank_spellings(counter, vouched):
    """Pick a spelling: MusicBrainz-vouched tier, then overall frequency, then
    upper-case count. Shared by artists and albums so both answer the same way --
    "Off The Bone" outnumbers "Off the Bone" 2 to 1, but only the latter is on a
    file MusicBrainz tagged, and it is the one the band actually used."""
    pool = vouched or counter
    return max(pool.items(), key=lambda kv: (kv[1], counter[kv[0]], sum(c.isupper() for c in kv[0])))[0]


def build_album_casing_map(records) -> dict:
    """(album-artist, album) lowercased -> canonical album spelling.

    Keyed by the artist too, deliberately. Album titles are not unique across the
    library -- forcing one casing on every "Greatest Hits" would rewrite the tags
    of unrelated releases that merely share a name.
    """
    seen = defaultdict(Counter)
    vouched = defaultdict(Counter)
    for r in records:
        if r.get("error"):
            continue
        tags = r.get("tags") or {}
        album = (_values(tags, "album") or [""])[0].strip()
        if not album:
            continue
        aa = (_values(tags, "albumartist") or _values(tags, "artist") or [""])[0].strip()
        key = (aa.lower(), album.lower())
        seen[key][album] += 1
        if tags.get("musicbrainz_albumid") or tags.get("musicbrainz_trackid"):
            vouched[key][album] += 1
    return {key: _rank_spellings(c, vouched.get(key)) for key, c in seen.items()}


def contested(evidence) -> dict:
    """Names spelled more than one way, i.e. the ones this pass exists for."""
    return {low: ev for low, ev in evidence.items() if len(ev["all"]) > 1}


def coin_flips(evidence) -> dict:
    """Contested names where the winning tier had no majority to work with.

    A 1-vs-1 with no MBID on either side is decided purely by the upper-case
    tiebreak, which is arbitrary. Worth a human glance.
    """
    out = {}
    for low, ev in contested(evidence).items():
        if ev["tier"] == "override":
            continue
        pool = ev["mbid"] if ev["mbid"] else ev["all"]
        ranked = sorted(pool, key=lambda s: (-pool[s], -ev["all"][s]))
        if len(ranked) > 1 and (pool[ranked[0]], ev["all"][ranked[0]]) == (pool[ranked[1]], ev["all"][ranked[1]]):
            out[low] = ev
    return out


def desired_values(tags, canon, albums=None) -> dict:
    """The artist/albumartist/album values this file should carry, case-fixed."""
    want = {}
    for field in FIELDS:
        cur = _values(tags, field)
        if not cur:
            continue
        new = [canon.get(v.strip().lower(), v) if v.strip() else v for v in cur]
        if new != cur:
            want[field] = new

    if albums is not None:
        cur = _values(tags, "album")
        if cur and cur[0].strip():
            aa = (_values(tags, "albumartist") or _values(tags, "artist") or [""])[0]
            # Look the album up under the artist's *canonical* spelling as well,
            # since the albumartist tag may be the one this pass is fixing.
            aa_l = aa.strip().lower()
            best = albums.get((aa_l, cur[0].strip().lower()))
            if best is None:
                canon_aa = canon.get(aa_l, aa).strip().lower()
                best = albums.get((canon_aa, cur[0].strip().lower()))
            if best and best != cur[0]:
                want["album"] = [best]
    return want


# --------------------------------------------------------------------------
# tag writing


def _write_tags(path, want) -> dict:
    """Apply the casing fix to one file. Returns the previous values."""
    mf = mutagen.File(path)
    if mf is None:
        raise ValueError("unrecognised audio format")
    before = {}

    if isinstance(mf, MP4):
        for field, values in want.items():
            key = MP4_KEYS[field]
            before[field] = [str(x) for x in (mf.tags.get(key) or [])]
            mf.tags[key] = values
    elif isinstance(mf.tags, ID3) or path.lower().endswith(".mp3"):
        if mf.tags is None:
            mf.add_tags()
        for field, values in want.items():
            frame, fid = ID3_FRAMES[field]
            existing = mf.tags.get(fid)
            before[field] = list(existing.text) if existing else []
            mf.tags.setall(fid, [frame(encoding=3, text=values)])
        mf.save(v2_version=4)
        return before
    else:
        for field, values in want.items():
            before[field] = _values(mf.tags or {}, field)
            mf.tags[field] = values

    mf.save()
    return before


# --------------------------------------------------------------------------
# directory merging


def variant_groups(root):
    """Directories that differ only by case, grouped, artist and album level."""
    groups = []
    tops = [d for d in os.listdir(root) if not d.startswith(".") and os.path.isdir(os.path.join(root, d))]
    by_lower = defaultdict(list)
    for d in tops:
        by_lower[d.lower()].append(d)
    for low, names in by_lower.items():
        if len(names) > 1:
            groups.append(("artist", "", low, sorted(names)))

    for d in tops:
        base = os.path.join(root, d)
        subs = [s for s in os.listdir(base) if os.path.isdir(os.path.join(base, s))]
        sub_lower = defaultdict(list)
        for s in subs:
            sub_lower[s.lower()].append(s)
        for low, names in sub_lower.items():
            if len(names) > 1:
                groups.append(("album", d, low, sorted(names)))
    return groups


def _audio_count(path):
    return sum(len([f for f in files if os.path.splitext(f)[1].lower() in AUDIO_EXTS]) for _, _, files in os.walk(path))


_JOINS = (", ", " and ")


def canon_folder_name(name, canon):
    """Apply the casing map to a folder name, including a joined one.

    An artist folder is not always one name -- `naming.join_artists` produces
    `X, Y and Z`, whose lowercased form is in no map. Resolving each component
    separately is what makes `pewdiepie and Party in Backyard` come out as
    `PewDiePie and Party in Backyard` on the evidence rather than by luck.
    """
    direct = canon.get(name.lower())
    if direct:
        return direct
    parts, rest = [], name
    seps = []
    while True:
        hits = [(rest.find(s), s) for s in _JOINS if rest.find(s) != -1]
        if not hits:
            break
        i, s = min(hits)
        parts.append(rest[:i])
        seps.append(s)
        rest = rest[i + len(s) :]
    parts.append(rest)
    if len(parts) < 2:
        return None
    fixed = [canon.get(p.strip().lower(), p) for p in parts]
    # Deliberately returned even when nothing changed: the caller is asking
    # "which spelling is canonical", and a joined name that is already correct
    # still has to be recognised as the winner rather than falling through to
    # the directory-size fallback.
    out = fixed[0]
    for sep, part in zip(seps, fixed[1:]):
        out += sep + part
    return out


def pick_winner(kind, names, canon, root, parent, albums=None):
    """Which spelling of a directory survives.

    The tag map decides wherever it can, because the tags are what
    `canonical_paths` regenerates the folder from -- picking anything else would
    leave the directory disagreeing with the tag and invite the split back. Only
    when no tag vouches for a spelling does the fullest directory win, so that
    the fewest files have to move.
    """
    if kind == "artist":
        want = canon_folder_name(names[0], canon)
        for n in names:
            if want and n == want:
                return n, "tag-canonical"
    elif kind == "album" and albums:
        want = albums.get((parent.lower(), names[0].lower()))
        if want is None:
            # The folder is the album artist's canonical spelling; the tags may
            # still be keyed under whatever spelling they had before.
            for (aa, alb), spelling in albums.items():
                if alb == names[0].lower() and canon.get(aa, aa).lower() == parent.lower():
                    want = spelling
                    break
        for n in names:
            if want and n == want:
                return n, "tag-canonical"
    counts = {n: _audio_count(os.path.join(root, parent, n)) for n in names}
    best = max(names, key=lambda n: (counts[n], sum(c.isupper() for c in n)))
    return best, f"most-audio({counts[best]})"


def _resolve_target_dir(root, rel_dir, commit):
    """Map a relative dir onto the target tree, reusing case-variant components.

    Walking component by component matters: moving into `5 Seconds of Summer`
    must reuse its existing `CALM`, not create a second `CALM` beside it.
    """
    cur = root
    for part in rel_dir.split(os.sep):
        if not part:
            continue
        nxt = os.path.join(cur, part)
        if not os.path.isdir(nxt) and os.path.isdir(cur):
            match = next(
                (d for d in os.listdir(cur) if d.lower() == part.lower() and os.path.isdir(os.path.join(cur, d))), None
            )
            if match:
                nxt = os.path.join(cur, match)
        cur = nxt
    return cur


def plan_dir_merges(root, canon, albums=None):
    """Every file that has to move so each case group lives in one directory."""
    moves, kept = [], []
    for kind, parent, low, names in variant_groups(root):
        winner, why = pick_winner(kind, names, canon, root, parent, albums)
        kept.append(
            {"kind": kind, "parent": parent, "winner": winner, "losers": [n for n in names if n != winner], "why": why}
        )
        win_root = os.path.join(root, parent, winner)
        for loser in names:
            if loser == winner:
                continue
            lose_root = os.path.join(root, parent, loser)
            for dirpath, _d, files in os.walk(lose_root):
                rel = os.path.relpath(dirpath, lose_root)
                rel = "" if rel == "." else rel
                for fn in files:
                    src = os.path.join(dirpath, fn)
                    dst_dir = _resolve_target_dir(win_root, rel, commit=False)
                    moves.append({"src": src, "dst": os.path.join(dst_dir, fn), "group": os.path.join(parent, low)})
    return moves, kept


def plan_file_renames(root, canon):
    """Filenames whose artist prefix still spells the artist the old way.

    The layout is `<Track Artist> - <Title>.ext`, so the artist name is in the
    basename as well as the folder and the tags. Fixing only the folder leaves
    `OutKast/Stankonia/Outkast - Ms. Jackson.flac`, which disagrees with itself.

    Deliberately restricted to changes that are **case-only**: this pass is not
    allowed to re-derive a name, only to re-case the one already there. Anything
    else is `plan`/`apply`'s job.
    """
    renames = []
    for dirpath, dirnames, files in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in files:
            stem, ext = os.path.splitext(fn)
            prefix, sep, rest = stem.partition(" - ")
            if not sep or not prefix.strip():
                continue
            want = canon.get(prefix.strip().lower()) or canon_folder_name(prefix, canon)
            if not want or want == prefix:
                continue
            if want.lower() != prefix.lower():
                continue  # not a casing difference; leave it alone
            src = os.path.join(dirpath, fn)
            dst = os.path.join(dirpath, f"{want}{sep}{rest}{ext}")
            if src != dst:
                renames.append({"src": src, "dst": dst})
    return renames


def _apply_renames(renames, jf):
    """Rename in place. On this case-sensitive mount src and dst are distinct
    paths, so no two-step dance is needed -- but an occupied target is still a
    different file and must never be clobbered."""
    done = skipped = 0
    for r in renames:
        src, dst = r["src"], r["dst"]
        if not os.path.exists(src) or os.path.exists(dst):
            skipped += 1
            continue
        try:
            os.rename(src, dst)
        except OSError:
            skipped += 1
            continue
        jf.write(
            json.dumps({"ts": time.time(), "op": "case-rename", "src": src, "dst": dst}, ensure_ascii=False) + "\n"
        )
        done += 1
    return done, skipped


def _apply_moves(moves, jf):
    moved = quarantined = failed = 0
    for m in moves:
        src, dst = m["src"], m["dst"]
        if not os.path.exists(src):
            failed += 1
            continue
        # An occupied target is a real file, not a casing artefact. Never
        # overwrite it -- the incoming copy steps aside instead.
        if os.path.exists(dst):
            rel = os.path.relpath(src, str(MUSIC_ROOT))
            qdst = os.path.join(str(Q_CASE), rel)
            os.makedirs(os.path.dirname(qdst), exist_ok=True)
            try:
                os.rename(src, qdst)
            except OSError:
                shutil.move(src, qdst)
            jf.write(
                json.dumps({"ts": time.time(), "op": "case-quarantine", "src": src, "dst": qdst}, ensure_ascii=False)
                + "\n"
            )
            quarantined += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.rename(src, dst)
        except OSError:
            shutil.move(src, dst)
        jf.write(json.dumps({"ts": time.time(), "op": "case-move", "src": src, "dst": dst}, ensure_ascii=False) + "\n")
        moved += 1
    return moved, quarantined, failed


def _prune_empty(root, kept):
    """Remove the directory shells the merge emptied.

    Emptiness is re-read from the filesystem at each step rather than taken from
    `os.walk`'s `dirnames`: walk caches that listing when it enters the parent,
    so after a child is removed the parent still lists it, and any check that
    trusts the cache either skips a now-empty parent or blows up calling
    `os.listdir` on the child that just went away.
    """
    removed = []
    for k in kept:
        for loser in k["losers"]:
            path = os.path.join(root, k["parent"], loser)
            # Deepest first, so a parent is only considered once its children
            # have had their chance to go.
            for dirpath, _dirnames, _files in os.walk(path, topdown=False):
                _rmdir_if_empty(dirpath, removed)
            _rmdir_if_empty(path, removed)
    return removed


def _rmdir_if_empty(path, removed):
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            removed.append(path)
    except OSError:
        pass


# --------------------------------------------------------------------------


def restore(journal_path, *, commit):
    """Reverse this pass: moves back to where they came from, then tags."""
    if not os.path.exists(journal_path):
        print(f"[casing] no journal at {journal_path}")
        return 0
    entries = [json.loads(l) for l in open(journal_path, encoding="utf-8") if l.strip()]
    ok = failed = 0
    for e in reversed(entries):
        op = e.get("op")
        try:
            if op in ("case-move", "case-quarantine", "case-rename"):
                if not commit:
                    ok += 1
                    continue
                os.makedirs(os.path.dirname(e["src"]), exist_ok=True)
                os.rename(e["dst"], e["src"])
                ok += 1
            elif op == "case-tags":
                if not commit:
                    ok += 1
                    continue
                _write_tags(e["path"], e["before"])
                ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    FAILED {e.get('dst') or e.get('path')}: {exc}")
            failed += 1
    print(f"[casing] {'restored' if commit else 'would restore'} {ok}, {failed} failed")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="One canonical casing per artist, in the tags and on disk (dry-run by default)."
    )
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--no-tags", action="store_true", help="skip the tag rewrite")
    ap.add_argument("--no-dirs", action="store_true", help="skip the directory merge")
    ap.add_argument("--no-renames", action="store_true", help="skip re-casing the artist prefix in filenames")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    if args.restore:
        return restore(args.journal, commit=args.commit)

    records = [json.loads(l) for l in open(args.inventory, encoding="utf-8") if l.strip()]
    canon, evidence = build_casing_map(records)
    albums = build_album_casing_map(records)
    fights = contested(evidence)
    flips = coin_flips(evidence)

    print(f"[casing] {len(canon)} distinct artist names; {len(fights)} spelled more than one way")
    for low, ev in sorted(fights.items(), key=lambda kv: -sum(kv[1]["all"].values())):
        allc = ", ".join(f"{s!r}={n}" for s, n in ev["all"].most_common())
        print(f"  -> {canon[low]!r}   [{ev['tier']}]   from {allc}")
    if flips:
        print(
            f"\n[casing] {len(flips)} decided only by the upper-case tiebreak "
            f"(no MBID, no majority) -- pin one in CASING_OVERRIDES if wrong:"
        )
        for low in sorted(flips):
            print(f"    {canon[low]!r}   from {dict(flips[low]['all'])}")

    changes = []
    if not args.no_tags:
        for r in records:
            if r.get("error"):
                continue
            want = desired_values(r.get("tags") or {}, canon, albums)
            if want:
                changes.append((r["path"], want))
        if args.limit:
            changes = changes[: args.limit]
    print(f"\n[casing] {len(changes)} files need a tag casing fix")
    for path, want in changes[:10]:
        bits = "; ".join(f"{k}={v}" for k, v in want.items())
        print(f"    {path}\n        {bits}")
    if len(changes) > 10:
        print(f"    ... and {len(changes) - 10} more")

    moves, kept = ([], [])
    if not args.no_dirs:
        moves, kept = plan_dir_merges(args.root, canon, albums)
    renames = [] if args.no_renames else plan_file_renames(args.root, canon)
    print(
        f"\n[casing] {len(kept)} case-variant directory groups, "
        f"{len(moves)} files to move; {len(renames)} filenames to re-case"
    )
    for k in kept:
        where = f"{k['parent']}/" if k["parent"] else ""
        print(f"    {where}{k['winner']!r}  <-  {k['losers']}   [{k['why']}]")

    if not args.commit:
        print("\n[casing] nothing written; re-run with --commit")
        return 0

    os.makedirs(os.path.dirname(args.journal), exist_ok=True)
    ok = failed = 0
    with open(args.journal, "a", encoding="utf-8") as jf:
        for path, want in changes:
            full = os.path.join(args.root, path)
            try:
                before = _write_tags(full, want)
                jf.write(
                    json.dumps(
                        {"ts": time.time(), "op": "case-tags", "path": full, "before": before, "after": want},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"    FAILED {path}: {exc}")
                failed += 1
        print(f"[casing] {ok} files retagged, {failed} failed")

        moved, quarantined, mfailed = _apply_moves(moves, jf)
        print(f"[casing] moved {moved}, quarantined {quarantined}, failed {mfailed}")

        # Re-planned, not just re-applied: the moves above changed where these
        # files live, so anything planned against the pre-move tree points at a
        # src that no longer exists and would be silently skipped.
        if not args.no_renames:
            renames = plan_file_renames(args.root, canon)
        rdone, rskipped = _apply_renames(renames, jf)
        print(f"[casing] re-cased {rdone} filenames, skipped {rskipped}")

    removed = _prune_empty(args.root, kept)
    print(f"[casing] removed {len(removed)} emptied directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

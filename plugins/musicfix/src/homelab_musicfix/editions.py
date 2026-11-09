# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Fold partial edition folders into the album they belong to.

A downloader that fetches one track at a time files each under whatever release
Spotify names it on, so `Fighting Demons` (23 tracks) ends up beside
`Fighting Demons (Deluxe)` (7 stray tracks). Navidrome and Symphonium then show
two albums, both with a broken tracklist.

For every `edition` / `variant` group from `albums.cross_folder_groups` the
largest folder is the base album, and each track of the others is:

  * a duplicate (same folded title, durations within 2 s): the better file by
    `quality_key` keeps the base slot, the other is quarantined. If the
    edition copy credits more artists (a featured guest) the kept file adopts
    that credit and is renamed.
  * otherwise an extra (remix, slowed, live...): retagged to the base album on
    the next disc, numbered in its original order, and moved into the base folder.

Sidecars (.lrc, .jpg, ...) follow their track. Nothing is deleted; losers go to
`tmp/quarantine/edition-merge/`. Dry-run by default, journalled to
`reports/editions_journal.jsonl`; `--restore --commit` reverses it.

    python -m homelab_musicfix.run editions --artist "Juice WRLD"
    python -m homelab_musicfix.run editions --artist "Juice WRLD" --commit
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import time

import mutagen

from .albums import cross_folder_groups, load_inventory
from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS, TMP_ROOT
from .naming import canonical_paths
from .plan import norm
from .quality import quality_key
from .tags import build_vocabulary

JOURNAL = REPORTS / "editions_journal.jsonl"
QUARANTINE = TMP_ROOT / "quarantine/edition-merge"
KEYS = (
    "artist",
    "albumartist",
    "album",
    "title",
    "tracknumber",
    "discnumber",
    "date",
    "musicbrainz_albumid",
    "musicbrainz_releasegroupid",
)
DUR_TOL = 2.0
# An extra whose title carries one of these is another version of a song and goes
# on a bonus disc; anything else is a real album track that merely landed in the
# wrong edition folder and keeps its place on the album.
VERSION_RE = re.compile(
    r"remix|slowed|sped|acoustic|accoustic|live|remaster|instrumental|version|edit\b"
    r"|reverb|nightcore|bonus|demo|mix\b|extended|radio|stripped|sessions?",
    re.I,
)


def _first(rec, key):
    v = rec["tags"].get(key) or [""]
    return v[0] if v else ""


def _num(s):
    try:
        return int(str(s).split("/")[0])
    except ValueError:
        return 0


def _common(rows, key):
    c = collections.Counter(_first(r, key) for r in rows if _first(r, key))
    return c.most_common(1)[0][0] if c else ""


def read_keys(path):
    mf = mutagen.File(path, easy=True)
    return {k: list(mf.get(k) or []) for k in KEYS if k in (mf.keys() if mf else [])}


def write_keys(path, values):
    """Set the given keys; an empty list removes the key. Returns error or None."""
    try:
        mf = mutagen.File(path, easy=True)
        for k, v in values.items():
            try:
                if v:
                    mf[k] = v
                elif k in mf:
                    del mf[k]
            except KeyError:
                pass  # easy mapping lacks this key for this container
        mf.save()
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"
    return None


def _sidecars(abspath):
    d, name = os.path.split(abspath)
    stem = os.path.splitext(name)[0]
    out = []
    for n in os.listdir(d):
        s, e = os.path.splitext(n)
        if s == stem and e.lower() not in AUDIO_EXTS:
            out.append(os.path.join(d, n))
    return out


def plan_group(members, by_dir, vocab, music):
    base_dir = max(members, key=lambda m: m["tracks"])["dir"]
    base_rows = by_dir[base_dir]
    base = {
        "album": _common(base_rows, "album"),
        "albumartist": next(r["tags"]["albumartist"] for r in base_rows if r["tags"].get("albumartist")),
        "date": max((_first(r, "date") for r in base_rows), key=len, default=""),
        "mbid": _common(base_rows, "musicbrainz_albumid"),
        "rgid": _common(base_rows, "musicbrainz_releasegroupid"),
    }
    by_title = collections.defaultdict(list)
    for r in base_rows:
        by_title[norm(_first(r, "title"))].append(r)
    discs = [_num(_first(r, "discnumber")) or 1 for r in base_rows]
    extra_disc = max(discs) + 1
    next_track = 1
    actions = []
    slots = {}  # id(base row) -> {"slot": base row, "keep": best so far, "loses": []}
    others = [
        r
        for m in members
        if m["dir"] != base_dir
        for r in sorted(by_dir[m["dir"]], key=lambda r: (m["dir"], _num(_first(r, "tracknumber"))))
    ]
    extras = []  # {"norm", "keep", "loses"}: secondaries with no base twin

    def fold_in(entry, r):
        if quality_key(r) > quality_key(entry["keep"]):
            entry["loses"].append(entry["keep"])
            entry["keep"] = r
        else:
            entry["loses"].append(r)

    for r in others:
        t = norm(_first(r, "title"))
        dur = r["quality"].get("duration") or 0
        twin = [b for b in by_title.get(t, []) if abs((b["quality"].get("duration") or 0) - dur) <= DUR_TOL]
        if twin:
            b = twin[0]
            fold_in(slots.setdefault(id(b), {"slot": b, "keep": b, "loses": []}), r)
            continue
        same = [
            e for e in extras if e["norm"] == t and abs((e["keep"]["quality"].get("duration") or 0) - dur) <= DUR_TOL
        ]
        if same:
            fold_in(same[0], r)
        else:
            extras.append({"norm": t, "keep": r, "loses": []})
    for e in slots.values():
        actions.append(
            {
                "op": "dupe" if e["keep"] is e["slot"] else "swap",
                "keep": e["keep"],
                "loses": e["loses"],
                "slot": e["slot"],
            }
        )
    used = {(_num(_first(r, "discnumber")) or 1, _num(_first(r, "tracknumber"))) for r in base_rows}
    for e in extras:
        r = e["keep"]
        if VERSION_RE.search(_first(r, "title")):
            disc, track, next_track = extra_disc, next_track, next_track + 1
        else:
            disc = _num(_first(r, "discnumber")) or 1
            track = _num(_first(r, "tracknumber"))
            if (disc, track) in used or not track:
                track = max([t for d, t in used if d == disc] or [0]) + 1
            used.add((disc, track))
        actions.append(
            {
                "op": "dupe" if e["loses"] else "extra",
                "keep": r,
                "loses": e["loses"],
                "slot": None,
                "disc": disc,
                "track": track,
                "extra": True,
            }
        )
    return base_dir, base, actions


def _artists(rec):
    return list(rec["tags"].get("artist") or [])


def target_tags(act, base):
    """New tag values for the file that stays, plus its credit list."""
    keep = act["keep"]
    slot = act.get("slot")
    vals = {"album": [base["album"]], "albumartist": list(base["albumartist"])}
    if base["date"]:
        vals["date"] = [base["date"]]
    if base["mbid"]:
        vals["musicbrainz_albumid"] = [base["mbid"]]
    if base["rgid"]:
        vals["musicbrainz_releasegroupid"] = [base["rgid"]]
    if act["op"] == "extra" or act.get("extra"):
        vals["discnumber"] = [str(act["disc"])]
        vals["tracknumber"] = [str(act["track"])]
    elif slot is not None:
        vals["discnumber"] = list(slot["tags"].get("discnumber") or ["1"])
        vals["tracknumber"] = list(slot["tags"].get("tracknumber") or [])
        vals["title"] = [_first(slot, "title")]
        vals["artist"] = max((_artists(r) for r in [keep] + act["loses"]), key=len)
    return vals


def build(args):
    by_dir = load_inventory(args.inventory)
    vocab = build_vocabulary([r for rows in by_dir.values() for r in rows])
    findings = [
        f
        for f in cross_folder_groups(by_dir)
        if f["kind"] in ("edition", "variant") and (not args.artist or f["albumartist"].lower() == args.artist.lower())
    ]
    jobs = []
    for f in findings:
        base_dir, base, actions = plan_group(f["members"], by_dir, vocab, str(MUSIC_ROOT))
        jobs.append((base_dir, base, actions, f))
    return jobs, vocab


def resolve(act, base, base_dir, vocab):
    """(tag values, destination rel path for the keeper)."""
    vals = target_tags(act, base)
    keep = act["keep"]
    rec = {"path": keep["path"], "tags": {**keep["tags"], **{k: v for k, v in vals.items()}}}
    dest = canonical_paths(rec, vocabulary=vocab)["rel_path"]
    # canonical_paths derives the folder from the tags, which now match the base
    # album; keep the base folder verbatim in case clean_album would rename it.
    dest = os.path.join(base_dir, os.path.basename(dest))
    return vals, dest


def run(args):
    jobs, vocab = build(args)
    music = str(MUSIC_ROOT)
    taken = set()
    n_dupe = n_extra = n_swap = n_fail = 0
    jf = open(args.journal, "a", encoding="utf-8") if args.commit else None

    def jot(**kw):
        if jf:
            jf.write(json.dumps({"ts": time.time(), **kw}, ensure_ascii=False) + "\n")
            jf.flush()

    def move(src, dst, op):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        jot(op=op, src=src, dst=dst)

    for base_dir, base, actions, f in jobs:
        print(
            f"\n== {base_dir}  <- " + ", ".join(m["dir"].split("/", 1)[1] for m in f["members"] if m["dir"] != base_dir)
        )
        for act in actions:
            keep = act["keep"]
            vals, dest = resolve(act, base, base_dir, vocab)
            key = dest.lower()
            if key in taken and not (act["op"] != "extra" and keep["path"] == dest):
                dest = os.path.splitext(dest)[0] + " (2)" + os.path.splitext(dest)[1]
                key = dest.lower()
            taken.add(key)
            loses = act["loses"]
            tag = {"extra": "EXTRA", "dupe": "dupe ", "swap": "SWAP "}[act["op"]]
            print(
                f"  {tag} keep {os.path.basename(keep['path'])}"
                + "".join(f"   drop {x['path'].split('/', 1)[1]}" for x in loses)
                + (f"   -> disc {act['disc']} #{act['track']}" if act.get("extra") else "")
            )
            if act["op"] == "extra":
                n_extra += 1
            elif act["op"] == "swap":
                n_swap += 1
            else:
                n_dupe += 1
            if not args.commit:
                continue
            try:
                # 1. loser (and its sidecars) to quarantine
                for lose in loses:
                    src = os.path.join(music, lose["path"])
                    for s in [src] + _sidecars(src):
                        q = os.path.join(QUARANTINE, os.path.relpath(s, music))
                        while os.path.exists(q):
                            q += ".dup"
                        move(s, q, "edition-quarantine")
                # 2. retag the keeper (only when it actually changes)
                kp = os.path.join(music, keep["path"])
                before = read_keys(kp)
                changed = {k: v for k, v in vals.items() if before.get(k) != v}
                if changed:
                    err = write_keys(kp, changed)
                    if err:
                        raise RuntimeError(err)
                    jot(
                        op="edition-tag",
                        path=keep["path"],
                        before={k: before.get(k, []) for k in changed},
                        after=changed,
                    )
                # 3. move the keeper (and sidecars) to its canonical slot
                if dest != keep["path"]:
                    dst = os.path.join(music, dest)
                    stem_dst = os.path.splitext(dst)[0]
                    for s in [kp] + _sidecars(kp):
                        move(s, stem_dst + os.path.splitext(s)[1] if s != kp else dst, "edition-move")
            except Exception as exc:  # noqa: BLE001
                n_fail += 1
                print(f"    FAILED: {exc}")
    print(
        f"\n[editions] {len(jobs)} groups: {n_dupe} duplicates, {n_swap} swaps, "
        f"{n_extra} extras, {n_fail} failed" + ("" if args.commit else "  (dry-run; --commit to apply)")
    )
    return 1 if n_fail else 0


def restore(args):
    rows = [json.loads(l) for l in open(args.journal, encoding="utf-8") if l.strip()]
    music = str(MUSIC_ROOT)
    for r in reversed(rows):
        print(("undo " if args.commit else "would undo ") + r["op"], r.get("path") or r.get("dst"))
        if not args.commit:
            continue
        if r["op"] in ("edition-move", "edition-quarantine"):
            os.makedirs(os.path.dirname(r["src"]), exist_ok=True)
            os.rename(r["dst"], r["src"])
        elif r["op"] == "edition-tag":
            write_keys(os.path.join(music, r["path"]), r["before"])
    if args.commit:
        os.replace(args.journal, str(args.journal) + f".restored-{int(time.time())}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--journal", default=str(JOURNAL))
    ap.add_argument("--artist", help="album artist to restrict to (exact, case-insensitive)")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args(argv)
    return restore(args) if args.restore else run(args)


if __name__ == "__main__":
    raise SystemExit(main())

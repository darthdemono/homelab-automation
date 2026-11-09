# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Give every loose comic archive at a library root its own series folder.

Komga builds a series from a *directory*. Archives sitting directly in the
library root all collapse into one series named after the root itself — the
eight Junji Ito one-shots in `Books/Manga` came out as a single series called
"manga". They are eight separate works, and komf cannot identify a series that
is really eight books by different titles.

Each archive therefore gets a folder of its own, named for the work rather than
for the scan: the release cruft that helps nothing here — `(digital)`,
`(Digital)`, `(Lo Pan)`, `(tunafan)`, `(SDdigital)`, `(danke-Empire)`, `(XRA9)`
— is dropped from the *folder* name so komf has a clean title to match against.
The archive keeps its original filename, so nothing is lost and the scan group
stays traceable.

A matching `<name>-poster.jpg` moves with its archive and is renamed `cover.jpg`,
which is what Komga looks for as a series cover.

Nothing is deleted, a journal is written to plans/, and --undo reverses it.

    ops/fold_manga_oneshots.py                          # plan
    ops/fold_manga_oneshots.py --apply
    ops/fold_manga_oneshots.py --undo plans/<stamp>-oneshots.json
"""

import argparse
import json
import os
import re
import shutil
import time

DEFAULT_ROOT = "<root>/Books/Manga"
PLANS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plans")
ARCHIVES = {".cbz", ".cbr", ".cb7", ".pdf", ".epub"}

# Parenthesised groups that describe the scan, not the work.
CRUFT = re.compile(
    r"\s*\((?:"
    r"\d{4}"  # a year
    r"|digital|sddigital|web|scan|c2c|f\d+"  # source
    r"|deluxe edition|complete deluxe edition"
    r"|lo pan|tunafan|xra9|danke-empire|empire|minutemen[^)]*|dcp"
    r")\)",
    re.I,
)


def clean_title(stem):
    t = CRUFT.sub("", stem)
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)  # a trailing group we did not name
    t = re.sub(r"\s+", " ", t).strip(" -_.")
    return t or stem


def build(root):
    moves, problems = [], []
    try:
        entries = sorted(os.listdir(root))
    except OSError as e:
        return [], [f"cannot read {root}: {e}"]

    loose = [f for f in entries if os.path.isfile(os.path.join(root, f)) and os.path.splitext(f)[1].lower() in ARCHIVES]

    for fname in loose:
        stem, _ext = os.path.splitext(fname)
        folder = clean_title(stem)
        dest_dir = os.path.join(root, folder)
        if os.path.exists(dest_dir) and not os.path.isdir(dest_dir):
            problems.append(f"{dest_dir} exists and is not a directory")
            continue
        dst = os.path.join(dest_dir, fname)
        if os.path.exists(dst):
            problems.append(f"destination occupied: {dst}")
            continue
        moves.append({"src": os.path.join(root, fname), "dst": dst, "why": "archive"})

        poster = os.path.join(root, f"{stem}-poster.jpg")
        if os.path.isfile(poster):
            moves.append({"src": poster, "dst": os.path.join(dest_dir, "cover.jpg"), "why": "cover"})
    return moves, problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--undo", metavar="JOURNAL")
    args = ap.parse_args()

    if args.undo:
        data = json.load(open(args.undo))
        n = 0
        for mv in reversed(data["moves"]):
            if os.path.exists(mv["dst"]):
                os.makedirs(os.path.dirname(mv["src"]), exist_ok=True)
                shutil.move(mv["dst"], mv["src"])
                n += 1
        for mv in data["moves"]:
            d = os.path.dirname(mv["dst"])
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        print(f"restored {n} files")
        return

    moves, problems = build(args.root)
    folders = sorted({os.path.dirname(m["dst"]) for m in moves})
    print(f"=== {len(folders)} series folder(s) from {len(moves)} file(s)")
    for f in folders:
        print(f"  {os.path.basename(f)}/")
        for m in moves:
            if os.path.dirname(m["dst"]) == f:
                print(f"      {os.path.basename(m['src'])}  ->  {os.path.basename(m['dst'])}")
    if problems:
        print(f"\n=== {len(problems)} problem(s)")
        for p in problems:
            print(f"  {p}")
    if not moves:
        print("nothing to do")
        return
    if not args.apply:
        print("\n[plan only] re-run with --apply")
        return

    stamp = time.strftime("%Y%m%d-%H%M%S")
    jpath = os.path.join(PLANS, f"{stamp}-oneshots.json")
    done = []

    def record():
        with open(jpath, "w") as fh:
            json.dump({"root": args.root, "moves": done}, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())

    record()
    try:
        for m in moves:
            os.makedirs(os.path.dirname(m["dst"]), exist_ok=True)
            shutil.move(m["src"], m["dst"])
            done.append(m)
            record()
            print(f"  moved {os.path.basename(m['src'])}")
    finally:
        record()

    print(f"\nmoved {len(done)} of {len(moves)} files into {len(folders)} folders")
    print(f"journal: {jpath}")
    print(f"undo: ./ops/fold_manga_oneshots.py --undo {jpath}")


if __name__ == "__main__":
    main()

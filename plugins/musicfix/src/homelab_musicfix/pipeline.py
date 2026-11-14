# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""One command for a Downloads batch: merge -> casing -> scan -> analyse -> artwork.

The step order is the one CLAUDE.md fixes and cannot be permuted: everything
that *moves* files (merge, casing) has to finish before anything that keys work
by path (the analysis json caches, the nfo writer), or the caches describe paths
that no longer exist.

Each step is a subprocess of the module that already implements it -- this
orchestrates, it never re-implements. Full output goes to a log file; the
console gets one line per step plus that step's own tally lines, so a routine
batch is ~20 lines instead of a screenful per phase.

Scoping is the whole point. A batch of three files must not trigger a 4600-file
nfo pass or an 11k re-probe, so after the moves the pipeline derives two subset
inventories from the merge journal and hands them to the later steps via their
existing --inventory flag:

  inventory_new.jsonl      exactly the files that arrived  -> analyze
  inventory_artists.jsonl  every row under those artists   -> nfo

(nfo needs the wider one: artist.nfo describes the whole artist, so a per-batch
subset would write a Three Days Grace artist.nfo listing only the new album.)

On failure it stops, restarts Navidrome, and writes reports/pipeline/FAILURE.md
with the exact command, exit code and the tail of the output -- that file is the
thing to hand to a human or an assistant. Nothing else needs reading.

    python -m homelab_musicfix.run pipeline              # dry-run: what would the batch do
    python -m homelab_musicfix.run pipeline --commit     # the real thing
    python -m homelab_musicfix.run pipeline --commit --resume    # continue after a failure
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from .settings import AUDIO_EXTS, MUSIC_ROOT, REPORTS
from .merge import DOWNLOADS_ROOT

PIPE_DIR = REPORTS / "pipeline"
STATE = PIPE_DIR / "state.json"
FAILURE = PIPE_DIR / "FAILURE.md"


class StepFailed(Exception):
    def __init__(self, name, cmd, rc, out):
        super().__init__(f"{name} exited {rc}")
        self.name, self.cmd, self.rc, self.out = name, cmd, rc, out


# ---------------------------------------------------------------- running


class Runner:
    """Runs each step, tees to the log, keeps the console short."""

    def __init__(self, log_path, tail=8):
        self.log = open(log_path, "a", encoding="utf-8")
        self.tail = tail
        self.results = []

    def run(self, name, argv, check=True, external=False, env=None):
        # external: argv is already a full command (another project), run as given
        from . import settings

        cmd = (
            list(argv)
            if external
            else [
                sys.executable,
                "-m",
                "homelab_musicfix.run",
                "--music",
                str(settings.MUSIC_ROOT),
                "--tmp",
                str(settings.TMP_ROOT),
                "--reports",
                str(settings.REPORTS),
                argv[0],
                *argv[1:],
            ]
        )
        self.log.write(f"\n=== {name}: {' '.join(cmd)}\n")
        self.log.flush()
        t0 = time.time()
        p = subprocess.run(cmd, cwd=str(REPORTS), capture_output=True, text=True, errors="replace", env=env)
        out = (p.stdout or "") + (p.stderr or "")
        self.log.write(out)
        self.log.flush()
        dt = time.time() - t0
        self.results.append((name, p.returncode, dt))
        mark = "ok " if p.returncode == 0 else "FAIL"
        print(f"[{mark}] {name}  ({dt:.1f}s)")
        for line in _tally(out, self.tail):
            print(f"       {line}")
        if check and p.returncode != 0:
            raise StepFailed(name, cmd, p.returncode, out)
        return out

    def close(self):
        self.log.close()


def _tally(out, n):
    """The last few informative lines of a step -- its own summary counts."""
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    return lines[-n:]


def _docker(action, container="navidrome"):
    p = subprocess.run(["docker", action, container], capture_output=True, text=True)
    return p.returncode == 0


def _navidrome_up():
    p = subprocess.run(
        ["docker", "ps", "--filter", "name=^navidrome$", "--format", "{{.Names}}"], capture_output=True, text=True
    )
    return "navidrome" in (p.stdout or "")


def _start_and_rescan():
    """Always leave Navidrome running, then rescan via its own CLI (no credentials)."""
    ok = _navidrome_up() or _docker("start")
    if not ok:
        return False, False
    time.sleep(5)  # let the server finish booting
    p = subprocess.run(["docker", "exec", "navidrome", "/app/navidrome", "scan"], capture_output=True, text=True)
    return True, p.returncode == 0


# ------------------------------------------------------- journal -> scope


def _prune_inventory(path):
    """Drop inventory rows whose file is gone. A stale row makes merge call a Downloads file "already in the library"
    and quarantine the only copy (it happened when artists were put back into Downloads by hand)."""
    from concurrent.futures import ThreadPoolExecutor

    if not os.path.exists(path):
        return 0
    rows = open(path, encoding="utf-8").readlines()
    with ThreadPoolExecutor(32) as ex:
        keep = list(ex.map(lambda l: os.path.exists(json.loads(l)["abspath"]), rows))
    gone = keep.count(False)
    if gone:
        os.replace(path, path + ".prune.bak")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(l for l, k in zip(rows, keep) if k)
    return gone


def _journal_tail(path, offset):
    """Rows appended to a journal after `offset` bytes, plus the new offset."""
    rows = []
    if not os.path.exists(path):
        return rows, offset
    with open(path, "r", encoding="utf-8") as f:
        f.seek(offset)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return rows, f.tell()


def _rel(path, root):
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return None


def scope_from_journal(rows, music):
    """(arrived audio, paths whose file was replaced) as library-relative paths.

    A replaced path keeps its name while the audio behind it changes, which is
    exactly what poisons the path-keyed analysis caches -- those need --force.
    """
    arrived, replaced = [], []
    for r in rows:
        op, src, dst = r.get("op"), r.get("src"), r.get("dst")
        if op == "merge-demote" and src:
            rel = _rel(src, music)
            if rel and os.path.splitext(rel)[1].lower() in AUDIO_EXTS:
                replaced.append(rel)
            continue
        if not dst:
            continue
        rel = _rel(dst, music)
        if rel is None or rel.startswith(".."):
            continue  # quarantine / playlist covers leave the library
        if os.path.splitext(rel)[1].lower() in AUDIO_EXTS:
            arrived.append(rel)
    return sorted(set(arrived)), sorted(set(replaced))


def journal_paths(rows, music):
    """Every library-relative path a set of journal rows names, both sides.

    A move has to re-probe its source too -- that row must leave the inventory.
    """
    out = set()
    for r in rows:
        for key in ("src", "dst", "path"):
            val = r.get(key)
            if not val:
                continue
            rel = val if not os.path.isabs(val) else _rel(val, music)
            if not rel or rel.startswith(".."):
                continue
            if os.path.splitext(rel)[1].lower() in AUDIO_EXTS:
                out.add(rel.replace("\\", "/"))
    return out


def write_subsets(paths, inventory, out_new, out_artists):
    """Filter the freshly patched inventory down to the batch and its artists.

    Returns (rows in the batch subset, rows in the artist subset). Matching is
    case-folded: the library volume is case-insensitive to Windows/Navidrome
    even though this mount is not, so a case-different path is the same row.
    """
    want = {p.replace("\\", "/").lower() for p in paths}
    artists = {p.replace("\\", "/").split("/")[0].lower() for p in paths}
    n_new = n_art = 0
    with (
        open(inventory, "r", encoding="utf-8") as f,
        open(out_new, "w", encoding="utf-8") as fn,
        open(out_artists, "w", encoding="utf-8") as fa,
    ):
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rel = (row.get("path") or "").replace("\\", "/")
            if not rel:
                continue
            low = rel.lower()
            if low in want:
                fn.write(line)
                n_new += 1
            if low.split("/")[0] in artists:
                fa.write(line)
                n_art += 1
    return n_new, n_art


# ------------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run a whole Downloads batch through the pipeline.")
    ap.add_argument(
        "--commit", action="store_true", help="actually move/write; without it only merge's dry-run plan is built"
    )
    ap.add_argument(
        "--resume", action="store_true", help="skip steps already recorded complete in reports/pipeline/state.json"
    )
    ap.add_argument("--downloads", default=None)
    ap.add_argument(
        "--keep-navidrome", action="store_true", help="do not stop the container (unsafe during the write phases)"
    )
    ap.add_argument("--skip-analysis", action="store_true")
    ap.add_argument("--with-nfo", action="store_true", help="also generate legacy Jellyfin/Emby .nfo files")
    ap.add_argument("--skip-casing", action="store_true")
    ap.add_argument(
        "--post-command",
        default="",
        help="after the batch, run this command; {artists} becomes the comma-separated artist folders in the batch "
        "(for example a tagging or publishing tool that takes artists)",
    )
    ap.add_argument("--tail", type=int, default=8, help="console lines kept per step (the log keeps everything)")
    args = ap.parse_args(argv)

    PIPE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = PIPE_DIR / f"run-{stamp}.log"
    music = str(MUSIC_ROOT)
    inventory = str(REPORTS / "inventory.jsonl")
    merge_journal = str(REPORTS / "merge_journal.jsonl")
    tag_journal = str(REPORTS / "tag_journal.jsonl")
    casing_journal = str(REPORTS / "casing_journal.jsonl")
    inv_new = str(PIPE_DIR / "inventory_new.jsonl")
    inv_art = str(PIPE_DIR / "inventory_artists.jsonl")

    state = {}
    if args.resume and STATE.exists():
        state = json.loads(STATE.read_text())
    done = set(state.get("done", []))

    def mark(step, **extra):
        done.add(step)
        state["done"] = sorted(done)
        state.update(extra)
        STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False))

    r = Runner(log_path, tail=args.tail)
    print(f"[pipeline] log -> {log_path}")
    stopped = False
    t_all = time.time()
    try:
        # ---- 1. what is in Downloads at all (cheap, no writes)
        n_gone = _prune_inventory(inventory)
        if n_gone:
            print(f"[pipeline] pruned {n_gone} inventory row(s) whose file is gone (backup: inventory.jsonl.prune.bak)")
        merge_args = ["merge"]
        if args.downloads:
            merge_args += ["--downloads", args.downloads]
        if "merge" not in done:
            out = r.run("merge (dry-run)", merge_args)
            plan = REPORTS / "merge_plan.jsonl"
            n_plan = sum(1 for _ in open(plan, encoding="utf-8")) if plan.exists() else 0
            if n_plan == 0:
                print("[pipeline] Downloads is empty -- nothing to do.")
                return 0
            if not args.commit:
                print(f"[pipeline] dry-run only: {n_plan} planned actions, re-run with --commit")
                return 0

            if not args.keep_navidrome and _navidrome_up():
                stopped = _docker("stop")
                print(f"[pipeline] navidrome stopped: {stopped}")

            offsets = {
                j: (os.path.getsize(j) if os.path.exists(j) else 0)
                for j in (merge_journal, tag_journal, casing_journal)
            }
            r.run("merge --commit", merge_args + ["--commit"])
            rows, _ = _journal_tail(merge_journal, offsets[merge_journal])
            arrived, replaced = scope_from_journal(rows, music)
            print(f"[pipeline] {len(arrived)} audio arrived, {len(replaced)} replaced")
            mark("merge", arrived=arrived, replaced=replaced, offsets=offsets)
        else:
            arrived = state.get("arrived", [])
            replaced = state.get("replaced", [])
            offsets = {j: state.get("offsets", {}).get(j, 0) for j in (merge_journal, tag_journal, casing_journal)}
            if not args.keep_navidrome and _navidrome_up():
                stopped = _docker("stop")

        if not arrived and not replaced:
            print("[pipeline] no audio landed in the library -- stopping here.")
            return 0

        # ---- 2. casing (moves files, so it must precede every path-keyed step)
        if not args.skip_casing and "casing" not in done:
            r.run("casing --commit", ["casing", "--commit"])
            mark("casing")

        # ---- 3. patch the inventory -- only the paths *this run* touched.
        # Handing scan the journals whole re-probes every path they have ever
        # named (1438 files, 5.5 minutes, for a 2-file batch); the journals are
        # cumulative and scan cannot know where this run started. So the run's
        # own tail is turned into a paths file instead: seconds, same result.
        if "scan" not in done:
            touched = set()
            for jp, off in offsets.items():
                rows_j, _ = _journal_tail(jp, off)
                touched |= journal_paths(rows_j, music)
            paths_file = str(PIPE_DIR / "scan_paths.txt")
            with open(paths_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(sorted(touched)) + "\n")
            print(f"[pipeline] re-probing {len(touched)} touched paths")
            r.run("scan --paths", ["scan", "--paths", paths_file])
            mark("scan")

        # ---- 4. subset inventories for everything downstream
        targets = sorted(set(arrived) | set(replaced))
        n_new, n_art = write_subsets(targets, inventory, inv_new, inv_art)
        print(f"[pipeline] scope: {n_new} batch rows, {n_art} rows under those artists")
        if n_new == 0:
            print(
                "[pipeline] batch rows not found in the inventory -- "
                "casing may have renamed them; run a full scan and --resume."
            )
            raise StepFailed("scope", ["write_subsets"], 1, "0 of %d target paths matched the inventory" % len(targets))

        # ---- 5. analysis, scoped to the batch
        if not args.skip_analysis:
            force = ["--force"] if replaced else []
            if "extract" not in done:
                r.run(
                    "analyze extract --fast",
                    ["analyze", "--inventory", inv_new, "extract", "--fast", "--workers", "4"] + force,
                )
                mark("extract")
            if "tag" not in done:
                r.run("analyze tag --commit", ["analyze", "--inventory", inv_new, "tag", "--commit"] + force)
                mark("tag")

        # ---- 6. nfo is legacy Jellyfin/Emby output and must be explicit.
        if args.with_nfo and "nfo" not in done:
            r.run("nfo --commit", ["nfo", "--commit", "--inventory", inv_art])
            mark("nfo")

        # ---- 7. Navidrome needs one folder cover, not per-track artwork.
        if "artwork" not in done:
            r.run("artwork --commit", ["artwork", "--commit"])
            mark("artwork")

        # ---- 7b. Any other tool can follow the batch: the command is the user's, musicFix only runs it.
        if args.post_command:
            artists = sorted({p.replace("\\", "/").split("/")[0] for p in targets})
            if "post-command" not in done:
                import shlex

                cmd = [part.replace("{artists}", ",".join(artists)) for part in shlex.split(args.post_command)]
                r.run("post-command", cmd, external=True)
                mark("post-command")

        # ---- 8. merge moves files out and leaves empty album/artist dirs behind.
        for d, _, _ in os.walk(DOWNLOADS_ROOT, topdown=False):
            if d != DOWNLOADS_ROOT and not os.listdir(d):
                os.rmdir(d)

        if STATE.exists():
            STATE.unlink()
        if FAILURE.exists():
            FAILURE.unlink()
        print(f"\n[pipeline] complete in {time.time() - t_all:.0f}s -- {len(arrived)} new tracks. ")
        return 0

    except StepFailed as e:
        _write_failure(e, log_path, done)
        print(f"\n[pipeline] FAILED at: {e.name} (exit {e.rc})")
        print(f"[pipeline] hand this to an assistant:  cat {FAILURE}")
        print("[pipeline] fix, then:  python -m homelab_musicfix.run pipeline --commit --resume")
        return e.rc or 1
    finally:
        r.close()
        if stopped or not args.keep_navidrome:
            up, scanning = _start_and_rescan()
            print(f"[pipeline] navidrome running: {up}, rescan started: {scanning}")


def _write_failure(e, log_path, done):
    tail = "\n".join(e.out.splitlines()[-60:])
    FAILURE.write_text(
        "# pipeline failure\n\n"
        f"step: `{e.name}`\nexit: {e.rc}\n"
        f"cmd: `{' '.join(e.cmd)}`\n"
        f"log: `{log_path}`\n"
        f"completed steps: {', '.join(sorted(done)) or '(none)'}\n\n"
        "Resume after fixing with `python -m homelab_musicfix.run pipeline --commit --resume`.\n\n"
        "## output tail\n\n```\n" + tail + "\n```\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())

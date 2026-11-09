# Never rescan the whole library for a small change

## Symptom

- A pass that touched a hundred files is followed by a rescan that takes 20 minutes or more.
- A walk of the whole drive never returns, or a script "hangs" before printing anything.
- A full feature-analysis run is estimated at a day when the question was only BPM and key for new tracks.
- The machine swaps and per-file throughput collapses while a heavy job runs beside others.

## Why it happens

- On a FUSE filesystem the cost is round trips, not bandwidth. A `stat()` per file is slow; probing each file with `ffprobe` runs at about 10 files per second, so 11,000 tracks take about 19 minutes.
- Walking the whole volume reaches folders that are not media (photo uploads, backups, recycle bins), and on a damaged volume one of them can block forever.
- Analysis jobs that skip "already done" files still pay for a full walk and for every file they are asked to consider.

## How to check

```sh
time find <library> -type f | wc -l                    # the walk alone
free -h; vmstat 5 3                                   # swap in and out while a job runs
```

If most of the time is spent probing files that did not change, the job is over-scoped.

## Fix

- Scope by journal or by path list: re-probe only the files the last run touched (see `stale-inventory-after-moves.md`).
- Walk the library roots, never the volume root. Give tools a `--roots` option. One torrent relink went from a walk that never returned to 31 seconds.
- List directories from a thread pool and use `entry.is_dir()` from `scandir`, so the type from `readdir` is enough without a `stat()`. One full walk went from about 48 minutes to about 57 seconds this way.
- For analysis, select only files missing the values you want. A BPM and key pass over the 11 percent of tracks that lacked them took about 40 minutes instead of a day.
- Run heavy model inference on the GPU when you can, one container at a time, so it does not push system RAM into swap.

## How to prevent

- Make every expensive step take an explicit list of paths, and make the batch pipeline pass it the paths of that batch only.
- Keep the full scan for a cold inventory, or for changes made outside your tools.
- After a merge that replaced files, force re-analysis of exactly those paths, not the library (see `path-keyed-caches-after-replace.md`).

## Which tool helps

- `jellyfix.verify` takes a library option, so you can verify one library instead of all.
- Scoped collectors in the planned data layer will work from run journals.

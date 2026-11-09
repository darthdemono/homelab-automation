# Caches keyed by path go stale when a file is replaced

## Symptom

- After a merge replaced some library files with better copies, those tracks keep their old BPM, key or mood tags, or get none at all.
- The tagging step reports the replaced tracks as "already done" and skips them, with no error.
- `--force` on an analysis step changes nothing: the step still skips the files.
- Tracks moved by a casing fix land in the library untagged, and the analysis output sits at paths that no longer exist.

## Why it happens

- Analysis results, model outputs and "done" journals are stored per library path. A path is only a stable identity while the audio at it stays the same file. A replacement lands on the same canonical path, so every cache still describes the old recording.
- A wrapper can filter its worklist on the host while the container it calls also skips any job whose output file already exists. Passing `--force` to one and not the other is a silent no-op.
- A step that moves files after analysis has run strands the results at the old paths.

## How to check

```sh
grep -c '"action": *"replace"' <reports>/merge_journal.jsonl     # did the last merge replace anything?
ls -l --time-style=+%F <reports>/analysis/<track-id>.json        # older than the file it describes?
stat -c '%y %n' "<library>/Some Artist/Some Album/Some Artist - Some Song.flac"
```

A cache file older than the audio file at that path describes another recording.

## Fix

1. Take the list of replaced paths from the merge journal.
2. Re-run each path-keyed step for exactly those paths with its force option, making sure the force reaches every layer (wrapper and container).
3. Re-run tagging for those paths with force, so the "done" journal is bypassed.

## How to prevent

- Run everything that moves files before anything that keys work by path: merge, casing, inventory patch, analysis, tagging, artwork.
- Let the batch pipeline add force to the analysis steps automatically when, and only when, the merge reported a replace.
- When you add a force flag, add it to both the host side and the container side in the same change, with a test that fails if one side ignores it.
- Better still, key caches by a content hash (decoded-audio md5) instead of the path.

## Which tool helps

Planned: the music cleanup plugin and its batch pipeline. The engine's `journal` action already records each move, which is the input this fix needs.

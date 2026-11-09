# Reading the Navidrome database gives wrong answers

## Symptom

- A file tagged with two artists looks the same as a file with one joined artist string.
- ISRC lookups against the database find zero matches, so everything falls back to fuzzy title matching.
- The track count rises while you move files, then sits still while a rescan is clearly running. It shows more tracks than the library holds.
- A count taken right after a rescan disagrees with one taken a minute later.

## Why it happens

- Navidrome collapses a multi-valued ARTIST tag into one display string.
- It stores ISRC inside the `media_file.tags` JSON as a list of objects (`[{"id": ..., "value": "USxxx"}]`), not as a bare string.
- `media_file` keeps a soft-deleted row (`missing = 1`) for every moved or removed file. A bare `count(*)` includes them.
- A rescan flips `missing` flags gradually, so the index lags the disk.

## How to check

```sh
sqlite3 "file:<navidrome-data>/navidrome.db?mode=ro" "select count(*) from media_file where missing = 0;"
find <library> -type f \( -name '*.flac' -o -name '*.mp3' -o -name '*.m4a' -o -name '*.opus' \) | wc -l
```

The live count should be stable across consecutive checks and close to the file count. A small gap is normal (trash folders, files Navidrome skips).

## Fix

- Read real tags from the files (mutagen, `metaflac`, `ffprobe`) when a decision depends on them.
- When you must use the database, open it read-only (`mode=ro`), pull `.value` out of the ISRC objects, and filter `missing = 0`.
- After a write, wait until the `missing = 0` count is stable and within the expected distance of the on-disk count before reading anything else.
- Navidrome has a `scan` subcommand, so a rescan needs no API credentials: `docker exec navidrome /app/navidrome scan` (check the binary path in your image).

## How to prevent

- Use the database only to map a file you already identified to its Navidrome id, for example to build a playlist through the API.
- Do not trust tag-reported bitrate either: use `ffprobe` on the audio stream. The container bitrate counts embedded cover art and inflated one file by about 10 percent.
- Stop Navidrome while files are being moved or retagged, so it cannot index half-moved state.

## Which tool helps

A Navidrome plugin (read-only checks, scoped rescans) is planned.

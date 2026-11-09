# Jellyfin: stuck scans, ghost items and shows listed twice

## Symptom

- `Scan Media Library` sits at a fixed percentage and will not even cancel (stays in `Cancelling`).
- After a rename, a show appears twice with its episodes split between the two entries.
- A show displays season names from another series, or an old entry points at a path that no longer exists.
- Quarantined or broken files show up as a phantom season.
- Films stored inside a series folder create a second entry for the series.
- Two shows with the same title but different years look like duplicates.

## Why it happens

- A scan blocked on an unreadable file (see `damaged-ntfs-volume-moves.md`) never returns.
- When a renamed folder and its original folder both still hold files, Jellyfin builds an item for each.
- A stale item keeps metadata it matched under its old path.
- Anything inside a library root is indexed, including quarantine folders and `Movies/` or `OVAs/` subfolders inside a series.
- A case-insensitive name comparison makes `Some Show (1999)` and `SOME SHOW (2023)` look like one show when they are two.

## How to check

- Dashboard, Scheduled Tasks: is a scan running, and for how long?
- List items whose path no longer exists, and top-level items with no provider id, through the API with an API key.
- Look for leftover files in the old release folder after a rename.

## Fix

- Stuck scan: put an `.ignore` file in the folder holding the bad entry (Jellyfin skips folders containing one) and restart the container. Leave it there until the disk is repaired.
- Ghost items: run a normal full scan. Jellyfin removes items whose files are gone; deleting them by hand is unnecessary and riskier.
- Split shows: move every file into the renamed folder, or quarantine the strays that no provider knows, then rescan.
- A wrong match: fix it per item (Identify in the UI, or `Items/RemoteSearch/Apply` in the API), then force a full metadata refresh.
- Films inside series folders: move them to the movie library and name them as films. Two-part films use the `- part1` and `- part2` suffix in one folder.

## How to prevent

- Keep quarantine outside every library root.
- After a rename, check that no old folder still holds media.
- Put the provider id in folder names (`[tvdbid-N]`, `[tmdbid-N]`) so matching never depends on the title alone.
- Use Komga (with komf for metadata) for books, comics and manga: Jellyfin's book libraries have no manga metadata provider.

## Which tool helps

- `jellyfix.rename` names folders with provider ids; `jellyfix.prune` quarantines folders left holding only regenerable metadata; `jellyfix.verify` finds broken video files.

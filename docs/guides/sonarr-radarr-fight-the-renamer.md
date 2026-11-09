# Sonarr and Radarr fight your renamer

## Symptom

- Files you renamed are renamed again in another style, or moved back.
- After a library-wide rename, Sonarr shows most series as missing and links only a handful of episode files. Radarr lists renamed folders as unmapped.
- An import drops a 2160p file into the folder of a 1080p release, or names a file `...-Group.mkv.mkv`.
- A re-run of your *arr setup script quietly re-enables things you had switched off.

## Why it happens

- Two tools own naming. Sonarr's `renameEpisodes`, Radarr's `renameMovies` and their Completed Download Handling importer rename files their own way.
- Sonarr and Radarr store a path per series or movie. Renaming folders on disk does not tell them, so the stored paths resolve to nothing.
- Radarr caches its unmapped-folder list, so it still shows folders moved away minutes ago.

## How to check

```sh
curl -s -H "X-Api-Key: $SONARR_API_KEY" http://sonarr:8989/api/v3/config/naming | grep -o '"renameEpisodes":[a-z]*'
curl -s -H "X-Api-Key: $RADARR_API_KEY" http://radarr:7878/api/v3/config/naming | grep -o '"renameMovies":[a-z]*'
curl -s -H "X-Api-Key: $SONARR_API_KEY" http://sonarr:8989/api/v3/config/downloadclient | grep -o '"enableCompletedDownloadHandling":[a-z]*'
```

Count series whose `path` no longer exists on disk.

## Fix

1. Pick one owner for names. If it is your renamer, set `renameEpisodes` and `renameMovies` to false, and set `enableCompletedDownloadHandling` to false in both apps. They then search, grab and hand off to qBittorrent, and nothing else.
2. Import completed downloads yourself: work out the item, build the final name, and ask qBittorrent to rename and relocate the file (`torrents/renameFile`, `torrents/setLocation`), so it lands in the library under its final name and keeps seeding.
3. Repoint Sonarr and Radarr at the renamed folders. Embed the provider id in each folder name (`Show (2004) [tvdbid-N]`, `Movie (1999) [tmdbid-N]`) and match on that id, never on the title.
4. Rescan in Radarr to refresh its cached unmapped-folder list.

## How to prevent

- Re-run the "renamers off" step after any *arr reinstall, and make that script keep existing tags and settings.
- Strip the file extension before parsing a release group, or `x265-Group.mkv` gives the group `Group.mkv`.
- Take the source and the release group from the torrent's release name, not from the file inside, which is often just `Title 2026.mkv`.
- Never file onto an episode that is already on disk; report it instead.

## Which tool helps

- The `jellyfix` plugin is the renamer (`jellyfix.rename`, `jellyfix.verify`, `jellyfix.prune`): plans are reviewed, moves are journalled and reversible, and subtitles follow their video.
- `sonarr_status`, `radarr_status`, `sonarr_queue` and `radarr_queue` are read-only views. Scripts that repoint *arr entries and turn off their renamers are planned.

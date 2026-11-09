# homelab-plugin-jellyfix

Renames and verifies Jellyfin libraries against TVDB (series) and TMDB (movies) using TRaSH-guides naming. It is the plugin form of the standalone jellyFix tool: same matching, same names, but every move is planned and reviewed first, journalled by the engine, and reversible with `undo`. Nothing is deleted.

## Switch it on

`config/plugins.yaml`:

```yaml
plugins:
  jellyfix:
    options:
      libraries:
        - {name: Shows, path: /mnt/media/Shows, kind: series}
        - {name: Anime, path: /mnt/media/Anime, kind: series, anime: true}
        - {name: Movies, path: /mnt/media/Movies, kind: movie}
```

Keys: `TVDB_API_KEY`, `TMDB_V4` or `TMDB_V3` (supplied by UnENVerse or a mode-600 `.env`). `ffprobe` must be installed.

## Jobs

```sh
homelab plan jellyfix.rename --set library=Shows    # nothing changes; notes list issues (ambiguous, unmatched, duplicates)
homelab apply latest --yes
homelab undo latest --yes
homelab plan jellyfix.prune                          # afterwards: quarantine folders left holding only metadata
homelab plan jellyfix.verify                         # ffprobe integrity check, read only
```

A plan with a blocking problem (two files claiming one name, a name over 255 bytes, a destination that exists) has no actions, so it cannot be applied by accident. Subtitle files move with their video.

## Jobs (14)

<!-- jobs:begin -->
| Job | What it does |
| --- | --- |
| `clean_sidecars` | Sweep orphaned sidecars out of the Jellyfin libraries. |
| `consolidate_series` | Merge leftover episodes from pre-rename folders into the real series folder. |
| `extract_films` | Move films out of series folders and into the matching movie library. |
| `fix_movie_names` | Bring the leftover movie folders up to jellyFix naming, and resolve duplicates. |
| `fix_season_nfo` | Normalise season.nfo <title> tags that Jellyfin filled with the wrong names. |
| `flatten_movie_dirs` | Lift movie folders out of collection folders so Radarr can see them. |
| `fold_manga_oneshots` | Give every loose comic archive at a library root its own series folder. |
| `place_specials` | Move specials that were left behind in pre-rename folders into the real series folder. |
| `prune` | Quarantine folders that hold no video and only regenerable metadata (run after rename) |
| `prune_dead_dirs` | Remove library directories that hold no video anywhere below them. |
| `rename` | Plan renames for one library or all (TVDB series, TMDB movies), subtitles follow their video |
| `stale_series` | Report series shown twice because an old folder sits beside the renamed one; quarantine its leftovers |
| `verify` | ffprobe every video and list broken or very short files (read only) |
| `write_episode_nfo` | Write a minimal episode .nfo beside every library episode that lacks one. |
<!-- jobs:end -->

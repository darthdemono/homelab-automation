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

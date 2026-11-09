# Troubleshooting guides

Problems that came back again and again while running a self-hosted media stack (Docker, an *arr stack, qBittorrent behind a VPN container, Jellyfin, Navidrome, Nextcloud, a large library on an NTFS drive). Each guide has the same parts: symptom, why it happens, how to check, fix, how to prevent, and which part of this project helps.

Find one by grepping for what you see: `grep -ril "permission denied" docs/guides/`.

| Guide | Symptom |
| --- | --- |
| [ntfs-case-insensitive-collisions.md](ntfs-case-insensitive-collisions.md) | `Song.flac` and `song.flac` overwrite each other; `:` or `/` vanish from names; two folders differ only in case |
| [damaged-ntfs-volume-moves.md](damaged-ntfs-volume-moves.md) | `rm` gives ENOENT on a file that exists; a move leaves both copies; a walk hangs in `request_wait_answer`; zero-filled files |
| [qbittorrent-behind-gluetun.md](qbittorrent-behind-gluetun.md) | `http://qbittorrent:8080` unreachable; forwarded port never reaches qBittorrent; up command `login failed` |
| [qbittorrent-downloads-land-in-root.md](qbittorrent-downloads-land-in-root.md) | downloads land in `/downloads`, not the category folder; one client gets everything; `directory does not appear to exist` |
| [sonarr-radarr-fight-the-renamer.md](sonarr-radarr-fight-the-renamer.md) | files renamed twice; series missing after a rename; `.mkv.mkv`; 4K file filed into a 1080p folder |
| [arr-wrong-matches.md](arr-wrong-matches.md) | file attached to an unmonitored calendar entry; `Show - 1175` parsed as S11E75; release profile "Must Contain" lets everything through |
| [navidrome-duplicate-albums.md](navidrome-duplicate-albums.md) | one folder shows as two albums; two folders merge into one; album shows `(single)` or `EP` from a raw tag |
| [navidrome-database-is-not-the-truth.md](navidrome-database-is-not-the-truth.md) | track count higher than the library; ISRC lookups find nothing; multi-artist tags collapsed |
| [stale-inventory-after-moves.md](stale-inventory-after-moves.md) | a merge quarantines hundreds of tracks that are not duplicates; plan acts on files that moved |
| [never-rescan-whole-library.md](never-rescan-whole-library.md) | a 20 minute rescan after a small change; a walk of the drive never returns; a day-long analysis for BPM and key |
| [path-keyed-caches-after-replace.md](path-keyed-caches-after-replace.md) | replaced tracks keep old analysis tags or none; `--force` does nothing; tracks land untagged |
| [music-duplicate-tracks.md](music-duplicate-tracks.md) | remix quarantined as a copy; WAV beats identical FLAC; fake lossless kept |
| [rate-limited-free-llm-workers.md](rate-limited-free-llm-workers.md) | worker model stalls with no output; `429` and cooldowns; fan-out makes the machine swap or crash |
| [pkill-kills-own-shell.md](pkill-kills-own-shell.md) | `pkill -f` ends your own command with exit 144; `pgrep -f` loop never finishes |
| [external-api-limits-and-lag.md](external-api-limits-and-lag.md) | MusicBrainz `503` or throttled; new entity not found by search; Spotify `429` stall; `403` HTML block page |
| [secrets-and-unv.md](secrets-and-unv.md) | `401` with a key that "is in the vault"; `invalid_token`; registry `denied`; a value shows up in a log |
| [root-owned-files-from-containers.md](root-owned-files-from-containers.md) | `Permission denied` on folders a container wrote; root-owned config database; wrong `PGID` |
| [nextcloud-dav-and-occ-traps.md](nextcloud-dav-and-occ-traps.md) | contact written by SQL not found or not synced; duplicate card; `OVERWRITE*` ignored; displayname will not change |
| [jellyfin-ghost-and-duplicate-items.md](jellyfin-ghost-and-duplicate-items.md) | scan stuck in `Cancelling`; show listed twice after a rename; phantom season from quarantine |
| [glob-brackets-in-media-names.md](glob-brackets-in-media-names.md) | `glob` returns nothing for a folder named `[tmdbid-N]`; filters match the wrong files |
| [monitoring-exporters-report-nothing.md](monitoring-exporters-report-nothing.md) | smartctl exporter reports 0 devices or empty metrics; Grafana `data source not found`; `${DS_PROMETHEUS}` not found |
| [docker-image-missing-or-denied.md](docker-image-missing-or-denied.md) | `Unable to find image` for a local build after a prune; `ghcr.io` pull `denied` |

Commands use placeholders: `<library>` for a media folder, `/mnt/media` for its mount, `Some Artist` for a name. Read a command before you run it, and plan before you apply.

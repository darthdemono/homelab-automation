# homelab-plugin-qbittorrent

Add, relink, rename, clean and file qBittorrent torrents, with the previous state recorded so it can be undone.

## Jobs (7)

<!-- jobs:begin -->
| Job | What it does |
| --- | --- |
| `add_torrents` | Add .torrent files to qBittorrent, placed and named the way jellyFix would. |
| `clean_torrent_names` | Strip tracker spam from every finished torrent's name, folders and files. |
| `cross_seed` | Find torrents for library files that no torrent seeds, and attach them. |
| `import_archive` | Extract the completed Oyasumi Punpun RAR into the Manga library once. |
| `import_watch` | File completed qBittorrent downloads into the library under TRaSH names. |
| `relink_torrents` | Re-point qBittorrent torrents at files jellyFix has since renamed or moved. |
| `rename_torrents` | Retitle qBittorrent's torrents to the name of the content they actually hold. |
<!-- jobs:end -->

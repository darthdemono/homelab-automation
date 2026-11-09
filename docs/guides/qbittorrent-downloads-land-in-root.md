# Downloads land in the qBittorrent root instead of category folders

## Symptom

- Every new torrent from Sonarr or Radarr lands in the global save path (`/downloads`), not `/downloads/<category>`.
- All series download through one client and one category, even when you set up several (one per library).
- Sonarr health: `directory does not appear to exist` for a download client.
- Folders such as `/downloads/E:/Games` appear after a category fix.

## Why it happens

- With Automatic Torrent Management off (`auto_tmm_enabled=false`) and `use_category_paths_in_manual_mode=false`, a category's save path is never consulted. Everything goes to the global `save_path`.
- Several qBittorrent clients in Sonarr or Radarr with no tags are all eligible for every item, so the one with the best priority always wins.
- A category save path that does not exist on disk raises the health error.
- Category paths imported from a Windows install (`E:/Games`) are relative paths on Linux.
- A setup script that writes `"tags": []` on every client update wipes tag routing when you re-run it.

## How to check

```sh
curl -s http://gluetun:8080/api/v2/app/preferences | python3 -c 'import json,sys;p=json.load(sys.stdin);print(p["auto_tmm_enabled"],p["use_category_paths_in_manual_mode"],p["save_path"])'
curl -s http://gluetun:8080/api/v2/torrents/categories | python3 -m json.tool
curl -s -H "X-Api-Key: $SONARR_API_KEY" http://sonarr:8989/api/v3/downloadclient | python3 -c 'import json,sys;[print(c["name"],c["priority"],c["tags"]) for c in json.load(sys.stdin)]'
```

(Run from a container on the same network, or use published ports. Add a login cookie if localhost auth is on.)

## Fix

1. Turn on `use_category_paths_in_manual_mode` through `/api/v2/app/setPreferences`. Do not turn on Automatic Torrent Management for this: it also relocates existing torrents whenever their category or path changes, which moves hand-placed torrents.
2. Create every category directory on disk (`/downloads/<category>`).
3. Blank or fix category paths that are not absolute Linux paths. Save the old values first.
4. Route by tag: give each library a tag, give each download client the matching tag, and tag every series or movie by its root folder. A tagged client serves only items with that tag.
5. Move torrents already stranded in the root with `/api/v2/torrents/setLocation` (same filesystem, so hardlinks survive).

## How to prevent

- Make the client setup script idempotent and keep the tags already on a client.
- Check the *arr health endpoint after any change to clients or categories.

## Which tool helps

- `qbittorrent_torrents` lists torrents with their save paths; `sonarr_status` and `radarr_status` show health.
- `qbittorrent_pause` stops new writes while you move things, and `undo` resumes exactly those torrents.
- A plugin that sets category routing is planned.

# homelab-plugin-jellyfin

Repair Jellyfin metadata, seasons and duplicate entries through its API, recording what it replaced.

## Jobs (3)

<!-- jobs:begin -->
| Job | What it does |
| --- | --- |
| `fix_season_names` | Set Jellyfin season display names from their folders, and lock them. |
| `jellyfin_providers` | Give every Jellyfin video library its metadata sources again. |
| `jellyfin_tvdb_only` | Make the TV libraries use TheTVDB alone for series/season metadata. |
<!-- jobs:end -->

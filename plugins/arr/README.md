# homelab-plugin-arr

Configure and repair Sonarr, Radarr, Prowlarr and Jackett: idempotent changes that record what they replaced.

## Jobs (9)

<!-- jobs:begin -->
| Job | What it does |
| --- | --- |
| `block_slop` | Stop Sonarr and Radarr grabbing slop and duplicates. |
| `configure_arr` | Wire Sonarr + Radarr to qBittorrent (through Gluetun) with per-library categories, and hand naming authority to jellyFix by disabling the *arr renamers. |
| `configure_prowlarr` | Populate Prowlarr: FlareSolverr proxy, public indexers, and app sync to Sonarr/Radarr. Idempotent — re-running updates rather than duplicating. |
| `configure_qui` | Wire qui to Sonarr and Radarr (and Prowlarr's indexers) so its cross-seed search can identify releases. Idempotent. The qui key comes from QUI_API_KEY (vault entry QUI, exported to a private temp file by the caller); Sonarr, Radarr and Prowlarr keys are read from their own config.xml. Nothing is printed or stored. |
| `fix_download_routing` | Make Sonarr/Radarr downloads land in their per-library folders instead of /downloads. |
| `fix_radarr_matches` | Repoint Radarr at the folders jellyFix renamed, and import the ones it never saw. |
| `fix_sonarr_matches` | Repoint Sonarr at the folders jellyFix renamed, and import the ones it never saw. |
| `jackett_to_prowlarr` | Expose Jackett's working indexers to Sonarr/Radarr by registering them in Prowlarr. |
| `pin_release_terms` | Pin one series to the exact release flavour its existing files already use. |
<!-- jobs:end -->

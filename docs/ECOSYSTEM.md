# What it can do, and what it supports

Status words: **available** means it ships in this repository and is tested; **planned** means it is on the roadmap as a bundled plugin and does not exist yet; **write one** means nothing ships but a script gets you there in minutes (`homelab script-new`).

## The engine (available)

| Capability | How |
| --- | --- |
| Plan first, apply exactly the reviewed plan, undo | `plan`, `apply`, `undo`, `show`, `runs`; every run is a ledger record |
| Safe primitives | actions `move`, `write`, `quarantine` (nothing is ever deleted), `exec`, and plugin-defined actions |
| Permissions | each plugin or script declares reads, writes, network and programs; the engine refuses anything else |
| Find a task | `homelab scripts <words>`, with a `when` hint per script |
| Hints for the model | a nudge (`when`, `before`, `after`, `avoid`) in `describe`, `plan` and `apply` |
| Agent contract | `--json` envelope, ten exit codes, `--dry-run`, `--limit`, `describe` |
| Secrets | UnENVerse (recommended) or a mode-600 `.env`; placeholders in plans, scrubbed output |
| Scheduling | `homelab schedule` renders systemd user timers from `jobs.yaml`; nothing is enabled for you |
| Pipelines | `jobs.yaml` chains `plugin.job` steps, plan-only or applied |
| Audit | `state/audit.jsonl` records every plan, apply, undo, cancel and pause, hash-chained; `homelab audit --verify` |
| Autonomy | `policy` levels (deny, plan, ask, auto) per script, `homelab pause`, scheduled pipelines apply only `auto` steps |
| Inventory | name the stacks a script may touch instead of passing paths |
| Reports | `homelab report` (typed changes since the last snapshot) and `homelab attention` (ranked) |
| Long runs | `apply --detach`, `show`, `cancel`, interrupted-run detection |
| Onboarding | `homelab demo`, `homelab skill --install`, `llms.txt`, Claude, Codex and Gemini manifests |

## Bundled scripts (available)

| Script | What it does |
| --- | --- |
| `clean_junk_files` | quarantine `Thumbs.db`, `desktop.ini`, `.DS_Store` and similar under a library path |
| `docker_ps` | list containers with state, status and image (read only) |
| `compose_restart` | restart some or all services of a Compose file |
| `compose_update` | `docker compose pull`, then `up -d`; records the image ids first, so `undo` returns to the previous images |
| `radarr_status`, `radarr_queue`, `sonarr_status`, `sonarr_queue` | version, health and download queue (read only) |
| `prowlarr_status`, `prowlarr_indexers` | version, health and indexers (read only) |
| `qbittorrent_status`, `qbittorrent_torrents` | version, speeds and torrents (read only) |
| `import_repo_history` | fetch an old repository's complete history into local-only `history/full/NAME/*` branches and copy its notes and handoffs next to the project, for tools being merged in |

## Docker ecosystem

Anything that runs under the `docker` CLI with Compose v2 works today through the three Docker scripts, because they only need the `docker` binary and a Compose file: list, restart and update stacks. The engine plans the exact command, shows it, and runs only that.

Application-aware plugins are **planned**, not shipped. The first ones follow the tools this project grew from:

| Area | Software | Planned plugin | What it will do |
| --- | --- | --- | --- |
| Media server | Jellyfin | `jellyfix` | rename and verify libraries against TVDB and TMDB with TRaSH naming, probe files, undo renames; provider settings |
| TV and movies | Sonarr, Radarr, Prowlarr | read-only scripts **available**; `media-stack` planned | status, queue and indexer views now; match fixes, indexer and client setup, import checks planned |
| Downloads | qBittorrent behind a VPN container such as Gluetun | status and torrent list **available**; `media-stack`, `gluetun` planned | category and path routing, cross-seed, forwarded-port updates |
| Music | Navidrome and a tagged library | `musicfix` | NTFS-safe cleanup, duplicate removal by real audio quality, tags, analysis in a container, playlists |
| Files, calendars, contacts | Nextcloud | `nextcloud` (**available**) | DAV `put` and `delete` of calendar events, contact cards and files: a copy is saved before anything is replaced or removed, and `undo` restores it. See `plugins/nextcloud/README.md` |
| Photos | Immich | write one | scripts through its API, with secrets from UnENVerse |
| Monitoring | Prometheus textfile collectors, SMART | `monitoring` | collectors that write facts for an agent to query |

Anything not listed is **write one**: describe the job to your agent, and it scaffolds a script with `homelab script-new`, so the new one-off is safe, reversible where the actions allow it, and findable next time.

## Roadmap

1. Plugin jobs for the planned plugins above, each proven by a dry-run parity check against the tool it replaces.
2. A `journal` action for runs with thousands of file moves.
3. Plugin-defined collectors for `homelab report`, and write operations for the service plugins.
4. `homelab init`: discover a stack (Compose files, timers, mounts) and draft the config for review.

See [PUBLISHING.md](PUBLISHING.md) for building and publishing, and [CLI.md](CLI.md) for the contract.

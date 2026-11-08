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
| Audit | `state/audit.jsonl` records every plan, apply and undo |

## Bundled scripts (available)

| Script | What it does |
| --- | --- |
| `clean_junk_files` | quarantine `Thumbs.db`, `desktop.ini`, `.DS_Store` and similar under a library path |
| `docker_ps` | list containers with state, status and image (read only) |
| `compose_restart` | restart some or all services of a Compose file |
| `compose_update` | `docker compose pull`, then `up -d`, for a Compose file |
| `import_repo_history` | fetch an old repository's complete history into local-only `history/full/NAME/*` branches and copy its notes and handoffs next to the project, for tools being merged in |

## Docker ecosystem

Anything that runs under the `docker` CLI with Compose v2 works today through the three Docker scripts, because they only need the `docker` binary and a Compose file: list, restart and update stacks. The engine plans the exact command, shows it, and runs only that.

Application-aware plugins are **planned**, not shipped. The first ones follow the tools this project grew from:

| Area | Software | Planned plugin | What it will do |
| --- | --- | --- | --- |
| Media server | Jellyfin | `jellyfix` | rename and verify libraries against TVDB and TMDB with TRaSH naming, probe files, undo renames; provider settings |
| TV and movies | Sonarr, Radarr, Prowlarr, Bazarr | `media-stack` | match fixes, indexer and download-client setup, import checks |
| Downloads | qBittorrent behind a VPN container such as Gluetun | `media-stack`, `gluetun` | category and path routing, cross-seed, forwarded-port updates |
| Music | Navidrome and a tagged library | `musicfix` | NTFS-safe cleanup, duplicate removal by real audio quality, tags, analysis in a container, playlists |
| Files, calendars, contacts | Nextcloud | `nextcloud` (**available**) | DAV `put` and `delete` of calendar events, contact cards and files: a copy is saved before anything is replaced or removed, and `undo` restores it. See `plugins/nextcloud/README.md` |
| Photos | Immich | write one | scripts through its API, with secrets from UnENVerse |
| Monitoring | Prometheus textfile collectors, SMART | `monitoring` | collectors that write facts for an agent to query |

Anything not listed is **write one**: describe the job to your agent, and it scaffolds a script with `homelab script-new`, so the new one-off is safe, reversible where the actions allow it, and findable next time.

## Roadmap

1. Plugin jobs for the planned plugins above, each proven by a dry-run parity check against the tool it replaces.
2. A `journal` action for runs with thousands of file moves, and `apply --detach` with a status file for long runs.
3. Read-only collector jobs that write facts to a local SQLite store, queried with `homelab query`.
4. `homelab init`: discover a stack (Compose files, timers, mounts) and draft the config for review.

See [PUBLISHING.md](PUBLISHING.md) for building and publishing, and [CLI.md](CLI.md) for the contract.

# Support policy

- **Versions.** Only the latest release receives fixes, including security fixes.
- **Python.** The two newest stable CPython releases that the CI matrix covers (currently 3.13 and 3.14).
- **Plugin API.** `homelab-plugin-api` has its own version and an `API_VERSION` string. A plugin declares the `api_version` it was written for, and the engine rejects a mismatch in `homelab doctor`. The API changes incompatibly only with a new `API_VERSION`, announced in the changelog one release ahead.
- **Config files.** The JSON schemas in `schemas/` describe `homelab.yaml`, `plugins.yaml` and `jobs.yaml`. An option is renamed or removed only with a changelog entry and a migration note.
- **Platform.** Linux is the supported platform. systemd user units are rendered for scheduling, and nothing is enabled for you.

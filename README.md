# homelab-automation

A small plugin engine for homelab automation. Every change is planned first, stored in a ledger, applied exactly as reviewed, and reversible. Nothing is ever deleted: removals are moves into a quarantine folder.

Three YAML files configure everything: `homelab.yaml` (paths, state, schedule wrapper), `plugins.yaml` (one block per plugin switches it on and holds its options) and `jobs.yaml` (named pipelines and their schedules).

```
homelab describe                # the whole CLI contract as JSON, read once by an agent
homelab plugins                 # what is installed, on, off or broken
homelab plan jellyfix.rename    # dry run, stored in the ledger
homelab apply <run-id>          # applies exactly that plan
homelab undo <run-id>
homelab run new-media           # plan every step of a pipeline (--apply to apply)
homelab schedule --write ~/.config/systemd/user
homelab plugin-new my_plugin    # scaffold a private plugin in custom/
```

Built for AI agents: every command takes `--json` (`{ok, command, data}`), exit codes are typed, `--dry-run` and `--limit` keep changes and output bounded, and secrets come from [UnENVerse](https://github.com/darthdemono/UnENVerse) (recommended) or a `.env` fallback. See [`docs/CLI.md`](docs/CLI.md), [`docs/SECRETS.md`](docs/SECRETS.md) and `docs/ARCHITECTURE.md`.

## Licence

Copyright 2026 Jubair Hasan (DarthDemono). Licensed under the [Apache License, Version 2.0](LICENSE). The [NOTICE](NOTICE) file must stay with every copy and derivative.

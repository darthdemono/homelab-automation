# homelab-automation

A small plugin engine for homelab automation. Every change is planned first, stored in a ledger, applied exactly as reviewed, and reversible. Nothing is ever deleted: removals are moves into a quarantine folder.

Three YAML files configure everything: `homelab.yaml` (paths, state, schedule wrapper), `plugins.yaml` (one block per plugin switches it on and holds its options) and `jobs.yaml` (named pipelines and their schedules).

```
homelab plugins                 # what is installed, on, off or broken
homelab plan jellyfix.rename    # dry run, stored in the ledger
homelab apply <run-id>          # applies exactly that plan
homelab undo <run-id>
homelab run new-media           # plan every step of a pipeline (--apply to apply)
homelab schedule --write ~/.config/systemd/user
homelab plugin-new my_plugin    # scaffold a private plugin in custom/
```

See `docs/ARCHITECTURE.md`.

## Licence

Copyright 2026 Jubair Hasan (DarthDemono). Licensed under the [Apache License, Version 2.0](LICENSE). The [NOTICE](NOTICE) file must stay with every copy and derivative.

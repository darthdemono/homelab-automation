# homelab-automation

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/darthdemono/homelab-automation/badge)](https://scorecard.dev/viewer/?uri=github.com/darthdemono/homelab-automation)

A plugin engine for homelab automation, built so an AI agent can run it. Drop it in, and the agent gets tools for a data-based homelab: find a task, plan it, show the plan, apply exactly what was reviewed, undo anything. Every change is planned first, stored in a ledger, and reversible. Nothing is ever deleted: removals are moves into a quarantine folder.

## The ecosystem

```mermaid
flowchart LR
    AG["AI agent or you<br/>any shell"]
    V["UnENVerse vault<br/>recommended"]
    ENV[".env fallback<br/>mode 600"]
    H["Your homelab<br/>media server, libraries,<br/>docker, torrent client, systemd"]

    subgraph REPO["homelab-automation"]
        DOC["AGENTS.md<br/>skills/homelab-ops<br/>Agents/Index.md, private"]
        CLI["homelab CLI<br/>describe, scripts, plan, apply, undo<br/>--json, typed exit codes"]
        subgraph ENG["Engine"]
            CFG["3 YAML files<br/>homelab, plugins, jobs"]
            REG["Registry<br/>entry points, scripts/, custom/"]
            RUN["Runner<br/>plan, re-check, apply, undo"]
            SEC["Secrets<br/>placeholders, scrubbing"]
            SCH["Scheduler<br/>systemd user timers"]
        end
        subgraph EXT["Plugins and scripts"]
            BP["Bundled plugins<br/>general tools"]
            BS["scripts/<br/>bundled one-offs"]
            PP["custom/<br/>private plugins and scripts<br/>gitignored"]
        end
        ST[("state/<br/>ledger, quarantine,<br/>audit log, journals<br/>gitignored")]
    end

    AG -->|reads first| DOC
    AG -->|runs| CLI
    CLI --> CFG
    CLI --> RUN
    REG --> BP
    REG --> BS
    REG --> PP
    CFG --> REG
    RUN -->|jobs return plans| REG
    RUN --> ST
    RUN --> SEC
    SEC -->|unv exec| V
    SEC -.->|fallback| ENV
    SCH -->|timers run| CLI
    RUN ==>|only the reviewed plan| H
```

The agent loop, with a nudge at each step (the engine calls no model; the hints are text the script author wrote once):

```mermaid
sequenceDiagram
    participant L as Agent
    participant C as homelab CLI
    participant O as Owner
    participant X as Library
    L->>C: describe (read the contract once)
    L->>C: scripts "season folders"
    C-->>L: up to 5 hits, each with a when hint
    L->>C: plan fix_season_names --set path=...
    C-->>L: plan, nudge.before, next command
    L->>O: show the plan and ask
    O-->>L: approve
    L->>C: apply latest --yes
    C->>X: runs the reviewed plan only
    C-->>L: result and nudge.after
    L->>C: undo latest --yes, if it was wrong
    C->>X: restores, nothing was deleted
```

## Quick start

Python 3.13 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run homelab describe                 # the whole CLI contract as JSON, read once
cp config/homelab.example.yaml config/homelab.yaml    # then plugins.example.yaml, jobs.example.yaml
uv run homelab scripts clean junk       # find a script
uv run homelab plan clean_junk_files --set path=/path/to/library
uv run homelab apply latest --yes       # applies exactly the plan you reviewed
uv run homelab undo latest --yes
```

## What you get

- **Plan first, apply what was reviewed, undo anything.** A plan is stored before anything runs, and `apply` refuses if the files changed since. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
- **Built for agents.** Every command takes `--json` (`{ok, command, data}`), exit codes are typed, `--dry-run` and `--limit` keep changes and output bounded, and no MCP server is needed. See [docs/CLI.md](docs/CLI.md).
- **Scripts with a nudge.** A one-off fix is one decorated function that gets plan, apply, undo and audit for free, and carries four short hints (`when`, `before`, `after`, `avoid`) that tell an agent when to use it and what to check. `homelab scripts` finds one, `homelab script-new` writes the next.
- **Plugins.** A plugin declares what it may touch, and the engine refuses anything else. General plugins are bundled; your own live in the gitignored `custom/` folder, one folder each, with the same contract. Three YAML files configure everything.
- **Secrets without leaks.** Use [UnENVerse](https://github.com/darthdemono/UnENVerse) (recommended, see [docs/UNV.md](docs/UNV.md)) or a mode-600 `.env` fallback (see [.env.example](.env.example)). Values are never printed, logged or stored; plans carry placeholders.
- **A public repo that works with your private files.** `AGENTS.md` is generic and tells a model to read `Agents/Index.md` if it exists. That folder, `custom/`, `state/`, `config/*.yaml`, `Handoff/` and `Research/` are gitignored.

## Documents

| | |
| --- | --- |
| [docs/CLI.md](docs/CLI.md) | The CLI contract: envelope, exit codes, scripts and the nudge |
| [docs/UNV.md](docs/UNV.md) | Secrets with UnENVerse, and the `.env` fallback |
| [docs/SECRETS.md](docs/SECRETS.md) | The engine side of secrets |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline, invariants and decisions |
| [THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY.md](SECURITY.md) | What is defended, and how to report a problem |
| [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) | Working on the repo, as a person or an agent |

## Licence

Copyright 2026 Jubair Hasan (DarthDemono). Licensed under the [Apache License, Version 2.0](LICENSE). The [NOTICE](NOTICE) file must stay with every copy and derivative.

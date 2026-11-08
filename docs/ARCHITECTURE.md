# Architecture

Normative description of how homelab-automation plans, applies and undoes changes. "Must" and "never" are requirements; tests enforce the ones marked (tested).

## Pipeline

```
config (3 YAML files) -> registry -> job.plan -> permission check -> ledger (planned)
                                                                        |
                                          apply (re-check, execute) -> ledger (applied) -> undo
```

1. **Config.** `homelab.yaml` holds paths, the state folder and the scheduler wrapper. `plugins.yaml` has one block per plugin: the block switches the plugin on and holds its options. `jobs.yaml` names pipelines (an ordered list of `plugin.job` steps) and their schedules. Unknown keys are errors (tested).
2. **Registry.** Plugins come from installed entry points (`homelab.plugins`) and from the private `custom/` folder, as a file or a package. A plugin that fails to load is reported and skipped; the others keep working (tested).
3. **Plan.** A job's `plan(ctx, options)` returns actions and changes nothing. The engine checks every action against the plugin's manifest and refuses the plan if any action is outside it (tested). The plan is stored in the ledger.
4. **Apply.** Applies exactly the stored plan. Before the first change every move and quarantine is re-checked, and a mismatch aborts with nothing changed (tested). Each executed action appends an undo record to the ledger immediately.
5. **Undo.** Reverses the records in reverse order after a preflight. It refuses if a file was edited after the run (tested). Irreversible `exec` steps are listed, not hidden (tested).
6. **Schedule.** `homelab schedule` renders systemd user units for pipelines with a schedule. It enables nothing.

## Invariants

- Nothing is ever deleted: removal is a move into `state/quarantine/<run-id>/` (tested).
- A plan changes nothing; an apply only runs a stored plan (tested).
- Commands are argument lists, never shell strings.
- The engine never stores or prints a secret; plugins name environment variables.
- A plugin's permissions come from its manifest, not from its code: `reads`, `writes` (a path prefix or a `{key}` from `homelab.yaml`), `network` and `exec`.

## Decisions

- **The contract is a separate package** that depends only on pydantic, so a plugin never needs the engine.
- **General tools are bundled, personal ones are private plugins.** The public code never reveals what one person uses; a private plugin lives in `custom/`, with its own folder, tests and README, and `homelab plugin-new` scaffolds one.
- **Wrap before rewriting.** An existing tool joins as a plugin that calls it, and is split into plugin jobs only after its dry-run output is shown identical.
- **A ledger on disk, no daemon.** Scheduling is systemd's job.

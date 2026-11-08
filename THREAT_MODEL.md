# Threat model

What homelab-automation defends against, and what it does not. It changes files and runs commands on a machine the owner controls, so the question is how far a mistake or a bad input can reach.

## Assets

- The files a plugin manages (media and music libraries, config folders).
- The run ledger in `state/`, which is the only record needed to undo a change.
- Credentials, which live in the process environment and never in this project's files.
- The systemd units the engine renders.

## Actors

- **The owner**, who is trusted and makes mistakes.
- **A plugin author**, whose code runs in the engine's process with the owner's rights.
- **A malicious or malformed input**: a hand-edited YAML file, a ledger file, a file name inside a managed library.
- **A compromised dependency or workflow.**

## What is defended

| Risk | Defence | Test |
| --- | --- | --- |
| A plan touches a path outside what the plugin declared | The engine resolves every path (symlinks included) and refuses a plan outside the manifest's `writes`; `exec` must name a declared program | `test_plan_outside_declared_writes_is_refused` |
| The library changed between review and apply | `apply` re-checks every move and quarantine and refuses before changing anything | `test_apply_refuses_when_the_library_changed` |
| Data loss | Nothing is deleted: removals are moves into `state/quarantine/<run>/`, and undo of a created file also moves it there | `test_nothing_is_ever_deleted` |
| Undo overwrites later edits | `undo` refuses if a written file was edited after the run | `test_undo_refuses_after_an_edit` |
| A command string is injected | Commands are argument lists, never a shell string | `exec` handling in `runner.py` |
| A broken plugin stops everything | Plugins load in isolation; a failure is reported and the others keep working | `test_discovery_finds_custom_files_and_isolates_broken_ones` |
| A bad config is accepted silently | Unknown keys are errors; options are validated by the plugin's own model | `test_config_rejects_unknown_keys` |
| Config parsing crashes | The parser is fuzzed | `fuzz/fuzz_config.py` |
| A tampered release | Keyless signatures, build provenance, SBOM and checksums | Release workflow |

## What is not defended

- **A plugin's code is trusted.** The permission check covers the actions a plan returns, not what a plugin's own Python does while planning. Install only plugins you have read. `plugin` actions are applied by the plugin itself.
- **`exec` actions are irreversible** unless the plan supplies an `undo_argv`; `undo` says which steps it skipped.
- **A race between the check and the move** is narrow but possible: the engine re-checks immediately before acting and does not lock the filesystem.
- **The owner's account.** Anyone who can run the engine or edit `state/runs/` can alter what `apply` does, because they hold the owner's rights.
- **Secrets in plugin output.** A plugin must not print the value of a secret; the engine cannot tell.

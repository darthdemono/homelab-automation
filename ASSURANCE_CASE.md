# Assurance case

Why the project's safety claims hold, and where an independent review should start.

## Claims and evidence

1. **No change happens without a stored plan.** `make_plan` writes a ledger record before anything runs; `apply` only accepts a record in the `planned` state. Evidence: `tests/test_runner.py::test_only_a_planned_run_can_be_applied_once`.
2. **Apply runs exactly the reviewed plan, or nothing.** Moves and quarantines are re-checked first; a mismatch aborts before the first change. Evidence: `test_apply_refuses_when_the_library_changed`.
3. **Every applied run can be undone, or says why not.** Undo is reversed step by step after a preflight; irreversible `exec` steps are listed. Evidence: `test_apply_then_undo_restores_everything`, `test_exec_is_irreversible_and_undo_says_so`.
4. **Plugins cannot plan outside their manifest.** Evidence: `test_plan_outside_declared_writes_is_refused`, and `homelab doctor` runs the same check as part of conformance.
5. **Releases are verifiable.** Evidence: the Release workflow signs, attests and publishes an SBOM.

## Boundaries

The engine and the plugin API package are the trusted core. Plugins, config files and the ledger directory are inputs. See the [threat model](THREAT_MODEL.md).

## Known weaknesses

- The permission check cannot see what a plugin's code does outside the actions it returns.
- A crash in the middle of `apply` leaves the run in the `failed` state with a partial undo list; recovering is `homelab undo <run-id>`.
- There is one maintainer, so no second person reviews changes; automated checks (CI, CodeQL, OSV, zizmor, Scorecard) stand in.

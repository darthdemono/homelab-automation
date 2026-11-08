# Verify it yourself

Do not take the README's word for it. Each claim below has a command that checks it.

| Claim | Check |
| --- | --- |
| The contract is what the binary runs | `homelab describe` prints every command, flag and exit code, generated from the same table the parser is built from. `tests/test_cli.py::test_describe_covers_every_command_and_cannot_drift` fails if they diverge |
| Nothing changes without a plan | `homelab plan <script>` writes only a ledger record. Compare the files before and after; `homelab apply` is the only command that acts |
| Apply runs exactly the reviewed plan | `homelab show <run>` lists the stored actions. Change a file the plan moves, then `homelab apply <run> --yes`: it refuses with exit 6 |
| Nothing is deleted | Removals land in `state/quarantine/<run>/`, and a replaced file's copy is kept; `homelab undo` restores them |
| The audit log cannot be edited quietly | `homelab audit --verify` recomputes the hash chain. Change one byte of `state/audit.jsonl` and it exits 1 and names the line |
| Secrets are not printed or stored | `homelab plan` of a job that uses one shows a `{{secret:NAME}}` placeholder. `grep -r <value> state/` finds nothing (`tests/test_cli.py` checks this) |
| Policy limits what runs unattended | `homelab policy` shows each script's level. A scheduled pipeline applies only `auto` steps; `homelab pause` stops every apply |
| The engine does not phone home | `grep -rn "urlopen\|requests\|http" src/homelab` shows no network use in the engine; only individual plugins and scripts talk to the services you configure, and only to the URL you give them |
| Releases are what CI built | `gh attestation verify <file> --owner darthdemono` checks the build provenance, and `cosign verify-blob` checks the signature (see `docs/PUBLISHING.md`) |
| The repository's security posture | The OpenSSF Scorecard report is public: `https://scorecard.dev/viewer/?uri=github.com/darthdemono/homelab-automation` |
| Nothing private is published | `python tools/leak_audit.py --all-history` scans every commit for local paths, secret-shaped values and your own patterns |

If a check fails, that is a bug: open an issue.

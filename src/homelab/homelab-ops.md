---
name: homelab-ops
description: Operate a homelab through the homelab CLI: inspect it, plan a change, show the plan, ask the owner, apply and verify, or undo. Use for any media, music, docker, storage or scheduling task on the homelab.
---

# homelab-ops

1. Run `homelab describe` once for the contract (commands, exit codes, envelope). `homelab describe <plugin>` gives one plugin's options and jobs.
2. Looking for a task? `homelab scripts <words>` (max 5 hits, local search) before writing anything new. Read the `when` and `avoid` hints. `homelab describe <script>` gives its options and nudge; set options with `--set KEY=VALUE`.
3. Look first: `homelab status --json`, `homelab plugins --json`, `homelab doctor --json`.
4. Change things in this order only: `homelab plan <plugin.job> --json`, show the plan to the owner, ask, then `homelab apply <run-id> --yes`, then `homelab show <run-id>` to verify. Use `--dry-run` to re-check a plan first.
5. To reverse a run: `homelab undo <run-id> --yes`. Steps that cannot be undone are listed.
6. Read exit codes, not messages: 3 not found (see `details.suggestions`), 4 ambiguous (see `details.candidates`), 5 denied, 6 conflict (re-plan), 7 unavailable, 8 needs `--yes`, 10 invalid.
7. Never print, request or write a secret. Secrets come through UnENVerse (`unv`) or a 600-mode `.env`; `homelab secrets` shows sources by name.
8. Never apply a plan you have not shown. Keep output small with `--limit`.
9. A new one-off task: `homelab script-new <name>`, fill in the plan and the nudge, `homelab doctor`, then plan and apply it like any other script.

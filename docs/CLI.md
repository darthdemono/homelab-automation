# CLI contract

The command line is the interface for people and for agents. `homelab describe` prints this contract as one JSON document, generated from the same table the parser is built from, so it cannot describe a flag that does not exist. An agent reads it once instead of guessing from error messages.

## Output

Pass `--json` to any command, before or after the subcommand. stdout is then exactly one compact JSON document (`--pretty` indents it). Without `--json` the output is short text meant for a person.

```json
{"ok": true, "command": "plan", "data": {}}
{"ok": false, "error": {"code": "not_found", "message": "...", "details": {"suggestions": ["demo.tidy"]}}}
```

`describe`, `schema` and `completions` print their document or script as is. A command that changes things accepts `--dry-run`: it runs every check, changes nothing, and the envelope carries `"dry_run": true`.

## Exit codes

| Code | Name | Meaning |
| --- | --- | --- |
| 0 | ok | |
| 1 | error | Unclassified failure, for example an action that failed during apply, or `doctor` found problems |
| 2 | usage | Bad arguments |
| 3 | not_found | No such plugin, job or run. `details.suggestions` lists near matches |
| 4 | ambiguous | Matched several things. `details.candidates` lists them; nothing is guessed |
| 5 | denied | A plan was refused because it leaves the plugin's declared permissions |
| 6 | conflict | The files or the run changed since the plan, or the run is in the wrong state |
| 7 | unavailable | Plugin not switched on in `plugins.yaml`, did not load, or `unv` is missing |
| 8 | needs_confirmation | `apply` or `undo` without `--yes` and without a terminal |
| 10 | invalid | Config, options or a `.env` file failed validation |

## Conventions

- **Workflow.** `describe` once, then `plan`, read the plan, ask the owner, `apply --yes`, verify with `show`. Never apply a plan you did not show.
- **Lookup.** A run is an id, a unique prefix of one, or `latest`. A job is `plugin.job`, or a bare name that exists in exactly one enabled plugin.
- **Bounded output.** `--limit` caps long lists, and a capped list says how many were left out (`left_out`).
- **Confirmation.** `apply` and `undo` ask on a terminal, and need `--yes` when stdin is not one.
- **Audit.** Every plan, apply and undo appends a line to `state/audit.jsonl`.
- **Secrets.** Never printed. See [SECRETS.md](SECRETS.md).

## Commands

`describe`, `status`, `plugins`, `policy`, `pause`, `resume`, `report`, `attention`, `skill`, `demo`, `audit`, `scripts`, `doctor`, `secrets`, `plan`, `apply`, `cancel`, `undo`, `show`, `runs`, `run`, `schedule`, `plugin-new`, `script-new`, `schema`, `completions`. Run `homelab describe` for the arguments of each, and `homelab completions bash|zsh|fish` for shell completion.

## Scripts and the nudge

A script is one decorated function (`@script`) that returns a plan, so a one-off fix gets plan, apply, undo, audit, secrets and `--dry-run` like any plugin. `homelab scripts [words] [--tag T] [--status S] [--limit N]` finds one (at most 5 hits by default; words match the name, summary, tags and the `when` hint), and `homelab plan <script>` runs it. Options are set with `--set KEY=VALUE` (a value is JSON when it parses as JSON, text otherwise) and are stored with the run, so `apply` uses the same ones.

A script carries a **nudge**: four short strings (`when`, `before`, `after`, `avoid`). The engine never calls a model. It shows the hints when they help: `describe <script>` prints all four, `plan` adds `data.nudge` (`before`, `avoid`), `data.ask_owner` and `data.next` (the exact next command), `apply` adds the `after` hint, and the text output ends with a `next:` line. `homelab script-new <name>` scaffolds one (private by default, `--public` for the bundled `src/homelab/scripts/` of a clone), and `homelab doctor` rejects a script with no `when` hint or no tag. Status is `one-off`, `stable` or `retired`.

## Autonomy, pause and the audit chain

`policy` in `homelab.yaml` sets how much a job may do: `deny` (not even a plan, exit 5), `plan` (plan only, apply is refused), `ask` (the default: apply needs a person, `--yes`), `auto` (a scheduled pipeline may apply it unattended). Rules are globs on `plugin.job` or on a plugin or script name; the first match wins. A scheduled pipeline (the rendered units set `HOMELAB_SCHEDULED=1`) applies only `auto` steps and leaves the rest planned for review. `homelab policy` shows the effective levels, `homelab pause` switches every apply off (plans and undo still work) and `homelab resume` switches them back on.

```yaml
policy:
  default: ask
  rules:
    - {match: "docker_ps", level: auto}
    - {match: "compose_*", level: ask}
    - {match: "nextcloud.delete", level: plan}
```

`inventory` names the things a script may touch, so an agent says `stack=media` and never passes a raw path:

```yaml
inventory:
  stacks: {media: ~/docker/docker-compose.yml}
```

Every plan, apply, undo, cancel, pause and resume is a line in `state/audit.jsonl` carrying the hash of the line before it. `homelab audit` lists the latest events and `homelab audit --verify` recomputes the chain, exiting 1 and naming the line if one was edited, removed or reordered.

## Reports, long runs and onboarding

- `homelab report` snapshots the stack (containers and free space on the configured paths) and lists what changed since the last snapshot, with typed kinds: `gone`, `new`, `replaced`, `state`, `restarts`, `disk`, `skipped` (a collector failed, so no comparison was possible; it is never reported as "no changes"). `--no-save` compares without writing, `--keep N` prunes. `homelab attention` ranks failed or stale runs, a broken audit chain, not-running containers and the latest changes.
- `homelab apply <run> --yes --detach` runs in the background and returns at once; `homelab show <run>` follows it (`running`, then `applied` or `failed`), `homelab cancel <run>` stops it, and what it already did can be undone. A run whose process vanished is marked failed (interrupted) so it can be undone.
- `homelab skill` prints the operating skill for this version and `homelab skill --install ~/.claude/skills` installs it. `homelab demo` creates a sandbox with fake files so plan, apply and undo can be tried safely. `llms.txt` indexes the docs for agents, and the plugin manifests (`.claude-plugin`, `.codex-plugin`, `gemini-extension.json`) expose the skill to Claude Code, Codex and Gemini.

## Letting an agent use it

Allow the read commands and make the agent ask before the ones that change things. For Claude Code, see [`agent-permissions.example.json`](agent-permissions.example.json). The agent's own permission prompt is the approval step: the engine cannot tell a person from an agent that holds a shell, and the [threat model](../THREAT_MODEL.md) says so.

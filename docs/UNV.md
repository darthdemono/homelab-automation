# Using UnENVerse (`unv`) for secrets

[UnENVerse](https://github.com/darthdemono/UnENVerse) is the recommended secret source. With it you keep API keys in an encrypted vault, and the engine receives them as environment variables for one run. A value never reaches stdout, a log, the ledger or a file. Everything below uses commands that exist in `unv`; `unv describe` prints the full contract.

If you do not use UnENVerse, see [the `.env` fallback](#the-env-fallback). Both are covered by `secrets.provider: auto`, which tries the process environment, then `unv`, then `.env`.

## How the engine uses it

A plugin or script declares the variable names it needs in its manifest (`secrets=["JELLYFIN_API_KEY"]`). Before it runs a job, the engine finds the names that are not in the environment and re-executes itself as

```
unv exec --entry <entry>=<NAME> ... -- homelab <your command>
```

so the values exist only inside that process. `homelab secrets` shows where each declared name would come from (names only), and `homelab doctor` reports a missing one.

## One-time setup

1. Install UnENVerse and unlock or point it at your vault. `unv status` shows where the CLI is pointed and what the vault holds. `unv doctor` checks the vault.
2. Add each key as an entry. Keep the value out of your shell history by reading it from stdin:

   ```sh
   printf '%s' "$VALUE" | unv entry add jellyfin --key-stdin
   ```

   (`--dry-run` on a mutating command reports what it would change and writes nothing.)
3. See which variable names the vault generates for your entries. Names only, never values:

   ```sh
   unv entry names
   ```

4. Tell the engine how a variable name maps to an entry, only when they differ. In `config/homelab.yaml`:

   ```yaml
   secrets:
     provider: auto            # auto | unv | dotenv | env
     project: homelab          # optional: load a whole unv project instead of single entries
     entries:                  # ENV_VAR -> unv entry, or entry:field for one field of an entry
       JELLYFIN_API_KEY: jellyfin
       SITE_EMAIL: mysite:email
   ```

   `SITE_EMAIL: mysite:email` becomes `unv exec --entry mysite=SITE_EMAIL:email`, which injects the entry's `email` field. A name that already equals the vault's generated name needs no mapping: the engine runs `unv exec --entry NAME`.

5. Check without running anything:

   ```sh
   homelab secrets            # where each declared name would come from
   homelab doctor             # missing secrets are reported by name
   ```

## Recipes

| You want | Command |
| --- | --- |
| Run one job with its secrets | `homelab plan my_script` (the engine re-executes under `unv exec` for you) |
| See what a vault entry is called | `unv list`, `unv entry names` |
| Use a one-time code (TOTP) a site issued | `unv totp code <entry>` prints the current code; `unv totp ls` lists entries with a seed (names and parameters, never codes) |
| Rotate between several keys for one service | `unv exec --pool <pool>=<NAME> -- ...`; membership is `unv entry set <entry> --pool <name>` |
| Run any command with a secret, no homelab | `unv exec --entry jellyfin=JELLYFIN_API_KEY -- curl ...` |
| Read command output without leaking values | `unv shield -- <command>` replaces known vault values in its output |

## Scheduled runs (systemd)

A timer has no terminal to unlock the vault, so give `unv` a way to authenticate by itself. Use one of:

- a remote `unv-server` and an API token: `UNV_SERVER_URL` and `UNV_TOKEN` in the unit's environment;
- a password file with mode 0600: `unv --password-file FILE ...`;
- a keyring helper: `--password-command 'pass show unv'`.

Put the wrapper in `homelab.yaml` so rendered units use it (`homelab schedule --write ~/.config/systemd/user` renders the units and enables nothing):

```yaml
schedule_wrapper: ["unv", "--password-file", "/home/you/.config/unv/password"]
```

Never put a vault password in a unit file or in `.env`.

## Troubleshooting

`unv` exit codes carry the answer (see `unv describe`): 3 not found, 4 ambiguous, 5 denied, 7 vault or server unavailable or locked.

- **Exit 3 on an entry.** The name does not match. Run `unv entry names` and add a `secrets.entries` mapping.
- **Exit 4.** The name matched several entries. Disambiguate with `provider:key_id` in `secrets.entries`.
- **Exit 7.** The vault is locked or the server is unreachable. Unlock it, or check `UNV_SERVER_URL`.
- **`homelab` says `unv` is not on PATH.** Install UnENVerse, or set `secrets.provider: dotenv`.

## The `.env` fallback

For machines without UnENVerse. Copy [`.env.example`](../.env.example) to `.env`, run `chmod 600 .env`, and fill in only the names your plugins declare (`homelab secrets` lists them). The engine refuses a `.env` that other users can read, reads only declared names, and never prints them. A secret in a file is readable by anything running as you, which is why UnENVerse is recommended.

## Rules the engine keeps

- A value is never printed, logged or written by the engine. Plans hold `{{secret:NAME}}` placeholders that are filled in at apply time.
- A value that still reaches a plan is shown as `[redacted:NAME]` in the ledger and in output.
- Commands are argument lists, so a secret is never part of a shell string.

See [SECRETS.md](SECRETS.md) for the engine side and [`.env.example`](../.env.example) for every variable the project uses.

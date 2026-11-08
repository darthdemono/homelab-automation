# Secrets

Plugins read secrets from environment variables and nowhere else. A plugin declares the names it needs in its manifest (`secrets=["JELLYFIN_API_KEY"]`).

## Sources, in the order `provider: auto` tries them

1. **The process environment.** If the variable is already set, it is used.
2. **UnENVerse (recommended).** The engine re-executes itself under `unv exec --entry NAME`, so the value enters the process environment and never reaches stdout or a file. `secrets.project` loads a whole project instead, and `secrets.entries` maps a variable name to a vault entry when the names differ (`JELLYFIN_API_KEY: jellyfin` runs `unv exec --entry jellyfin=JELLYFIN_API_KEY`).
3. **A `.env` file (fallback).** `secrets.dotenv` (default `.env`). The engine refuses the file unless only its owner can read it (`chmod 600`) and reads only the names plugins declare. `.env` is gitignored.

`provider` can be pinned to `unv`, `dotenv` or `env`. `homelab secrets` shows where each declared name would come from, names only. `homelab doctor` reports a missing secret.

## Rules the engine keeps

- A value is never printed, logged or written by the engine.
- A plugin puts `ctx.secret_ref("NAME")` in an action's args, not the value. The engine fills in `{{secret:NAME}}` when it applies, so the plan, the ledger and every printout hold the placeholder.
- If a value ends up in a plan anyway, the ledger and the output show `[redacted:NAME]` instead (values shorter than 8 characters are not recognised).
- Commands are argument lists, so a secret is never part of a shell string.

## Configuration

```yaml
# homelab.yaml
secrets:
  provider: auto          # auto | unv | dotenv | env
  project: homelab        # optional: unv exec --project homelab
  entries:                # optional: ENV_VAR -> unv entry
    JELLYFIN_API_KEY: jellyfin
  dotenv: .env
```

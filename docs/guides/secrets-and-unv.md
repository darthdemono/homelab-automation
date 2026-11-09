# Secrets: 401s, wrong fields and keys that end up in files

## Symptom

- A service answers `401` although the key "is in the vault".
- An API answers `401 invalid_token` because the value sent was the client id, not a token.
- Nextcloud rejects the admin login over DAV with `401`, or a profile update with `403 Password confirmation is required`.
- A script works by hand but returns an empty result when the vault is locked, as if the key were missing.
- Image pulls from a public registry fail with `denied`.
- A key turns up in a log, a plan file or a shell history.

## Why it happens

- A vault entry often holds several fields (client id, client secret, token). Injecting the wrong field gives a valid-looking value that the service rejects.
- Nextcloud DAV and OCS calls from scripts need an app password, not the account password, and some OCS endpoints refuse app passwords entirely.
- Code that returns an empty dict when the vault cannot be read turns "locked" into "missing".
- A stale credential is worse than none: `docker` sends an expired token from `~/.docker/config.json` to a public registry and is refused, where an anonymous pull would work.
- Values pass through `echo`, string-built shell commands or files written "just for a moment".

## How to check

```sh
homelab secrets                       # where each declared name would come from, names only
homelab doctor                        # reports a missing secret
unv exec --entry <entry>=NAME -- sh -c 'test -n "$NAME" && echo set'      # presence, never the value
grep -n '"auths"' -A3 ~/.docker/config.json                                # registries with stored logins
```

## Fix

- Map each variable to the right field explicitly (`entry:field` or `entry#field` in `secrets.entries`).
- For Nextcloud, create an app password and store it as `NEXTCLOUD_APP_PASSWORD`. On a server you administer you can mint one per run with `occ user:auth-tokens:add <user>`, use it, and delete it in the same run. Delete it by matching the token's name, never by "newest id": the other rows are real device sessions.
- Make secret loading raise when the vault is locked or unreachable.
- Remove an expired registry login with `docker logout <registry>` and pull again.
- If a value leaked into a committed file, rotate it first, then clean history (see `docs/PUBLISHING.md`).

## How to prevent

- Inject values into the one process that needs them (`unv exec --entry ... -- <command>`), never into files. A mode-600 `.env` is the fallback, not the default.
- Pass commands as argument lists, so a value is never part of a shell string.
- Keep plans free of values: plugins use `ctx.secret_ref(NAME)` placeholders that are filled at apply time.
- Run the leak audit before every push (`python3 tools/leak_audit.py`).

## Which tool helps

`homelab secrets`, `homelab doctor`, the secrets layer described in `docs/UNV.md` and `docs/SECRETS.md`, and `tools/leak_audit.py`. The `nextcloud` plugin reads `NEXTCLOUD_APP_PASSWORD`.

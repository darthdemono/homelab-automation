# Building, publishing and keeping local files local

This repository is public. The machine that works on it also holds private files: personal instruction notes, private plugins, real config, the run ledger, env files. This page is the local procedure for building the project, publishing it, and making sure none of that leaves.

## What stays local

| Path | What it holds | Why it cannot leak |
| --- | --- | --- |
| `Agents/` | personal instruction files; `Agents/Index.md` lists them, `Agents/leak-patterns.txt` holds personal strings to block | gitignored |
| `custom/` | private plugins and scripts, one folder each | gitignored |
| `config/*.yaml` | the real config (the `*.example.yaml` files are tracked) | gitignored |
| `state/` | run ledger, quarantine, audit log, per-plugin journals | gitignored |
| `.env`, `.env.*` | the secret fallback (`.env.example` is tracked) | gitignored |
| `Handoff/`, `Research/`, `CLAUDE*.md`, `CODEX*.md`, `GEMINI*.md` | local notes and per-tool files | gitignored |
| `history/*` branches | filtered history of tools being migrated | refused by the pre-push hook |

`.gitignore` is the first guard, `tools/leak_audit.py` is the second, and the `main` ruleset on GitHub is the third.

## Build

Python 3.13 or newer and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                                   # environment from uv.lock
uv run pytest -q --cov=homelab --cov-fail-under=85
uv run ruff check . && uv run ruff format --check .
uv run python tools/gen_schemas.py --check
uv run python tools/gen_env_example.py --check
uv lock --check
uv build --all-packages                   # sdist and wheel of the engine and the plugin API into dist/
```

These are the same steps CI runs on Python 3.13 and 3.14. Run them before every push.

## Publish a change

`main` is protected: no direct pushes, no force pushes, linear history, a pull request, the `CI result` check and the CodeQL gate. Work on a branch.

```sh
git fetch origin
git switch -c my-change origin/main       # always cut from origin/main
# ... edit, commit in small topical groups, one plain sentence per commit ...
git config core.hooksPath .githooks       # once: ruff on commit, leak audit on push
python tools/leak_audit.py                # explicit leak check of every unpushed commit
git push -u origin my-change
gh pr create --base main                  # then wait for CI result, and merge with squash or rebase
```

Merge commits are disabled on GitHub (squash or rebase only), and a merged branch is deleted automatically.

## The leak audit

`tools/leak_audit.py` reads every commit that is not on a remote (or `--all-history`, `--range A..B`, `--tree`) and reports, without ever printing a matched value:

- paths that must stay local (the table above), even if a later commit removed them;
- the home folder and host name of the machine running it, and secret-shaped values (private keys, tokens, `api_key = ...`);
- your own patterns, one regular expression per line, in `Agents/leak-patterns.txt` (gitignored, so the personal strings stay local too).

The `pre-push` hook (`.githooks/pre-push`) runs it on every push and also refuses any `history/*` branch. Never use `git push --all` or `git push --mirror` in this repository.

## Release

A release is manual and starts from `main` after CI passed in the same run.

1. On a branch: bump the version (`uv version --bump minor`), move the `Unreleased` entries in `CHANGELOG.md` under the new version, open a pull request, merge it.
2. Run the **Release** workflow from the Actions tab (or `gh workflow run release.yml`). It builds the sdist and wheel, writes a CycloneDX SBOM and `SHA256SUMS`, attests build provenance, signs every file with cosign (keyless), and creates the GitHub release.
3. PyPI is off until the repository variable `PUBLISH_PYPI` is `true` and a trusted publisher is configured on PyPI for the `release.yml` workflow and the `pypi` environment.

## What exists on GitHub

| | |
| --- | --- |
| Workflows | `CI` (tests, lint, schemas, env sample, lockfile, gate `CI result`), `CodeQL`, `Scorecard`, `Security audit` (gitleaks, zizmor, OSV, dependency review), `Fuzz` (weekly), `Release` (manual) |
| Ruleset `protect-main` | `.github/rulesets/protect-main.json`: pull request, `CI result`, CodeQL gate at medium or higher, linear history, no deletion, no force push. The repository admin can bypass only through a pull request |
| Repository settings | secret scanning with push protection, private vulnerability reporting, Dependabot alerts and security updates, read-only workflow token, squash or rebase merges only |
| Dependabot | weekly, after a 7-day cooldown, for `uv` and `github-actions`; each update is a pull request that must pass `CI result` |
| Pinned actions | every action is pinned by full commit SHA with a version comment; Dependabot proposes the bumps |

Re-apply the ruleset after editing the JSON: `gh api -X PUT repos/OWNER/REPO/rulesets/ID --input .github/rulesets/protect-main.json` (`gh api repos/OWNER/REPO/rulesets` lists the id).

On the very first push the gitleaks action cannot scan from the root commit's parent and reports a failure; the next push and the scheduled runs scan normally.

## Import the history of another repository

To keep the commit history of a tool that moves into a plugin, without its private files:

```sh
git clone --no-local /path/to/old-repo /tmp/old
cd /tmp/old
uvx --from git-filter-repo git-filter-repo --force \
  --path src_dir --path tests --path README.md \
  --to-subdirectory-filter plugins/NAME \
  --message-callback 'return message.replace(b"CLAUDE.md", b"the instruction file")'
cd /path/to/homelab-automation
git fetch /tmp/old HEAD:history/NAME      # a local-only branch; the hook refuses to push it
```

Keep only code paths (never plans, reports, journals, handoffs or data), then run `python tools/leak_audit.py --range history/NAME` on it. When the plugin is ported, merge it on a pull-request branch with `git merge --allow-unrelated-histories history/NAME`, and add `plugins/NAME/pyproject.toml` in the same merge, because every `plugins/*` folder must be a workspace member. `git log --follow` then shows the old history.

## If something leaked

1. Stop. If it was not pushed, fix the commits (`git rebase`, or `git filter-repo --replace-text`), run the audit, and push.
2. If it was pushed: rotate every credential that was exposed first (the history is public the moment it lands), then rewrite the history and force-push. `main` refuses force pushes, so the owner must switch the ruleset's enforcement to disabled for the push and back to active afterwards. Ask GitHub support to drop cached views of the old commits.

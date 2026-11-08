# Contributing

Thanks for helping. homelab-automation changes files on a real library, so the bar for changes to the engine, the ledger or the permission check is high. Small fixes, tests, docs and new general-purpose plugins are very welcome.

## Before you start

- Open an issue first for anything bigger than a bug fix, so the design is agreed before the work is done.
- **Security problems are not issues.** Report them privately, as described in [SECURITY.md](SECURITY.md).
- By contributing you agree your work is licensed under Apache-2.0, the licence of this repository, and you certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the change or have the right to submit it under that licence. The pull request template carries this as a checkbox; `Signed-off-by` (`git commit -s`) is welcome but not required. Be kind: see the [Code of Conduct](CODE_OF_CONDUCT.md).

## Good first issues

Small tasks for new or casual contributors carry the [`good first issue`](https://github.com/darthdemono/homelab-automation/labels/good%20first%20issue) label. They are scoped to one file or one command, say what the expected behaviour is, and name the test to extend.

## Reporting a bug

Open an issue with the bug form. Say which version (`homelab --version`), your operating system and Python version, the steps, and what you expected. Do not paste secrets, tokens, ledger files or private paths.

## Build and run

Python 3.13 or newer and [uv](https://docs.astral.sh/uv/) are required.

```sh
uv sync
uv run homelab --help
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run python scripts/gen_schemas.py --check
git config core.hooksPath .githooks
```

CI runs the same checks on Python 3.13 and 3.14, with a coverage floor of 85 percent.

## Pull requests

- Branch from `main`; `main` is protected and takes pull requests only, gated by the `CI result` check.
- A bug fix comes with a regression test that fails without the fix.
- A changed config option or model means regenerating `schemas/` (`uv run python scripts/gen_schemas.py`).
- A change a user can see gets a line in [CHANGELOG.md](CHANGELOG.md).
- Use [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the invariants: plan first, apply what was reviewed, undo anything, never delete.

## Writing a plugin

Run `homelab plugin-new my_plugin` for a private one in `custom/`, or read [`packages/homelab-plugin-api`](packages/homelab-plugin-api/README.md) for a published one. A plugin declares what it may touch in its manifest, and `homelab doctor` runs the conformance checks.

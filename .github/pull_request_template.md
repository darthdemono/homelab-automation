## What changed and why

## How you checked it

- [ ] `uv run pytest` and `uv run ruff check .` pass (or the part that applies, and why)
- [ ] `CHANGELOG.md` has a line for a change a user can see
- [ ] A bug fix has a regression test that fails without the fix
- [ ] A new or changed plugin option updates the checked-in schemas (`uv run python scripts/gen_schemas.py`)
- [ ] No secrets, library paths with personal data or ledger files in the diff
- [ ] I wrote this change or have the right to submit it under Apache-2.0 ([Developer Certificate of Origin](https://developercertificate.org/))

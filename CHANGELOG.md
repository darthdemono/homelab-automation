# Changelog

## Unreleased

- Engine with plan, apply and undo: every run is a ledger record, applied exactly as reviewed, and reversible. Nothing is ever deleted; removals are moves into a quarantine folder.
- Plugin contract (`homelab-plugin-api` 1.0.0): manifest with declared permissions, jobs that return plans, optional plugin-defined actions with undo.
- Plugin discovery from installed entry points (`homelab.plugins`) and a private `custom/` folder, with broken plugins isolated and reported.
- Three YAML config files (`homelab.yaml`, `plugins.yaml`, `jobs.yaml`) with checked-in JSON schemas.
- `homelab` commands: `plugins`, `doctor`, `plan`, `apply`, `undo`, `runs`, `run`, `schedule`, `plugin-new`, `schema`.
- Public trust files: security policy, governance, threat model, assurance case, support policy, Scorecard, CodeQL, OSV, gitleaks and zizmor workflows, a protect-main ruleset, signed releases with SBOM and provenance, weekly Atheris fuzzing.

# Security policy

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or disclose secrets in an issue. Use
[GitHub's private vulnerability-reporting form](https://github.com/darthdemono/homelab-automation/security/advisories/new)
for this repository, or contact the maintainer through the email address on the
[GitHub profile](https://github.com/darthdemono).

Include a minimal reproduction, the affected version, the impact and any proposed mitigation.

### Security contacts

The maintainer, Jubair Hasan ([@darthdemono](https://github.com/darthdemono)), is the only security contact.

### What to expect

- Reports are acknowledged within seven days.
- The maintainer confirms or rejects the report, then works with the reporter on a fix and a disclosure date. The reporter is credited unless they prefer not to be.
- Once a fix is released, the vulnerability is published as a [GitHub Security Advisory](https://github.com/darthdemono/homelab-automation/security/advisories) with a CVE where one applies. It names the affected versions, how to tell if you are affected, and the fixed version or the mitigation.
- Vulnerabilities in dependencies that do not affect this project are recorded with a reason in [`osv-scanner.toml`](osv-scanner.toml).

### What a good report contains

- The affected version and platform, and the component (engine, plugin API, ledger, schedule renderer, a bundled plugin).
- The impact: what an attacker gains, and what they need first.
- Steps to reproduce or a proof of concept, and the root cause if you found it.
- Whether you plan to publish, and when.

Please keep the report private until a fix ships or 90 days have passed, whichever comes first. There is no bug bounty and no PGP key: the private form is encrypted in transit and visible only to the maintainer.

### Safe harbour

Good-faith research that follows this policy is welcome. The maintainer will not start legal action over it, provided you stay within the scope below, test only systems you own or are authorised to test, do not read, change or destroy other people's data, and stop and report if you reach real secrets.

### Security incidents

Incidents without a direct exploit are handled the same way: a compromised dependency, a tampered release or workflow, a leaked token, or an attack on the repository. The maintainer confirms the incident, revokes and rotates what was exposed, ships a release, and publishes an advisory that says who is affected and what to do.

## Scope

The engine, the plugin API package, the run ledger, the schedule renderer, the bundled plugins and the release artefacts are in scope. The [threat model](THREAT_MODEL.md) lists what the project does and does not defend against. Private plugins that a user keeps in their own `custom/` folder are that user's code and out of scope.

## Supported versions

Only the latest release receives security fixes. See [SUPPORT_POLICY.md](SUPPORT_POLICY.md).

## Secrets and credentials used by the project

- The engine never stores a secret. A plugin option names an environment variable (`*_env`), and the value comes from the process environment, which the owner fills from a secret manager.
- The project's own CI needs no long-lived credentials: releases are signed keylessly from the workflow identity, and PyPI publishing, when enabled, uses trusted publishing.
- GitHub secret scanning with push protection is on, `.gitignore` excludes `state/`, local config and private plugins, and test data must not look like real credentials.

## Dependency vulnerability policy

- Dependencies come from PyPI through uv, with a committed `uv.lock`. Dependabot proposes updates weekly, after a seven day cooldown.
- Every pull request runs OSV (lockfile), gitleaks, zizmor and dependency review. A release cannot start from a red CI run.
- Threshold: a known vulnerability in a dependency that ships in a release is fixed by upgrading or replacing it before the next release. If it does not affect this project, it is recorded with the reason in `osv-scanner.toml`.

## Static analysis policy

- CodeQL (Python) and ruff with the security rules (`S`) run on every push and pull request.
- Threshold: any finding of medium severity or higher is fixed, or dismissed with a written reason, before the next release.

## Fuzzing

The three YAML config files are written by hand, so their parsing is fuzzed: [`fuzz/fuzz_config.py`](fuzz/fuzz_config.py) is an Atheris target run weekly by the Fuzz workflow, and its seeds and a Hypothesis property replay in the normal test suite.

## Release integrity

Releases are built by the Release workflow after CI passed in the same run. Every asset is signed with cosign (keyless), has an attested build provenance, and the release carries a CycloneDX SBOM and `SHA256SUMS`.

# Governance

homelab-automation is maintained by one person. This file says who has access to what, what each role is responsible for, and how that access is granted.

## Members with access to sensitive resources

| Who | Role | Sensitive resources |
| --- | --- | --- |
| Jubair Hasan ([@darthdemono](https://github.com/darthdemono)) | Maintainer | Repository admin, GitHub Actions configuration, release publishing, private vulnerability reports |

There are no other collaborators with write access. Releases are built and signed by the GitHub Actions workflow (keyless Sigstore signing from the workflow's identity), so no personal signing key is involved in a release.

## Roles and responsibilities

- **Maintainer.** Reviews and merges changes, cuts releases, triages issues, answers vulnerability reports within the time in [SECURITY.md](SECURITY.md), and decides who else gets access.
- **Contributor.** Anyone who sends a pull request or a patch. Follows [CONTRIBUTING.md](CONTRIBUTING.md). Has no special access.
- **Reporter.** Anyone who opens an issue or a private vulnerability report.

## Granting access

- New collaborators start with read or triage access. Write access is granted only after the maintainer has reviewed a series of that person's contributions and is satisfied with their care and judgement. Admin access is not delegated while the project has a single maintainer.
- Every account with write access or above must use two-factor authentication on GitHub.
- Access is reviewed at each release and removed when no longer needed.

## Legal

Contributions are made under the project licence, Apache-2.0, as described in section 5 of that licence and in GitHub's Terms of Service. By opening a pull request you confirm you have the right to contribute the work.

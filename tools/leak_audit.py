# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Checks that nothing local is about to be published.

Looks at every commit that is not on a remote yet (or the whole history with --all-history) for
1. paths that must stay local: private notes, private plugins, real config, state, env files, handoffs;
2. text that leaks the machine or the owner: the home folder of whoever runs this, the host name, secret-shaped values;
3. the owner's own patterns, one regular expression per line in Agents/leak-patterns.txt (gitignored, so the
   personal strings stay local too).
Matched values are never printed. Exit 0 clean, 1 findings.

  python tools/leak_audit.py                  commits not on any remote
  python tools/leak_audit.py --range A..B     an explicit range
  python tools/leak_audit.py --all-history    everything reachable from HEAD
  python tools/leak_audit.py --tree           only the files in HEAD
  git pre-push hook: see .githooks/pre-push
"""

from __future__ import annotations

import getpass
import re
import socket
import subprocess
import sys
from pathlib import Path

LOCAL_PATHS = [
    (r"^Agents/", "private instruction files"),
    (r"^custom/(?!\.gitkeep$)", "private plugins"),
    (r"^config/(?!.*\.example\.ya?ml$).+\.ya?ml$", "real config"),
    (r"^state/", "run ledger, journals and quarantine"),
    (r"(^|/)\.env($|\.(?!example$))", "environment file"),
    (r"^(Research|research|Handoff|handoff)/", "local notes"),
    (r"(^|/)(CLAUDE|CODEX|GEMINI)[^/]*\.md$", "per-tool instruction file"),
    (r"^\.(claude|codex|gemini|serena|agents)/", "tool state"),
    (r"^(reports|plans|cache|journals|data)/", "working data of a migrated tool"),
    (r"(^|/)handoff-[^/]*\.md$", "session handoff"),
    (r"\.journal\.jsonl$", "journal"),
    (r"\.bak(\.json)?$", "backup file"),
]

SECRET_SHAPES = [
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"\bghp_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bsk-[A-Za-z0-9]{20,}", "API key"),
    (r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.", "JSON web token"),
    (r"(?i)\b(api[_-]?key|token|password|secret)\b[\"' ]*[:=][\"' ]*[A-Za-z0-9_\-/+]{20,}", "credential assignment"),
]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)
    return result.stdout


def personal_patterns(repo: Path) -> list[re.Pattern]:
    path = repo / "Agents" / "leak-patterns.txt"
    if not path.is_file():
        return []
    lines = [ln.strip() for ln in path.read_text().splitlines()]
    return [re.compile(ln, re.IGNORECASE) for ln in lines if ln and not ln.startswith("#")]


def machine_patterns() -> list[tuple[re.Pattern, str]]:
    home = str(Path.home())
    user = re.escape(getpass.getuser())
    found = [(re.compile(re.escape(home)), "home folder of this machine")]
    found.append((re.compile(rf"/(home|Users)/{user}\b"), "home folder of this user"))
    host = socket.gethostname()
    if host and host not in ("localhost", ""):
        found.append((re.compile(rf"\b{re.escape(host)}\b"), "host name of this machine"))
    return found


def default_range(repo: Path) -> str:
    remotes = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").split()
    return "HEAD" if not remotes else "HEAD --not --remotes"


def audit(repo: Path, rev: str, tree_only: bool = False) -> list[str]:
    """Return one finding per problem: 'kind: where'. Never includes a matched value."""
    findings: list[str] = []
    local = [(re.compile(p), why) for p, why in LOCAL_PATHS]
    secrets = [(re.compile(p), why) for p, why in SECRET_SHAPES]
    machine = machine_patterns()
    mine = personal_patterns(repo)

    if tree_only:
        paths = git(repo, "ls-files").splitlines()
    else:
        paths = sorted(set(git(repo, "log", "--name-only", "--format=", *rev.split()).split("\n")) - {""})
    for path in paths:
        for pattern, why in local:
            if pattern.search(path):
                findings.append(f"path ({why}): {path}")

    def scan(path: str, line_no: int, text: str, commit: str = "") -> None:
        where = f"{path}:{line_no}" + (f" in {commit[:8]}" if commit else "")
        for pattern, why in secrets:
            if pattern.search(text):
                findings.append(f"secret ({why}): {where}")
        for pattern, why in machine:
            if pattern.search(text):
                findings.append(f"machine ({why}): {where}")
        for number, pattern in enumerate(mine, 1):
            if pattern.search(text):
                findings.append(f"personal pattern #{number}: {where}")

    if tree_only:
        for path in paths:
            file = repo / path
            if file.is_file():
                for number, text in enumerate(file.read_text(errors="replace").splitlines(), 1):
                    scan(path, number, text)
    else:
        commit = path = ""
        number = 0
        for line in git(repo, "log", "-p", "-U0", "--format=commit %H", *rev.split()).splitlines():
            if line.startswith("commit "):
                commit = line[7:]
            elif line.startswith("+++ b/"):
                path = line[6:]
            elif line.startswith("@@"):
                match = re.search(r"\+(\d+)", line)
                number = int(match.group(1)) - 1 if match else 0
            elif line.startswith("+") and not line.startswith("+++"):
                number += 1
                scan(path, number, line[1:], commit)
    return sorted(set(findings))


def check_push_refs(lines: list[str]) -> list[str]:
    """Refuse refs that must never be published, for the pre-push hook."""
    problems = []
    for line in lines:
        parts = line.split()
        if len(parts) == 4 and parts[0].startswith("refs/heads/history/"):
            problems.append(f"refusing to push {parts[0]}: history/* branches hold the old repos' files and stay local")
    return problems


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").strip() or ".")
    tree_only = "--tree" in argv
    if "--stdin-pre-push" in argv:
        refs = sys.stdin.read().splitlines()
        problems = check_push_refs(refs)
        rev = "HEAD --not --remotes"
        findings = problems + audit(repo, rev)
    elif "--range" in argv:
        findings = audit(repo, argv[argv.index("--range") + 1])
    elif "--all-history" in argv:
        findings = audit(repo, "HEAD")
    else:
        findings = audit(repo, default_range(repo), tree_only)
    for finding in findings:
        print(finding)
    if findings:
        print(
            f"\n{len(findings)} finding(s): nothing was pushed or changed. Fix the commits, then run again.",
            file=sys.stderr,
        )
        return 1
    print("leak audit: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

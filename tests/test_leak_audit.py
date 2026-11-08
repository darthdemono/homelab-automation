# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load():
    spec = importlib.util.spec_from_file_location("leak_audit", ROOT / "tools" / "leak_audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = load()


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    return tmp_path


def commit(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        git(repo, "add", "-f", "--", name)
    git(repo, "commit", "-q", "-m", "x")


def test_a_clean_repo_passes(repo):
    commit(repo, {"README.md": "hello\n", "src/a.py": "x = 1\n", "AGENTS.md": "generic\n", ".env.example": "# A=\n"})
    assert audit.audit(repo, "HEAD") == []


@pytest.mark.parametrize(
    "path",
    [
        "Agents/Index.md",
        "custom/plugin/__init__.py",
        "config/homelab.yaml",
        "state/runs/a.json",
        ".env",
        "Research/n.md",
        "Handoff/h.md",
        "CLAUDE.md",
        "handoff-3.md",
        "reports/a.jsonl",
        "x/notes.bak",
    ],
)
def test_local_paths_are_flagged_even_if_removed_later(repo, path):
    commit(repo, {path: "x\n"})
    git(repo, "rm", "-q", "-f", "--", path)
    git(repo, "commit", "-q", "-m", "remove")
    assert any(f.startswith("path") and path in f for f in audit.audit(repo, "HEAD"))


def test_example_files_and_gitkeep_are_allowed(repo):
    commit(repo, {"config/homelab.example.yaml": "a: 1\n", "custom/.gitkeep": "", ".env.example": "# A=\n"})
    assert audit.audit(repo, "HEAD") == []


def test_the_home_folder_of_this_machine_is_flagged_without_printing_it(repo):
    home = str(Path.home())
    commit(repo, {"docs.md": f"see {home}/Code/thing\n"})
    findings = audit.audit(repo, "HEAD")
    assert any("home folder" in f for f in findings)
    assert all(home not in f for f in findings)


def test_secret_shapes_are_flagged_and_never_echoed(repo):
    value = "abcdefghijklmnopqrstuvwxyz0123456789"
    commit(repo, {"a.py": f'api_key = "{value}"\n'})
    findings = audit.audit(repo, "HEAD")
    assert any("credential assignment" in f for f in findings) and all(value not in f for f in findings)


def test_a_placeholder_is_not_a_secret(repo):
    commit(repo, {"a.py": 'api_key = "{{secret:NAME}}"\ntoken = None\n'})
    assert audit.audit(repo, "HEAD") == []


def test_personal_patterns_from_the_private_file_are_honoured(repo):
    (repo / "Agents").mkdir()
    (repo / "Agents" / "leak-patterns.txt").write_text("# mine\nmy-private-plugin\n")
    commit(repo, {"docs.md": "uses My-Private-Plugin here\n"})
    findings = audit.audit(repo, "HEAD")
    assert findings == ["personal pattern #1: docs.md:1 in " + findings[0].rsplit(" in ", 1)[1]]
    assert all("my-private-plugin" not in f.lower() for f in findings)


def test_only_the_new_range_is_scanned(repo):
    commit(repo, {"a.md": f"{Path.home()}/x\n"})
    old = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    commit(repo, {"b.md": "fine\n"})
    assert audit.audit(repo, f"{old}..HEAD") == []
    assert audit.audit(repo, "HEAD") != []


def test_tree_mode_scans_current_files(repo):
    commit(repo, {"ok.md": "fine\n"})
    (repo / "ok.md").write_text(f"{Path.home()}\n")
    git(repo, "add", "ok.md")
    git(repo, "commit", "-q", "-m", "y")
    assert audit.audit(repo, "HEAD", tree_only=True) != []


def test_history_branches_are_refused_by_the_push_check():
    line = "refs/heads/history/musicfix 1234 refs/heads/history/musicfix 0000"
    assert audit.check_push_refs([line]) and not audit.check_push_refs(["refs/heads/main 1 refs/heads/main 2"])


def test_main_returns_one_on_findings_and_zero_when_clean(repo, monkeypatch, capsys):
    commit(repo, {"a.md": "fine\n"})
    monkeypatch.chdir(repo)
    assert audit.main(["--all-history"]) == 0
    commit(repo, {".env": "A=1\n"})
    assert audit.main(["--all-history"]) == 1
    assert "nothing was pushed" in capsys.readouterr().err

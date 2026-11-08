# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Bring an old repository's full history and notes next to this project, locally only."""

import subprocess
from pathlib import Path

from homelab_plugin_api import Action, Nudge, Plan, script
from pydantic import BaseModel, ConfigDict


class Options(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str
    name: str


def git_lines(repo: Path, *args: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)  # noqa: S603, S607
    return out.stdout.splitlines()


def notes_of(repo: Path) -> list[Path]:
    found = sorted(repo.glob("*.md")) + sorted((repo / "docs").glob("*.md"))
    return [p for p in found if p.is_file()]


def heading(path: Path) -> str:
    for line in path.read_text(errors="replace").splitlines():
        if line.strip():
            return line.strip().lstrip("# ")[:90]
    return ""


@script(
    summary="Fetch an old repo's complete history into local-only history/full/NAME branches and copy its notes",
    tags=["migration", "history", "git"],
    status="stable",
    writes=["{agents}"],
    exec=["git", "mkdir", "cp"],
    options=Options,
    example={"repo": ".", "name": "old"},
    nudge=Nudge(
        when="a tool is being merged into this project and sessions here must know its whole history",
        before="the branches are local only (the pre-push hook refuses history/*); notes land in the agents folder",
        after="read history/NAME/INDEX.md in the agents folder; `git log history/full/NAME/<branch>` has the commits",
        avoid="never push the history/full branches: they hold the old repo's personal files",
    ),
)
def import_repo_history(ctx, options: Options) -> Plan:
    repo = Path(options.repo).expanduser().resolve()
    project = ctx.path("project")
    target = ctx.path("agents") / "history" / options.name
    if not (repo / ".git").exists():
        return Plan(notes=[f"{repo} is not a git repository"])
    branches = git_lines(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    refspec = f"+refs/heads/*:refs/heads/history/full/{options.name}/*"
    actions = [
        Action(
            kind="exec",
            describe=f"fetch every branch of {repo.name} into history/full/{options.name}/*",
            args={"argv": ["git", "-C", str(project), "fetch", "--quiet", str(repo), refspec]},
        )
    ]
    notes = notes_of(repo)
    actions.append(Action(kind="exec", describe=f"create {target}", args={"argv": ["mkdir", "-p", str(target)]}))
    for path in notes:
        flat = str(path.relative_to(repo)).replace("/", "__")  # docs/a.md becomes docs__a.md
        actions.append(
            Action(
                kind="exec",
                describe=f"copy {path.relative_to(repo)}",
                args={"argv": ["cp", "-n", str(path), str(target / flat)]},
            )
        )
    log = git_lines(repo, "log", "--format=%h %ad %s", "--date=short", "-n", "40")
    index = [
        f"# History of {options.name}",
        "",
        f"Source: {repo}",
        f"Branches: {', '.join(branches)} (fetched as history/full/{options.name}/<branch>, local only)",
        f"Commits on the current branch: {len(git_lines(repo, 'rev-list', 'HEAD'))}",
        "",
        "## Notes copied here",
        "",
        *[f"- `{str(p.relative_to(repo)).replace(chr(47), chr(95) * 2)}`: {heading(p)}" for p in notes],
        "",
        "## Latest commits",
        "",
        *[f"- {line}" for line in log],
        "",
    ]
    actions.append(
        Action(
            kind="write",
            describe=f"write {target / 'INDEX.md'}",
            args={"path": str(target / "INDEX.md"), "text": "\n".join(index)},
        )
    )
    return Plan(actions=actions, notes=[f"{len(notes)} notes, {len(branches)} branches, local only"])

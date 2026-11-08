# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The homelab command line. It is the agent surface: one JSON contract, typed exit codes,
nothing that needs a terminal unless a human is typing. See docs/CLI.md."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from pydantic import ValidationError

from . import __version__, report, scaffold, secrets
from .config import Core, Jobs, Plugins, config_dir, load
from .conformance import check
from .errors import Invalid, NotFound, RunError
from .registry import discover
from .runner import Runner

CONTRACT = 1

EXIT_CODES = {
    "0": "ok",
    "1": "error: unclassified failure, for example an action that failed during apply",
    "2": "usage: bad arguments",
    "3": "not_found: no such plugin, job or run; error.details.suggestions may list near matches",
    "4": "ambiguous: matched several things; error.details.candidates lists them, nothing is guessed",
    "5": "denied: a plan was refused because it leaves the plugin's declared permissions",
    "6": "conflict: the files or the run changed since the plan, or the run is in the wrong state",
    "7": "unavailable: plugin not switched on in plugins.yaml, did not load, or unv is missing",
    "8": "needs_confirmation: apply or undo without --yes and without a terminal",
    "10": "invalid: config, options or a .env file failed validation",
}

ENVELOPE = {
    "note": "Pass --json to any command. stdout is then exactly one JSON document.",
    "success": {"ok": True, "command": "<command>", "data": "<command-specific>"},
    "failure": {"ok": False, "error": {"code": "<code>", "message": "<text>", "details": "<optional object>"}},
    "dry_run": 'Commands that change things accept --dry-run; the envelope then carries "dry_run": true and nothing changes.',
}

CONVENTIONS = {
    "workflow": "describe once, then: plan, read the plan, ask the owner, apply --yes, verify with show. Never apply a plan you did not show.",
    "confirmation": "apply and undo need --yes when stdin is not a terminal.",
    "lookup": "A run is an id, a unique prefix of one, or `latest`. A job is plugin.job, or a bare name that exists in one enabled plugin. Ambiguity is refused (exit 4), never guessed.",
    "bounded_output": "--limit caps long lists; a capped list says how many were left out.",
    "secrets": "Plugins read secrets from environment variables. Use UnENVerse (unv), which the engine runs under automatically; a .env file with mode 600 is the fallback. Values are never printed or stored.",
}

# One table builds the parser, `describe` and the shell completions, so none can drift from the others.
LIMIT = (("--limit",), {"type": int, "default": 20, "help": "show at most N entries, 0 for all"})
YES = (("--yes",), {"action": "store_true", "help": "do not ask for confirmation"})
DRY = (("--dry-run",), {"action": "store_true", "help": "check everything, change nothing"})

COMMANDS: dict[str, tuple[str, list]] = {
    "describe": (
        "print this contract as JSON; add a plugin or plugin.job for its options",
        [(("target",), {"nargs": "?", "help": "plugin or plugin.job"})],
    ),
    "status": ("where the CLI is pointed and what it holds", []),
    "plugins": ("list plugins, their state and load errors", []),
    "policy": ("show the autonomy levels: the default, the rules and the level of every plugin and script", []),
    "pause": ("switch every apply off (plans and undo still work)", []),
    "resume": ("switch applies back on", []),
    "report": (
        "snapshot the stack and list what changed since the last snapshot (typed kinds)",
        [
            (("--no-save",), {"action": "store_true", "help": "compare, but do not write a snapshot"}),
            (("--keep",), {"type": int, "default": 30, "help": "keep the latest N snapshots, 0 to keep all"}),
        ],
    ),
    "attention": (
        "what needs attention now: failed or stale runs, a broken audit chain, and the latest changes, ranked",
        [(("--limit",), {"type": int, "default": 10, "help": "show at most N items, 0 for all"})],
    ),
    "audit": (
        "show the latest audit events, or check the hash chain",
        [
            (("--verify",), {"action": "store_true", "help": "recompute the chain; exit 1 and say where it breaks"}),
            (("--limit",), {"type": int, "default": 10, "help": "show at most N events, 0 for all"}),
        ],
    ),
    "doctor": ("run the conformance checks and the secret check on every enabled plugin", []),
    "secrets": ("where each declared secret would come from (names only, never values)", []),
    "plan": (
        "plan one job or script, change nothing",
        [
            (("target",), {"help": "plugin.job, a script name, or a unique job name"}),
            (("--set",), {"action": "append", "default": [], "metavar": "KEY=VALUE", "help": "override an option"}),
            LIMIT,
        ],
    ),
    "scripts": (
        "find or list scripts: words match name, summary, tags and the `when` hint",
        [
            (("words",), {"nargs": "*", "help": "what you want to do"}),
            (("--tag",), {"help": "only scripts with this tag"}),
            (("--status",), {"choices": ["one-off", "stable", "retired"], "help": "only scripts with this status"}),
            (("--limit",), {"type": int, "default": 5, "help": "show at most N entries, 0 for all"}),
        ],
    ),
    "apply": (
        "apply a reviewed run",
        [
            (("target",), {"help": "run id, unique prefix or latest"}),
            YES,
            DRY,
            (
                ("--detach",),
                {"action": "store_true", "help": "run in the background; follow it with `show`, stop it with `cancel`"},
            ),
        ],
    ),
    "cancel": (
        "stop a running (detached) run; what it already did can be undone",
        [(("target",), {"help": "run id, unique prefix or latest"})],
    ),
    "undo": ("undo an applied run", [(("target",), {"help": "run id, unique prefix or latest"}), YES, DRY]),
    "show": ("show one run", [(("target",), {"help": "run id, unique prefix or latest"}), LIMIT]),
    "runs": ("list the ledger, newest last", [LIMIT]),
    "run": (
        "plan every step of a pipeline from jobs.yaml",
        [
            (("pipeline",), {}),
            (("--apply",), {"action": "store_true", "help": "apply each plan right after it is made"}),
            YES,
            DRY,
            LIMIT,
        ],
    ),
    "schedule": (
        "print or write systemd user units",
        [(("--write",), {"metavar": "DIR", "help": "write the units here instead of printing"})],
    ),
    "plugin-new": ("scaffold a private plugin in custom/", [(("name",), {})]),
    "script-new": (
        "scaffold a script with its nudge in custom/scripts (private) or scripts (public)",
        [(("name",), {}), (("--public",), {"action": "store_true", "help": "write to the tracked scripts/ folder"})],
    ),
    "schema": ("print the JSON schema of the three config files", []),
    "completions": ("print a shell completion script", [(("shell",), {"choices": ["bash", "zsh", "fish"]})]),
}

GLOBAL = [
    (("--json",), {"action": "store_true", "default": argparse.SUPPRESS, "help": "machine-readable output"}),
    (("--pretty",), {"action": "store_true", "default": argparse.SUPPRESS, "help": "indent the JSON"}),
    (("--config",), {"default": argparse.SUPPRESS, "help": "config folder (default: $HOMELAB_CONFIG or ./config)"}),
]


@dataclass
class Result:
    command: str
    data: dict = field(default_factory=dict)
    text: list[str] = field(default_factory=list)
    raw: bool = False  # describe, schema and completions print their document as is
    dry_run: bool = False
    exit: int = 0  # 1 when a check ran and found problems (doctor)


class NeedsConfirmation(RunError):
    code, exit_code = "needs_confirmation", 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homelab", description="Plan first, apply what was reviewed, undo anything.")
    parser.add_argument("--version", action="version", version=f"homelab {__version__}")
    parser.set_defaults(json=False, pretty=False, config=None)
    for names, kwargs in GLOBAL:
        parser.add_argument(*names, **{**kwargs, "default": None if "action" not in kwargs else False})
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name, (help_text, args) in COMMANDS.items():
        p = sub.add_parser(name, help=help_text, description=help_text)
        for names, kwargs in [*GLOBAL, *args]:
            p.add_argument(*names, **kwargs)
    return parser


def _arg_doc(names: tuple, kwargs: dict) -> dict:
    doc = {"name": names[0], "required": not names[0].startswith("-") and kwargs.get("nargs") != "?"}
    for key in ("help", "choices", "metavar"):
        if key in kwargs:
            doc[key] = kwargs[key]
    if "default" in kwargs and kwargs["default"] is not argparse.SUPPRESS:
        doc["default"] = kwargs["default"]
    return doc


def contract() -> dict:
    return {
        "tool": "homelab",
        "version": __version__,
        "contract": CONTRACT,
        "summary": "Plugin engine for homelab automation: plan first, apply what was reviewed, undo anything, never delete.",
        "global_args": [_arg_doc(n, k) for n, k in GLOBAL],
        "commands": {name: {"help": h, "args": [_arg_doc(n, k) for n, k in a]} for name, (h, a) in COMMANDS.items()},
        "envelope": ENVELOPE,
        "exit_codes": EXIT_CODES,
        "conventions": CONVENTIONS,
    }


def completions(shell: str) -> str:
    commands = list(COMMANDS)
    flags = {
        name: [n for names, _ in [*GLOBAL, *args] for n in names if n.startswith("-")]
        for name, (_, args) in COMMANDS.items()
    }
    if shell == "bash":
        cases = "\n".join(f'    {name}) opts="{" ".join(flags[name])}" ;;' for name in commands)
        return (
            "_homelab() {\n  local cur=${COMP_WORDS[COMP_CWORD]} cmd=${COMP_WORDS[1]} opts=\n"
            f'  if [ "$COMP_CWORD" -eq 1 ]; then COMPREPLY=($(compgen -W "{" ".join(commands)}" -- "$cur")); return; fi\n'
            f'  case "$cmd" in\n{cases}\n  esac\n  COMPREPLY=($(compgen -W "$opts" -- "$cur"))\n}}\ncomplete -F _homelab homelab\n'
        )
    if shell == "zsh":
        items = " ".join(f'"{name}:{COMMANDS[name][0]}"' for name in commands)
        return f"#compdef homelab\n_homelab() {{\n  local -a cmds\n  cmds=({items})\n  _describe 'command' cmds\n}}\n_homelab \"$@\"\n"
    lines = [f'complete -c homelab -n "__fish_use_subcommand" -a {name} -d "{COMMANDS[name][0]}"' for name in commands]
    for name in commands:
        lines += [
            f'complete -c homelab -n "__fish_seen_subcommand_from {name}" -l {f.lstrip("-")}'
            for f in flags[name]
            if f.startswith("--")
        ]
    return "\n".join(lines) + "\n"


class App:
    """Config, plugins and the runner load on first use, so `describe`, `schema` and
    `completions` work with no config folder at all."""

    def __init__(self, args, argv: list[str]):
        self.args = args
        self.argv = argv

    @cached_property
    def directory(self) -> Path:
        return config_dir(self.args.config)

    @cached_property
    def config(self):
        return load(self.directory)

    @cached_property
    def found(self):
        return discover(self.config.custom_dir, self.config.script_dirs)

    @cached_property
    def runner(self) -> Runner:
        return Runner(self.config, self.found)

    def confirm(self, prompt: str) -> None:
        if getattr(self.args, "yes", False) or getattr(self.args, "dry_run", False):
            return
        if sys.stdin.isatty():
            if input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes"):
                return
            raise NeedsConfirmation("not confirmed")
        raise NeedsConfirmation("pass --yes to do this without a terminal")

    def load_secrets(self, plugins: list[str]) -> None:
        names = list(secrets.declared(self.config, self.found, plugins))
        if names:
            secrets.load(self.config, names, self.argv)

    def plugin_state(self, name: str) -> str:
        block = self.config.plugins.get(name)
        if block is None:
            return "on" if self.runner.is_script(name) else "off"
        return "disabled" if not block.enabled else "on"

    def nudge_data(self, ref: str, keys: tuple[str, ...]) -> dict:
        plugin = self.found.plugins.get(ref.partition(".")[0])
        nudge = plugin.nudge if plugin else None
        return {k: getattr(nudge, k) for k in keys if nudge and getattr(nudge, k)}

    def view(self, record: dict, limit: int) -> dict:
        shown = dict(record)
        actions = record["plan"]["actions"]
        if limit and len(actions) > limit:
            shown["plan"] = {**record["plan"], "actions": actions[:limit], "left_out": len(actions) - limit}
        return shown

    def lines(self, record: dict) -> list[str]:
        out = [f"run {record['id']}  {record['ref']}  [{record['status']}]"]
        out += [f"  {a['kind']:<10} {a['describe']}" for a in record["plan"]["actions"]]
        out += [f"  ... {record['plan']['left_out']} more actions" for _ in [0] if record["plan"].get("left_out")]
        out += [f"  note: {n}" for n in record["plan"]["notes"]]
        if not record["plan"]["actions"]:
            out.append("  (no actions)")
        return out

    # ---- commands -------------------------------------------------------------------------
    def cmd_describe(self) -> Result:
        target = self.args.target
        if not target:
            return Result("describe", contract(), raw=True)
        name, _, job = target.partition(".")
        plugin = self.found.plugins.get(name)
        if plugin is None:
            raise NotFound(f"no plugin {name!r}", {"candidates": sorted(self.found.plugins)})
        if job and job not in plugin.jobs:
            raise NotFound(f"plugin {name!r} has no job {job!r}", {"candidates": sorted(plugin.jobs)})
        doc = {
            "plugin": name,
            "state": self.plugin_state(name),
            "manifest": plugin.manifest.model_dump(),
            "options_schema": plugin.config_model.model_json_schema(),
            "example": plugin.example,
            "jobs": {j: plugin.jobs[j].summary for j in ([job] if job else plugin.jobs)},
        }
        if plugin.manifest.category == "script":
            doc |= {
                "tags": plugin.tags,
                "status": plugin.status,
                "nudge": self.nudge_data(name, ("when", "before", "after", "avoid")),
            }
        return Result("describe", doc, raw=True)

    def cmd_status(self) -> Result:
        states = {"on": 0, "off": 0, "disabled": 0}
        for name in self.found.plugins:
            states[self.plugin_state(name)] += 1
        counts: dict[str, int] = {}
        for record in self.runner.runs():
            counts[record["status"]] = counts.get(record["status"], 0) + 1
        data = {
            "version": __version__,
            "config_dir": str(self.directory),
            "state_dir": str(self.config.state_dir),
            "custom_dir": str(self.config.custom_dir),
            "plugins": {**states, "broken": len(self.found.errors)},
            "runs": counts,
            "pipelines": len(self.config.pipelines),
            "secrets_provider": self.config.core.secrets.provider,
            "paused": self.runner.paused,
            "policy_default": self.config.core.policy.default,
            "unv_installed": secrets.shutil.which("unv") is not None,
        }
        text = [
            f"homelab {__version__}  config {self.directory}",
            f"plugins {states}  broken {len(self.found.errors)}",
            f"runs {counts or 'none'}",
            f"secrets: {data['secrets_provider']} (unv {'found' if data['unv_installed'] else 'not found'})",
        ]
        return Result("status", data, text)

    def cmd_plugins(self) -> Result:
        rows = [
            {
                "name": n,
                "state": self.plugin_state(n),
                "origin": self.found.origin[n],
                "summary": p.manifest.summary,
                "jobs": sorted(p.jobs),
            }
            for n, p in sorted(self.found.plugins.items())
            if p.manifest.category != "script"
        ]
        missing = [n for n in self.config.plugins if n not in self.found.plugins and n not in self.found.errors]
        text = [f"{r['name']:<18} {r['state']:<9} {r['origin']:<10} {r['summary']}" for r in rows]
        text += [f"{k:<18} BROKEN    {e}" for k, e in self.found.errors.items()]
        text += [f"{n:<18} MISSING   block in plugins.yaml but no plugin found" for n in missing]
        return Result("plugins", {"plugins": rows, "broken": self.found.errors, "missing": missing}, text)

    def cmd_policy(self) -> Result:
        pol = self.config.core.policy
        rows = []
        for name, plugin in sorted(self.found.plugins.items()):
            level, rule = self.config.level_for(f"{name}.{next(iter(plugin.jobs))}")
            rows.append({"name": name, "kind": plugin.manifest.category, "level": level, "rule": rule})
        data = {
            "default": pol.default,
            "rules": [r.model_dump() for r in pol.rules],
            "paused": self.runner.paused,
            "targets": rows,
        }
        text = [f"default {pol.default}" + ("  PAUSED" if self.runner.paused else "")]
        text += [f"rule {r.match} -> {r.level}" for r in pol.rules]
        text += [f"{r['name']:<24} {r['level']:<5} {r['rule']}" for r in rows if r["rule"] != "default"]
        text.append(
            "levels: deny (not even a plan), plan (plan only), ask (apply needs --yes), auto (scheduled pipelines may apply)"
        )
        return Result("policy", data, text)

    def cmd_pause(self) -> Result:
        self.runner.set_paused(True)
        return Result(
            "pause", {"paused": True}, ["paused: no apply will run until `homelab resume`; plans and undo still work"]
        )

    def cmd_resume(self) -> Result:
        self.runner.set_paused(False)
        return Result("resume", {"paused": False}, ["resumed"])

    def cmd_report(self) -> Result:
        snaps = report.saved(self.config)
        previous = json.loads(snaps[-1].read_text()) if snaps else None
        now = report.take(self.config)
        changes = report.diff(previous, now) if previous else []
        if not self.args.no_save:
            report.save(self.config, now, self.args.keep)
        counts: dict[str, int] = {}
        for ch in changes:
            counts[ch["kind"]] = counts.get(ch["kind"], 0) + 1
        problems = report.trouble(now)
        data = {
            "compared_with": previous["ts"] if previous else None,
            "saved": not self.args.no_save,
            "changes": changes,
            "counts": counts,
            "not_running": problems,
        }
        if previous is None:
            text = [
                "first snapshot: a baseline was saved" if not self.args.no_save else "no snapshot to compare with yet"
            ]
        else:
            text = [
                f"compared with {previous['ts']}: "
                + (", ".join(f"{n} {k}" for k, n in sorted(counts.items())) if changes else "no changes")
            ]
            text += [f"  {ch['kind']:<9} {ch['subject']:<24} {ch['detail']}" for ch in changes]
        text += [f"  not running: {p}" for p in problems]
        return Result("report", data, text)

    def cmd_attention(self) -> Result:
        items = []
        for rec in self.runner.runs():
            if rec["status"] == "failed":
                items.append(
                    {
                        "severity": "high",
                        "what": f"run {rec['id']} {rec['ref']} failed",
                        "next": f"homelab undo {rec['id']} --yes",
                    }
                )
        verify = self.runner.verify_audit()
        if not verify["ok"]:
            items.append(
                {
                    "severity": "high",
                    "what": f"audit chain broken at line {verify['broken_at']}: {verify['why']}",
                    "next": "homelab audit --verify",
                }
            )
        snaps = report.saved(self.config)
        if len(snaps) >= 2:
            for ch in report.diff(json.loads(snaps[-2].read_text()), json.loads(snaps[-1].read_text())):
                high = ch["kind"] in ("gone", "restarts") or (
                    ch["kind"] == "state" and not ch["detail"].endswith("running")
                )
                items.append(
                    {
                        "severity": "high"
                        if high
                        else "medium"
                        if ch["kind"] in ("replaced", "disk", "state")
                        else "low",
                        "what": f"{ch['kind']} {ch['subject']}: {ch['detail']}",
                        "next": "homelab report",
                    }
                )
        if snaps:
            items += [
                {"severity": "high", "what": p, "next": "homelab scripts docker"}
                for p in report.trouble(json.loads(snaps[-1].read_text()))
            ]
        if self.runner.paused:
            items.append({"severity": "medium", "what": "applies are paused", "next": "homelab resume"})
        for rec in self.runner.runs():
            if rec["status"] == "planned":
                items.append(
                    {
                        "severity": "low",
                        "what": f"planned run {rec['id']} {rec['ref']} was never applied",
                        "next": f"homelab show {rec['id']}",
                    }
                )
        order = {"high": 0, "medium": 1, "low": 2}
        items.sort(key=lambda i: order[i["severity"]])
        shown = items[: self.args.limit] if self.args.limit else items
        text = [f"{i['severity']:<6} {i['what']}  -> {i['next']}" for i in shown] or ["nothing needs attention"]
        if len(items) > len(shown):
            text.append(f"... {len(items) - len(shown)} more")
        return Result("attention", {"items": shown, "left_out": len(items) - len(shown)}, text)

    def cmd_audit(self) -> Result:
        if self.args.verify:
            result = self.runner.verify_audit()
            text = (
                [f"audit chain ok: {result['events']} events ({result['legacy']} from before chaining)"]
                if result["ok"]
                else [f"audit chain BROKEN at line {result['broken_at']}: {result['why']}"]
            )
            return Result("audit", result, text, exit=0 if result["ok"] else 1)
        events = self.runner.audit_events()
        shown = events[-self.args.limit :] if self.args.limit else events
        text = [f"{e['ts'][:19]}  {e['event']:<7} {e.get('ref', '')} {e.get('run', '')[:15]}" for e in shown]
        return Result("audit", {"events": shown, "left_out": len(events) - len(shown)}, text or ["no events yet"])

    def cmd_secrets(self) -> Result:
        wanted = secrets.declared(self.config, self.found)
        where = secrets.sources(self.config, list(wanted))
        rows = [{"name": n, "plugins": wanted[n], "source": where[n]} for n in wanted]
        text = [f"{r['name']:<28} {r['source']:<8} {', '.join(r['plugins'])}" for r in rows] or [
            "no enabled plugin declares a secret"
        ]
        if any(r["source"] == "dotenv" for r in rows):
            text.append("note: a .env file is the fallback; UnENVerse (unv) is recommended")
        return Result("secrets", {"provider": self.config.core.secrets.provider, "secrets": rows}, text)

    def cmd_doctor(self) -> Result:
        results, bad = [], 0
        wanted = secrets.declared(self.config, self.found)
        where = secrets.sources(self.config, list(wanted)) if wanted else {}
        names = [n for n, b in self.config.plugins.items() if b.enabled]
        names += [n for n in self.found.plugins if self.runner.is_script(n) and n not in self.config.plugins]
        names += [k for k in self.found.errors if k.startswith("script:")]
        for name in names:
            if name in self.found.errors:
                problems = [self.found.errors[name]]
            elif name in self.found.plugins:
                problems = check(name, self.found.plugins[name], self.runner.context(name))
                problems += [
                    f"secret {s} is missing"
                    for s in self.found.plugins[name].manifest.secrets
                    if where.get(s) == "missing"
                ]
            else:
                problems = ["not found"]
            results.append({"plugin": name, "ok": not problems, "problems": problems})
            bad += bool(problems)
        text = []
        for r in results:
            text.append(f"{r['plugin']}: {'ok' if r['ok'] else 'FAILED'}")
            text += [f"  {p}" for p in r["problems"]]
        return Result(
            "doctor", {"ok": not bad, "plugins": results}, text or ["no enabled plugins"], exit=1 if bad else 0
        )

    def cmd_plan(self) -> Result:
        ref = self.runner.resolve_job(self.args.target)
        self.load_secrets([ref.partition(".")[0]])
        record = self.runner.make_plan(ref, parse_sets(self.args.set))
        shown = self.view(record, self.args.limit)
        data = dict(shown)
        text = self.lines(shown)
        hints = self.nudge_data(ref, ("before", "avoid"))
        if hints:
            data["nudge"] = hints
            text += [f"  {k}: {v}" for k, v in hints.items()]
        if record["plan"]["actions"]:
            data["ask_owner"] = True
            data["next"] = [f"homelab apply {record['id']} --yes"]
            text.append(f"next: show this plan to the owner, then homelab apply {record['id']} --yes")
        return Result("plan", data, text)

    def cmd_show(self) -> Result:
        found = self.runner.get(self.args.target)
        self.runner.alive(found)
        record = self.view(found, self.args.limit)
        return Result("show", record, self.lines(record))

    def cmd_runs(self) -> Result:
        records = self.runner.runs()
        shown = records[-self.args.limit :] if self.args.limit else records
        rows = [
            {"id": r["id"], "ref": r["ref"], "status": r["status"], "actions": len(r["plan"]["actions"])} for r in shown
        ]
        text = [f"{r['id']}  {r['ref']:<28} {r['status']}" for r in rows]
        return Result("runs", {"runs": rows, "left_out": len(records) - len(rows)}, text or ["no runs yet"])

    def cmd_apply(self) -> Result:
        record = self.runner.get(self.args.target)
        if self.args.dry_run:
            self.runner.check_apply(record["id"])
            return Result(
                "apply", self.view(record, 20), ["would apply:", *self.lines(self.view(record, 20))], dry_run=True
            )
        self.confirm(f"Apply {len(record['plan']['actions'])} actions of run {record['id']}?")
        if self.args.detach:
            return self.detach(record)
        self.load_secrets([record["ref"].partition(".")[0]])
        done = self.runner.apply(record["id"])
        data = self.view(done, 20)
        text = self.lines(data)
        hints = self.nudge_data(record["ref"], ("after",))
        if hints:
            data["nudge"] = hints
            text.append(f"after: {hints['after']}")
        return Result("apply", data, text)

    def detach(self, record: dict) -> Result:
        """Start the apply as its own process, so a long run outlives this command and can be followed."""
        import subprocess

        self.runner.check_apply(record["id"])  # refuse now, not silently in the background
        log = self.config.state_dir / "runs" / f"{record['id']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, "-m", "homelab.cli", "--config", str(self.directory), "apply", record["id"], "--yes"]
        with log.open("w") as handle:
            proc = subprocess.Popen(argv, stdout=handle, stderr=handle, start_new_session=True)  # noqa: S603
        self.runner.audit("detach", run=record["id"], ref=record["ref"], pid=proc.pid)
        data = {
            "run": record["id"],
            "detached": True,
            "pid": proc.pid,
            "log": str(log),
            "follow": f"homelab show {record['id']}",
            "stop": f"homelab cancel {record['id']}",
        }
        return Result(
            "apply",
            data,
            [
                f"started run {record['id']} in the background (pid {proc.pid}); follow: homelab show {record['id']}, stop: homelab cancel {record['id']}"
            ],
        )

    def cmd_cancel(self) -> Result:
        done = self.runner.cancel(self.args.target)
        return Result(
            "cancel",
            {"run": done["id"], "status": done["status"]},
            [f"run {done['id']} cancelled; `homelab undo {done['id']} --yes` restores what it already did"],
        )

    def cmd_undo(self) -> Result:
        record = self.runner.get(self.args.target)
        if self.args.dry_run:
            _, _, steps = self.runner.check_undo(record["id"])
            skipped = [s["describe"] for s in steps if s["kind"] == "irreversible"]
            data = {"run": record["id"], "steps": len(steps), "irreversible": skipped}
            return Result(
                "undo", data, [f"would undo {len(steps)} steps, {len(skipped)} cannot be undone"], dry_run=True
            )
        self.confirm(f"Undo run {record['id']}?")
        self.load_secrets([record["ref"].partition(".")[0]])
        done = self.runner.undo(record["id"])
        skipped = done["skipped_irreversible"]
        return Result(
            "undo",
            {"run": done["id"], "status": done["status"], "irreversible": skipped},
            [f"run {done['id']} undone", *[f"  could not undo: {s}" for s in skipped]],
        )

    def cmd_run(self) -> Result:
        pipe = self.config.pipelines.get(self.args.pipeline)
        if pipe is None:
            raise NotFound(
                f"no pipeline {self.args.pipeline!r} in jobs.yaml", {"candidates": sorted(self.config.pipelines)}
            )
        applying = pipe.apply or self.args.apply
        if applying and not self.args.dry_run:
            self.confirm(f"Plan and apply {len(pipe.steps)} steps of {self.args.pipeline}?")
        self.load_secrets(sorted({s.partition(".")[0] for s in pipe.steps}))
        records, text, left = [], [], []
        for step in pipe.steps:
            record = self.runner.make_plan(step)
            if applying and not self.args.dry_run:
                level, _ = self.config.level_for(step)
                if os.environ.get("HOMELAB_SCHEDULED") and level != "auto":
                    left.append(step)
                    record["plan"]["notes"].append(
                        f"left for review: policy level is {level}, only auto steps apply unattended"
                    )
                else:
                    record = self.runner.apply(record["id"])
            shown = self.view(record, self.args.limit)
            records.append(shown)
            text += self.lines(shown)
        return Result(
            "run",
            {
                "pipeline": self.args.pipeline,
                "applied": bool(applying and not self.args.dry_run),
                "left_for_review": left,
                "runs": records,
            },
            text,
            dry_run=self.args.dry_run,
        )

    def cmd_schedule(self) -> Result:
        from .schedule import render

        files = render(self.config, str(self.directory))
        if self.args.write:
            out = Path(self.args.write).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            for name, body in files.items():
                (out / name).write_text(body)
            return Result(
                "schedule",
                {"written": sorted(files), "dir": str(out)},
                [f"wrote {len(files)} unit files to {out}; enable with systemctl --user enable --now <name>.timer"],
            )
        return Result(
            "schedule", {"units": files}, [f"# {n}\n{b}" for n, b in files.items()] or ["no pipeline has a schedule"]
        )

    def cmd_plugin_new(self) -> Result:
        try:
            folder = scaffold.create(self.config.custom_dir, self.args.name)
        except ValueError as err:
            raise Invalid(str(err)) from err
        except FileExistsError as err:
            from .errors import Conflict

            raise Conflict(f"{err} already exists") from err
        return Result("plugin-new", {"created": str(folder)}, [f"created {folder}"])

    def cmd_scripts(self) -> Result:
        words = [w.lower() for w in self.args.words]
        rows = []
        for name, plugin in sorted(self.found.plugins.items()):
            if plugin.manifest.category != "script":
                continue
            if self.args.tag and self.args.tag not in plugin.tags:
                continue
            if self.args.status and plugin.status != self.args.status:
                continue
            when = plugin.nudge.when if plugin.nudge else ""
            haystack = " ".join([name.replace("_", " "), plugin.manifest.summary, " ".join(plugin.tags), when]).lower()
            score = sum(haystack.count(w) for w in words)
            if words and not score:
                continue
            rows.append(
                {
                    "name": name,
                    "status": plugin.status,
                    "tags": plugin.tags,
                    "summary": plugin.manifest.summary,
                    "when": when,
                    "score": score,
                }
            )
        rows.sort(key=lambda r: (-r["score"], r["name"]))
        shown = rows[: self.args.limit] if self.args.limit else rows
        text = [
            f"{r['name']:<24} {r['status']:<8} {r['summary']}" + (f"  (when: {r['when']})" if r["when"] else "")
            for r in shown
        ]
        if len(rows) > len(shown):
            text.append(f"... {len(rows) - len(shown)} more, narrow it with --tag or more words")
        errors = {k: v for k, v in self.found.errors.items() if k.startswith("script:")}
        text += [f"{k:<24} BROKEN   {v}" for k, v in errors.items()]
        data = {"scripts": shown, "left_out": len(rows) - len(shown), "broken": errors}
        return Result(
            "scripts", data, text or ["no script matches; try fewer words, or write one with `homelab script-new`"]
        )

    def cmd_script_new(self) -> Result:
        folder = self.config.script_dirs[0 if self.args.public else 1]
        try:
            path = scaffold.create_script(folder, self.args.name)
        except ValueError as err:
            raise Invalid(str(err)) from err
        except FileExistsError as err:
            from .errors import Conflict

            raise Conflict(f"{err} already exists") from err
        return Result(
            "script-new", {"created": str(path)}, [f"created {path}", "fill in the nudge, then `homelab doctor`"]
        )

    def cmd_schema(self) -> Result:
        doc = {
            "homelab.yaml": Core.model_json_schema(),
            "plugins.yaml": Plugins.model_json_schema(),
            "jobs.yaml": Jobs.model_json_schema(),
        }
        return Result("schema", doc, raw=True)

    def cmd_completions(self) -> Result:
        script = completions(self.args.shell)
        return Result("completions", {"shell": self.args.shell, "script": script}, [script.rstrip("\n")])


def parse_sets(items: list[str]) -> dict:
    """`--set key=value` pairs; a value is JSON when it parses as JSON (5, true, ["a"]), text otherwise."""
    out = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise Invalid(f"--set expects KEY=VALUE, got {item!r}")
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


def _dump(doc: dict, pretty: bool) -> str:
    return json.dumps(doc, indent=2 if pretty else None, separators=None if pretty else (",", ":"), default=str)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    as_json, pretty = bool(args.json), bool(args.pretty)
    try:
        result = getattr(App(args, argv), f"cmd_{args.command.replace('-', '_')}")()
    except RunError as err:
        if as_json:
            error = {"code": err.code, "message": str(err)}
            if err.details:
                error["details"] = err.details
            print(_dump({"ok": False, "error": error}, pretty))
        else:
            print(f"error: {err}", file=sys.stderr)
            if err.details and err.details.get("suggestions"):
                print(f"did you mean: {', '.join(err.details['suggestions'])}", file=sys.stderr)
            if err.details and err.details.get("candidates"):
                print(f"candidates: {', '.join(err.details['candidates'])}", file=sys.stderr)
        return err.exit_code
    except ValidationError as err:
        problems = err.errors(include_url=False, include_context=False, include_input=False)
        if as_json:
            print(
                _dump(
                    {
                        "ok": False,
                        "error": {
                            "code": "invalid",
                            "message": "a config file is invalid",
                            "details": {"errors": problems},
                        },
                    },
                    pretty,
                )
            )
        else:
            print(f"error: a config file is invalid: {problems}", file=sys.stderr)
        return 10
    if result.raw:
        print(_dump(result.data, pretty))
    elif as_json:
        envelope = {"ok": result.exit == 0, "command": result.command, "data": result.data}
        if result.dry_run:
            envelope["dry_run"] = True
        print(_dump(envelope, pretty))
    else:
        print("\n".join(result.text))
    return result.exit


if __name__ == "__main__":
    raise SystemExit(main())

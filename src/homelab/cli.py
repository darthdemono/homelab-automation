from __future__ import annotations

import argparse
import json
import sys

from . import __version__, scaffold
from .config import Core, Jobs, Plugins, config_dir, load
from .conformance import check
from .registry import discover
from .runner import RunError, Runner


def _show(record: dict) -> None:
    print(f"run {record['id']}  {record['ref']}  [{record['status']}]")
    for action in record["plan"]["actions"]:
        print(f"  {action['kind']:<10} {action['describe']}")
    for note in record["plan"]["notes"]:
        print(f"  note: {note}")
    if not record["plan"]["actions"]:
        print("  (no actions)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homelab")
    parser.add_argument("--config", help="config folder (default: $HOMELAB_CONFIG or ./config)")
    parser.add_argument("--version", action="version", version=f"homelab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("plugins", help="list plugins, their state and load errors")
    sub.add_parser("doctor", help="run the conformance checks on every enabled plugin")
    for name, text in (
        ("plan", "plan one job, change nothing"),
        ("apply", "apply a reviewed run"),
        ("undo", "undo an applied run"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("target", help="plugin.job for plan, a run id otherwise")
    sub.add_parser("runs", help="list the ledger")
    run = sub.add_parser("run", help="plan every step of a pipeline from jobs.yaml")
    run.add_argument("pipeline")
    run.add_argument("--apply", action="store_true", help="apply each plan right after it is made")
    sched = sub.add_parser("schedule", help="print or write systemd user units")
    sched.add_argument("--write", metavar="DIR", help="write the units here instead of printing")
    new = sub.add_parser("plugin-new", help="scaffold a private plugin in custom/")
    new.add_argument("name")
    sub.add_parser("schema", help="print the JSON schema of the three config files")
    args = parser.parse_args(argv)

    directory = config_dir(args.config)
    if args.command == "schema":
        print(
            json.dumps(
                {
                    "homelab.yaml": Core.model_json_schema(),
                    "plugins.yaml": Plugins.model_json_schema(),
                    "jobs.yaml": Jobs.model_json_schema(),
                },
                indent=2,
            )
        )
        return 0
    config = load(directory)
    if args.command == "plugin-new":
        print(f"created {scaffold.create(config.custom_dir, args.name)}")
        return 0
    found = discover(config.custom_dir)
    runner = Runner(config, found)
    try:
        if args.command == "plugins":
            for name, plugin in sorted(found.plugins.items()):
                block = config.plugins.get(name)
                state = "off" if block is None else ("disabled" if not block.enabled else "on")
                print(f"{name:<18} {state:<9} {found.origin[name]:<10} {plugin.manifest.summary}")
            for key, err in found.errors.items():
                print(f"{key:<18} BROKEN    {err}")
            for name in config.plugins:
                if name not in found.plugins and name not in found.errors:
                    print(f"{name:<18} MISSING   block in plugins.yaml but no plugin found")
        elif args.command == "doctor":
            bad = 0
            for name, block in config.plugins.items():
                if not block.enabled:
                    continue
                problems = (
                    [found.errors[name]]
                    if name in found.errors
                    else (
                        check(name, found.plugins[name], runner.context(name))
                        if name in found.plugins
                        else ["not found"]
                    )
                )
                print(f"{name}: {'ok' if not problems else 'FAILED'}")
                for problem in problems:
                    print(f"  {problem}")
                bad += bool(problems)
            return 1 if bad else 0
        elif args.command == "plan":
            _show(runner.make_plan(args.target))
        elif args.command == "apply":
            _show(runner.apply(args.target))
        elif args.command == "undo":
            record = runner.undo(args.target)
            print(f"run {record['id']} undone")
            for item in record["skipped_irreversible"]:
                print(f"  could not undo: {item}")
        elif args.command == "runs":
            for record in runner.runs():
                print(f"{record['id']}  {record['ref']:<28} {record['status']}")
        elif args.command == "run":
            pipe = config.pipelines.get(args.pipeline)
            if pipe is None:
                raise RunError(f"no pipeline {args.pipeline!r} in jobs.yaml")
            for step in pipe.steps:
                record = runner.make_plan(step)
                if pipe.apply or args.apply:
                    record = runner.apply(record["id"])
                _show(record)
        elif args.command == "schedule":
            from .schedule import render

            files = render(config, str(directory))
            if args.write:
                from pathlib import Path

                out = Path(args.write).expanduser()
                out.mkdir(parents=True, exist_ok=True)
                for name, text in files.items():
                    (out / name).write_text(text)
                print(f"wrote {len(files)} unit files to {out}; enable with systemctl --user enable --now <name>.timer")
            else:
                for name, text in files.items():
                    print(f"# {name}\n{text}")
    except RunError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

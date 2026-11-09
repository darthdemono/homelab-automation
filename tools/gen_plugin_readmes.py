#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Write the job table of every bundled plugin into its README, between markers. Run after adding a job."""

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BEGIN, END = "<!-- jobs:begin -->", "<!-- jobs:end -->"


def table(plugin) -> str:
    rows = ["| Job | What it does |", "| --- | --- |"]
    for name, job in sorted(plugin.jobs.items()):
        rows.append(f"| `{name}` | {job.summary.replace('|', '/')} |")
    return f"{BEGIN}\n{chr(10).join(rows)}\n{END}"


def main() -> None:
    for folder in sorted((ROOT / "plugins").iterdir()):
        modules = [p.name for p in (folder / "src").iterdir() if p.is_dir() and p.name.startswith("homelab_")]
        if not modules:
            continue
        plugin = importlib.import_module(modules[0]).PLUGIN
        readme = folder / "README.md"
        text = readme.read_text() if readme.exists() else f"# homelab-plugin-{folder.name}\n"
        block = table(plugin)
        if BEGIN in text:
            text = re.sub(f"{re.escape(BEGIN)}.*?{re.escape(END)}", lambda _m, b=block: b, text, flags=re.S)
        else:
            text = text.rstrip() + f"\n\n## Jobs ({len(plugin.jobs)})\n\n{block}\n"
        readme.write_text(text)


if __name__ == "__main__":
    main()

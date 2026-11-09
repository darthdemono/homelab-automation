# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Run one maintenance script: python -m homelab_jellyfix.runop --stack JSON MODULE [flags]

The settings (library folders, service URLs, quarantine, ...) are installed in `stack` before the script module is
imported, because a script reads them when it loads. `--undo @latest` means: the journal this script wrote the last
time it was applied (recorded under the state folder).
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from . import stack


def _journals() -> set[Path]:
    folder = Path(stack.PLANS)
    return {p for p in folder.glob("*.json")} if folder.is_dir() else set()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3 or argv[0] != "--stack":
        raise SystemExit("usage: python -m homelab_jellyfix.runop --stack JSON MODULE [flags]")
    stack.configure(json.loads(argv[1]))
    module_name, rest = argv[2], argv[3:]
    marker = Path(stack.STATE) / "last_journal" / (module_name.rsplit(".", 1)[-1] + ".txt")
    if "--undo" in rest and rest[rest.index("--undo") + 1 : rest.index("--undo") + 2] == ["@latest"]:
        if not marker.exists():
            raise SystemExit(f"no journal recorded for {module_name}: nothing to undo")
        rest[rest.index("--undo") + 1] = marker.read_text().strip()
    Path(stack.PLANS).mkdir(parents=True, exist_ok=True)
    before = _journals()
    sys.argv = [module_name, *rest]
    try:
        runpy.run_module(module_name, run_name="__main__", alter_sys=True)
        result = 0
    except SystemExit as stop:
        result = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
        if isinstance(stop.code, str):
            print(stop.code, file=sys.stderr)
    new = sorted(_journals() - before, key=lambda p: p.stat().st_mtime)
    if new and "--undo" not in rest:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(new[-1]))
    return result or 0


if __name__ == "__main__":
    sys.exit(main())

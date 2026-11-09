# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Run one musicfix stage's own command line: python -m homelab_musicfix.run --music M --tmp T --reports R STAGE ...

The paths are set before the stage module is imported, so every stage sees the user's library. The engine's jobs
call this for the part that does the work and for the stage's own journal-based undo.
"""

from __future__ import annotations

import argparse
import importlib
import sys

from . import settings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="homelab_musicfix.run")
    ap.add_argument("--music", required=True)
    ap.add_argument("--tmp", required=True)
    ap.add_argument("--reports", required=True)
    ap.add_argument("--leftovers", default="", help="JSON object with the leftover-file rules")
    ap.add_argument("stage")
    ap.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    settings.configure(args.music, args.tmp, args.reports)
    if args.leftovers:
        import json

        settings.set_leftovers(json.loads(args.leftovers))
    module = importlib.import_module(f"homelab_musicfix.{args.stage}")
    return module.main(args.rest) or 0


if __name__ == "__main__":
    sys.exit(main())

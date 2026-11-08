# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Writes (or with --check, verifies) the JSON schemas of the three config files."""

import json
import sys
from pathlib import Path

from homelab.config import Core, Jobs, Plugins

OUT = Path(__file__).resolve().parent.parent / "schemas"
MODELS = {"homelab": Core, "plugins": Plugins, "jobs": Jobs}


def main() -> int:
    stale = []
    for name, model in MODELS.items():
        text = json.dumps(model.model_json_schema(), indent=2) + "\n"
        path = OUT / f"{name}.schema.json"
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            OUT.mkdir(exist_ok=True)
            path.write_text(text)
    if stale:
        print("stale schemas, run scripts/gen_schemas.py:", ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

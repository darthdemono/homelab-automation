# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Fuzz target: the three YAML config files are hand-written, so their parsing must never crash.

Run: uv run --with atheris python fuzz/fuzz_config.py -max_total_time=60
pytest replays the seeds in tests/test_fuzz_seeds.py without atheris.
"""

import contextlib
import sys

import yaml
from pydantic import ValidationError

from homelab.config import Core, Jobs, Plugins

try:
    import atheris
except ImportError:
    atheris = None


def fuzz(data: bytes) -> None:
    try:
        loaded = yaml.safe_load(data.decode("utf-8", errors="replace"))
    except yaml.YAMLError:
        return
    if not isinstance(loaded, dict):
        return
    for model in (Core, Plugins, Jobs):
        with contextlib.suppress(ValidationError, TypeError):
            model(**loaded)


if __name__ == "__main__":
    if atheris is None:
        sys.exit("atheris is not installed: uv run --with atheris python fuzz/fuzz_config.py")
    atheris.Setup(sys.argv, fuzz)
    atheris.Fuzz()

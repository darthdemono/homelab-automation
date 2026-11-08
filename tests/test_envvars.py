# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""The sample environment lists every variable the project uses, and cannot drift from the code."""

import importlib.util
import re
from pathlib import Path

from homelab.envvars import ENGINE_ENV, UNV_ENV

ROOT = Path(__file__).resolve().parent.parent


def generator():
    spec = importlib.util.spec_from_file_location("gen_env_example", ROOT / "scripts" / "gen_env_example.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_example_is_current():
    assert (ROOT / ".env.example").read_text() == generator().build()


def test_every_homelab_variable_in_the_code_is_registered():
    used = set()
    for path in (ROOT / "src" / "homelab").glob("*.py"):
        if path.name != "envvars.py":
            used |= set(re.findall(r"HOMELAB_[A-Z_]+", path.read_text()))
    assert used <= set(ENGINE_ENV), f"unregistered: {sorted(used - set(ENGINE_ENV))}"


def test_every_registered_variable_is_in_the_sample():
    text = (ROOT / ".env.example").read_text()
    assert all(name in text for name in [*ENGINE_ENV, *UNV_ENV])


def test_the_sample_is_a_safe_template():
    for line in (ROOT / ".env.example").read_text().splitlines():
        assert not re.match(r"^[A-Z_]+=\S", line), f"a value is filled in: {line}"

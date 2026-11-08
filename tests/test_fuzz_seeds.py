# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fuzz"))
from fuzz_config import fuzz  # noqa: E402

SEEDS = [
    b"",
    b"{}",
    b"plugins: {a: {enabled: true}}",
    b"pipelines: {x: {steps: [a.b]}}",
    b"[1, 2",
    b"\xff\xfe",
    b"state_dir: [",
    b"paths: 3",
]


def test_seeds_never_crash():
    for seed in SEEDS:
        fuzz(seed)


@given(st.binary(max_size=300))
def test_random_bytes_never_crash(data):
    fuzz(data)

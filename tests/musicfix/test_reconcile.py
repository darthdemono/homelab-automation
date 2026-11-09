# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Small invariants for the read-only Dupe reconciliation audit."""

from homelab_musicfix.reconcile import classify


def check(label, got, want):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, want {want!r}")


good = {"quality": {"error": None}}
bad = {"quality": {"error": "ffprobe failed"}}
check("missing is not imported", classify(good, None), "target-missing")
check("unreadable source cannot repair", classify(bad, good), "source-unreadable")
check("unreadable target is repair candidate", classify(good, bad), "repair-candidate")
check("two readable files are not assumed identical", classify(good, good), "matching-readable")

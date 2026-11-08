# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Typed errors. `code` and `exit_code` are the CLI contract: see docs/CLI.md."""

from __future__ import annotations


class RunError(Exception):
    """Base error. `code` and `exit_code` are the CLI contract: see docs/CLI.md."""

    code = "error"
    exit_code = 1

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details


class NotFound(RunError):
    code, exit_code = "not_found", 3


class Ambiguous(RunError):
    code, exit_code = "ambiguous", 4


class Denied(RunError):
    code, exit_code = "denied", 5


class Conflict(RunError):
    code, exit_code = "conflict", 6


class Unavailable(RunError):
    code, exit_code = "unavailable", 7


class Invalid(RunError):
    code, exit_code = "invalid", 10

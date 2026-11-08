# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Checks a plugin against the contract before anyone relies on it."""

from __future__ import annotations

from homelab_plugin_api import API_VERSION, Context, Plugin

from .runner import check_permissions


def check(name: str, plugin: Plugin, ctx: Context | None = None) -> list[str]:
    problems = []
    manifest = plugin.manifest
    if manifest.name != name:
        problems.append(f"manifest name {manifest.name!r} differs from {name!r}")
    if manifest.api_version != API_VERSION:
        problems.append(f"api_version {manifest.api_version!r}, engine speaks {API_VERSION!r}")
    if not plugin.jobs:
        problems.append("no jobs")
    ctx = ctx or Context(plugin=name)
    try:
        options = plugin.config_model(**plugin.example)
    except Exception as err:
        return problems + [f"example options do not validate: {err}"]
    for job_name, job in plugin.jobs.items():
        try:
            first = job.plan(ctx, options)
            second = job.plan(ctx, options)
        except Exception as err:
            problems.append(f"job {job_name}: plan raised {type(err).__name__}: {err}")
            continue
        if first.model_dump() != second.model_dump():
            problems.append(f"job {job_name}: two plans from the same input differ")
        problems += [f"job {job_name}: {p}" for p in check_permissions(manifest, ctx, first)]
        if any(a.kind == "plugin" for a in first.actions) and not (plugin.apply_action and plugin.undo_action):
            problems.append(f"job {job_name}: plugin actions need apply_action and undo_action")
    return problems

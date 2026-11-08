# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Renders systemd user units from jobs.yaml. Nothing is enabled for you."""

from __future__ import annotations

import shlex
import sys

from .config import Config

SERVICE = """[Unit]
Description=homelab-automation pipeline {name}

[Service]
Type=oneshot
Environment=HOMELAB_SCHEDULED=1
ExecStart={command}
"""

TIMER = """[Unit]
Description=Schedule for homelab-automation pipeline {name}

[Timer]
OnCalendar={when}
Persistent=true

[Install]
WantedBy=timers.target
"""


def render(config: Config, config_dir: str) -> dict[str, str]:
    files = {}
    wrapper = " ".join(shlex.quote(w) for w in config.core.schedule_wrapper)
    for name, pipe in config.pipelines.items():
        if not pipe.schedule:
            continue
        command = f"{wrapper} {shlex.quote(sys.argv[0])} --config {shlex.quote(config_dir)} run {shlex.quote(name)}"
        if pipe.apply:
            command += " --apply --yes"  # the owner chose apply in jobs.yaml; the policy still limits it to auto steps
        files[f"homelab-{name}.service"] = SERVICE.format(name=name, command=command.strip())
        files[f"homelab-{name}.timer"] = TIMER.format(name=name, when=pipe.schedule)
    return files

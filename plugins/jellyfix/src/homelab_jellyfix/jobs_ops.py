# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Job table: every maintenance script of this plugin as an engine job."""

from pathlib import Path

from homelab_jellyfix.opsbridge import ops_jobs

JOBS = ops_jobs("homelab_jellyfix", Path(__file__).parent / "ops")

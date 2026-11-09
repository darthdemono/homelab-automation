# Free worker models: rate limits, stalls and memory

This guide covers handing bulk work (summaries, per-file analysis, drafts) to a worker model served by a free or rate-limited provider pool.

## Symptom

- Workers start, then produce nothing for 15 minutes or more. No error reaches you.
- The provider log or usage report shows `429`, `Too Many Requests` or a connection in cooldown.
- A fan-out of many workers makes the whole machine swap, then freeze or crash.
- Killing a stuck worker with `pkill -f <pattern>` also kills the shell that ran it (exit 144).

## Why it happens

- Free pools enforce per-minute and per-day limits. Once a connection is in cooldown every request waits or fails, and a worker that retries quietly looks like a hang.
- Each agentic worker is a full process tree (runtime plus its tool servers), roughly 300 MB each. Ten at once on a 16 GB machine that already swaps is enough to crash it.
- `pkill -f` matches the full command line, and the shell running `pkill -f 'worker x'` contains that pattern too.

## How to check

- Look at the pool's usage or health report before a fan-out: calls, errors, cooldowns per provider and model.
- Watch memory while workers run: `free -h`, and the `MemAvailable` line of `/proc/meminfo`.
- Find the stuck processes without matching yourself: `pgrep -af '[w]orker-name'`.

## Fix

- Kill by process id: `pgrep -f '[w]orker-name'` then `kill <pid>`. The `[w]` bracket keeps the pattern from matching its own command line.
- Switch the stalled job to another tier or pool, and tell whoever owns the run that a pool is in cooldown.
- Rerun only the parts that produced no output.

## How to prevent

- Check pool health first. A fan-out of agentic workers needs a healthy connection, not one in cooldown.
- For bulk text work use one lightweight process that fans out requests with per-pool concurrency caps, failover and backoff, and pauses when available memory drops low (1.5 GB worked as a floor).
- Run no more than two full agentic workers at once on a 16 GB machine.
- Honour `Retry-After` on every `429`, log each wait, and run Python unbuffered (`python -u`) so progress shows up in the log. Silent buffering looks exactly like a rate-limit stall.
- Give workers no secrets and no access to private data. Treat their output as unverified until a person or a review step has read it.

## Which tool helps

None in this repository yet. `homelab` itself never calls a model; it gives a worker a JSON CLI (`homelab describe`, `--json`, `--limit`) whose output stays small.

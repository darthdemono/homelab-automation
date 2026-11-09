# Stale inventory after moves

## Symptom

- A merge of new downloads quarantines hundreds of tracks as "collisions" or "already in the library" that are not.
- A plan proposes moving files that are no longer there, or misses files that just arrived.
- After an undo or a restore, the next pass acts on the pre-restore state.
- A crawler or seed script crashes on a path that was moved or restored since.

## Why it happens

Tools that plan from a saved inventory (a JSON-lines file of path, tags and probe data) see the library as it was when the inventory was written. Every move, retag, undo or manual change after that makes some rows wrong. A collision check against wrong rows gives wrong answers with full confidence. One merge quarantined 367 tracks this way before it was caught.

## How to check

```sh
python3 - <<'EOF'
import json, os, sys
inv = "<reports>/inventory.jsonl"
rows = [json.loads(l) for l in open(inv)]
gone = [r["path"] for r in rows if not os.path.exists(os.path.join("<library>", r["path"]))]
print(len(rows), "rows,", len(gone), "point at nothing")
EOF
```

Any nonzero count means the inventory is stale. Also compare its modification time with the newest journal.

## Fix

1. Restore what the bad pass did from its journal (every mutation should have one).
2. Patch the inventory instead of rebuilding it: re-probe only the paths a journal names (both sides of each move), drop rows whose file is gone, leave every other row alone.
3. Feed the patch only the tail of the journal written by the last run. Journals are cumulative: re-probing every path one has ever named turned a 2-file batch into 1438 files and 5.5 minutes.
4. Run a full rescan only when files changed outside the tools that journal.

## How to prevent

- Prune rows that point at nothing before every merge, automatically.
- Order the steps so anything that moves files runs before anything that keys work by path: merge, then casing fixes, then the inventory patch, then analysis and tagging.
- Record each journal's size before a run's write steps, and turn only what was appended into the list of paths to re-probe.
- After an undo or restore, patch the inventory from the restored journal before planning again.

## Which tool helps

- `homelab report` diffs two snapshots by kind (gone, new, replaced) and shows what changed since the last run.
- Engine moves are journalled per run, so the paths a run touched are always known.
- An inventory collector in the planned data layer will patch from run journals.

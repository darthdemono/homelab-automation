# pkill -f kills the shell that ran it

## Symptom

- `pkill -f 'python -m some.module'` ends your own command with exit code 144 (or 143), and nothing after it in the same command runs.
- A monitoring loop that checks `pgrep -f 'some pattern'` never sees the job finish, because it always finds itself.

## Why it happens

`-f` matches against the full command line of every process. When you run the command through `bash -c "...; pkill -f 'some pattern'; ..."`, that bash process's own command line contains the pattern, so it is matched and signalled too. The same applies to `pgrep -f` in a polling loop.

## How to check

```sh
bash -c "pgrep -af 'sleep 999'"        # lists the bash process itself, though nothing runs sleep
```

## Fix

Use the bracket trick, so the regular expression matches the target but not the text of the command that contains it:

```sh
pgrep -af '[s]ome.module'              # look first
pkill -f '[s]ome.module'               # then signal
```

Or find the process id and kill exactly that:

```sh
pid=$(pgrep -f '[s]ome.module' | head -1); [ -n "$pid" ] && kill "$pid"
```

## How to prevent

- Always look with `pgrep -af` before `pkill -f`.
- In polling loops, use the bracket form or check a pid file the job writes itself.
- Prefer a job runner that tracks its own process ids, so nothing has to be found by pattern.

## Which tool helps

`homelab apply --detach` runs a long apply in the background with a status file, and `homelab cancel` stops it by run id, so no pattern matching is needed.

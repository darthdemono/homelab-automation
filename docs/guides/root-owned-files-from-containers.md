# Root-owned files left by containers

## Symptom

- `Permission denied` when your user writes into a folder that a container wrote earlier (an analysis output folder, a config database).
- `touch: cannot touch '<library>/.t': Permission denied` on a library root after a new disk or filesystem was set up.
- A tool's own config database (SQLite) cannot be edited from the host.
- A dashboard container cannot read the Docker socket although `PGID` is set.
- `git status` warns `unable to access '.../.gitattributes': Permission denied` inside a folder holding container data.

## Why it happens

- A container that runs as root writes root-owned files into a bind mount. The host user cannot change them later.
- A freshly created filesystem gets root-owned top-level folders, so containers running as your uid cannot write into them.
- `PGID` set to a number that is not the docker group's gid on this host (group ids differ between machines).

## How to check

```sh
find <folder> ! -user "$(id -u)" -printf '%u:%g %p\n' | head
getent group docker                      # the real gid of the docker group
docker inspect -f '{{.Config.User}}' <container>
```

## Fix

- Change ownership from inside a container that already runs as root, with the same mount, instead of needing `sudo` on the host:

```sh
docker run --rm -v <host-folder>:/data alpine chown -R 1000:1000 /data
```

- Or, if `sudo` is available: `sudo chown -R "$USER": <folder>`.
- Edit a root-owned SQLite file through a throwaway container: `docker run --rm -v <config>:/data alpine sh -c "apk add -q sqlite && sqlite3 /data/app.db '<SQL>'"`. Stop the app first.
- If you cannot change ownership, move the root-owned folder aside (rename, do not delete) and recreate it as your user.
- For the socket, run the dashboard as `user: "1000:1000"` with `group_add: ["<docker gid>"]`.

## How to prevent

- Run your own one-off containers with `--user "$(id -u):$(id -g)"` when they write into your folders.
- Create and chown library roots right after a disk change, before the first container starts.
- Look up group ids on the host instead of copying them from another machine.

## Which tool helps

- `docker_ps` (read only) shows what is running and what restarted.
- A storage plugin that checks ownership and permissions of library roots is planned.

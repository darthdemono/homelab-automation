# Docker: image missing after a prune, or pull denied

## Symptom

- A job that runs a locally built image fails with `Unable to find image 'my-tool:latest' locally` followed by a pull error.
- `docker compose pull` fails for a public `ghcr.io` image with `denied`.
- A rebuild after the image disappeared takes a long time and may print an EOF during unpack.

## Why it happens

- A locally built image has no registry to pull from. Cleanup (`docker image prune -a`, `docker system prune -a`) removes any image no container uses, including tools you only run now and then.
- Docker sends stored credentials for a registry. An expired or revoked token makes the registry refuse a pull that would work anonymously.

## How to check

```sh
docker image ls my-tool                        # is the tag still there?
docker inspect -f '{{.Config.Image}}' <container>
grep -A3 '"auths"' ~/.docker/config.json       # which registries have stored logins (do not print the values)
```

## Fix

- Rebuild the image from its Dockerfile, with the exact tag the job expects:

```sh
docker build -t my-tool:latest -f docker/my-tool/Dockerfile docker/my-tool
```

  Check that the image exists and runs once, even if the build printed a warning.
- Remove the stale registry login and pull again: `docker logout ghcr.io`. Back up `~/.docker/config.json` first.

## How to prevent

- Prune dangling layers (`docker image prune -f`) and old build cache (`docker builder prune --filter until=168h`), not every unused image.
- Keep a build command next to every locally built image, in the tool's docs or in a script.
- Make jobs check that their image exists before a long run, and fail with the build command in the message.
- Before pulling a new image, record the old image id, so the update can be undone.

## Which tool helps

- `compose_update` records image ids before pulling, and `undo` retags them and recreates the services.
- `docker_ps` shows what runs; `compose_restart` restarts services.

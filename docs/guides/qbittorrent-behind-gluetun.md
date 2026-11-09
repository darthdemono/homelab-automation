# qBittorrent behind Gluetun

## Symptom

- Sonarr, Radarr, Homepage or another UI cannot reach `http://qbittorrent:8080` (name does not resolve, or connection refused).
- The Gluetun port shows closed, or the forwarded port never reaches qBittorrent. The Gluetun log shows the up command failing with `login failed`.
- After recreating the stack, qBittorrent listens on a stale port and peers cannot connect.

## Why it happens

- With `network_mode: "service:gluetun"` qBittorrent has no network of its own. Its ports belong to the `gluetun` container, so every other container must use `http://gluetun:8080`.
- Gluetun runs `VPN_PORT_FORWARDING_UP_COMMAND` when it gets a forwarded port. A script that logs in with a hard-coded user and password breaks as soon as the password changes.
- The two containers share one network namespace. Recreating one without the other leaves qBittorrent with the old port, or without a namespace at all.

## How to check

```sh
docker inspect -f '{{.HostConfig.NetworkMode}}' qbittorrent                    # container:<gluetun id>
docker exec gluetun cat /tmp/gluetun/forwarded_port                            # the port the VPN gave
docker exec gluetun wget -qO- http://127.0.0.1:8080/api/v2/app/preferences | grep -o '"listen_port":[0-9]*'
docker logs gluetun 2>&1 | grep -i 'port forward' | tail
```

The two port numbers must match.

## Fix

- Point every client at `http://gluetun:8080`, including UIs that keep it in their own database (qui, for example).
- Inside the namespace `127.0.0.1:8080` is localhost to qBittorrent. With `WebUI\LocalHostAuth=false` the up command can set the port without a password:

```sh
PORT=$(cat /tmp/gluetun/forwarded_port)
wget -qO- --post-data="json={\"listen_port\":$PORT,\"random_port\":false,\"upnp\":false}" \
  http://127.0.0.1:8080/api/v2/app/setPreferences
```

  Keep an authenticated fallback for when the bypass is off, and read the port back to confirm.
- After a full stack recreate, run the up command again by hand or recreate `gluetun` and `qbittorrent` together.

## How to prevent

- Never hard-code the WebUI password in the up command. Use the localhost bypass or a secret injected at run time.
- Publish qBittorrent's ports on the `gluetun` service, never on `qbittorrent`.
- Re-run your *arr download-client setup after any reinstall, and check the URL it writes.
- Test VPN protocol changes under real load before switching: in one measurement WireGuard matched OpenVPN on one stream but collapsed with several parallel streams.

## Which tool helps

- `qbittorrent_status` and `qbittorrent_torrents` (read only) show whether qBittorrent answers and moves data. Pass the Gluetun URL: `homelab plan qbittorrent_status --set url=http://gluetun:8080`.
- `compose_restart` restarts both services together.
- A Gluetun plugin that checks the forwarded port is planned.

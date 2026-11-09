# Monitoring: exporters that look healthy and report nothing

## Symptom

- `smartctl_exporter` logs `Smartctl open device: /dev/sdX failed: Permission denied` and reports 0 devices.
- After adding the `disk` group it reports devices, but every metric is an empty string or zero, and `Read Device Identity failed: Operation not permitted` appears.
- Grafana crash-loops with `Datasource provisioning error: data source not found` after you added a `uid` to a provisioned datasource.
- Provisioned community dashboards show `Datasource ${DS_PROMETHEUS} was not found` on every panel.
- The `dashboard` table in Grafana's database is empty, so provisioning looks broken.
- Prometheus cannot scrape a `node_exporter` that runs with host networking on `127.0.0.1`.
- A systemd user timer (a textfile collector, for example) stops after you log out.
- The Scrutiny container is "Up" but its web UI refuses connections.

## Why it happens

- Docker puts `cap_add` capabilities in the bounding set, but a non-root user gets an empty effective set. The exporter image runs as `nobody`, so `SYS_RAWIO` is present and unusable.
- Grafana matches provisioned datasources by uid. One provisioned without a uid got a random one; setting a uid later points at a row that does not exist.
- Only the UI importer resolves `__inputs`; file provisioning does not.
- Recent Grafana versions keep dashboards in unified storage, not the legacy table.
- A container on a bridge network cannot reach the host's loopback.
- User services stop at logout unless lingering is enabled.
- An all-in-one image can time out during init and leave the web server unstarted.

## How to check

```sh
docker exec smartctl-exporter smartctl -d sat -i /dev/sdX      # a Device Model line means the exporter will work
loginctl show-user "$USER" -p Linger
docker logs grafana 2>&1 | grep -c level=error
```

## Fix

- smartctl exporter: `user: "0:0"`, `cap_drop: [ALL]`, `cap_add: [SYS_RAWIO, SYS_ADMIN]`, and keep its port on loopback.
- Grafana uid change: add a `deleteDatasources:` block naming the datasource, so the file removes the old row before inserting.
- Community dashboards: remove `__inputs` and `__requires`, replace `${DS_PROMETHEUS}` with the datasource uid, set `id` to null.
- Check dashboards in the `resource` table, not `dashboard`.
- Run `node_exporter` on the monitoring bridge with `/proc`, `/sys` and `/` mounted read-only and `pid: host`; publish its port on loopback.
- `sudo loginctl enable-linger "$USER"` for user timers.
- Give Scrutiny a health check that restarts it after a few failures, or `docker restart` it.

## How to prevent

- Bind every monitoring port to `127.0.0.1`: Grafana ships with a default admin login and Prometheus has no auth.
- Create empty `provisioning/plugins` and `provisioning/alerting` folders so Grafana's startup log has no errors that hide real ones.

## Which tool helps

- `docker_ps` shows unhealthy and restarting containers; `homelab attention` ranks what needs a look.
- Monitoring collectors are planned.

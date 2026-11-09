# A plugin that lives in its own repository

A tool can be a public project of its own and a homelab plugin at the same time. A music-tagging tool is the worked example here: its repository has its own CLI, tests and release cycle, and one extra module that registers it with the engine.

## What the plugin repository adds

1. A module that builds a `Plugin` (see [ARCHITECTURE.md](ARCHITECTURE.md)): `mytool/homelab.py` exports `PLUGIN`.
2. An entry point, so installing the package is enough:

   ```toml
   [project.entry-points."homelab.plugins"]
   mytool = "mytool.homelab:PLUGIN"

   [project.optional-dependencies]
   homelab = ["homelab-plugin-api>=1.4"]
   ```

3. Nothing else. The CLI never imports the module, so the tool still runs without homelab-automation.

## What the user does

```sh
uv pip install -e ~/Code/mytool              # or pip install "mytool[homelab]"
homelab plugins                               # mytool shows up
homelab plan mytool.status --set artist="Some Artist"
```

Plugins in other repositories are found the same way as the bundled ones. Secrets stay in the vault: the plugin declares the variable names, and `secrets.entries` in your own `homelab.yaml` maps each to a vault entry, so no entry id is ever committed to the plugin repository.

## Rules of thumb

- Read-only jobs run the command at plan time and show the output as notes. Jobs that change something are one `exec` action, run only by `homelab apply`. Put the risky ones at `ask` in `policy`.
- If the tool uses a different Python environment, set an environment variable such as `MYTOOL_PYTHON` so the plugin runs that interpreter.
- Keep personal data out of the plugin repository: working data, vault entry ids and local paths belong in ignored files or in your homelab config.

The plugin repository can stay private or go public: nothing in it needs homelab-automation to run.

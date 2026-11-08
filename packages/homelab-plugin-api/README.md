# homelab-plugin-api

The contract a homelab-automation plugin implements: `Manifest`, `Permissions`, `Action`, `Plan`, `Job`, `Plugin` and `Context`. It depends only on pydantic, so a plugin never needs the engine.

A plugin module exposes one `PLUGIN` object. A published plugin registers it in the `homelab.plugins` entry point group. A private plugin is a file or folder in the engine's gitignored `custom/` directory.

```toml
[project.entry-points."homelab.plugins"]
my_plugin = "my_package:PLUGIN"
```

A job's `plan(ctx, options)` lists intended actions and changes nothing. The engine stores the plan, and `apply` runs exactly that reviewed plan. Every plan is reversible with `undo`, and nothing is ever deleted: removals are moves into the quarantine folder.

# homelab-plugin-nextcloud

Writes to a Nextcloud server over WebDAV (CalDAV and CardDAV included) with the engine's guarantees: the change is planned and reviewed first, a copy of whatever is there is saved before it is replaced or removed, and `undo` puts it back. Nothing is removed without a saved copy.

## Switch it on

`config/plugins.yaml`:

```yaml
plugins:
  nextcloud:
    options:
      url: https://cloud.example.com     # or http://localhost:8081
      user: alice
```

The app password comes from `NEXTCLOUD_APP_PASSWORD`, supplied by UnENVerse or a mode-600 `.env` (see the repository's `docs/UNV.md`). Create an app password in Nextcloud under Settings, Security.

## Jobs

| Job | Options | What it does |
| --- | --- | --- |
| `put` | `path`, `file`, optional `content_type` | Upload the local `file` to the server `path`, replacing what is there. The plan says whether it creates or replaces |
| `delete` | `path` | Remove the resource at `path`, after saving a copy |

```sh
homelab plan nextcloud.put --set path=/remote.php/dav/calendars/alice/personal/exam.ics --set file=./exam.ics
homelab apply latest --yes
homelab undo latest --yes               # restores the previous copy, or removes what was created (after saving it)
```

Saved copies live in `state/plugins/nextcloud/` (gitignored). Why not SQL: a card or event row is not self-contained in Nextcloud, and writing the database directly leaves the search index and the sync journal stale, so synced phones never see the change. DAV is the supported write path.

# Nextcloud: writes that do not sync, settings that do not stick

## Symptom

- A contact inserted with SQL exists but cannot be found, and phones never receive it.
- A second copy of a contact appears after an "update".
- A vCard edit with `sed` silently changes nothing.
- A contacts export parses as an empty address book, with no error.
- An `OVERWRITE*` variable in compose has no effect; federated ids read `user@localhost`.
- An `occ user:setting ... displayname` command reports success and nothing changes.
- External storage shows `Storage local::/<mount>/ not available` and `Permission denied` on `opendir`.

## Why it happens

- A card is more than its row: search rows (`oc_cards_properties`), the sync token and change log, and etag and size. SQL writes skip all of them.
- The DAV file name must be the card's `UID`. A different name creates a second card.
- vCard lines end in CRLF, so `$` in `sed` never matches. The export stream, however, is LF-terminated.
- `OVERWRITECLIHOST` does not exist (the real names are `OVERWRITECLIURL` and `OVERWRITEHOST`), and `OVERWRITE*` values only apply at install or upgrade.
- `user:setting ... settings displayname` writes a store nothing reads. The visible name is the `display_name` key; `user:profile` writes the profile tables.
- The web server user reaches external storage through group permissions; one folder without `g+rx` breaks the mount.

## How to check

```sh
docker exec -u www-data nextcloud php occ config:system:get overwrite.cli.url
docker exec -u www-data nextcloud php occ config:app:get core backgroundjobs_mode    # want: cron
find <external-mount> \( -type d ! -perm -g=rx -o -type f ! -perm -g=r \) -printf '%M %g %p\n'
```

## Fix

- Write contacts and events only over DAV: `PUT /remote.php/dav/addressbooks/users/<user>/contacts/<uid>.vcf` (201 created, 204 replaced). Save the old copy first; contacts have no trash.
- Edit vCards in a program that keeps CRLF (Python with `newline=''`), unfold before parsing, bump `REV`, write vCard 3.0.
- Normalise the export (`\r\n` to `\n`) before parsing.
- Set `overwrite.cli.url` with `occ config:system:set` as well as in compose.
- Rename a user with `occ user:profile <uid> displayname "<name>"` and `occ user:setting <uid> settings display_name "<name>"`, then `occ dav:sync-system-addressbook`. Do not run `occ user:sync-account-data` afterwards.
- Give folders `g+rx` and files `g+r` for the group the web server runs in.

## How to prevent

- Keep background jobs on cron, not AJAX, so cleanup runs without an open browser tab.
- Read the help of destructive `occ` commands first: `occ app:remove` drops the app's tables.

## Which tool helps

The `nextcloud` plugin: `nextcloud.put` and `nextcloud.delete` over DAV save the previous copy before any replace or remove, and `undo` restores it.

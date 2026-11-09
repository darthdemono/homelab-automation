# NTFS libraries: case collisions and forbidden characters

## Symptom

- Two files that differ only in case (`Song.flac`, `song.flac`) overwrite each other, or a rename "succeeds" but the old file is gone.
- A rename to `Re:Re:` or `AC/DC` fails, or the name arrives as `Re Re ` or `AC DC`.
- A folder named `Album.` or `Album ` appears as `Album` and no longer matches its tag.
- Two folders such as `Some Band Of Rock` and `Some Band of Rock` both exist, and one holds only stray metadata.

## Why it happens

A library on an NTFS drive mounted through `ntfs-3g` (type `fuseblk`) carries Windows rules:

1. `< > : " / \ | ? *` cannot appear in a name. Trailing dots and spaces are dropped silently. `CON`, `NUL`, `COM1` and the other reserved names are unusable.
2. The volume is case-insensitive for Windows and for most media servers, so every collision check must compare names lowercased.
3. At the POSIX layer `ntfs-3g` can still create two entries that differ only in case. They coexist as two inodes, so "same name ignoring case" does not prove "same file".

## How to check

```sh
findmnt -no FSTYPE /mnt/media                     # fuseblk means ntfs-3g
ls -di "/mnt/media/Music/Some Band Of Rock" "/mnt/media/Music/Some Band of Rock"   # different inodes = two real folders
find /mnt/media/Music -maxdepth 1 | awk '{print tolower($0)}' | sort | uniq -d     # case-only duplicates
```

## Fix

- Build the target name first, then check for a collision against the lowercased set of existing names, not against the exact string.
- Before treating two case-different paths as one, compare inodes (`os.path.samefile`). If they differ, move the contents of the stray one into the canonical folder, then quarantine the empty stray.
- Map forbidden characters to look-alikes instead of deleting them, which destroys information. Mappings that have worked: `:` to `꞉`, `/` to `⁄`, `?` to `？`.
- Strip trailing dots and spaces yourself so the name you write is the name you get.

## How to prevent

- Keep one resolver that turns tags into a path, and use it everywhere (rename, merge, retag), so two passes can never pick different spellings.
- Fold the same way everywhere you match: case, accents, and the look-alike characters above. Remember that a folder on disk may hold `⁄⁄` where the tag says `//`.
- Never fold to plain ASCII: it collapses every non-Latin name onto an empty string.
- Choose the folder from the Album Artist tag, not the track artist, or one album splits across several folders.

## Which tool helps

- The engine `move` and `journal` actions check planned writes against the files before applying, and `undo` reverses them.
- A music-library cleanup plugin with this resolver is planned.

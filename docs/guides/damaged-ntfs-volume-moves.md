# Moving files on a damaged NTFS volume

## Symptom

- `os.remove` or `rm` raises `FileNotFoundError` (ENOENT) on a file that `ls` lists and `stat` reads with the right size.
- `shutil.move` copies the file, then fails on the unlink: you hold both copies and an exception.
- A script or a media-server scan sits forever with no error. The process is in `request_wait_answer`; `except OSError` never fires.
- A file reads back the right byte count but holds megabytes of zeros. `ffprobe` calls it healthy.
- `ls -l` shows `-?????????` and any access returns EIO.
- An atomic write (`name.part` then `os.replace`) leaves `.part` files behind across the library.
- A move journal claims moves that never reached disk.

## Why it happens

The NTFS directory index (`$I30`) disagrees with the MFT. Through `ntfs-3g` an entry can be listable but not unlinkable, a directory can block inside FUSE, and data can be zero-filled. `os.replace` must unlink the target, so it fails on the same entries.

## How to check

```sh
python3 -c "import os,sys; p=sys.argv[1]; print(os.stat(p).st_size); os.remove(p)" "<file>"   # stat works, remove fails
timeout 30 ls "<dir>" || echo "this directory blocks"
sha256sum "<copy-a>" "<copy-b>"           # size, duration and codec can match while content differs
cat /proc/<pid>/wchan; echo               # request_wait_answer means stuck in FUSE
```

## Fix

- Write the move journal after every single move and fsync it, never once at the end. A crash must leave a journal that matches the disk.
- A destination that already holds a byte-identical copy is a half-finished move: verify with sha256, then drop the source.
- An unlink that fails on a file that still exists is a report, not a crash. Renaming the parent directory still works, so carry a stuck entry out of the library by moving its folder to quarantine.
- When `os.replace` fails, remove the temp file and fall back to an in-place rewrite (open the path with `"w"`). Count those fallbacks: a growing number measures the damage.
- A media-server scan wedged on a bad entry: put an `.ignore` file in that folder (Jellyfin skips such folders) and restart the server.
- The real repair is an offline `ntfsfix` or a Windows `chkdsk` with the volume unmounted, which means stopping every service that uses it. Moving the data to a native Linux filesystem ends the problem.

## How to prevent

- Never walk the whole volume. Walk the library roots and give every tool a `--roots` option.
- Run scripts with a timeout, so a hang shows up as a failure.
- Treat any journal from a run that crashed as untrusted until each move is checked against disk.
- Back up before bulk moves. Two episodes were lost once when a journal recorded moves that did not happen.

## Which tool helps

- The engine `journal` action writes each finished move to its own journal as it goes, so a run that dies half way can still be undone.
- `jellyfix.verify` runs `ffprobe` over a library (read only); it does not catch zero-fill, so pair it with sha256 when a copy is in doubt.

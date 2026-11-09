# Brackets in media names break glob

## Symptom

- `glob("<library>/Some Movie (1999) [tmdbid-12345]/*.mkv")` returns nothing, though the files are there.
- A shell loop over `"$dir"/*` with such a folder in `$dir` skips it, or `ls <path>/*` says `No such file or directory`.
- A `find -name` or `fnmatch` filter built from a real file name matches the wrong files or none.

## Why it happens

In glob patterns `[...]` is a character class. `[tmdbid-12345]` matches one single character from that set, so the literal folder name never matches. Release names are full of brackets (`[1080p]`, `[Group]`, `[tvdbid-N]`), so this hits media scripts all the time and fails silently.

## How to check

```python
import glob, os

d = "<library>/Some Movie (1999) [tmdbid-12345]"
print(os.path.isdir(d), glob.glob(d + "/*"))  # True, [] means the brackets were read as a pattern
```

## Fix

- Do not glob on real paths. Use `os.listdir`, `os.scandir` or `Path.iterdir()` and filter in code.
- If you must glob, escape the literal part: `glob.glob(glob.escape(d) + "/*.mkv")`.
- In the shell, quote the variable and let the shell glob only the part you mean: `for f in "$dir"/*.mkv` works because the quoted `$dir` is literal; `find "$dir" -name '*.mkv'` is safer still.
- For `find -name` or `fnmatch`, escape `[` as `[[]`.

## How to prevent

- Add a test fixture whose folder name contains `[` and `]` to every script that walks a library.
- Related release-name trap: strip the file extension before detecting the release group, or `x265-Group.mkv` gives `Group.mkv` and the new name ends in `.mkv.mkv`.

## Which tool helps

The `jellyfix` plugin lists folders with `os.listdir`, not glob.

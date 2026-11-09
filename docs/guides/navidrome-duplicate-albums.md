# Navidrome shows duplicate albums, or merges two into one

## Symptom

- One folder shows up as two or more albums with the same name, the tracks split between them.
- Two different folders (`Some Album` and `Some Album 25`) appear as one album with all tracks.
- An album shows `Some Album (single)` or `Some EP EP` although the folder name is clean.
- A clean-up pass finds a few new files of work on every run and never converges.

## Why it happens

- Navidrome keys an album on tags, not on the folder. Its album id (`ND_PID_ALBUM`) defaults to `musicbrainz_albumid|albumartistid,album,albumversion,releasedate`. One file with a different MusicBrainz album id, or a different release date, makes a second album row.
- Two folders with the same ALBUM tag and album artist fold into one.
- A file moved into a clean folder can keep its raw tags. The UI shows the tag.
- A tag reader that falls back from one key to another (RELEASEDATE to DATE) invents values, so writes keep changing what it reads.
- Taggers write `0001-01-01` for an empty date, which looks like a second pressing.

## How to check

Read the real tags of every file in one folder and compare the identity keys: album artist, album, MusicBrainz album id, date, release date, original date, label, catalogue number, release type.

```sh
for f in "<library>/Some Artist/Some Album"/*.flac; do
  metaflac --show-tag=ALBUM --show-tag=MUSICBRAINZ_ALBUMID --show-tag=RELEASEDATE --show-tag=DATE "$f"; echo; done
```

Do not use the Navidrome database for the diagnosis: it shows the effect, not the cause.

## Fix

Sort each duplicate group into one of four cases. Only the first is safe to automate.

| case | meaning | action |
| --- | --- | --- |
| fill | one release, some files under-tagged | the minority adopts the folder consensus; journal every write |
| split | two real releases share a folder (different label, release group, media) | report; merging destroys information |
| variant | same album, two spellings across folders | one canonical ALBUM tag and a folder rename |
| edition | base album beside a deluxe or remaster folder | report the track overlap; a person decides |

Stop Navidrome while writing tags, then rescan. The rows only collapse once it re-reads the tags.

## How to prevent

- Retag on every move: the ALBUM written must equal the album the folder was named after.
- An absent tag reads as absent. Delete only case variants of the key you write, never a "fallback" key.
- Accept dates only between 1900 and 2100, and let a unanimous album id outrank a disagreeing year.
- Do not stamp one album id onto a large folder where only a few files carry it (a leak dump with three tagged singles is not that single).
- Nominating candidates from two tags misses groups that split on RELEASEDATE alone. Check every multi-file folder for the real answer.

## Which tool helps

A music-library cleanup plugin with album-identity checks is planned.

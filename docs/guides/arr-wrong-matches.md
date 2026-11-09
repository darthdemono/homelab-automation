# Sonarr and Radarr match the wrong item

## Symptom

- A real file gets attached to a series or movie that has no files and is only there to show on the calendar.
- `/api/v3/parse` reads `Some Show - 1175` as season 11 episode 75.
- Episodes of an anime lose their absolute number in the name, even though the series sits in the anime library.
- A release profile that requires `GroupA`, `1080p`, `x265` lets through anything tagged `1080p`.
- A search for episode 1 of an old series returns episode 1 of a newer series with the same name, and Sonarr maps it onto the old one.
- A title lookup returns the wrong film of two with near-identical titles, and you take the first hit.

## Why it happens

- Unmonitored entries added for the calendar have no file, so they win a loose title-and-year fallback.
- The parse endpoint guesses season and episode from bare numbers.
- `/parse` always reports `seriesType: standard`, even for anime, and some series under the anime root are `standard` in Sonarr.
- "Must Contain" in a release profile is OR, not AND.

## How to check

```sh
curl -s -H "X-Api-Key: $SONARR_API_KEY" "http://sonarr:8989/api/v3/parse?title=Some%20Show%20-%201175" | python3 -m json.tool | grep -E 'seasonNumber|episodeNumbers|absolute'
curl -s -H "X-Api-Key: $SONARR_API_KEY" http://sonarr:8989/api/v3/series | python3 -c 'import json,sys;[print(s["title"],s["seriesType"],s["monitored"]) for s in json.load(sys.stdin) if "/anime" in s["path"]]'
```

## Fix

- In any script that maps files onto *arr entries, treat `monitored == false` as "not a candidate". Offer a flag to include them on purpose.
- Use Sonarr to identify the series, never the episode. An explicit `SxxEyy` wins; a bare 3 or 4 digit number is absolute; anything else is reported, not guessed.
- Re-fetch the full series record instead of trusting `/parse`, and set `seriesType: anime` on everything under the anime root.
- Write one regex with lookaheads to AND the terms: `(?=.*GroupA)(?=.*1080p)(?=.*x265)`. Bind the profile to a tag carried only by the series it is for.
- Unmonitor the single episode that keeps being mismatched and fill it by hand.
- For films, confirm the runtime against the file before accepting a lookup hit.

## How to prevent

- Refusing to match is a feature: ambiguity becomes a reported issue, never a guess. A wrong confident match misfiles a whole series.
- When a series must come from one release group that no public indexer carries, unmonitor its episodes but keep the series monitored, so manual grabs can still be filed.

## Which tool helps

- `sonarr_queue` and `radarr_queue` show what is being grabbed (read only).
- The `jellyfix` plugin scores titles against every alias and translation and reports ambiguous matches instead of renaming them.
- Release-profile and import scripts are planned.

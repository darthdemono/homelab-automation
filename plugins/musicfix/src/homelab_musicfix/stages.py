# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Every musicfix stage as a job. The table says how each stage is planned, applied and undone (see bridge.py)."""

from .bridge import Stage, jobs

R = ["--restore", "--commit"]  # the usual way a stage reverses its last run
NEEDS_SCAN = "needs the inventory: run the `scan` job first"

STAGES = {
    "scan": Stage(
        "scan",
        'Inventory the library into the cache every other stage reads; music files are only read (use args ["--journal", file] to patch after changes)',
    ),
    "plan": Stage(
        "plan",
        "Turn the inventory into a reviewable plan of moves: duplicates, junk, bad tags, renames and sidecars (read only)",
        needs=NEEDS_SCAN,
    ),
    "report": Stage("report", "Human-readable summaries of the inventory and the plan (read only)", needs=NEEDS_SCAN),
    "verify": Stage("verify", "Post-move consistency checks: did every file arrive, nothing lost (read only)"),
    "tidy": Stage(
        "apply",
        "Apply the plan: quarantine, stage and commit moves, then prune empty folders; undo reverses the journal",
        commit=["--phase", "all", "--commit"],
        dry=["--phase", "all"],
        undo=["--commit"],
        undo_module="undo",
        needs="run `plan` first",
    ),
    "merge": Stage(
        "merge",
        "Move new music from a downloads folder into the library with canonical names (tags are journalled for `fixtags --restore`)",
        commit=["--commit"],
        undo=["--journal", "{reports}/merge_journal.jsonl", "--commit"],
        undo_module="undo",
        needs='set the downloads folder with args=["--downloads", "<folder>"]',
    ),
    "casing": Stage(
        "casing",
        "One canonical casing per artist decided library-wide, then made true in tags, folders and names",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "fixtags": Stage(
        "fixtags",
        "Rewrite Artist and Album Artist into clean multi-valued tags; no packed separators, no feat. in the album",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "albums": Stage(
        "albums",
        "Find why one album shows up twice in a player (release identity tags) and fix it",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "editions": Stage(
        "editions",
        "Fold partial edition folders into the album they belong to",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "discs": Stage(
        "discs",
        "Fill a missing DISCNUMBER from the rest of the folder so players group discs correctly",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "genres": Stage(
        "genres",
        "Normalise genres: consistent casing, de-duplicated, in tags and nfo files",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "comments": Stage("comments", "Strip a tagger's settings dump out of COMMENT tags", commit=["--commit"], undo=R),
    "credits": Stage(
        "credits",
        "Embed hand-sourced credits from a JSON file into tracks the database lacks",
        commit=["--commit"],
        undo=R,
        needs='pass the credits file with args=["<file.json>"]',
    ),
    "artwork": Stage(
        "artwork",
        "Reduce the library to one album cover per audio folder; extra images go to quarantine",
        commit=["--commit"],
        undo=R,
    ),
    "nfo": Stage(
        "nfo",
        "Generate album.nfo and artist.nfo for Emby, Jellyfin and Kodi",
        commit=["--commit"],
        undo=R,
        needs=NEEDS_SCAN,
    ),
    "nomusic": Stage(
        "nomusic", "Quarantine folders that hold no music, only leftover metadata", commit=["--commit"], undo=R
    ),
    "convert": Stage(
        "convert",
        "Convert ALAC (m4a) to FLAC without losing audio or metadata; the originals are kept in quarantine",
        commit=["--commit"],
        needs=NEEDS_SCAN,
    ),
    "reconcile": Stage(
        "reconcile",
        "Compare a replica library with a recovery tree: matched by derived path, not by layout (read only)",
    ),
    "spectral": Stage(
        "spectral",
        "Detect lossless files that were transcoded from a lossy source (read only report)",
        needs=NEEDS_SCAN,
    ),
    "mbseed": Stage(
        "mbseed",
        "Build MusicBrainz add-release seeding forms from library tags (writes files under the state folder)",
        needs=NEEDS_SCAN,
    ),
    "moodbar": Stage(
        "moodbar",
        "Generate .mood sidecars (spectral colour bars) for players that show them; undo removes only the ones this made",
        commit=["--commit"],
        undo=["--clean", "--commit"],
        needs=NEEDS_SCAN,
    ),
    "analyze_extract": Stage(
        "analyze",
        "Audio analysis, step 1: BPM, key, loudness and low-level features with the native extractor",
        dry=["extract", "--dry-run"],
        commit=["extract"],
        needs=NEEDS_SCAN,
    ),
    "analyze_models": Stage(
        "analyze",
        "Audio analysis, step 2: mood, danceability, genre, instrument and voice models in Docker (add --gpu in args)",
        dry=["models", "--dry-run"],
        commit=["models"],
    ),
    "analyze_calibrate": Stage(
        "analyze", "Audio analysis: computed BPM against existing BPM tags (read only)", dry=["calibrate"]
    ),
    "analyze_tag": Stage(
        "analyze",
        "Audio analysis, step 3: write the results to tags; undo restores the previous tags",
        dry=["tag"],
        commit=["tag", "--commit"],
        undo=["tag", *R],
    ),
    "playlist": Stage(
        "playlist",
        "Import a CSV track list into a Navidrome playlist with fuzzy matching (set NAVIDROME_URL, NAVIDROME_USER, NAVIDROME_TOKEN)",
        commit=["--commit"],
        needs="no undo of its own: delete the playlist in Navidrome to reverse it",
    ),
    "pipeline": Stage(
        "pipeline",
        "A whole downloads batch in the fixed order: merge, casing, scan, analyse, artwork",
        commit=["--commit"],
    ),
}

JOBS = jobs(STAGES)

# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Checks for the logic that is easy to get subtly, silently wrong.

Run with:  ./venv/bin/python -m tests.test_naming
"""

from __future__ import annotations

import sys

from homelab_musicfix.naming import clean_album, sanitize_component
from homelab_musicfix.plan import norm
from homelab_musicfix.quality import quality_key
from homelab_musicfix.tags import build_vocabulary, join_artists, split_artists

FAILURES = []


import collections


def check(label, got, want):
    if got != want:
        FAILURES.append(f"{label}\n     got:  {got!r}\n     want: {want!r}")


# --- weak separators must never split without corroboration ----------------
# Each of these is a real artist name containing an ambiguous separator.
# Splitting them silently loses part of the name, and nothing downstream can
# detect the loss, so this is the most important group in the file.
for name in [
    "Malcolm X",
    "X Ambassadors",
    "Now, Now",
    "Tyler, The Creator",
    "Sam and Dave",
    "Earth, Wind & Fire",
    "Simon & Garfunkel",
    "Florence + The Machine",
    "Cardi B & Megan Thee Stallion",
    "Godspeed You! Black Emperor",
    "Sleater-Kinney",
]:
    check(f"no-vocab keeps whole: {name}", split_artists([name]), [name])

# --- strong separators always split ----------------------------------------
check("bullet", split_artists(["ANGUISH • EXILED • Elfass"]), ["ANGUISH", "EXILED", "Elfass"])
check(
    "semicolon",
    split_artists(["Alan Walker; Sabrina Carpenter; Farruko"]),
    ["Alan Walker", "Sabrina Carpenter", "Farruko"],
)
check("feat", split_artists(["Drake feat. Rihanna"]), ["Drake", "Rihanna"])

# --- a genuine multi-valued tag is already authoritative -------------------
check("multivalue", split_artists(["akiaura", "Olya Holiday"]), ["akiaura", "Olya Holiday"])

# --- weak separators split once the library corroborates every fragment ----
VOCAB = {"afrojack", "eva simons", "cardi b", "megan thee stallion"}
check("vocab-gated comma", split_artists(["AFROJACK, Eva Simons"], VOCAB), ["AFROJACK", "Eva Simons"])
check("vocab-gated amp", split_artists(["Cardi B & Megan Thee Stallion"], VOCAB), ["Cardi B", "Megan Thee Stallion"])
check("vocab absent -> whole", split_artists(["Malcolm X"], VOCAB), ["Malcolm X"])

# --- vocabulary construction ------------------------------------------------
vocab = build_vocabulary(
    [
        {"tags": {"artist": ["akiaura", "Olya Holiday"]}},  # multi-valued: both stand alone
        {"tags": {"artist": ["Tyler, The Creator"]}},  # weak separator: not trusted
        {"tags": {"artist": ["21 Savage"]}},  # clean single: trusted
    ]
)
check("vocab has multivalue parts", "olya holiday" in vocab, True)
check("vocab has clean single", "21 savage" in vocab, True)
check("vocab excludes ambiguous", "tyler, the creator" in vocab, False)

# --- join form --------------------------------------------------------------
check("join 1", join_artists(["X"]), "X")
check("join 2", join_artists(["X", "Y"]), "X and Y")
check("join 3", join_artists(["X", "Y", "Z"]), "X, Y and Z")
check("join 4", join_artists(["W", "X", "Y", "Z"]), "W, X, Y and Z")

# --- album feature stripping ------------------------------------------------
check("album feat", clean_album("The Thrill (feat. Empire of the Sun)"), "The Thrill")
check(
    "album feat keeps qualifier",
    clean_album("Take Over Control (feat. Eva Simons) [Radio Edit]"),
    "Take Over Control [Radio Edit]",
)
check("album untouched", clean_album("SAVAGE MODE II"), "SAVAGE MODE II")
check("album with", clean_album("Sunflower (with Post Malone)"), "Sunflower")

# --- release-format suffixes are stripped from album names ------------------
check("(single) stripped", clean_album("Watercolor Eyes (single)"), "Watercolor Eyes")
check("trailing EP stripped", clean_album("Vroom Vroom EP"), "Vroom Vroom")
check("- Single stripped", clean_album("Ke Ki Chay - Single"), "Ke Ki Chay")
check("lowercase Ep stripped", clean_album("Heartbound Ep"), "Heartbound")
check(
    "only trailing (single) goes, qualifier stays",
    clean_album("You Want It Darker (Paul Kalkbrenner Remix) (single)"),
    "You Want It Darker (Paul Kalkbrenner Remix)",
)
check("qualifier kept before EP", clean_album("Lesser Man (extended version) EP"), "Lesser Man (extended version)")
# Must not eat real words or albums that are literally named EP/Single.
check("Sleep is not stripped", clean_album("Sleep"), "Sleep")
check("Deep End is not stripped", clean_album("Deep End"), "Deep End")
check("Bleep is not stripped", clean_album("Bleep"), "Bleep")
check("album literally 'EP' is kept", clean_album("EP"), "EP")
check("album literally 'Single' is kept", clean_album("Single"), "Single")

# --- NTFS sanitisation ------------------------------------------------------
check("colon", sanitize_component("Re:Re:"), "Re꞉Re꞉")
check("slash", sanitize_component("AC/DC"), "AC⁄DC")
check("question", sanitize_component("R U Mine?"), "R U Mine？")
check("collapse spaces", sanitize_component("Re Re  "), "Re Re")
check("trailing dot", sanitize_component("trailing dot."), "trailing dot")
check("reserved name", sanitize_component("CON"), "_CON")
check("no reserved chars survive", any(c in sanitize_component('a"b|c*d<e>f') for c in '<>:"/\\|?*'), False)

# --- duplicate ranking ------------------------------------------------------
flac = {"quality": {"lossless": True, "bit_depth": 16, "sample_rate": 44100, "bit_rate": 900_000, "codec": "flac"}}
mp3 = {"quality": {"lossless": False, "bit_depth": None, "sample_rate": 44100, "bit_rate": 320_000, "codec": "mp3"}}
hires = {"quality": {"lossless": True, "bit_depth": 24, "sample_rate": 96000, "bit_rate": 2_000_000, "codec": "flac"}}
check("lossless beats lossy", quality_key(flac) > quality_key(mp3), True)
check("hi-res beats cd", quality_key(hires) > quality_key(flac), True)

# A bit-identical FLAC must beat an uncompressed WAV. Ranking lossless copies by
# bitrate made the WAV win purely for being bigger, which is backwards.
wav = {
    "quality": {"lossless": True, "bit_depth": 24, "sample_rate": 44100, "bit_rate": 2_116_000, "codec": "pcm_s24le"}
}
flac24 = {"quality": {"lossless": True, "bit_depth": 24, "sample_rate": 44100, "bit_rate": 1_687_000, "codec": "flac"}}
check("flac beats wav at equal depth/rate", quality_key(flac24) > quality_key(wav), True)

# --- duplicate matching must not merge distinct recordings -----------------
# Stripping every bracketed suffix collapsed "GUMMO (remix)" into "GUMMO" and
# "Mary (Instrumental)" into "Mary", which would quarantine a recording the
# library has no other copy of.
check("remix is not the original", norm("GUMMO") == norm("GUMMO (remix)"), False)
check("instrumental is not the original", norm("Mary") == norm("Mary (Instrumental)"), False)
check("live is not the studio take", norm("Song") == norm("Song (Live)"), False)
check("remaster is not the original", norm("Song") == norm("Song (2011 Remaster)"), False)
# ...but a featured-artist suffix genuinely is the same recording.
check("feat suffix is the same song", norm("The Thrill") == norm("The Thrill (feat. Empire of the Sun)"), True)
check("identical titles match", norm("Wildflower") == norm("Wildflower"), True)

# --- WAV is kept alongside FLAC, never weighed against it ------------------
from homelab_musicfix.plan import format_class  # noqa: E402

check("wav is its own dupe class", format_class("a/b/x.wav"), "wav")
check("flac competes normally", format_class("a/b/x.flac"), "other")
check("mp3 competes normally", format_class("a/b/x.mp3"), "other")
check("wav and flac never collide", format_class("x.wav") == format_class("x.flac"), False)
check("case insensitive", format_class("x.WAV"), "wav")

# --- spectral cutoff detection ---------------------------------------------
# np.convolve(mode="same") zero-pads, and on dB data 0 dB is enormous: it
# dragged a -132 dB tail up to -71 dB, past the threshold, so every file looked
# full-band and nothing was ever flagged. Smoothing must use edge padding.
import numpy as np  # noqa: E402
from homelab_musicfix.spectral import WINDOW, find_cutoff  # noqa: E402

rate = 44100
freqs = np.fft.rfftfreq(WINDOW, 1.0 / rate)
# Synthetic spectrum: flat to 16 kHz, then a cliff -- a classic 128k MP3 shape.
spec = np.where(freqs <= 16_000, 1.0, 1e-14)
cut = find_cutoff(spec, rate)
check("cliff at 16kHz is detected", 15_000 < cut < 17_500, True)

# A genuinely full-band spectrum must reach Nyquist.
spec_full = np.ones_like(freqs)
check("full-band reaches nyquist", find_cutoff(spec_full, rate) > 20_500, True)

from homelab_musicfix.spectral import classify  # noqa: E402

verdict, _src = classify(16_000, 44100, lossless=True)
check("lossless with 16kHz cutoff is suspect", verdict, "SUSPECT")
verdict, _src = classify(16_000, 44100, lossless=False)
check("lossy with 16kHz cutoff is expected", verdict, "lossy (expected)")
verdict, _src = classify(22_000, 44100, lossless=True)
check("lossless reaching nyquist is fine", verdict, "full-band")

# --- a suspected transcode is demoted, but only inside its own tier --------
# Ranking "genuine" ahead of everything made a suspect 943 kbps FLAC lose to a
# 230 kbps MP3, which is strictly worse -- the FLAC stores that same audio
# without discarding anything further. Lossy files are never "suspect", so the
# flag has to sit *after* the lossless flag, not before it.
from homelab_musicfix.plan import _is_suspect  # noqa: E402


def rank(rec, spectral):
    lossless, *rest = quality_key(rec)
    genuine = 0 if _is_suspect(rec, spectral) else 1
    return (lossless, genuine, tuple(rest))


suspect_flac = {
    "path": "a.flac",
    "quality": {"lossless": True, "bit_depth": 16, "sample_rate": 44100, "bit_rate": 943_000, "codec": "flac"},
}
clean_mp3 = {
    "path": "b.mp3",
    "quality": {"lossless": False, "bit_depth": None, "sample_rate": 44100, "bit_rate": 230_000, "codec": "mp3"},
}
clean_flac = {
    "path": "c.flac",
    "quality": {"lossless": True, "bit_depth": 16, "sample_rate": 44100, "bit_rate": 830_000, "codec": "flac"},
}
SPEC = {"a.flac": {"verdict": "SUSPECT"}, "c.flac": {"verdict": "full-band"}}

check("suspect flac still beats mp3", rank(suspect_flac, SPEC) > rank(clean_mp3, SPEC), True)
check("genuine flac beats suspect flac even at lower bitrate", rank(clean_flac, SPEC) > rank(suspect_flac, SPEC), True)

# --- sidecar matching survives a different naming convention ---------------
# ~1200 .lrc files were fetched with a track-number prefix while the audio uses
# an artist prefix; exact-stem matching alone would orphan every one of them.
from homelab_musicfix.plan import find_sidecar_owner, sidecar_stem_variants  # noqa: E402

tracks = [
    {"stem": "A Boogie Wit da Hoodie - Demons and Angels", "n_title": norm("Demons and Angels"), "rec": {"target": "x"}}
]
check(
    "lrc with track-number prefix finds its track",
    find_sidecar_owner("6. Demons and Angels.lrc", tracks) is not None,
    True,
)
check(
    "exact stem still matches",
    find_sidecar_owner("A Boogie Wit da Hoodie - Demons and Angels.lrc", tracks) is not None,
    True,
)
check("unrelated sidecar stays unmatched", find_sidecar_owner("Some Other Song.lrc", tracks) is None, True)
# Sidecars often carry a version qualifier the audio filename lacks:
# "The Smiths - Ask - 2008 Remaster.jpg" beside "The Smiths - Ask.flac".
smiths = [
    {"stem": "The Smiths - Ask", "n_title": norm("Ask"), "rec": {"target": "x"}},
    {"stem": "The Smiths - Asleep", "n_title": norm("Asleep"), "rec": {"target": "y"}},
]
check(
    "sidecar with extra version qualifier attaches",
    (find_sidecar_owner("The Smiths - Ask - 2008 Remaster.jpg", smiths) or {}).get("stem"),
    "The Smiths - Ask",
)

# Prefix matching must never guess when two tracks are equally plausible.
ambiguous_tracks = [
    {"stem": "A - Love", "n_title": norm("Love"), "rec": {"target": "x"}},
    {"stem": "A - Love Song", "n_title": norm("Love Song"), "rec": {"target": "y"}},
]
check(
    "longest unique prefix wins",
    (find_sidecar_owner("A - Love Song Remaster.jpg", ambiguous_tracks) or {}).get("stem"),
    "A - Love Song",
)

check(
    ".cover infix is stripped",
    "joji - pixelated kisses" in [v.lower() for v in sidecar_stem_variants("joji - pixelated kisses.cover")],
    True,
)

# --- guests must be split too, using the same vocabulary -------------------
# strip_feat() used to call split_artists() without the vocabulary, so
# "6ix9ine ft. A Boogie Wit da Hoodie & Fetty Wap" produced a single bogus
# artist "A Boogie Wit da Hoodie & Fetty Wap" even though both names are
# top-level artist folders in this library.
GUEST_VOCAB = {"6ix9ine", "a boogie wit da hoodie", "fetty wap", "tory lanez", "young thug"}
check(
    "guest list is split with vocabulary",
    split_artists(["6ix9ine ft. A Boogie Wit da Hoodie & Fetty Wap"], GUEST_VOCAB),
    ["6ix9ine", "A Boogie Wit da Hoodie", "Fetty Wap"],
)
check(
    "guest list stays whole without corroboration",
    split_artists(["X ft. Some Unknown & Other Unknown"]),
    ["X", "Some Unknown & Other Unknown"],
)

# --- metadata recovered from the path --------------------------------------
# Files with no tags at all still sit at "<Artist>/<Album>/<Artist> - <Title>";
# quarantining them as unnameable threw away 9 real songs.
from homelab_musicfix.naming import canonical_paths, recover_from_path  # noqa: E402

rec = recover_from_path("Arctic Monkeys/Favourite Worst Nightmare/Arctic Monkeys - This House Is a Circus.flac")
check("path recovery: title", rec.get("title"), ["This House Is a Circus"])
check("path recovery: artist", rec.get("artist"), ["Arctic Monkeys"])
check("path recovery: album", rec.get("album"), ["Favourite Worst Nightmare"])

canon = canonical_paths({"path": "Kee Nola/Kee Nola/Sad Boy/Kee Nola - Void.flac", "tags": {}})
check("tagless file becomes nameable", canon["rel_path"], "Kee Nola/Sad Boy/Kee Nola - Void.flac")
check("recovery is recorded as a problem", "recovered-from-path" in canon["problems"], True)

# A real tag must always beat the filename.
canon2 = canonical_paths(
    {
        "path": "Wrong/Wrong/Wrong - Wrong.flac",
        "tags": {
            "title": ["Real Title"],
            "artist": ["Real Artist"],
            "album": ["Real Album"],
            "albumartist": ["Real Artist"],
        },
    }
)
check(
    "existing tags are not overwritten by the path",
    canon2["rel_path"],
    "Real Artist/Real Album/Real Artist - Real Title.flac",
)

# --- player leftovers are detected only by the rules the user configured; music is never mistaken for one -------
from homelab_musicfix import settings  # noqa: E402
from homelab_musicfix.plan import is_leftover  # noqa: E402

check("nothing is a leftover until rules are set", is_leftover("Player/Library.mbl"), False)
settings.set_leftovers(
    {
        "exts": [".mbl", ".mbp"],
        "name_prefixes": ["playerlibrary"],
        "exact_names": ["subscriptions.dat"],
        "dirs": ["Player", "Podcasts"],
    }
)
for p in ["Player/Library.mbl", "PlayerLibrary.bak", "NowPlaying.mbp", "Podcasts/Subscriptions.dat"]:
    check(f"leftover detected: {p}", is_leftover(p), True)
for p in [
    "Some Artist/Album/Some Artist - Song.flac",
    "Humble Pie/Smokin/Humble Pie - Hot 'n' Nasty.flac",  # 'mbl' substring only
    "Player Tribute Band/Album/Song.flac",
]:
    check(f"not a leftover: {p}", is_leftover(p), False)
settings.set_leftovers({})

# --- genre normalisation: library-wide casing + de-dup + unpacking ----------
from homelab_musicfix.genres import build_canonical_map, normalise_list, split_genre_value  # noqa: E402

recs = [
    {"tags": {"genre": ["Pop", "Rock"]}},
    {"tags": {"genre": ["Pop", "pop", "POP"]}},  # 'Pop' wins on frequency
    {"tags": {"genre": ["rock"]}},
]
canon = build_canonical_map(recs)
check("most frequent casing wins", canon["pop"], "Pop")
check("single-occurrence casing is canonicalised too", canon["rock"], "Rock")
check("de-dup collapses case variants", normalise_list(["Pop", "pop", "POP", "Rock"], canon), ["Pop", "Rock"])
check("packed genre value is unpacked", split_genre_value("Hip Hop;Pop"), ["Hip Hop", "Pop"])
check("html entity semicolon is not a split point", split_genre_value("R&amp;B"), ["R&amp;B"])
check(
    "normalise unpacks and canonicalises together",
    normalise_list(
        ["Electronic; Soundtrack; ambient"], build_canonical_map([{"tags": {"genre": ["Ambient", "Ambient"]}}])
    ),
    ["Electronic", "Soundtrack", "Ambient"],
)

# --- sidecar names with no track to inherit from must still lose ";" --------
from homelab_musicfix.plan import clean_sidecar_name  # noqa: E402

SC_VOCAB = {"bts", "halsey", "21 savage", "metro boomin"}
check(
    "stray sidecar artist is rejoined",
    clean_sidecar_name("BTS; Halsey - Boy With Luv (feat. Halsey).jpg", SC_VOCAB),
    "BTS and Halsey - Boy With Luv (feat. Halsey).jpg",
)
check(
    "stray sidecar with reserved char is sanitised",
    ";" in clean_sidecar_name("21 Savage; Metro Boomin - Glock.lrc", SC_VOCAB),
    False,
)
check("folder art name is untouched by artist logic", clean_sidecar_name("folder.jpg", SC_VOCAB), "folder.jpg")
check("plain sidecar without artist pattern is left alone", clean_sidecar_name("cover.png", SC_VOCAB), "cover.png")

# --- a featured artist removed from a title must land in the artist tag -----
from homelab_musicfix.naming import canonical_paths as _cp  # noqa: E402
from homelab_musicfix.tags import augment_with_guests  # noqa: E402

check("guest added when absent", augment_with_guests(["WesGhost"], ["Diggy Graves"]), ["WesGhost", "Diggy Graves"])
check(
    "guest not duplicated when present",
    augment_with_guests(["WesGhost", "Diggy Graves"], ["Diggy Graves"]),
    ["WesGhost", "Diggy Graves"],
)
check("guest de-dup is case-insensitive", augment_with_guests(["diggy graves"], ["Diggy Graves"]), ["diggy graves"])

teeth = _cp(
    {
        "path": "WesGhost/TEETH/WesGhost - TEETH (feat. Diggy Graves).flac",
        "tags": {
            "artist": ["WesGhost"],
            "albumartist": ["WesGhost"],
            "album": ["TEETH"],
            "title": ["TEETH (feat. Diggy Graves)"],
        },
    },
    vocabulary={"wesghost", "diggy graves"},
)
check("title feat stripped", teeth["title"], "TEETH")
check("guest moved into track artist", teeth["track_artists"], ["WesGhost", "Diggy Graves"])
check("album artist stays headliner only", teeth["album_artists"], ["WesGhost"])
check(
    "path reflects augmented artist and clean title",
    teeth["rel_path"],
    "WesGhost/TEETH/WesGhost and Diggy Graves - TEETH.flac",
)
check("title-has-feat flagged", "title-has-feat" in teeth["problems"], True)

# Guest already credited: nothing added, title still cleaned.
teeth2 = _cp(
    {
        "path": "x.flac",
        "tags": {
            "artist": ["WesGhost", "Diggy Graves"],
            "albumartist": ["WesGhost"],
            "album": ["TEETH"],
            "title": ["TEETH (feat. Diggy Graves)"],
        },
    },
    vocabulary={"wesghost", "diggy graves"},
)
check("no duplicate guest", teeth2["track_artists"], ["WesGhost", "Diggy Graves"])
check("no title-guest-added flag when already present", "title-guest-added-to-artist" in teeth2["problems"], False)

# Bare "with"/"w" must NOT be read as a feature marker -- it is an ordinary word
# in countless titles. Only bracketed "(with X)" counts.
from homelab_musicfix.tags import strip_feat as _sf  # noqa: E402

check("'Boy With Luv' is not split on with", _sf("Boy With Luv -Japanese ver.-")[0], "Boy With Luv -Japanese ver.")
check(
    "bare 'with' in title is preserved",
    _sf("break up with your girlfriend, i’m bored")[0],
    "break up with your girlfriend, i’m bored",
)
check("bracketed (with X) is a feature", _sf("Sunflower (with Post Malone)"), ("Sunflower", ["Post Malone"]))
check("bare feat. still works", _sf("Song feat. Drake"), ("Song", ["Drake"]))

# Guest hardening: compound guests split, phrases dropped, apostrophes folded.
check(
    "compound guest already present adds nothing",
    augment_with_guests(["Chris Brown", "Lil Wayne", "Busta Rhymes"], ["Lil' Wayne & Busta Rhymes"]),
    ["Chris Brown", "Lil Wayne", "Busta Rhymes"],
)
check(
    "production note is not an artist",
    augment_with_guests(["Juice WRLD"], ["instrumental by Ghost Loft"]),
    ["Juice WRLD"],
)
check("structural part 'intro' is not an artist", augment_with_guests(["MJ"], ["intro"]), ["MJ"])

# A tight interpunct is a name-internal separator in transliterated CJK names,
# not a tagger packing two artists. Splitting it invented an artist called 高.
check("tight interpunct in a CJK name is not a split", split_artists("希林娜依•高"), ["希林娜依•高"])
check(
    "tight interpunct splits when the library knows both halves",
    split_artists("希林娜依•高", build_vocabulary([{"tags": {"artist": ["希林娜依"]}}, {"tags": {"artist": ["高"]}}])),
    ["希林娜依", "高"],
)
check(
    "a SPACED bullet is still a packer and always splits",
    split_artists("akiaura • Olya Holiday"),
    ["akiaura", "Olya Holiday"],
)
check("semicolon still splits regardless of spacing", split_artists("Hip Hop;Pop"), ["Hip Hop", "Pop"])

# A guest that is a short form of someone already credited must not be added
# again -- it double-credits one artist and doubles them in the filename too.
check(
    "short-form guest already credited is not re-added",
    augment_with_guests(["Mia Martina", "Waka Flocka Flame"], ["Waka Flocka"]),
    ["Mia Martina", "Waka Flocka Flame"],
)
check(
    "a one-word guest is still added next to a longer unrelated name",
    augment_with_guests(["Drake Bell"], ["Drake"]),
    ["Drake Bell", "Drake"],
)
check(
    "an unrelated multi-word guest is still added",
    augment_with_guests(["Mia Martina"], ["Waka Flocka"]),
    ["Mia Martina", "Waka Flocka"],
)

# Sidecar pairing. Taggers name the audio from the tags and the sidecar from
# whatever listing they scraped, so an exact-stem match strands lyrics and art
# that plainly belong to the track sitting next to them. These check that the
# recovery pairs the obvious cases and still refuses the ambiguous ones.
from collections import defaultdict  # noqa: E402

from homelab_musicfix.merge import _plan_leftover_sidecars  # noqa: E402


def pair(tracks, sidecars):
    """Run the leftover pass over one directory; return {sidecar: dst or None}."""
    recs = [{"path": f"D/{n}", "tags": t, "_target": f"OUT/{os.path.splitext(n)[0]}.flac"} for n, t in tracks]
    scs = [{"path": f"D/{n}", "ext": os.path.splitext(n)[1]} for n in sidecars]
    actions, stats = [], defaultdict(int)
    _plan_leftover_sidecars(scs, set(), defaultdict(set), {"D": recs}, actions, stats)
    out = {}
    for a in actions:
        out[os.path.basename(a["src"])] = (a.get("dst") or "").split("/")[-1] or None
    return out


import os  # noqa: E402

# One track in the directory: the pairing is forced regardless of how far the
# names have drifted (U+2010 vs "-", feat. moved, artist alias, sub-title).
check(
    "sole track claims a drifted sidecar",
    pair([("ENHYPEN - Drunk‐Dazed.flac", {"title": ["Drunk-Dazed"]})], ["ENHYPEN - Drunk-Dazed.lrc"]),
    {"ENHYPEN - Drunk-Dazed.lrc": "ENHYPEN - Drunk‐Dazed.lrc"},
)
check(
    "sole track claims a feat.-reordered sidecar",
    pair([("Brown Eyed Girls feat. X - Oasis.flac", {"title": ["Oasis"]})], ["Brown Eyed Girls - Oasis (Feat. X).jpg"]),
    {"Brown Eyed Girls - Oasis (Feat. X).jpg": "Brown Eyed Girls feat. X - Oasis.jpg"},
)

# A track holds at most one sidecar per extension, or the second would collide
# on "<track stem>.<ext>". The loser stays an orphan.
check(
    "second sidecar of the same ext is not filed over the first",
    sorted(
        str(v)
        for v in pair(
            [("A - One.flac", {"title": ["One"]})], ["A - One.jpg", "A - Something Else Entirely.jpg"]
        ).values()
    ),
    ["A - One.jpg", "None"],
)

# Two tracks, two sidecars: scoring must put each with its own, not cross them.
check(
    "multi-track dir pairs each sidecar with its own track",
    pair(
        [("G - Alpha.flac", {"title": ["Alpha"]}), ("G - Beta.flac", {"title": ["Beta"]})],
        ["G - Alpha - Live Ver.lrc", "G - Beta - Live Ver.lrc"],
    ),
    {"G - Alpha - Live Ver.lrc": "G - Alpha.lrc", "G - Beta - Live Ver.lrc": "G - Beta.lrc"},
)

# The last-pair rule: scoring cannot bridge a translated title, but when one
# sidecar and one track are all that remain the pairing is forced anyway.
check(
    "last remaining sidecar and track are paired despite zero similarity",
    pair(
        [
            ("LE SSERAFIM - ANTIFRAGILE.flac", {"title": ["ANTIFRAGILE"]}),
            ("LE SSERAFIM - 이브.flac", {"title": ["이브"]}),
        ],
        ["LE SSERAFIM - ANTIFRAGILE - Japanese ver.lrc", "LE SSERAFIM - Eve, Psyche.lrc"],
    ),
    {
        "LE SSERAFIM - ANTIFRAGILE - Japanese ver.lrc": "LE SSERAFIM - ANTIFRAGILE.lrc",
        "LE SSERAFIM - Eve, Psyche.lrc": "LE SSERAFIM - 이브.lrc",
    },
)

# ...but only when the score was blind. Two same-script leftovers scored low
# because they are different songs: a Volta batch offered the sidecars for
# "Earth Intruders (Spike) - Extended Mark; Stent Mix" with no such track, and
# the last-pair rule filed the Earth Intruders lyrics against "I See Who You
# Are (Mark Bell mix)".
check(
    "same-script last pair is refused rather than guessed",
    pair(
        [
            ("Björk - Wanderlust.flac", {"title": ["Wanderlust"]}),
            ("Björk - I See Who You Are (Mark Bell mix).flac", {"title": ["I See Who You Are (Mark Bell mix)"]}),
        ],
        ["Björk - Wanderlust.lrc", "Björk - Earth Intruders (Spike) - Extended Mark; Stent Mix.lrc"],
    ),
    {
        "Björk - Wanderlust.lrc": "Björk - Wanderlust.lrc",
        "Björk - Earth Intruders (Spike) - Extended Mark; Stent Mix.lrc": None,
    },
)

# The same-script refusal must not swallow the version-qualifier drift, where
# the score is low purely on length: "It Went" scores 0.48 against
# "S3RL - It Went - Original Mix" yet is plainly the same track.
check(
    "same-script last pair is kept when one title contains the other",
    pair(
        [
            ("S3RL - Bass Slut.flac", {"title": ["Bass Slut"]}),
            ("S3RL feat. JessKah - It Went.flac", {"title": ["It Went"]}),
        ],
        ["S3RL - Bass Slut - Original Mix.lrc", "S3RL - It Went - Original Mix.lrc"],
    ),
    {
        "S3RL - Bass Slut - Original Mix.lrc": "S3RL - Bass Slut.lrc",
        "S3RL - It Went - Original Mix.lrc": "S3RL feat. JessKah - It Went.lrc",
    },
)

# Digits and punctuation carry no script, so a romanised stem beside a Japanese
# title is still recognised as unscorable and still gets its forced pairing.
check(
    "romanised sidecar still pairs with its Japanese title",
    pair(
        [("高橋洋子 - 心よ原始に戻れ2020.flac", {"title": ["心よ原始に戻れ2020"]})],
        ["Yoko Takahashi - Kokoro yo Genshi ni Modore 2020.lrc"],
    ),
    {"Yoko Takahashi - Kokoro yo Genshi ni Modore 2020.lrc": "高橋洋子 - 心よ原始に戻れ2020.lrc"},
)

# No track at all in the directory: a true orphan, never guessed onto a track
# from somewhere else.
check(
    "sidecar with no audio in its directory stays an orphan",
    pair([], ["Nikko Miles - Little Mermaid.lrc"]),
    {"Nikko Miles - Little Mermaid.lrc": None},
)

# Artist casing. One spelling per artist has to be decided from the whole
# library, or two spellings derive two folders and every merge re-splits them.
from homelab_musicfix.casing import (
    build_album_casing_map,
    build_casing_map,
    canon_folder_name,  # noqa: E402
    desired_values,
)


def rec(artist, albumartist=None, mbid=False, album=None):
    t = {"artist": [artist], "albumartist": [albumartist or artist]}
    if album:
        t["album"] = [album]
    if mbid:
        t["musicbrainz_artistid"] = ["x"]
    return {"path": f"{artist}/a/b.flac", "tags": t}


# A MusicBrainz-backed spelling beats a merely more common one -- this is the
# tier that gets `blackbear` right, where the majority says `Blackbear`.
canon, _ev = build_casing_map([rec("Blackbear"), rec("Blackbear"), rec("blackbear", mbid=True)])
check("MBID-backed spelling beats the more frequent one", canon["blackbear"], "blackbear")

# With no MBID anywhere, frequency decides.
canon2, _ = build_casing_map([rec("DJ Scheme"), rec("DJ Scheme"), rec("Dj Scheme")])
check("frequency decides when no MBID vouches", canon2["dj scheme"], "DJ Scheme")

# Tied on the MBID tier, overall frequency breaks it before the case rule --
# otherwise the capital "Of" wins over the spelling 3 other files agree on.
canon3, _ = build_casing_map(
    [
        rec("5 Seconds of Summer", mbid=True),
        rec("5 Seconds Of Summer", mbid=True),
        rec("5 Seconds of Summer"),
        rec("5 Seconds of Summer"),
    ]
)
check("overall frequency outranks the upper-case tiebreak", canon3["5 seconds of summer"], "5 Seconds of Summer")

# A joined folder name is in no map; each component must be resolved.
jc = {"pewdiepie": "PewDiePie", "party in backyard": "Party in Backyard"}
check(
    "joined folder name is resolved component-wise",
    canon_folder_name("pewdiepie and Party in Backyard", jc),
    "PewDiePie and Party in Backyard",
)
check(
    "already-canonical joined name is still recognised, not dropped",
    canon_folder_name("PewDiePie and Party in Backyard", jc),
    "PewDiePie and Party in Backyard",
)
check(
    "multi-separator join keeps its separators",
    canon_folder_name("a, b and c", {"a": "A", "b": "B", "c": "C"}),
    "A, B and C",
)

# Album casing is keyed by artist: two unrelated albums sharing a title must not
# have one casing forced onto the other.
albums = build_album_casing_map(
    [
        rec("Alex G", album="TRICK"),
        rec("Alex G", album="TRICK"),
        rec("Alex G", album="Trick"),
        rec("Someone Else", album="trick"),
        rec("Someone Else", album="trick"),
    ]
)
check("album casing resolves per artist", albums[("alex g", "trick")], "TRICK")
check("same album title under another artist is untouched", albums[("someone else", "trick")], "trick")

# The casing fix must not invent changes where none are needed.
check(
    "a file already canonical needs no change",
    desired_values({"artist": ["blackbear"], "albumartist": ["blackbear"]}, {"blackbear": "blackbear"}),
    {},
)

# --- album-level duplicate detection ---------------------------------------
# Every one of these is a case the first cut of albums.py got wrong against the
# real library, so each is pinned.
from homelab_musicfix.albums import base_album, classify_folder, dates_compatible, fold, plausible_date

check(
    "edition suffix is stripped so the base album groups with it",
    base_album("Slipknot (10th Anniversary Edition)"),
    "Slipknot",
)
check("a remaster is an edition, not a different album", base_album("Revolver (Remastered)"), "Revolver")
# The mirror of plan.norm's rule: a version qualifier is part of the identity.
check(
    "a remix qualifier is never mistaken for an edition suffix",
    base_album("You Want It Darker (Paul Kalkbrenner Remix)"),
    "You Want It Darker (Paul Kalkbrenner Remix)",
)
check("an album actually named for its edition is not emptied", base_album("Deluxe"), "Deluxe")

# fold() stripped to ASCII in the first cut, which folded every Japanese album
# name onto "" and reported the whole shelf as one duplicate group.
check("folding keeps non-latin scripts distinct", fold("ソルファ") == fold("ファンクラブ"), False)
check("folding ignores the curly-apostrophe split", fold("Let's Cheers to This"), fold("Let’s Cheers to This"))

check("a year and a full date of that year are one release", dates_compatible("2020", "2020-03-27"), True)
check("two different years are not", dates_compatible("1989", "2009"), False)
check("0001-01-01 is a tagger's empty field, not a year", plausible_date("0001-01-01"), False)
check("a placeholder date contradicts nothing", dates_compatible("0001-01-01", "2001-10-29"), True)

# One release, one file under-specifying it -> fill.
drift = {
    "a.flac": {"musicbrainz_albumid": "mb-1", "date": "2020-03-27", "label": "X"},
    "b.flac": {"musicbrainz_albumid": "", "date": "2020", "label": "X"},
}
verdict = classify_folder(drift)
check("a missing id beside a year-only date is drift", verdict["verdict"], "fill")
check("the under-specified file adopts the id", verdict["fills"]["b.flac"]["musicbrainz_albumid"], "mb-1")
check("and the more precise date, which only one file carried", verdict["fills"]["b.flac"]["date"], "2020-03-27")
check("the file that already had both is left alone", "a.flac" in verdict["fills"], False)

# Two real releases sharing a folder -> never merged. This is the 1999
# Roadrunner album and the 1989 Revelation 7" by the other Slipknot.
two = {
    "a.flac": {
        "musicbrainz_albumid": "mb-1",
        "musicbrainz_releasegroupid": "rg-1",
        "date": "2009-09-07",
        "label": "Roadrunner",
        "releasetype": "album",
        "totaltracks": "25",
    },
    "b.flac": {
        "musicbrainz_albumid": "mb-2",
        "musicbrainz_releasegroupid": "rg-2",
        "date": "1989",
        "label": "Revelation",
        "releasetype": "ep",
        "totaltracks": "6",
    },
}
check("two corroborated releases in one folder are reported, not merged", classify_folder(two)["verdict"], "split")
check("and nothing is written for them", classify_folder(two)["fills"], {})

# One release pressed twice: same release group, same label, same year. The
# ids differ but merging them is right.
pressings = {
    "a.flac": {
        "musicbrainz_albumid": "mb-1",
        "musicbrainz_releasegroupid": "rg-1",
        "date": "2011-05-23",
        "label": "Interscope",
        "releasetype": "album",
    },
    "b.flac": {
        "musicbrainz_albumid": "mb-2",
        "musicbrainz_releasegroupid": "rg-1",
        "date": "2011-05-23",
        "label": "Interscope",
        "releasetype": "album",
    },
    "c.flac": {
        "musicbrainz_albumid": "mb-1",
        "musicbrainz_releasegroupid": "rg-1",
        "date": "2011-05-23",
        "label": "Interscope",
        "releasetype": "album",
    },
}
pv = classify_folder(pressings)
check("two ids with no corroboration are one release", pv["verdict"], "fill")
check("and the id the most files carry wins", pv["fills"]["b.flac"]["musicbrainz_albumid"], "mb-1")

# A corrupt date on the majority must not look like a second pressing when
# every file already agrees on the album id.
corrupt = {
    f"t{i}.flac": {"musicbrainz_albumid": "mb-1", "date": "0001-01-01", "label": "Epic", "totaltracks": "16"}
    for i in range(12)
}
corrupt.update(
    {
        f"u{i}.flac": {"musicbrainz_albumid": "mb-1", "date": "2001-10-29", "label": "Epic", "totaltracks": "16"}
        for i in range(4)
    }
)
cv = classify_folder(corrupt)
check("a unanimous album id outranks a corrupt year", cv["verdict"], "fill")
check("and the corrupt date is the one replaced", cv["fills"]["t0.flac"]["date"], "2001-10-29")
check("while the good files are untouched", "u0.flac" in cv["fills"], False)

# Propagating a MusicBrainz album id claims every file in the folder is that
# release. `Juice WRLD/Unreleased` is a 1160-track leak dump holding exactly
# three properly-tagged singles; the consensus proposed stamping one of them
# onto the other 1159 files.
dump = {f"x{i}.flac": {"musicbrainz_albumid": "", "date": ""} for i in range(1157)}
for i, mb in enumerate(("mb-a", "mb-b", "mb-c")):
    dump[f"single{i}.flac"] = {
        "musicbrainz_albumid": mb,
        "musicbrainz_releasegroupid": f"rg-{mb}",
        "releasetype": "single",
        "date": "",
    }
dv = classify_folder(dump)
check(
    "three stray singles never define a 1160-track folder's identity",
    any("musicbrainz_albumid" in w for w in dv["fills"].values()),
    False,
)
check(
    "and the singles keep their own ids rather than being unified",
    any("musicbrainz_albumid" in w for p, w in dv["fills"].items() if p.startswith("single")),
    False,
)

# The permissive side of the same rule: in a small folder one properly-tagged
# track is good evidence for the rest, and that is the common correct case.
small = {f"t{i}.flac": {"musicbrainz_albumid": "", "date": "2018-01-26"} for i in range(10)}
small["tagged.flac"] = {
    "musicbrainz_albumid": "mb-1",
    "musicbrainz_releasegroupid": "rg-1",
    "releasetype": "album",
    "date": "2018-01-26",
    "totaltracks": "11",
}
sv = classify_folder(small)
check("one tagged track among eleven still seeds the other ten", sv["fills"]["t0.flac"]["musicbrainz_albumid"], "mb-1")

# A single cannot be the identity of a folder holding a whole album.
single_dump = {f"t{i}.flac": {"musicbrainz_albumid": "", "date": "2020"} for i in range(9)}
single_dump["s.flac"] = {"musicbrainz_albumid": "mb-s", "releasetype": "single", "date": "2020"}
check(
    "a single's id is not stamped across a ten-track folder",
    any("musicbrainz_albumid" in w for w in classify_folder(single_dump)["fills"].values()),
    False,
)

# --- two recordings of one song under one album ----------------------------
# Every other collision check in merge.py is name-derived, so two *different*
# recordings can derive one canonical path. A Bauhaus batch shipped the album
# version of "Spirit" (334.1s) and the single version (225.6s), both tagged
# title="Spirit"; the quality comparison quarantined the album version as a
# "lower-quality duplicate" and reported success.
from homelab_musicfix.merge import same_recording

check("the album version and the single version are not one recording", same_recording(334.1, 225.6), False)
check("the same take re-encoded is", same_recording(225.6, 225.9), True)
check("encoder padding on a long track stays within tolerance", same_recording(600.0, 601.5), True)
check("a missing duration is no evidence, so the old behaviour stands", same_recording(0, 225.6), True)

# --- a .nfo write must never strand its temp file ---------------------------
# On this library's NTFS volume some directory entries are readable but not
# unlinkable ($I30 index vs MFT disagree), so os.replace fails. The first cut
# let the exception escape with "<name>.part" still on disk: one run stranded
# 183 of them across the library. os.replace is monkeypatched here because the
# real condition needs a corrupted filesystem to reproduce.
import io as _io
import os as _os
import tempfile as _tempfile

from homelab_musicfix import nfo as _nfo

with _tempfile.TemporaryDirectory() as _root:
    _rel = "Artist/Album/album.nfo"
    _path = _os.path.join(_root, _rel)
    _os.makedirs(_os.path.dirname(_path))
    open(_path, "w").write("<old/>")

    _real_replace = _os.replace
    _os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError(2, "No such file or directory"))
    try:
        _how = _nfo.write_journalled(_root, _rel, "<new/>", _io.StringIO())
    finally:
        _os.replace = _real_replace

    check("an unlinkable target falls back to an in-place rewrite", _how, "in-place")
    check("the new content still lands", open(_path).read(), "<new/>")
    check("and no .part file is left behind", _os.path.exists(_path + ".part"), False)

    # When the fallback cannot work either, the temp file still must not survive.
    _real_open = open

    def _no_write_open(f, mode="r", *a, **k):
        if mode.startswith("w") and str(f) == _path:
            raise OSError(5, "Input/output error")
        return _real_open(f, mode, *a, **k)

    _os.replace = lambda *a, **k: (_ for _ in ()).throw(OSError(5, "Input/output error"))
    _nfo_open = __builtins__["open"] if isinstance(__builtins__, dict) else __builtins__.open
    try:
        import builtins as _builtins

        _builtins.open = _no_write_open
        try:
            _nfo.write_journalled(_root, _rel, "<newer/>", _io.StringIO())
            check("a doubly-failed write raises", True, False)
        except OSError:
            pass
    finally:
        _builtins.open = _real_open
        _os.replace = _real_replace
    check("even when both paths fail, no .part survives", _os.path.exists(_path + ".part"), False)

# --- a merged-in file must be retagged to match the path it was named from --
# The path comes from clean_album()/split_artists()/strip_feat(), so a folder
# reads "Zombie" while the file itself stayed tagged ALBUM="Zombie (single)".
# Navidrome keys the album on the tag, not the folder, so the uncleaned name is
# what showed up in the UI. merge._retag closes that gap; the invariant is that
# the album it writes is always exactly the album the folder was named with.
from homelab_musicfix.merge import _retag as _mrt
from homelab_musicfix.naming import canonical_paths as _cp

for _album, _want in (
    ("Zombie (single)", "Zombie"),
    ("The Bela Session EP", "The Bela Session"),
    ("Watercolor Eyes - Single", "Watercolor Eyes"),
    ("Clean Album", None),  # nothing to do
    ("Sleep", None),  # bare "EP" rule is word-boundaried
    ("EP", None),
):  # an album literally named EP
    _rec = {
        "path": f"A/{_album}/A - Song.flac",
        "tags": {"artist": ["A"], "albumartist": ["A"], "album": [_album], "title": ["Song"]},
    }
    _rt = _mrt(_rec, set())
    check(f"retag of {_album!r}", (_rt or {}).get("to", {}).get("album"), _want)
    check(f"retag of {_album!r} agrees with its folder", (_rt["to"]["album"] if _rt else _album), _cp(_rec)["album"])

# A packed artist and a feat-in-title are cleaned on the same pass, so the file
# does not sit in a "X and Y" folder still tagged with the packed string.
_rec = {
    "path": "X/Alb/X - T.flac",
    "tags": {"artist": ["Alpha;Beta"], "albumartist": ["Alpha;Beta"], "album": ["Alb"], "title": ["T (feat. Gamma)"]},
}
_rt = _mrt(_rec, {"alpha", "beta", "gamma"})
check("retag unpacks a strong-separator artist", _rt["to"]["artist"][:2], ["Alpha", "Beta"])
check("retag strips feat from the title", _rt["to"]["title"], "T")
check("retag credits the guest", "Gamma" in _rt["to"]["artist"], True)

# --- album art must become folder.jpg, not steal a track's .jpg slot --------
from homelab_musicfix.merge import _plan_album_covers as _pac


def _cover_case(tracknames, images, album="One-X"):
    _tracks = []
    for _n in tracknames:
        _tracks.append({"path": f"AA/{album}/{_n}.flac", "_target": f"AA/{album}/{_n}.flac", "canon": {"album": album}})
    _scs = [{"path": f"AA/{album}/{i}.jpg", "ext": ".jpg"} for i in images]
    _acts, _stats, _claimed = [], collections.defaultdict(int), set()
    _pac(_scs, _claimed, {f"AA/{album}": _tracks}, "/nonexistent", _acts, _stats)
    return _acts, dict(_stats)


_acts, _stats = _cover_case(["TDG - One-X", "TDG - Pain"], ["One-X", "TDG - One X"])
check("the album-named image becomes folder.jpg", [a["dst"] for a in _acts], ["AA/One-X/folder.jpg"])
check("and only that one image is claimed", _stats.get("album-covers"), 1)

_acts, _ = _cover_case(["Yazida - greedy", "Yazida - L-O-V-E"], ["must-haves"], album="must-haves")
check("an album cover with no same-named track is still filed", [a["dst"] for a in _acts], ["AA/must-haves/folder.jpg"])

_acts, _ = _cover_case(["A - Song", "A - Other"], ["cover"])
check("a generically named cover is recognised too", [a["dst"] for a in _acts], ["AA/One-X/folder.jpg"])

# A track's own art is never taken as the album cover, even when the album and
# the title track share a name.
_acts, _ = _cover_case(["One-X", "TDG - Pain"], ["One-X"])
check("a track's own art is left to the track", _acts, [])

# One track in the directory: the sole-track rule already handles it, and the
# album name and the title are routinely the same string there.
_acts, _ = _cover_case(["A - One-X"], ["One-X"])
check("single-track directories are left alone", _acts, [])

# --- a romanised guest is the sort spelling of a credited artist -----------
# ARTISTSORT is written from ARTIST, so every name in it belongs to someone the
# file already credits. "Bad Apple!! feat. nomico" beside ARTIST="まらしぃ feat.
# のみこ" / ARTISTSORT="Marasy feat. nomico" is two artists; without the sort tag
# nothing in the file ties nomico to のみこ and the guest was added a second time.
check(
    "romanised guest from the sort tag is not re-added",
    augment_with_guests(["まらしぃ", "のみこ"], ["nomico"], ["Marasy feat. nomico"]),
    ["まらしぃ", "のみこ"],
)
check(
    "a genuine guest absent from the sort tag is still added",
    augment_with_guests(["まらしぃ"], ["Hatsune Miku"], ["Marasy"]),
    ["まらしぃ", "Hatsune Miku"],
)
check(
    "no sort tag leaves behaviour unchanged",
    augment_with_guests(["まらしぃ", "のみこ"], ["nomico"]),
    ["まらしぃ", "のみこ", "nomico"],
)
# Weak separators are not applied to sort names: "Tyler, The Creator" is one
# name, so an unrelated guest "Tyler" must survive it.
check(
    "a sort name is not weak-split",
    augment_with_guests(["Kali Uchis"], ["Tyler"], ["Tyler, The Creator"]),
    ["Kali Uchis", "Tyler"],
)

_marasy = _cp(
    {
        "path": "in.flac",
        "tags": {
            "artist": ["まらしぃ feat. のみこ"],
            "albumartist": ["まらしぃ"],
            "album": ["Museum of marasy"],
            "title": ["Bad Apple!! feat. nomico"],
            "artistsort": ["Marasy feat. nomico"],
            "albumartistsort": ["Marasy"],
        },
    },
    vocabulary=set(),
)
check(
    "romanised guest does not reach the path",
    _marasy["rel_path"],
    "まらしぃ/Museum of marasy/まらしぃ and のみこ - Bad Apple!!.flac",
)
check("track artists are the two real artists", _marasy["track_artists"], ["まらしぃ", "のみこ"])

if FAILURES:
    print(f"FAILED ({len(FAILURES)}):")
    for f in FAILURES:
        print("  - " + f)
    sys.exit(1)
print("all checks passed")

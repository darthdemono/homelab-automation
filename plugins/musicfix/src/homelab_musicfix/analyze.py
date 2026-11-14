# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Audio-feature analysis: BPM, key, loudness/ReplayGain and the Essentia model
zoo (mood, danceability, genre, instrument, voice/gender, arousal/valence, ...).

The heavy lifting runs in the `musicfix-essentia` Docker container (the host is
on Python 3.14, which Essentia has no wheels for). This module is the host-side
orchestrator around it, in three phases:

    python -m homelab_musicfix.run analyze extract     # run the container over the library
    python -m homelab_musicfix.run analyze calibrate   # computed BPM/key vs existing tags
    python -m homelab_musicfix.run analyze tag          # map results -> tags (dry-run)
    python -m homelab_musicfix.run analyze tag --commit

`extract` writes one JSON per track under reports/analysis/ and is resumable:
a track whose JSON already exists is skipped, so an interrupted run just
continues. Because the model graphs take ~20s to load, the whole worklist is
handed to a single long-lived container rather than one spawn per file.

BPM and key follow the "fill missing, keep existing" rule the library was
curated under: a track that already carries a BPM/key/ReplayGain tag keeps it,
and `calibrate` reports how well the algorithm agrees with those known-good
values so the automatic ones can be trusted. The semantic model fields (mood_*,
genre_*, danceability, arousal/valence, ...) are new to the library and are
written wherever missing.

Every write is journalled to reports/analysis_tag_journal.jsonl with the exact
prior value of each field, so `tag --restore --commit` reverses it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import defaultdict

import mutagen
from mutagen.id3 import ID3, TBPM, TKEY, TMOO, TXXX
from mutagen.mp4 import MP4

from .settings import MUSIC_ROOT, REPO_ROOT, REPORTS

IMAGE_CPU = "musicfix-essentia:latest"
IMAGE_GPU = "musicfix-essentia:gpu"
MODELS_DIR = os.environ.get("MUSICFIX_MODELS", os.path.join(os.path.expanduser("~"), ".cache", "musicfix-models"))

# Two producers, two directories, merged at tag time:
#   analysis/        <- native AcousticBrainz binary: lowlevel/rhythm/tonal
#   analysis_models/ <- Docker TF zoo: genre/mood/danceability/arousal-valence/...
ANALYSIS_DIR = REPORTS / "analysis"
MODELS_ANALYSIS_DIR = REPORTS / "analysis_models"
JOURNAL = REPORTS / "analysis_tag_journal.jsonl"

# The native AcousticBrainz streaming_extractor_music binary (Essentia v2.1),
# statically linked, runs on the host. Same schema as the library's existing
# ab:lo:* tags, and being a plain subprocess it parallelises across cores. A
# central excerpt keeps it fast; whole track = set start/end to 0.
NATIVE_EXTRACTOR = str(REPO_ROOT / "streaming_extractor_music")
NATIVE_PROFILE = REPORTS / "ab_extract.profile"
EXCERPT_START = float(os.environ.get("MUSICFIX_EXCERPT_START", "30"))
EXCERPT_END = float(os.environ.get("MUSICFIX_EXCERPT_END", "90"))

# --------------------------------------------------------------------------- #
# key / camelot helpers
# --------------------------------------------------------------------------- #
_PITCH_FLAT = {"A#": "Bb", "C#": "Db", "D#": "Eb", "F#": "Gb", "G#": "Ab"}

# Camelot wheel: (pitch, scale) -> code. Pitches normalised to the sharp names
# Essentia emits.
_CAMELOT = {
    ("B", "major"): "1B",
    ("Ab", "minor"): "1A",
    ("G#", "minor"): "1A",
    ("F#", "major"): "2B",
    ("Gb", "major"): "2B",
    ("Eb", "minor"): "2A",
    ("D#", "minor"): "2A",
    ("Db", "major"): "3B",
    ("C#", "major"): "3B",
    ("Bb", "minor"): "3A",
    ("A#", "minor"): "3A",
    ("Ab", "major"): "4B",
    ("G#", "major"): "4B",
    ("F", "minor"): "4A",
    ("Eb", "major"): "5B",
    ("D#", "major"): "5B",
    ("C", "minor"): "5A",
    ("Bb", "major"): "6B",
    ("A#", "major"): "6B",
    ("G", "minor"): "6A",
    ("F", "major"): "7B",
    ("D", "minor"): "7A",
    ("C", "major"): "8B",
    ("A", "minor"): "8A",
    ("G", "major"): "9B",
    ("E", "minor"): "9A",
    ("D", "major"): "10B",
    ("B", "minor"): "10A",
    ("A", "major"): "11B",
    ("F#", "minor"): "11A",
    ("Gb", "minor"): "11A",
    ("E", "major"): "12B",
    ("Db", "minor"): "12A",
    ("C#", "minor"): "12A",
}


def camelot(key, scale):
    return _CAMELOT.get((key, scale)) or _CAMELOT.get((_PITCH_FLAT.get(key, key), scale))


def tkey_short(key, scale):
    """ID3 TKEY-style compact key, e.g. 'Abm' (minor) / 'Ab' (major)."""
    return key + ("m" if scale.startswith("min") else "")


# --------------------------------------------------------------------------- #
# JSON -> desired tags
# --------------------------------------------------------------------------- #
def _g(me, key):
    v = me.get(key)
    return v if v not in (None, "") else None


def desired_from_analysis(rec, *, have_bpm, have_key, have_rg):
    """Map one analysis JSON to {TAGKEY: "value"}.

    `have_*` say whether the file already carries that curated tag; when it
    does, the corresponding computed value is *not* emitted (fill-missing).
    Model/semantic fields are always emitted -- they are new to the library.
    """
    out = {}
    me = rec.get("music_extractor") or {}
    models = rec.get("models") or {}

    # --- BPM (fill-missing) ---
    bpm = _g(me, "rhythm.bpm")
    if bpm is not None and not have_bpm:
        out["BPM"] = str(int(round(float(bpm))))

    # --- key (fill-missing) --- AcousticBrainz binary emits tonal.key_key /
    # tonal.key_scale; the older essentia-python path used tonal.key_edma.*.
    kk = _g(me, "tonal.key_key") or _g(me, "tonal.key_edma.key")
    ks = _g(me, "tonal.key_scale") or _g(me, "tonal.key_edma.scale")
    if kk and ks and not have_key:
        out["KEY"] = f"{kk} {ks}"
        out["INITIALKEY"] = f"{kk} {ks}"
        out["TKEY_SHORT"] = tkey_short(kk, ks)  # consumed by the id3 writer
        cam = camelot(kk, ks)
        if cam:
            out["CAMELOT"] = cam

    # --- ReplayGain (fill-missing) --- Essentia's replay_gain follows the
    # ReplayGain reference the library's existing values already use.
    rg = _g(me, "metadata.audio_properties.replay_gain")
    if rg is not None and not have_rg:
        out["REPLAYGAIN_TRACK_GAIN"] = f"{float(rg):.2f} dB"

    # --- always-on descriptors (new to the library) ---
    def put(k, v, nd=4):
        if v is not None:
            out[k] = f"{float(v):.{nd}f}" if isinstance(v, (int, float)) else str(v)

    put("DYNAMIC_COMPLEXITY", _g(me, "lowlevel.dynamic_complexity"))
    put("LOUDNESS_EBU128", _g(me, "lowlevel.loudness_ebu128.integrated"), 2)
    put("ONSET_RATE", _g(me, "rhythm.onset_rate"))
    put("TUNING_FREQUENCY", _g(me, "tonal.tuning_frequency"), 1)
    put("BEATS_COUNT", _g(me, "rhythm.beats_count"), 0)

    # --- model heads ---
    def head_value(task):
        h = models.get(task)
        return h.get("value") if isinstance(h, dict) else None

    for task, tag in [
        ("danceability", "DANCEABILITY"),
        ("mood_happy", "MOOD_HAPPY"),
        ("mood_sad", "MOOD_SAD"),
        ("mood_aggressive", "MOOD_AGGRESSIVE"),
        ("mood_relaxed", "MOOD_RELAXED"),
        ("mood_party", "MOOD_PARTY"),
        ("mood_acoustic", "MOOD_ACOUSTIC"),
        ("mood_electronic", "MOOD_ELECTRONIC"),
        ("voice_instrumental", "VOICE_INSTRUMENTAL"),
        ("gender", "GENDER"),
        ("tonal_atonal", "TONAL_ATONAL"),
        ("approachability", "APPROACHABILITY"),
        ("engagement", "ENGAGEMENT"),
    ]:
        put(tag, head_value(task))

    # valence / arousal -- average the three regression models that agree on a
    # 1-9 scale, then derive a 1-10 EnergyLevel from arousal.
    va = [models.get(t) for t in ("deam", "emomusic", "muse")]
    valences = [m["valence"] for m in va if isinstance(m, dict) and "valence" in m]
    arousals = [m["arousal"] for m in va if isinstance(m, dict) and "arousal" in m]
    if valences:
        out["VALENCE"] = f"{sum(valences) / len(valences):.3f}"
    if arousals:
        a = sum(arousals) / len(arousals)
        out["AROUSAL"] = f"{a:.3f}"
        out["ENERGYLEVEL"] = str(max(1, min(10, int(round(a / 9 * 10)))))

    # top categorical labels
    def top_label(task):
        h = models.get(task)
        if isinstance(h, dict) and h.get("top"):
            return h["top"][0]["label"]
        return None

    gd = top_label("genre_discogs400")
    if gd:
        out["GENRE_DISCOGS400"] = gd
    jg = top_label("mtg_jamendo_genre")
    if jg:
        out["MTG_JAMENDO_GENRE"] = jg
    mt = top_label("mtg_jamendo_moodtheme")
    if mt:
        out["MOOD"] = mt.capitalize()  # standard MOOD tag Navidrome/Jellyfin read
    inst = top_label("mtg_jamendo_instrument")
    if inst:
        out["INSTRUMENT"] = inst

    return out


# --------------------------------------------------------------------------- #
# generic multi-format tag read / write
# --------------------------------------------------------------------------- #
# id3 frames with a native home; everything else becomes a TXXX description.
_ID3_NATIVE = {"BPM": ("TBPM", TBPM), "TKEY_SHORT": ("TKEY", TKEY), "MOOD": ("TMOO", TMOO)}


def read_current(mf, keys):
    """Current value (str) of each key for this file, or None if absent."""
    cur = {}
    tags = mf.tags
    for k in keys:
        val = None
        if isinstance(tags, ID3):
            if k in _ID3_NATIVE:
                fr = tags.get(_ID3_NATIVE[k][0])
                val = str(fr.text[0]) if fr and fr.text else None
            else:
                fr = tags.get(f"TXXX:{k}")
                val = str(fr.text[0]) if fr and fr.text else None
        elif isinstance(mf, MP4):
            atom = f"----:com.apple.iTunes:{k}" if k not in ("BPM",) else "tmpo"
            if atom in tags:
                v = tags[atom][0]
                val = (
                    str(int(v))
                    if atom == "tmpo"
                    else (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
                )
        else:  # vorbis
            if k in tags:
                val = tags[k][0]
        cur[k] = val
    return cur


def apply_tags(mf, want):
    """Write the desired tags. `want` maps TAGKEY -> str value."""
    tags = mf.tags
    if isinstance(tags, ID3):
        for k, v in want.items():
            if k == "TKEY_SHORT":
                tags.setall("TKEY", [TKEY(encoding=3, text=[v])])
            elif k in _ID3_NATIVE:
                name, cls = _ID3_NATIVE[k]
                tags.setall(name, [cls(encoding=3, text=[v])])
            else:
                tags.setall(f"TXXX:{k}", [TXXX(encoding=3, desc=k, text=[v])])
        mf.save(v2_version=4)
    elif isinstance(mf, MP4):
        for k, v in want.items():
            if k == "BPM":
                try:
                    tags["tmpo"] = [int(v)]
                    continue
                except ValueError:
                    pass
            if k == "TKEY_SHORT":
                k = "INITIALKEY"
            tags[f"----:com.apple.iTunes:{k}"] = [v.encode("utf-8")]
        mf.save()
    else:  # vorbis (flac / ogg / opus)
        for k, v in want.items():
            if k == "TKEY_SHORT":
                continue  # vorbis keeps the full "Ab minor" in KEY/INITIALKEY
            tags[k] = [v]
        mf.save()


def write_file(path, want):
    """Returns (before_dict, error_or_None). `want` already filtered to changes."""
    try:
        mf = mutagen.File(path)
    except Exception as exc:  # noqa: BLE001
        return {}, f"open: {type(exc).__name__}: {exc}"
    if mf is None:
        return {}, "unrecognised format"
    try:
        if mf.tags is None:
            mf.add_tags()
    except Exception as exc:  # noqa: BLE001
        return {}, f"add_tags: {type(exc).__name__}: {exc}"
    before = read_current(mf, list(want.keys()))
    try:
        apply_tags(mf, want)
    except Exception as exc:  # noqa: BLE001
        return before, f"save: {type(exc).__name__}: {exc}"
    return before, None


# --------------------------------------------------------------------------- #
# existing-tag detection (for fill-missing)
# --------------------------------------------------------------------------- #
def existing_flags(path):
    """(have_bpm, have_key, have_rg, existing_bpm) read straight off the file."""
    try:
        mf = mutagen.File(path)
    except Exception:  # noqa: BLE001
        return False, False, False, None
    if mf is None or mf.tags is None:
        return False, False, False, None
    keys = {str(k).lower() for k in mf.tags.keys()}

    def has(*subs):
        return any(any(s in k for s in subs) for k in keys)

    have_bpm = has("bpm") or "tbpm" in keys
    have_key = "key" in keys or "initialkey" in keys or "tkey" in keys or any(k.endswith(":initialkey") for k in keys)
    have_rg = has("replaygain_track_gain") or has("r128_track_gain")

    ebpm = None
    for k in mf.tags.keys():
        kl = str(k).lower()
        if kl in ("bpm", "tbpm") or kl.endswith(":bpm"):
            try:
                raw = mf.tags[k]
                raw = raw[0] if isinstance(raw, list) else raw
                ebpm = float(str(getattr(raw, "text", [raw])[0] if hasattr(raw, "text") else raw))
            except Exception:  # noqa: BLE001
                pass
            break
    return have_bpm, have_key, have_rg, ebpm


# --------------------------------------------------------------------------- #
# phases
# --------------------------------------------------------------------------- #
def json_path_for(rel):
    return ANALYSIS_DIR / (rel + ".json")


def models_json_path_for(rel):
    return MODELS_ANALYSIS_DIR / (rel + ".json")


def flatten_ab(obj, prefix=""):
    """Flatten the AcousticBrainz nested JSON to dotted scalar keys.

    Keeps numbers and strings (round floats); drops arrays/histograms. Yields
    e.g. rhythm.bpm, tonal.key_key, metadata.audio_properties.replay_gain,
    lowlevel.spectral_centroid.mean -- the same dotted names the tag mapper and
    the old essentia-python path used.
    """
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten_ab(v, key + "."))
        elif isinstance(v, bool):
            continue
        elif isinstance(v, (int, float)):
            out[key] = round(float(v), 6)
        elif isinstance(v, str):
            out[key] = v
        # lists (histograms, mfcc means, chords_histogram) are dropped
    return out


def load_merged(rel):
    """Combine the native lowlevel json and the TF-models json for one track."""
    rec = {}
    jp = json_path_for(rel)
    if jp.exists():
        try:
            rec["music_extractor"] = json.load(open(jp)).get("music_extractor", {})
        except Exception:  # noqa: BLE001
            pass
    mp = models_json_path_for(rel)
    if mp.exists():
        try:
            rec["models"] = json.load(open(mp)).get("models", {})
        except Exception:  # noqa: BLE001
            pass
    rec["ok"] = bool(rec.get("music_extractor") or rec.get("models"))
    return rec


def load_inventory(inv):
    return [json.loads(l) for l in open(inv, encoding="utf-8") if l.strip()]


def _write_native_profile():
    lines = ["analysisSampleRate: 44100", "requireMbid: false"]
    if EXCERPT_END > EXCERPT_START >= 0 and EXCERPT_END > 0:
        lines += [f"startTime: {EXCERPT_START}", f"endTime: {EXCERPT_END}"]
    NATIVE_PROFILE.parent.mkdir(parents=True, exist_ok=True)
    NATIVE_PROFILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _native_one(rel, root):
    """Run the AcousticBrainz binary on one track; flatten -> json_path_for."""
    abspath = os.path.join(root, rel)
    outp = json_path_for(rel)
    outp.parent.mkdir(parents=True, exist_ok=True)
    raw = str(outp) + ".ab"
    try:
        r = subprocess.run(
            [NATIVE_EXTRACTOR, abspath, raw, str(NATIVE_PROFILE)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if r.returncode != 0 or not os.path.exists(raw):
            return rel, False
        flat = flatten_ab(json.load(open(raw)))
        tmp = str(outp) + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"ok": bool(flat), "music_extractor": flat}, fh, ensure_ascii=False)
        os.replace(tmp, outp)
        return rel, bool(flat)
    except Exception:  # noqa: BLE001
        return rel, False
    finally:
        if os.path.exists(raw):
            os.remove(raw)


def _missing_bpmkey(rel, root):
    """(rel, True) if the file lacks bpm or key -- i.e. worth analysing."""
    have_bpm, have_key, _, _ = existing_flags(os.path.join(root, rel))
    return rel, not (have_bpm and have_key)


def phase_extract_fast(args):
    """Fast BPM/key-only pass: analyse ONLY files missing bpm or key.

    Most libraries already carry bpm/key on the bulk of their files (here ~78%),
    so the honest job is small. Runs the essentia container in --bpmkey mode
    (no MusicExtractor, no model zoo), thread-parallel.
    """
    from concurrent.futures import ThreadPoolExecutor

    records = [r for r in load_inventory(args.inventory) if not r.get("error")]
    if args.limit:
        records = records[: args.limit]

    # Skip files that already have an analysis json before touching their tags.
    candidates = [r["path"] for r in records if args.force or not json_path_for(r["path"]).exists()]
    print(
        f"[analyze] scanning tags of {len(candidates)} files for missing bpm/key "
        f"({len(records) - len(candidates)} already analysed) ...",
        flush=True,
    )
    pending = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for rel, miss in pool.map(lambda rel: _missing_bpmkey(rel, args.root), candidates):
            if miss:
                pending.append(rel)
    print(f"[analyze] {len(pending)} files miss bpm or key (the rest already have both — skipped)")
    if not pending or args.dry_run:
        return 0

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    wl = REPORTS / "bpmkey_worklist.tsv"
    wl.write_text("\n".join(f"/music/{r}\t/out/{r}.json" for r in pending) + "\n", encoding="utf-8")
    cmd = [
        "docker",
        "run",
        "--rm",  # no fixed --name: retries must not collide
        "-v",
        f"{MUSIC_ROOT}:/music:ro",
        "-v",
        f"{ANALYSIS_DIR}:/out",
        "-v",
        f"{wl}:/wl.tsv:ro",
        IMAGE_CPU,
        "--worklist",
        "/wl.tsv",
        "--bpmkey",
        "--workers",
        str(args.workers),
    ]
    if args.force:
        cmd.append("--force")  # the container skips on output existence too
    print(f"[analyze] bpmkey container over {len(pending)} files, {args.workers} threads ...")
    return subprocess.call(cmd)


def phase_extract(args):
    """Native AcousticBrainz binary over the library, parallel across cores.

    Being a plain subprocess it parallelises cleanly (unlike the Docker path),
    so N workers ≈ N× throughput. Produces the full lowlevel/rhythm/tonal set in
    the library's own ab:lo:* schema. `--fast` diverts to the bpm/key-only pass.
    """
    if getattr(args, "fast", False):
        return phase_extract_fast(args)
    if not os.path.exists(NATIVE_EXTRACTOR):
        print(f"[analyze] native extractor not found at {NATIVE_EXTRACTOR}")
        return 1
    records = load_inventory(args.inventory)
    records = [r for r in records if not r.get("error")]
    if args.limit:
        records = records[: args.limit]

    pending = [r["path"] for r in records if args.force or not json_path_for(r["path"]).exists()]
    print(
        f"[analyze] native extract: {len(pending)} files need lowlevel "
        f"analysis ({len(records) - len(pending)} already done)"
    )
    if not pending or args.dry_run:
        if args.dry_run:
            print(f"  would run {NATIVE_EXTRACTOR} with {args.workers} workers, excerpt {EXCERPT_START}-{EXCERPT_END}s")
        return 0

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    _write_native_profile()
    ok = failed = 0
    t0 = time.time()
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, (rel, good) in enumerate(pool.map(lambda p: _native_one(p, args.root), pending), 1):
            ok += good
            failed += not good
            if i % 200 == 0:
                rate = i / max(time.time() - t0, 1e-9)
                print(
                    f"[analyze] {i}/{len(pending)}  {rate:.1f}/s  eta {(len(pending) - i) / max(rate, 1e-9) / 60:.0f}m",
                    flush=True,
                )
    print(f"[analyze] native extract done: {ok} ok, {failed} failed in {(time.time() - t0) / 60:.1f}m")
    return 0


def phase_models(args):
    """Docker TF model zoo (genre/mood/danceability/arousal-valence/...).

    Runs the container in --no-descriptors mode (the native binary handles
    lowlevel), writing only the models section to analysis_models/.
    """
    records = load_inventory(args.inventory)
    records = [r for r in records if not r.get("error")]
    if args.limit:
        records = records[: args.limit]

    worklist = [
        f"/music/{r['path']}\t/out/{r['path']}.json"
        for r in records
        if args.force or not models_json_path_for(r["path"]).exists()
    ]
    print(f"[analyze] models: {len(worklist)} files need the TF zoo ({len(records) - len(worklist)} already done)")
    if not worklist:
        return 0

    MODELS_ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    wl_path = REPORTS / "models_worklist.tsv"
    wl_path.write_text("\n".join(worklist) + "\n", encoding="utf-8")
    cmd = ["docker", "run", "--rm"]  # no fixed --name: retries must not collide
    if args.gpu:
        cmd += ["--gpus", "all"]
    cmd += [
        "-v",
        f"{MUSIC_ROOT}:/music:ro",
        "-v",
        f"{MODELS_DIR}:/models",
        "-v",
        f"{MODELS_ANALYSIS_DIR}:/out",
        "-v",
        f"{wl_path}:/wl.tsv:ro",
        IMAGE_GPU if args.gpu else IMAGE_CPU,
        "--worklist",
        "/wl.tsv",
        "--no-descriptors",
    ]
    # The container skips any job whose output already exists, so --force has to
    # reach it too -- filtering only the worklist here leaves it a no-op.
    if args.force:
        cmd.append("--force")
    print(f"[analyze] launching {'GPU' if args.gpu else 'CPU'} model container over {len(worklist)} files ...")
    if args.dry_run:
        print("  " + " ".join(cmd))
        return 0
    return subprocess.call(cmd)


def phase_calibrate(args):
    records = load_inventory(args.inventory)
    root = args.root
    exact = within1 = within2pct = halfdouble = wayoff = compared = 0
    key_have = 0
    out = open(REPORTS / "bpm_calibration.jsonl", "w", encoding="utf-8")
    for r in records:
        if r.get("error"):
            continue
        rel = r["path"]
        jp = json_path_for(rel)
        if not jp.exists():
            continue
        try:
            rec = json.load(open(jp))
        except Exception:  # noqa: BLE001
            continue
        cbpm = (rec.get("music_extractor") or {}).get("rhythm.bpm")
        if cbpm is None:
            continue
        have_bpm, have_key, _, ebpm = existing_flags(os.path.join(root, rel))
        if have_key:
            key_have += 1
        if ebpm and ebpm > 0:
            compared += 1
            c = float(cbpm)
            diff = abs(c - ebpm)
            rel_ok = diff <= 0.02 * ebpm
            if diff < 0.5:
                exact += 1
            elif diff <= 1:
                within1 += 1
            elif rel_ok:
                within2pct += 1
            elif abs(c - 2 * ebpm) <= 2 or abs(c - 0.5 * ebpm) <= 2:
                halfdouble += 1
            else:
                wayoff += 1
            out.write(
                json.dumps(
                    {"path": rel, "computed": round(c, 2), "existing": round(ebpm, 2), "diff": round(diff, 2)},
                    ensure_ascii=False,
                )
                + "\n"
            )
    out.close()
    agree = exact + within1 + within2pct
    print(f"[analyze] BPM calibration over {compared} tracks with an existing BPM tag:")
    if compared:
        print(f"    exact (<0.5)      {exact:5d}  {exact / compared:5.1%}")
        print(f"    within 1 bpm      {within1:5d}  {within1 / compared:5.1%}")
        print(f"    within 2%         {within2pct:5d}  {within2pct / compared:5.1%}")
        print(f"    -> agree          {agree:5d}  {agree / compared:5.1%}")
        print(f"    half/double tempo {halfdouble:5d}  {halfdouble / compared:5.1%}")
        print(f"    way off           {wayoff:5d}  {wayoff / compared:5.1%}")
    print("    (details -> reports/bpm_calibration.jsonl)")
    return 0


def phase_tag(args):
    if args.restore:
        return restore(args.root, commit=args.commit)

    records = load_inventory(args.inventory)
    records = [r for r in records if not r.get("error")]
    if args.limit:
        records = records[: args.limit]

    # Resume: skip files already written successfully in a prior (possibly
    # interrupted) run, so a re-run only does what is left.
    done = set()
    if JOURNAL.exists() and not getattr(args, "force", False):
        for line in open(JOURNAL, encoding="utf-8"):
            if line.strip():
                e = json.loads(line)
                if not e.get("error"):
                    done.add(e["path"])

    changes = []  # (rel, want_filtered)
    missing_json = 0
    for r in records:
        rel = r["path"]
        if rel in done:
            continue
        rec = load_merged(rel)  # native lowlevel + TF models, whichever exist
        if not rec.get("ok"):
            missing_json += 1
            continue
        path = os.path.join(args.root, rel)
        have_bpm, have_key, have_rg, _ = existing_flags(path)
        want = desired_from_analysis(rec, have_bpm=have_bpm, have_key=have_key, have_rg=have_rg)
        if want:
            changes.append((rel, want))

    print(
        f"[analyze] {'COMMIT' if args.commit else 'DRY-RUN'} tag: "
        f"{len(changes)} files to update  ({missing_json} without analysis json yet)"
    )
    for rel, want in changes[:8]:
        keys = ", ".join(sorted(want.keys()))
        print(f"  {rel}\n      {keys}")
    if len(changes) > 8:
        print(f"  ... and {len(changes) - 8} more")

    if not args.commit:
        print("[analyze] nothing written; re-run with --commit")
        return 0

    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    with open(JOURNAL, "a", encoding="utf-8") as jf:
        for rel, want in changes:
            path = os.path.join(args.root, rel)
            before, err = write_file(path, want)
            jf.write(
                json.dumps(
                    {"ts": time.time(), "path": rel, "before": before, "after": want, "error": err}, ensure_ascii=False
                )
                + "\n"
            )
            if err:
                failed += 1
                if failed <= 20:
                    print(f"  ERROR {rel}: {err}")
            else:
                ok += 1
    print(f"[analyze] {ok} files updated, {failed} failed")
    return 0


def restore(root, *, commit):
    if not JOURNAL.exists():
        print(f"[analyze] no journal at {JOURNAL}")
        return 1
    entries = [json.loads(l) for l in open(JOURNAL, encoding="utf-8") if l.strip()]
    # earliest record per file holds the original state of each key it touched
    original = {}
    for e in entries:
        if e.get("error"):
            continue
        cur = original.setdefault(e["path"], {})
        for k, v in e["before"].items():
            cur.setdefault(k, v)  # keep the earliest-seen prior value
    print(f"[analyze] {'COMMIT' if commit else 'DRY-RUN'} restore: {len(original)} files")
    if not commit:
        return 0
    ok = failed = 0
    for rel, before in original.items():
        path = os.path.join(root, rel)
        try:
            mf = mutagen.File(path)
            if mf is None:
                raise ValueError("unrecognised")
            if mf.tags is None:
                mf.add_tags()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {rel}: {exc}")
            continue
        set_back = {k: v for k, v in before.items() if v is not None}
        # delete keys that did not exist before
        _delete_keys(mf, [k for k, v in before.items() if v is None])
        try:
            if set_back:
                apply_tags(mf, set_back)  # applies deletions + writes, saves once
            else:
                mf.save()
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {rel}: {exc}")
    print(f"[analyze] {ok} restored, {failed} failed")
    return 0


def _delete_keys(mf, keys):
    tags = mf.tags
    for k in keys:
        try:
            if isinstance(tags, ID3):
                name = _ID3_NATIVE[k][0] if k in _ID3_NATIVE else f"TXXX:{k}"
                if k == "TKEY_SHORT":
                    name = "TKEY"
                tags.delall(name)
            elif isinstance(mf, MP4):
                atom = "tmpo" if k == "BPM" else f"----:com.apple.iTunes:{k}"
                tags.pop(atom, None)
            else:
                # Vorbis comments are stored lowercased and matched
                # case-insensitively, so delete by folded comparison.
                low = k.lower()
                for existing in [e for e in tags.keys() if e.lower() == low]:
                    del tags[existing]
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(description="Essentia audio-feature analysis + tagging.")
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--limit", type=int, default=0)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="native AcousticBrainz binary -> lowlevel/rhythm/tonal (fast, parallel)")
    pe.add_argument("--workers", type=int, default=4, help="parallel workers")
    pe.add_argument(
        "--fast", action="store_true", help="bpm/key/ReplayGain only, and ONLY for files missing them (fast)"
    )
    pe.add_argument("--force", action="store_true", help="re-analyse even if a json exists")
    pe.add_argument("--dry-run", action="store_true", help="report counts, run nothing")
    pe.set_defaults(func=phase_extract)

    pm = sub.add_parser("models", help="Docker TF model zoo -> genre/mood/danceability/... ")
    pm.add_argument("--gpu", action="store_true", help="use musicfix-essentia:gpu with --gpus all")
    pm.add_argument("--force", action="store_true", help="re-analyse even if a json exists")
    pm.add_argument("--dry-run", action="store_true", help="print the docker command, run nothing")
    pm.set_defaults(func=phase_models)

    pc = sub.add_parser("calibrate", help="computed BPM vs existing BPM tags")
    pc.set_defaults(func=phase_calibrate)

    pt = sub.add_parser("tag", help="map analysis results to tags (dry-run default)")
    pt.add_argument("--commit", action="store_true")
    pt.add_argument("--restore", action="store_true")
    pt.add_argument(
        "--force",
        action="store_true",
        help="re-tag a file the journal already lists as done. The "
        "journal is keyed by path, so when merge replaces a "
        "library file the new recording inherits the old one's "
        "'done' mark and is silently skipped.",
    )
    pt.set_defaults(func=phase_tag)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

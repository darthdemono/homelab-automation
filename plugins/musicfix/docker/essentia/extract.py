#!/usr/bin/env python3
"""Essentia analysis runner (runs *inside* the container).

Given a worklist of audio files it emits one JSON per file holding:

  * every scalar descriptor MusicExtractor produces -- bpm, key/scale/strength,
    chords, loudness (EBU-128 + replay_gain), dynamic-complexity, onset-rate,
    tuning, spectral, zero-crossing, beats -- i.e. the classic AcousticBrainz
    low/rhythm/tonal set;
  * the TensorFlow model zoo predictions -- genre (discogs400 + jamendo),
    mood_*, danceability, voice/instrumental, gender, instruments, top-50 tags,
    approachability/engagement, and arousal/valence (deam/emomusic/muse).

Models load once and stay resident for the whole worklist -- per-file container
spawns would pay the multi-second graph load 11k times over. Downloads are
cached under $MUSICFIX_MODELS and are best-effort: a model that cannot be
fetched is skipped with a warning, never fatal, so a partial zoo still runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

import numpy as np

MODELS_DIR = os.environ.get("MUSICFIX_MODELS", "/models")
MANIFEST = os.path.join(os.path.dirname(__file__), "models.json")


def log(*a):
    print("[extract]", *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# model fetching
# --------------------------------------------------------------------------- #
def _download(url, dest):
    tmp = dest + ".part"
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as fh:
        fh.write(r.read())
    os.replace(tmp, dest)


def fetch(base_url, path_stem, ext, versions=(1, 2, 3)):
    """Return a local path for <path_stem>-<v>.<ext>, trying version suffixes.

    Cached under MODELS_DIR mirroring the remote layout. Returns None if every
    candidate 404s (or the network is down) -- the caller treats that as
    "this model is unavailable" and moves on.
    """
    for v in versions:
        rel = f"{path_stem}-{v}.{ext}"
        dest = os.path.join(MODELS_DIR, rel)
        if os.path.exists(dest):
            return dest
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        url = base_url + rel
        try:
            _download(url, dest)
            log(f"downloaded {rel}")
            return dest
        except Exception:  # noqa: BLE001
            continue
    log(f"UNAVAILABLE {path_stem}.{ext} (tried v{list(versions)})")
    return None


# --------------------------------------------------------------------------- #
# model loading
# --------------------------------------------------------------------------- #
class Zoo:
    """Holds resident embedding + classifier graphs."""

    def __init__(self, manifest):
        import essentia.standard as es

        self.es = es
        self.base = manifest["base_url"]
        self.embeddings = {}  # key -> (algo_instance, meta)
        self.classifiers = []  # list of dicts

        for emb in manifest["embeddings"]:
            pb = fetch(self.base, emb["path"], "pb")
            if not pb:
                continue
            algo_cls = getattr(es, emb["algo"])
            try:
                inst = algo_cls(graphFilename=pb, output=emb["output"])
            except Exception as exc:  # noqa: BLE001
                log(f"embedding {emb['key']} failed to load: {exc}")
                continue
            self.embeddings[emb["key"]] = inst
            log(f"embedding ready: {emb['key']}")

        for clf in manifest["classifiers"]:
            meta_path = fetch(self.base, clf["path"], "json")
            pb_path = fetch(self.base, clf["path"], "pb")
            if not (meta_path and pb_path):
                continue
            try:
                meta = json.load(open(meta_path))
            except Exception:
                meta = {}
            emb_key = self._embedding_for(meta)
            if emb_key not in self.embeddings:
                log(f"skip {clf['task']}: needs embedding {emb_key!r} which is unavailable")
                continue
            in_node = self._schema_node(meta, "inputs", default="serving_default_model_Placeholder")
            out_node = self._schema_node(meta, "outputs", default="PartitionedCall")
            try:
                predict = es.TensorflowPredict2D(graphFilename=pb_path, input=in_node, output=out_node)
            except Exception as exc:  # noqa: BLE001
                log(f"classifier {clf['task']} failed to load: {exc}")
                continue
            self.classifiers.append(
                {
                    "task": clf["task"],
                    "kind": clf.get("kind", "labels"),
                    "positive": clf.get("positive"),
                    "classes": meta.get("classes") or [],
                    "embedding": emb_key,
                    "predict": predict,
                }
            )
            log(f"classifier ready: {clf['task']} on {emb_key}")

    @staticmethod
    def _embedding_for(meta):
        name = ""
        try:
            name = meta["inference"]["embedding_model"]["model_name"].lower()
        except Exception:
            pass
        if "effnet" in name or "discogs" in name:
            return "discogs-effnet"
        return "msd-musicnn"

    @staticmethod
    def _schema_node(meta, side, default):
        try:
            items = meta["schema"][side]
            if side == "outputs":
                for it in items:
                    if it.get("output_purpose") in ("predictions", ""):
                        return it["name"]
            return items[0]["name"]
        except Exception:
            return default

    def embed(self, audio16k):
        out = {}
        for key, inst in self.embeddings.items():
            try:
                out[key] = inst(audio16k)
            except Exception as exc:  # noqa: BLE001
                log(f"embedding {key} failed on file: {exc}")
        return out

    def classify(self, embeds):
        result = {}
        for clf in self.classifiers:
            emb = embeds.get(clf["embedding"])
            if emb is None:
                continue
            try:
                preds = clf["predict"](emb)
            except Exception as exc:  # noqa: BLE001
                log(f"{clf['task']} predict failed: {exc}")
                continue
            avg = np.mean(np.asarray(preds), axis=0)
            result[clf["task"]] = self._shape(clf, avg)
        return result

    @staticmethod
    def _shape(clf, avg):
        avg = np.asarray(avg, dtype=float).ravel()
        classes = clf["classes"]
        kind = clf["kind"]
        if kind == "binary" and classes:
            pos = clf.get("positive")
            idx = classes.index(pos) if pos in classes else 0
            return {"value": round(float(avg[idx]), 5), "label": classes[idx]}
        if kind == "regression":
            return {"value": round(float(avg[0]), 5)}
        if kind == "valence_arousal":
            names = classes if len(classes) == len(avg) else ["valence", "arousal"][: len(avg)]
            return {n: round(float(v), 5) for n, v in zip(names, avg)}
        # multi-label: keep the strongest tags
        pairs = sorted(zip(classes, avg), key=lambda kv: kv[1], reverse=True)
        return {"top": [{"label": c, "p": round(float(p), 5)} for c, p in pairs[:8]]}


# --------------------------------------------------------------------------- #
# per-file analysis
# --------------------------------------------------------------------------- #
# Analyse a central excerpt rather than the whole track: BPM/key/mood are
# near-stationary, and both the embeddings and the beat tracker cost scale with
# duration, which is where the time goes. 0 = whole track.
EXCERPT_SEC = float(os.environ.get("MUSICFIX_EXCERPT_SEC", "60"))


def central_excerpt(audio, sr):
    if EXCERPT_SEC <= 0:
        return audio
    want = int(EXCERPT_SEC * sr)
    if len(audio) <= want:
        return audio
    start = (len(audio) - want) // 2
    return audio[start : start + want]


def fast_descriptors(es, audio):
    """BPM / key / loudness / rhythm via dedicated Essentia algorithms.

    Takes an already-loaded 44.1 kHz mono excerpt. Much cheaper than
    MusicExtractor's full 135-descriptor pass while still yielding every scalar
    we tag on, keyed with the same dotted names so the host mapper is unchanged.
    Each algorithm degrades independently.
    """
    out = {}

    def guard(fn):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log(f"descriptor failed on {os.path.basename(path)}: {exc}")

    def bpm():
        bpm_v, beats, conf, _, _ = es.RhythmExtractor2013(method="degara")(audio)
        out["rhythm.bpm"] = round(float(bpm_v), 4)
        out["rhythm.beats_count"] = int(len(beats))
        out["rhythm.beats_confidence"] = round(float(conf), 4)

    guard(bpm)

    def key():
        k, scale, strength = es.KeyExtractor(profileType="edma")(audio)
        out["tonal.key_edma.key"] = k
        out["tonal.key_edma.scale"] = scale
        out["tonal.key_edma.strength"] = round(float(strength), 4)

    guard(key)

    def rg():
        out["metadata.audio_properties.replay_gain"] = round(float(es.ReplayGain()(audio)), 4)

    guard(rg)

    def dyn():
        dc, _ = es.DynamicComplexity()(audio)
        out["lowlevel.dynamic_complexity"] = round(float(dc), 4)

    guard(dyn)

    def onset():
        _, rate = es.OnsetRate()(audio)
        out["rhythm.onset_rate"] = round(float(rate), 4)

    guard(onset)

    def dance():
        d = es.Danceability()(audio)
        out["rhythm.danceability"] = round(float(d[0]), 4)

    guard(dance)

    def tuning():
        tf = es.TuningFrequencyExtractor()(audio)
        vals = np.asarray(tf, dtype=float)
        if vals.size:
            out["tonal.tuning_frequency"] = round(float(vals.mean()), 2)

    guard(tuning)

    return out


def bpmkey_one(es, path):
    """Fast path: BPM + key + ReplayGain only, on a central excerpt.

    No MusicExtractor, no model zoo -- the ~20x cheaper job most libraries want.
    Keyed the same as the full path so the host tag mapper is unchanged.
    """
    a = es.MonoLoader(filename=path, sampleRate=44100)()
    n = len(a)
    if n > 90 * 44100:  # central 30-90 s excerpt
        a = a[30 * 44100 : 90 * 44100]
    out = {}
    try:
        bpm, _b, conf, _e, _i = es.RhythmExtractor2013(method="degara")(a)
        out["rhythm.bpm"] = round(float(bpm), 4)
        out["rhythm.beats_confidence"] = round(float(conf), 4)
    except Exception as exc:  # noqa: BLE001
        log(f"bpm failed {os.path.basename(path)}: {exc}")
    try:
        k, scale, strength = es.KeyExtractor()(a)
        out["tonal.key_key"] = k
        out["tonal.key_scale"] = scale
        out["tonal.key_strength"] = round(float(strength), 4)
    except Exception as exc:  # noqa: BLE001
        log(f"key failed {os.path.basename(path)}: {exc}")
    try:
        out["metadata.audio_properties.replay_gain"] = round(float(es.ReplayGain()(a)), 4)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": bool(out), "music_extractor": out}


def run_bpmkey(es, jobs, workers, force=False):
    """Parallel BPM/key over a worklist. Essentia releases the GIL in its C++
    algorithms, so a thread pool genuinely parallelises."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    done = skipped = failed = 0
    lock = threading.Lock()
    t0 = time.time()

    def work(job):
        nonlocal done, skipped, failed
        inp, outp = job
        if os.path.exists(outp) and not force:
            with lock:
                skipped += 1
            return
        try:
            rec = bpmkey_one(es, inp)
        except Exception as exc:  # noqa: BLE001
            rec = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        tmp = outp + ".part"
        with open(tmp, "w") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        os.replace(tmp, outp)
        with lock:
            done += 1
            if not rec.get("ok"):
                failed += 1
            d = done
        if d % 100 == 0:
            rate = d / max(time.time() - t0, 1e-9)
            log(f"{d}  {rate:.1f}/s  eta {(len(jobs) - d) / max(rate, 1e-9) / 60:.0f}m")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    log(f"bpmkey complete: {done} analysed ({failed} degraded), {skipped} already done")
    return 0


def analyze_one(zoo, path, descriptors=True):
    es = zoo.es
    rec = {"ok": False}

    # With the native AcousticBrainz binary handling lowlevel/rhythm/tonal, the
    # container is usually run --no-descriptors: it only needs the 16 kHz signal
    # for the TF embeddings, so it skips the 44.1 kHz load entirely.
    try:
        if descriptors:
            audio44 = central_excerpt(es.MonoLoader(filename=path, sampleRate=44100)(), 44100)
            audio16 = es.Resample(inputSampleRate=44100, outputSampleRate=16000)(audio44)
        else:
            audio16 = central_excerpt(es.MonoLoader(filename=path, sampleRate=16000)(), 16000)
    except Exception as exc:  # noqa: BLE001
        rec["decode_error"] = f"{type(exc).__name__}: {exc}"
        return rec

    if descriptors:
        try:
            rec["music_extractor"] = fast_descriptors(es, audio44)
        except Exception as exc:  # noqa: BLE001
            rec["music_extractor_error"] = f"{type(exc).__name__}: {exc}"

    if zoo.embeddings and zoo.classifiers:
        try:
            embeds = zoo.embed(audio16)
            rec["models"] = zoo.classify(embeds)
        except Exception as exc:  # noqa: BLE001
            rec["models_error"] = f"{type(exc).__name__}: {exc}"

    rec["ok"] = "music_extractor" in rec or bool(rec.get("models"))
    return rec


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    ap = argparse.ArgumentParser(description="Essentia analysis over a worklist.")
    ap.add_argument("--worklist", required=True, help="TSV of <input_audio_path>\\t<output_json_path> per line")
    ap.add_argument("--models-only", action="store_true", help="just download/warm the model cache, analyse nothing")
    ap.add_argument(
        "--no-descriptors",
        action="store_true",
        help="skip lowlevel/rhythm/tonal (the native binary does those); TF models only",
    )
    ap.add_argument("--bpmkey", action="store_true", help="fast BPM+key+ReplayGain only, thread-parallel, no model zoo")
    ap.add_argument("--workers", type=int, default=4, help="threads for --bpmkey")
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-analyse and overwrite an output that already exists. "
        "Needed when a file is replaced by a different recording "
        "at the same library path: the stale json describes the "
        "old audio, and skipping on existence would keep it.",
    )
    args = ap.parse_args(argv)

    # Fast path skips the whole model zoo -- no graphs to load, straight to work.
    if args.bpmkey:
        import essentia.standard as es

        jobs = []
        with open(args.worklist) as fh:
            for line in fh:
                line = line.rstrip("\n")
                if line:
                    inp, _, outp = line.partition("\t")
                    jobs.append((inp, outp))
        log(f"bpmkey: {len(jobs)} files, {args.workers} threads")
        return run_bpmkey(es, jobs, args.workers, force=args.force)

    manifest = json.load(open(MANIFEST))
    t0 = time.time()
    zoo = Zoo(manifest)
    log(f"zoo loaded in {time.time() - t0:.0f}s: {len(zoo.embeddings)} embeddings, {len(zoo.classifiers)} classifiers")
    if args.models_only:
        return 0

    jobs = []
    with open(args.worklist) as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            inp, _, outp = line.partition("\t")
            jobs.append((inp, outp))

    done = skipped = failed = 0
    t0 = time.time()
    for i, (inp, outp) in enumerate(jobs, 1):
        if os.path.exists(outp) and not args.force:
            skipped += 1
            continue
        rec = analyze_one(zoo, inp, descriptors=not args.no_descriptors)
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        tmp = outp + ".part"
        with open(tmp, "w") as fh:
            json.dump(rec, fh, ensure_ascii=False)
        os.replace(tmp, outp)
        done += 1
        if not rec["ok"]:
            failed += 1
        if i % 20 == 0:
            rate = i / max(time.time() - t0, 0.001)
            log(f"{i}/{len(jobs)}  {rate:.2f}/s  eta {(len(jobs) - i) / max(rate, 1e-9) / 60:.0f}m")
    log(f"complete: {done} analysed ({failed} degraded), {skipped} already done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

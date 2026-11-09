# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Detect lossless files that were really transcoded from a lossy source.

A file can be a genuine FLAC container holding audio that came out of a 128 kbps
MP3. The size and the codec both say "lossless", and no tag or header reveals
otherwise -- but every lossy encoder discards everything above a cutoff
frequency, and re-encoding to FLAC cannot put it back. So the spectrum still
dies at ~16 kHz, and that is what this measures. Same principle as
"Fakin' The Funk".

The audio is decoded through ffmpeg, several windows are sampled across the
track, their power spectra are averaged, and the highest frequency still
carrying real energy is reported.

This is evidence, not proof. Quiet recordings, mastering choices, and genuinely
dull material all produce low cutoffs, so nothing here quarantines a file on its
own -- it only reports, and demotes a suspect copy when ranking duplicates.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from .settings import MUSIC_ROOT, REPORTS

# Cutoffs characteristic of common lossy encoders. Measured at the point where
# the spectrum falls away, so these are upper edges rather than exact values.
LOSSY_SIGNATURES = [
    (15_200, "MP3 ~128 kbps or lower"),
    (16_500, "MP3 ~160 kbps"),
    (17_500, "MP3 ~192 kbps / AAC ~128 kbps"),
    (19_000, "MP3 ~256 kbps / AAC ~192 kbps"),
    (20_200, "MP3 320 kbps / V0 / AAC ~256 kbps"),
]

# Above this we treat the spectrum as full-band, i.e. consistent with real
# lossless. CD material rolls off at 22.05 kHz (Nyquist for 44.1 kHz).
GENUINE_CUTOFF_HZ = 20_500

WINDOW = 8192  # FFT size; ~5.4 Hz resolution at 44.1 kHz
N_WINDOWS = 14  # spread across the track
SKIP_FRACTION = 0.10  # ignore the first/last 10%: intros and fades are quiet


def decode(path, sample_rate=None, max_seconds=420):
    """Decode to mono float32 samples via ffmpeg. Returns (samples, rate)."""
    rate = sample_rate or 44100
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",  # audio only; ignore embedded cover art
        "-t",
        str(max_seconds),
        "-ac",
        "1",  # mono: cutoff is a per-file property
        "-ar",
        str(rate),
        "-f",
        "f32le",
        "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=300)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return None, f"{type(exc).__name__}"
    if res.returncode != 0:
        return None, (res.stderr.decode("utf-8", "replace") or "decode failed").strip()[:200]
    samples = np.frombuffer(res.stdout, dtype=np.float32)
    if samples.size < WINDOW * 2:
        return None, "too short to analyse"
    return samples, None


def average_spectrum(samples):
    """Average power spectrum over windows spread across the track."""
    n = samples.size
    lo = int(n * SKIP_FRACTION)
    hi = int(n * (1 - SKIP_FRACTION))
    if hi - lo < WINDOW * 2:
        lo, hi = 0, n
    starts = np.linspace(lo, hi - WINDOW, N_WINDOWS).astype(int)
    win = np.hanning(WINDOW).astype(np.float32)

    acc = np.zeros(WINDOW // 2 + 1, dtype=np.float64)
    used = 0
    for s in starts:
        chunk = samples[s : s + WINDOW]
        if chunk.size < WINDOW:
            continue
        # Near-silent windows carry no information about bandwidth and would
        # drag the average down, so they are skipped.
        if float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))) < 1e-4:
            continue
        acc += np.abs(np.fft.rfft(chunk * win)) ** 2
        used += 1
    if used == 0:
        return None
    return acc / used


def find_cutoff(spectrum, sample_rate, floor_db=-72.0):
    """Highest frequency still carrying energy, in Hz.

    The threshold is relative to the spectrum's own peak, so it adapts to the
    overall level of the recording instead of assuming a fixed loudness.
    """
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / sample_rate)
    power = spectrum / max(spectrum.max(), 1e-30)
    db = 10.0 * np.log10(np.maximum(power, 1e-30))

    # Smooth over ~9 bins so a single noisy bin cannot define the edge.
    #
    # The array is edge-padded first. np.convolve(mode="same") zero-pads
    # instead, and on dB data 0 is enormous -- it dragged the top bins from
    # -132 dB up to -71 dB, pushing them past the threshold and making every
    # file look full-band. That silently disabled this whole check.
    half = 4
    padded = np.pad(db, half, mode="edge")
    kernel = np.ones(2 * half + 1) / (2 * half + 1)
    smooth = np.convolve(padded, kernel, mode="valid")

    above = np.where(smooth > floor_db)[0]
    if above.size == 0:
        return 0.0
    return float(freqs[above[-1]])


# Below this, a low cutoff stops being evidence of transcoding. No encoder in
# common use cuts here (only <=64 kbps material does), so a track reading this
# low is almost always genuinely dull or lo-fi -- vinyl simulation, tape, noise
# and ambient work all land in this range. Reporting it as "probably a 128k MP3"
# would be a confident wrong answer, so it gets its own honest label.
LOFI_FLOOR_HZ = 14_000


def classify(cutoff_hz, sample_rate, lossless):
    """Turn a cutoff into a verdict. Deliberately cautious."""
    nyquist = sample_rate / 2.0
    # A cutoff at Nyquist just means the file uses its full bandwidth.
    if cutoff_hz >= min(GENUINE_CUTOFF_HZ, nyquist * 0.96):
        return "full-band", None
    if not lossless:
        return "lossy (expected)", None
    if cutoff_hz < LOFI_FLOOR_HZ:
        return "low-bandwidth", (
            "no common encoder cuts this low; most likely lo-fi/quiet source material, not a transcode"
        )
    for edge, label in LOSSY_SIGNATURES:
        if cutoff_hz <= edge:
            return "SUSPECT", label
    return "borderline", "below full band but above common encoder cutoffs"


def analyse(path):
    rec = {
        "path": str(path),
        "cutoff_hz": None,
        "verdict": None,
        "likely_source": None,
        "sample_rate": None,
        "error": None,
    }
    # Ask ffprobe for the real rate so the FFT bins map to true frequencies.
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,codec_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        info = json.loads(out.stdout or "{}").get("streams", [{}])[0]
        rate = int(info.get("sample_rate") or 44100)
        codec = info.get("codec_name") or ""
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"probe: {exc}"
        return rec

    rec["sample_rate"] = rate
    lossless = codec in {"flac", "alac", "wavpack", "tta", "ape"} or codec.startswith("pcm")

    samples, err = decode(path, sample_rate=rate)
    if err:
        rec["error"] = err
        return rec
    spec = average_spectrum(samples)
    if spec is None:
        rec["error"] = "no usable (non-silent) audio windows"
        return rec

    cutoff = find_cutoff(spec, rate)
    verdict, source = classify(cutoff, rate, lossless)
    rec.update(
        {
            "cutoff_hz": round(cutoff, 1),
            "verdict": verdict,
            "likely_source": source,
            "codec": codec,
            "lossless": lossless,
        }
    )
    return rec


def targets_from_plan(plan_path, inventory_path):
    """Files worth analysing: the members of every duplicate group.

    That is where the answer changes a decision -- it decides which copy is
    kept. A library-wide census is available via --all.
    """
    keep, drop = set(), set()
    with open(plan_path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            a = json.loads(line)
            if a.get("kind") == "quarantine-dupe":
                drop.add(a["src"])
                if a.get("kept"):
                    keep.add(a["kept"])
    return sorted(keep | drop)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Detect lossless files transcoded from a lossy source.")
    ap.add_argument("--root", default=str(MUSIC_ROOT))
    ap.add_argument("--plan", default=str(REPORTS / "plan.jsonl"))
    ap.add_argument("--inventory", default=str(REPORTS / "inventory.jsonl"))
    ap.add_argument("--out", default=str(REPORTS / "spectral.jsonl"))
    ap.add_argument("--tsv", default=str(REPORTS / "fake_lossless.tsv"))
    ap.add_argument("--workers", type=int, default=6, help="decoding is CPU bound; more than core count will not help")
    ap.add_argument("--all", action="store_true", help="analyse every lossless file, not just duplicate candidates")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--reclassify",
        action="store_true",
        help="recompute verdicts from the cutoffs already measured, without decoding",
    )
    args = ap.parse_args(argv)

    if args.reclassify:
        results = []
        for rec in _read_jsonl(args.out):
            if rec.get("cutoff_hz") is not None:
                verdict, source = classify(rec["cutoff_hz"], rec.get("sample_rate") or 44100, rec.get("lossless", True))
                rec["verdict"], rec["likely_source"] = verdict, source
            results.append(rec)
        with open(args.out, "w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _write_summary(results, args.tsv)
        return 0

    if args.all:
        paths = []
        with open(args.inventory, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                if (r.get("quality") or {}).get("lossless"):
                    paths.append(r["path"])
    else:
        paths = targets_from_plan(args.plan, args.inventory)
    if args.limit:
        paths = paths[: args.limit]

    print(f"[spectral] analysing {len(paths)} files with {args.workers} workers")
    results = []
    with open(args.out, "w", encoding="utf-8") as fh, ThreadPoolExecutor(max_workers=args.workers) as pool:
        for i, rec in enumerate(pool.map(lambda p: _analyse_rel(p, args.root), paths), 1):
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            results.append(rec)
            if i % 100 == 0:
                print(f"[spectral] {i}/{len(paths)}", flush=True)

    _write_summary(results, args.tsv)
    return 0


def _read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _write_summary(results, tsv_path):
    from collections import Counter

    counts = Counter(r.get("verdict") for r in results if r.get("verdict"))
    flagged = [r for r in results if r.get("verdict") in ("SUSPECT", "low-bandwidth", "borderline")]
    with open(tsv_path, "w", encoding="utf-8") as fh:
        fh.write("cutoff_kHz\tverdict\tcodec\tlikely_source\tpath\n")
        for r in sorted(flagged, key=lambda r: r.get("cutoff_hz") or 0):
            fh.write(
                f"{(r.get('cutoff_hz') or 0) / 1000:.1f}\t{r.get('verdict', '')}\t"
                f"{r.get('codec', '')}\t{r.get('likely_source', '')}\t{r['path']}\n"
            )

    errs = [r for r in results if r.get("error")]
    print(f"[spectral] {len(results)} analysed, {len(errs)} errors")
    for verdict, n in counts.most_common():
        print(f"[spectral]   {verdict:20} {n}")
    suspects = [r for r in results if r.get("verdict") == "SUSPECT"]
    print(f"[spectral] full list of flagged files in {tsv_path}")
    for r in sorted(suspects, key=lambda r: r.get("cutoff_hz") or 0)[:12]:
        print(f"  {(r.get('cutoff_hz') or 0) / 1000:5.1f} kHz  {r['path']}")
        print(f"           {r.get('likely_source')}")
    if len(suspects) > 12:
        print(f"  ... and {len(suspects) - 12} more")


def _analyse_rel(rel, root):
    rec = analyse(os.path.join(root, rel))
    rec["path"] = rel
    return rec


if __name__ == "__main__":
    raise SystemExit(main())

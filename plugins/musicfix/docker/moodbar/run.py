#!/usr/bin/env python3
"""Batched moodbar runner (runs inside the container).

Produces the Clementine/Amarok/Strawberry .mood format: 1000 RGB triplets
(3000 bytes). Audio is decoded to mono PCM via ffmpeg, split into 1000 equal
time segments; each segment's FFT magnitude is folded into three frequency
bands (bass->R, mid->G, treble->B). Values are normalised across the whole
track and gamma-lifted, matching the look of the original moodbar tool.

Reads a worklist of <input_audio>\\t<output_mood> lines and is resumable: an
existing .mood is skipped.
"""

import argparse
import os
import subprocess
import sys
import time

import numpy as np

FRAMES = 1000
SR = 22050


def decode(path):
    """Mono float32 PCM via ffmpeg, or None on failure."""
    p = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if p.returncode != 0 or not p.stdout:
        return None
    a = np.frombuffer(p.stdout, dtype="<i2").astype(np.float32) / 32768.0
    return a if a.size >= FRAMES * 4 else None


def moodbar(samples):
    n = samples.size
    seg = n // FRAMES
    if seg < 2:
        return None
    usable = samples[: seg * FRAMES].reshape(FRAMES, seg)
    win = np.hanning(seg).astype(np.float32)
    spec = np.abs(np.fft.rfft(usable * win, axis=1))  # (FRAMES, seg//2+1)
    nb = spec.shape[1]
    # three log-ish bands: bass / mid / treble
    b1, b2 = nb // 8, nb // 2
    r = spec[:, :b1].sum(axis=1)
    g = spec[:, b1:b2].sum(axis=1)
    b = spec[:, b2:].sum(axis=1)
    rgb = np.stack([r, g, b], axis=1)
    # per-track normalise, then gamma lift so quiet detail is visible
    mx = rgb.max()
    if mx <= 0:
        return None
    rgb = np.power(rgb / mx, 0.55)
    return np.clip(rgb * 255.0, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--worklist", required=True)
    args = ap.parse_args()

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
        if os.path.exists(outp):
            skipped += 1
            continue
        samples = decode(inp)
        bar = moodbar(samples) if samples is not None else None
        if bar is None:
            failed += 1
            print(f"[moodbar] FAIL {inp}", file=sys.stderr, flush=True)
            continue
        os.makedirs(os.path.dirname(outp), exist_ok=True)
        tmp = outp + ".part"
        with open(tmp, "wb") as fh:
            fh.write(bar.tobytes())
        os.replace(tmp, outp)
        done += 1
        if i % 100 == 0:
            rate = i / max(time.time() - t0, 1e-9)
            print(
                f"[moodbar] {i}/{len(jobs)}  {rate:.1f}/s  eta {(len(jobs) - i) / max(rate, 1e-9) / 60:.0f}m",
                file=sys.stderr,
                flush=True,
            )
    print(f"[moodbar] done: {done} generated, {skipped} skipped, {failed} failed", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()

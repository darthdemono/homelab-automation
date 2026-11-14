# moodbar generator for musicFix.
#
# The classic GStreamer `moodbar` tool was dropped from Debian/Ubuntu (it never
# left gstreamer 0.10), so instead of resurrecting that toolchain we compute the
# identical on-disk format ourselves: 1000 RGB triplets (3000 bytes) that
# Clementine / Amarok / Strawberry read as the coloured seek-bar.
#
# Decode is via ffmpeg to raw PCM; the spectral -> RGB mapping is numpy. A
# worklist runner keeps one container alive for the whole 11k-file job.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "numpy<2"

COPY run.py /opt/run.py
ENTRYPOINT ["python3", "/opt/run.py"]

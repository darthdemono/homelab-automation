# Essentia analysis toolchain for musicFix.
#
# The host runs Python 3.14, for which essentia / essentia-tensorflow publish no
# wheels (they cap at cp311). So the whole analysis engine lives in this
# container built on python:3.11-slim, and the host talks to it over a mounted
# music volume plus a mounted model cache.
#
# essentia-tensorflow bundles the classic C++ algorithms (MusicExtractor: bpm,
# key, chords, loudness, dynamic-complexity, tuning, spectral, onset-rate, ...)
# AND the TensorFlow inference algorithms used for the model zoo (genre, mood,
# danceability, instrument, arousal/valence, approachability, ...).
FROM python:3.11-slim

# ffmpeg: MonoLoader/AudioLoader decode through it for the odd container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# TensorFlow is pulled in by essentia-tensorflow. Pin numpy<2 — the essentia
# wheels were built against the 1.x ABI and segfault on numpy 2.
RUN pip install --no-cache-dir "numpy<2" essentia-tensorflow mutagen

WORKDIR /work
COPY extract.py models.json /opt/musicfix-essentia/

# Models are large and change rarely; they are downloaded on first run into a
# mounted /models cache instead of baked into the image, so the image stays
# small and a rebuild never refetches gigabytes.
ENV MUSICFIX_MODELS=/models
ENTRYPOINT ["python", "/opt/musicfix-essentia/extract.py"]

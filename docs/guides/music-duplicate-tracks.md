# Finding duplicate tracks without losing recordings

## Symptom

- A dedup pass quarantined `Song (Remix)` or `Song (Instrumental)` as a copy of `Song`, and the library had no other copy.
- An uncompressed WAV "won" against a bit-identical FLAC.
- A lossy file won against a better one because its reported bitrate was higher.
- Two copies of the same recording with differently spelled titles both stay in the library.
- A FLAC that came from a 128 kbps MP3 is kept as the best copy.

## Why it happens

- A title normaliser that strips every bracketed suffix merges different versions.
- For lossless audio, bitrate measures compressibility, not fidelity.
- Tag-reported and container bitrates lie: transcodes keep the original header, and the container figure counts embedded cover art.
- Name-based checks cannot see a duplicate whose title derives a different canonical path.
- A lossless container proves nothing about where the audio came from.

## How to check

```sh
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name,sample_rate,bits_per_raw_sample,bit_rate -of default=nw=1 "<file>"
ffmpeg -v error -i "<file>" -map 0:a -f md5 -          # decoded PCM fingerprint; equal md5 = same audio
metaflac --show-tag=ISRC "<file>"
```

## Fix

- A duplicate is the same title inside the same album at a different quality, not the same song on two albums.
- Fold case, accents, punctuation and featured-artist suffixes when matching, but keep version qualifiers (remix, instrumental, live, slowed, remaster).
- Rank lossless by bit depth, then sample rate, then codec. Rank lossy by sample rate, then audio-stream bitrate, then codec. Break ties on metadata completeness (MusicBrainz ids, ISRC, date, genre, art).
- Keep WAV and FLAC of one track as a pair if you want both, and sync their tags so they stay in one folder.
- Settle hard cases with the ISRC and the decoded-PCM md5.
- For suspected fake lossless, look at the spectrum: lossy encoders cut everything above a frequency (around 16 kHz for 128 kbps, about 20 kHz for LAME 320). Quiet material also reads low, so use it to demote a copy when ranking, never as the only reason to quarantine.

## How to prevent

- Every normaliser change gets a regression test with real version-qualifier pairs.
- Move losers to quarantine with their relative path, never delete, and journal the move.
- Watch numeric edge cases: zero-padding (`np.convolve(mode="same")`) on dB data once lifted a -132 dB tail above the threshold and silently disabled a spectral check. Pad with edge values.

## Which tool helps

- `clean_junk_files` quarantines operating-system clutter (`Thumbs.db`, `desktop.ini`, `.DS_Store`) before a dedup pass, so it does not muddy the counts.
- A music cleanup plugin with this ranking is planned.

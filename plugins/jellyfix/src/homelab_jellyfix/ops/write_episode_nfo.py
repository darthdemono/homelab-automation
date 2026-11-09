# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
"""Write a minimal episode .nfo beside every library episode that lacks one.

jellyFix names carry everything Jellyfin needs for the title row:
`Show (Year) - S02E01 - 011 - Episode Title [tags]-Group.mkv`. A local .nfo is read
before any online provider, so the title and numbers stay ours even when a
provider plugin overwrites the name with the series title. Jellyfin regenerates
or ignores these freely; nothing else reads them. Idempotent, dry run unless --apply.
"""

import argparse
import os
import re
from xml.sax.saxutils import escape

from homelab_jellyfix import stack

ROOTS = [stack.lib("Anime"), stack.lib("Shows"), stack.lib("Animated Shows")]
VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
NAME = re.compile(r"^.+? - S(?P<s>\d+)E(?P<e>\d+)(?: - (?P<abs>\d{2,4}))? - (?P<title>.+?)(?: \[|\.\w+$)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    made = skipped = 0
    for root in ROOTS:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                base, ext = os.path.splitext(fn)
                if ext.lower() not in VIDEO or os.path.exists(os.path.join(dp, base + ".nfo")):
                    continue
                m = NAME.match(fn)
                if not m:
                    skipped += 1
                    continue
                xml = (
                    '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n<episodedetails>\n'
                    f"  <title>{escape(m['title'])}</title>\n  <season>{int(m['s'])}</season>\n"
                    f"  <episode>{int(m['e'])}</episode>\n"
                    + (f"  <absolute_number>{int(m['abs'])}</absolute_number>\n" if m["abs"] else "")
                    + "</episodedetails>\n"
                )
                made += 1
                if a.apply:
                    open(os.path.join(dp, base + ".nfo"), "w", encoding="utf-8").write(xml)
    print(f"{'wrote' if a.apply else 'would write'} {made} nfo, {skipped} names not parsed")


if __name__ == "__main__":
    main()

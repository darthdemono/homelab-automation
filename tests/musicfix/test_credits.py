# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
from homelab_musicfix.credits import merge


def test_merge_dedupes_spelling_and_keeps_longer():
    assert merge(["SM Areb Amin", "Richard Gomes"], ["S.M Areb Amin", "Nobin Richard Gomes"]) == [
        "SM Areb Amin",
        "Nobin Richard Gomes",
    ]


def test_artist_art_filed_in_canonical_artist_dir(tmp_path):
    from collections import defaultdict

    from homelab_musicfix.merge import _plan_artist_art

    actions, stats = [], defaultdict(int)
    sc = [{"path": "Umbra Mire/Umbra Mire_Avatar.jpg"}, {"path": "Umbra Mire/Umbra Mire_Header.jpg"}]
    rec = [{"path": "Umbra Mire/Depiction/x.flac", "_target": "Umbra ⁄⁄ Mire/Depiction/x.flac"}]
    _plan_artist_art(sc, set(), rec, str(tmp_path), actions, stats)
    assert [a["dst"] for a in actions] == ["Umbra ⁄⁄ Mire/artist.jpg", "Umbra ⁄⁄ Mire/fanart.jpg"]

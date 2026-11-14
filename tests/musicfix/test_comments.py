# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Jubair Hasan (DarthDemono)
from homelab_musicfix.comments import is_junk


def test_settings_dump_is_junk_but_real_comments_are_kept():
    assert is_junk("{'Classical Extras': {'Artists options': {}}}")
    assert is_junk("{'classical Extras': {'works options': {}}}")
    assert not is_junk("https://open.spotify.com/track/abc")
    assert not is_junk("Brought to you by Musica_MaGiC")

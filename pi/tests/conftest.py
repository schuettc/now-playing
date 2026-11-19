"""Suite-wide fixtures."""
from __future__ import annotations

import pytest

from nowplaying import art_cache


@pytest.fixture(autouse=True)
def _no_musicbrainz_spacing(monkeypatch):
    """Mocked MusicBrainz calls need no 1 req/s spacing between tests."""
    monkeypatch.setattr(art_cache, "MUSICBRAINZ_MIN_INTERVAL_S", 0.0)

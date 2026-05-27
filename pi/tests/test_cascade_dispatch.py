"""Tests for the fingerprint cascade match dispatcher.

``_cascade_match_dispatch`` routes match queries to:
- vinyl.fingerprint when ``locked_rid`` is set (Discogs lock),
- discovery.fingerprint when ``locked_mbid`` is set (MBID lock),
- BOTH stores (unioned, sorted) when both are None (blind scan).
"""
from __future__ import annotations

from unittest import mock

from nowplaying.orchestrator import _heartbeat_handlers as hh
from nowplaying.vinyl.fingerprint import Hit


_DISCOGS_HIT = Hit(
    ref_id=1, release_id=42, track_position="A1", hits=20,
    track_position_s=0.0, mbid=None,
)
_MBID_HIT = Hit(
    ref_id=2, release_id=None, track_position="A1", hits=30,
    track_position_s=0.0, mbid="harvest-mb-1",
)


def test_dispatch_routes_to_discogs_when_locked_rid_set():
    with mock.patch.object(
        hh.fingerprint, "match", return_value=[_DISCOGS_HIT],
    ) as fp_match, mock.patch.object(
        hh.discovery_fingerprint, "match",
    ) as dfp_match:
        out = hh._cascade_match_dispatch(b"wav", 42, None)
    assert out == [_DISCOGS_HIT]
    fp_match.assert_called_once_with(b"wav", 42)
    dfp_match.assert_not_called()


def test_dispatch_routes_to_discovered_when_locked_mbid_set():
    with mock.patch.object(
        hh.fingerprint, "match",
    ) as fp_match, mock.patch.object(
        hh.discovery_fingerprint, "match", return_value=[_MBID_HIT],
    ) as dfp_match:
        out = hh._cascade_match_dispatch(b"wav", None, "harvest-mb-1")
    assert out == [_MBID_HIT]
    fp_match.assert_not_called()
    dfp_match.assert_called_once_with(b"wav", "harvest-mb-1")


def test_blind_dispatch_unions_both_stores_sorted_by_hits():
    with mock.patch.object(
        hh.fingerprint, "match", return_value=[_DISCOGS_HIT],
    ), mock.patch.object(
        hh.discovery_fingerprint, "match", return_value=[_MBID_HIT],
    ):
        out = hh._cascade_match_dispatch(b"wav", None, None)
    # Sorted descending by hits — MBID (30) ranks above Discogs (20).
    assert out == [_MBID_HIT, _DISCOGS_HIT]


def test_blind_dispatch_returns_discogs_only_when_discovered_empty():
    with mock.patch.object(
        hh.fingerprint, "match", return_value=[_DISCOGS_HIT],
    ), mock.patch.object(
        hh.discovery_fingerprint, "match", return_value=[],
    ):
        out = hh._cascade_match_dispatch(b"wav", None, None)
    assert out == [_DISCOGS_HIT]


def test_blind_dispatch_returns_discovered_only_when_discogs_empty():
    with mock.patch.object(
        hh.fingerprint, "match", return_value=[],
    ), mock.patch.object(
        hh.discovery_fingerprint, "match", return_value=[_MBID_HIT],
    ):
        out = hh._cascade_match_dispatch(b"wav", None, None)
    assert out == [_MBID_HIT]

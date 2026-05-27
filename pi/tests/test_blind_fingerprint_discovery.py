"""F4: Integration tests for blind fingerprint discovery.

These exercise the new ``_try_blind_fingerprint`` path — cold-start
recognition without a locked album anchor — and verify that the
existing confirmation path (F3) is not regressed.

Design notes:
- ``fingerprint.match`` and ``discogs_catalog.get_release`` are mocked;
  no real DB or audio is needed.
- Existing F3 tests live in ``test_fingerprint_cascade.py``.  The tests
  here cover only the blind path and the routing logic that selects it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.vinyl.fingerprint import Hit


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

_DISCOGS_RELEASE = {
    "id": 31427573,
    "artist": "Failure",
    "title": "Fantastic Planet",
    "year": 1996,
    "label": "Slash Records",
    "catno": "PRO-A-8453",
    "tracks": [
        {"position": "C10", "side": "C", "title": "Pitiful", "duration_seconds": 240},
        {"position": "C11", "side": "C", "title": "Another", "duration_seconds": 200},
        {"position": "C12", "side": "C", "title": "Segue 3", "duration_seconds": 60},
    ],
}


@pytest.fixture
def orch_cold_start(tmp_path):
    """Orchestrator with FP enabled and NO locked album (cold start)."""
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.fingerprint_enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = None  # cold start — no lock
    orch.state.idle_task = None
    orch.state.unmatched_streak = 3
    orch.state.predicted_position = {"track_position": "C11"}
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.dismissed_guesses = {}
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    orch.state.sonos_source = "vinyl"
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._anchor_and_publish = lambda payload: payload
    return orch


@pytest.fixture
def orch_no_rid(tmp_path):
    """Orchestrator with FP enabled and last_vinyl set but release_id=None."""
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.fingerprint_enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {
        "title": "Unknown",
        "track_position": None,
        "release_id": None,
        "source": "vinyl",
    }
    orch.state.idle_task = None
    orch.state.unmatched_streak = 2
    orch.state.predicted_position = None
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.dismissed_guesses = {}
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    orch.state.sonos_source = "vinyl"
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._anchor_and_publish = lambda payload: payload
    return orch


# ---------------------------------------------------------------------------
# 1. Cold-start hit
# ---------------------------------------------------------------------------


def test_blind_cold_start_hit_publishes(orch_cold_start, monkeypatch, tmp_path):
    """last_vinyl=None + confident blind hit → publish with correct metadata."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda wav, release_filter, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    assert orch_cold_start.bcast.publish.await_count == 1
    published = orch_cold_start.bcast.publish.await_args.args[0]
    assert published["match_method"] == "fingerprint"
    assert published["release_id"] == 31427573
    assert published["artist"] == "Failure"
    assert published["album"] == "Fantastic Planet"
    assert published["track_position"] == "C10"
    assert published["side"] == "C"
    assert published["title"] == "Pitiful"
    assert published["art_url"] == "/art/31427573"
    assert published["source"] == "vinyl"
    # State invariants
    assert orch_cold_start.state.unmatched_streak == 0
    assert orch_cold_start.state.predicted_position is None
    assert orch_cold_start.state.pending_guess is None
    assert orch_cold_start.state.last_vinyl is published


# ---------------------------------------------------------------------------
# 2. Cold-start no-match
# ---------------------------------------------------------------------------


def test_blind_cold_start_no_match(orch_cold_start, monkeypatch, tmp_path):
    """last_vinyl=None + empty blind scan → returns False, no publish."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()
    # Streak not reset on miss
    assert orch_cold_start.state.unmatched_streak == 3


# ---------------------------------------------------------------------------
# 3. No-lock (last_vinyl set, release_id=None) routes to blind path
# ---------------------------------------------------------------------------


def test_blind_no_rid_routes_to_blind_scan(orch_no_rid, monkeypatch, tmp_path):
    """last_vinyl present but release_id=None → blind path fires, not confirmation."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    match_calls: list = []

    def fake_match(wav, release_filter, **kw):
        match_calls.append(release_filter)
        return [Hit(ref_id=10, release_id=31427573, track_position="C10", hits=100, track_position_s=0.0)]

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", fake_match)
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    result = _run(orch_no_rid._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    # Blind scan passes release_filter=None to fingerprint.match
    assert len(match_calls) == 1
    assert match_calls[0] is None


# ---------------------------------------------------------------------------
# 4. Top-2 margin reject on blind path
# ---------------------------------------------------------------------------


def test_blind_margin_reject(orch_cold_start, monkeypatch, tmp_path):
    """Near-tie on blind scan (top=80, runner_up=70) → returns False."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=80, track_position_s=0.0),
        Hit(ref_id=11, release_id=31427573, track_position="C11", hits=70, track_position_s=0.0),
    ])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()
    assert orch_cold_start.state.unmatched_streak == 3


# ---------------------------------------------------------------------------
# 5. Below-threshold reject on blind path
# ---------------------------------------------------------------------------


def test_blind_below_threshold(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at 20 hits (below MIN_FINGERPRINT_HITS_BLIND=30) → returns False.

    Single-result case: the margin gate vacuously passes (no runner-up), so only
    the absolute floor rejects this hit.  hits=51 was the old rejection boundary
    under the unified MIN=60; after the split the blind floor is 30, so 51 now
    publishes (see test_blind_hits_54_publishes).
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=20, track_position_s=0.0),
    ])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Discogs catalog miss after blind match → fall through
# ---------------------------------------------------------------------------


def test_blind_hit_catalog_miss_falls_through(orch_cold_start, monkeypatch, tmp_path):
    """Confident blind hit but release not in discogs.sqlite → returns False."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=99999, track_position="A1", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: None)

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()
    # Streak not reset — miss, not hit
    assert orch_cold_start.state.unmatched_streak == 3


# ---------------------------------------------------------------------------
# 7. Disabled gate — fingerprint_enabled=False
# ---------------------------------------------------------------------------


def test_blind_skips_when_disabled(orch_cold_start, monkeypatch, tmp_path):
    """fingerprint_enabled=False → no scan even on cold start."""
    orch_cold_start.fingerprint_enabled = False
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    match_calls: list = []
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: match_calls.append(a) or [])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    assert match_calls == []
    orch_cold_start.bcast.publish.assert_not_called()


# ---------------------------------------------------------------------------
# 8. State invariants after blind hit
# ---------------------------------------------------------------------------


def test_blind_hit_state_invariants(orch_cold_start, monkeypatch, tmp_path):
    """After blind hit: streak=0, prediction cleared, last_vinyl updated."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    # Seed non-zero streak and stale prediction
    orch_cold_start.state.unmatched_streak = 5
    orch_cold_start.state.predicted_position = {"track_position": "C11"}
    orch_cold_start.state.pending_guess = {"position": "C11", "source": "heuristic"}

    _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert orch_cold_start.state.unmatched_streak == 0
    assert orch_cold_start.state.predicted_position is None
    assert orch_cold_start.state.pending_guess is None
    # last_vinyl was updated to the published payload
    assert orch_cold_start.state.last_vinyl is not None
    assert orch_cold_start.state.last_vinyl["release_id"] == 31427573


# ---------------------------------------------------------------------------
# 9. Confirmation path unaffected when lock + release_id present
# ---------------------------------------------------------------------------


def test_confirmation_path_unaffected_when_lock_present(
    monkeypatch, tmp_path,
):
    """lock present + release_id → confirmation scan runs, NOT blind scan.

    Regression guard: the routing logic must not change the existing F3
    behaviour when last_vinyl has a release_id.
    """
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.fingerprint_enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {
        "title": "Heart and Soul",
        "track_position": "D1",
        "side": "D",
        "release_id": 12345,
        "artist": "Joy Division",
        "album": "Closer",
        "tracklist": [
            {"track_position": "D1", "title": "Heart and Soul"},
        ],
    }
    orch.state.idle_task = None
    orch.state.unmatched_streak = 2
    orch.state.predicted_position = None
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.dismissed_guesses = {}
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    orch.state.sonos_source = "vinyl"
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._anchor_and_publish = lambda payload: payload

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    match_calls: list = []

    def fake_match(wav, release_filter, **kw):
        match_calls.append(release_filter)
        return [Hit(ref_id=1, release_id=12345, track_position="D1", hits=100, track_position_s=0.0)]

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", fake_match)
    # get_release should NOT be called on the confirmation path
    get_release_calls: list = []
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: get_release_calls.append(rid) or None)

    result = _run(orch._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    # Confirmation scan used release_id=12345 (not None)
    assert len(match_calls) == 1
    assert match_calls[0] == 12345
    # Discogs catalog NOT consulted on confirmation path
    assert get_release_calls == []
    # Published payload built from locked tracklist, not discogs
    published = orch.bcast.publish.await_args.args[0]
    assert published["release_id"] == 12345
    assert published["title"] == "Heart and Soul"


# ---------------------------------------------------------------------------
# 10. Blind scan does NOT call pin-promotion or track-guess
# ---------------------------------------------------------------------------


def test_blind_hit_no_pin_promotion_no_track_guess(
    orch_cold_start, monkeypatch, tmp_path,
):
    """Blind match must never call _maybe_schedule_pin_promotion or
    _compute_track_guess — those require a locked album anchor.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    pin_calls: list = []
    guess_calls: list = []
    orch_cold_start._maybe_schedule_pin_promotion = lambda wav: pin_calls.append(wav)
    orch_cold_start._compute_track_guess = mock.AsyncMock(
        side_effect=lambda s: guess_calls.append(s) or None,
    )

    _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert pin_calls == [], "Blind path must not call _maybe_schedule_pin_promotion"
    assert guess_calls == [], "Blind path must not call _compute_track_guess"


# ---------------------------------------------------------------------------
# 11. Blind payload tracklist uses "position" key (pin-track compatibility)
# ---------------------------------------------------------------------------


def test_blind_payload_tracklist_uses_position_key(
    orch_cold_start, monkeypatch, tmp_path,
):
    """Tracklist entries in a blind-discovery payload must use the key
    ``"position"`` (not ``"track_position"``) so that ``_find_pin_track``
    in pin_track.py can locate any track on the release.

    Regression guard: before the fix, the key was ``"track_position"``,
    which caused every pin-track lookup to fail for blind-discovered anchors
    even when the full tracklist was present.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    # Full release with three tracks on side C
    full_release = {
        "id": 31427573,
        "artist": "Failure",
        "title": "Fantastic Planet",
        "year": 1996,
        "label": "Slash Records",
        "catno": "PRO-A-8453",
        "tracks": [
            {"position": "C10", "side": "C", "title": "Pitiful", "duration_seconds": 240},
            {"position": "C11", "side": "C", "title": "Leo", "duration_seconds": 200},
            {"position": "C12", "side": "C", "title": "Segue 3", "duration_seconds": 60},
        ],
    }

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(full_release))

    _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    published = orch_cold_start.bcast.publish.await_args.args[0]
    tracklist = published.get("tracklist") or []
    assert len(tracklist) == 3, "All three tracks must be in the payload tracklist"

    # Every entry must have "position", not "track_position"
    for entry in tracklist:
        assert "position" in entry, f"Entry missing 'position' key: {entry}"
        assert "track_position" not in entry, (
            f"Entry has 'track_position' key (should be 'position'): {entry}"
        )

    # Confirm the full set of positions is present and pin-findable
    positions = {e["position"] for e in tracklist}
    assert positions == {"C10", "C11", "C12"}

    # Directly exercise _find_pin_track to confirm it resolves C11 from the
    # blind payload — this was the failure mode before the key fix.
    from nowplaying.control.pin_track import _find_pin_track
    assert _find_pin_track(tracklist, "C11") is not None, (
        "_find_pin_track must resolve C11 from blind-discovery tracklist"
    )


# ---------------------------------------------------------------------------
# 12. Fingerprint anchor set on strong blind hit (≥ MIN_ANCHORED * 1.5)
# ---------------------------------------------------------------------------


def test_strong_blind_hit_sets_anchor(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at hits=136 (≥ MIN_FINGERPRINT_HITS_ANCHORED * 1.5 = 90) → anchor set."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=136, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    # Freeze monotonic time so anchor's monotonic_ts is deterministic.
    import asyncio
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_000.0
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    anchor = orch_cold_start.state.fingerprint_anchor
    assert anchor is not None, "Strong blind hit must set fingerprint_anchor"
    assert anchor["release_id"] == 31427573
    assert anchor["track_position"] == "C10"
    assert anchor["hits"] == 136
    assert anchor["monotonic_ts"] == 1_000_000.0
    # duration_seconds from _DISCOGS_RELEASE tracklist entry for C10 = 240
    assert anchor["duration_seconds"] == 240


# ---------------------------------------------------------------------------
# 13. Fingerprint anchor NOT set on weak blind hit (MIN ≤ hits < MIN * 1.5)
# ---------------------------------------------------------------------------


def test_publish_worthy_blind_hit_also_sets_anchor(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at hits=30 (= MIN_FINGERPRINT_HITS_BLIND) publishes AND sets anchor.

    With STRONG_FINGERPRINT_ANCHOR_MULTIPLIER=0.5 (tuned 2026-05-19), the
    anchor threshold equals the blind publish threshold:
    `MIN_FINGERPRINT_HITS_ANCHORED * 0.5 = 60 * 0.5 = 30 = MIN_FINGERPRINT_HITS_BLIND`.

    Any match strong enough to publish via the blind path is now also strong
    enough to anchor — closing the previous gap where 30–89 hits published
    but did not anchor, leaving predicted-advance free to flip wrongly.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=30, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    orch_cold_start.bcast.publish.assert_awaited_once()
    # Anchor now set at the same threshold as publish — publish-worthy == anchor-worthy.
    assert orch_cold_start.state.fingerprint_anchor is not None
    assert orch_cold_start.state.fingerprint_anchor["release_id"] == 31427573
    assert orch_cold_start.state.fingerprint_anchor["track_position"] == "C10"


# ---------------------------------------------------------------------------
# 14. Below-threshold miss does not set anchor
# ---------------------------------------------------------------------------


def test_below_threshold_blind_miss_does_not_set_anchor(
    orch_cold_start, monkeypatch, tmp_path,
):
    """Blind hit at hits=20 (< MIN_FINGERPRINT_HITS_BLIND=30) → miss, no anchor, no publish."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=20, track_position_s=0.0),
    ])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()
    assert orch_cold_start.state.fingerprint_anchor is None


# ---------------------------------------------------------------------------
# 15. Live-session regression: hits=54 publishes via blind discovery
# ---------------------------------------------------------------------------


def test_blind_hits_54_publishes(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at hits=54 → publishes (regression for 2026-05-18 Pitiful session).

    With the old unified MIN_FINGERPRINT_HITS=60 this heartbeat was rejected.
    With MIN_FINGERPRINT_HITS_BLIND=30 it passes the floor gate; the single
    result also passes the margin gate vacuously (no runner-up to compare).
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=54, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    orch_cold_start.bcast.publish.assert_awaited_once()
    published = orch_cold_start.bcast.publish.await_args.args[0]
    assert published["release_id"] == 31427573
    assert published["track_position"] == "C10"
    assert published["title"] == "Pitiful"


# ---------------------------------------------------------------------------
# 16. hits=29 (one below floor) → miss
# ---------------------------------------------------------------------------


def test_blind_hits_29_below_threshold(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at hits=29 (one below MIN_FINGERPRINT_HITS_BLIND=30) → miss."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=29, track_position_s=0.0),
    ])

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch_cold_start.bcast.publish.assert_not_called()


# ---------------------------------------------------------------------------
# 17. hits=30 (exactly at floor) → publishes
# ---------------------------------------------------------------------------


def test_blind_hits_30_at_floor_publishes(orch_cold_start, monkeypatch, tmp_path):
    """Blind hit at hits=30 (exactly MIN_FINGERPRINT_HITS_BLIND=30) → publishes."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=10, release_id=31427573, track_position="C10", hits=30, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    orch_cold_start.bcast.publish.assert_awaited_once()


# ---------------------------------------------------------------------------
# 18. Confirmation path still rejects at 59 hits (anchored threshold = 60)
# ---------------------------------------------------------------------------


def test_confirmation_rejects_at_59_hits(monkeypatch, tmp_path):
    """Confirmation path (album lock present) rejects hits=59 — anchored floor is 60."""
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.fingerprint_enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {
        "title": "Pitiful",
        "track_position": "C10",
        "side": "C",
        "release_id": 31427573,
        "artist": "Failure",
        "album": "Fantastic Planet",
        "tracklist": [
            {"track_position": "C10", "title": "Pitiful"},
        ],
    }
    orch.state.idle_task = None
    orch.state.unmatched_streak = 2
    orch.state.predicted_position = None
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.dismissed_guesses = {}
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    orch.state.sonos_source = "vinyl"
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._anchor_and_publish = lambda payload: payload

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda wav, release_filter, **kw: [
        Hit(ref_id=1, release_id=31427573, track_position="C10", hits=59, track_position_s=0.0),
    ])

    result = _run(orch._try_fingerprint_fallback(clip, "vinyl"))

    assert result is False
    orch.bcast.publish.assert_not_called()


# ---------------------------------------------------------------------------
# 19. Confirmation path accepts at 60 hits (boundary)
# ---------------------------------------------------------------------------


def test_confirmation_accepts_at_60_hits(monkeypatch, tmp_path):
    """Confirmation path accepts hits=60 (exactly MIN_FINGERPRINT_HITS_ANCHORED=60)."""
    from nowplaying.main import Orchestrator
    orch = Orchestrator.__new__(Orchestrator)
    orch.fingerprint_enabled = True
    orch.state = mock.MagicMock()
    orch.state.last_vinyl = {
        "title": "Pitiful",
        "track_position": "C10",
        "side": "C",
        "release_id": 31427573,
        "artist": "Failure",
        "album": "Fantastic Planet",
        "tracklist": [
            {"track_position": "C10", "title": "Pitiful"},
        ],
    }
    orch.state.idle_task = None
    orch.state.unmatched_streak = 2
    orch.state.predicted_position = None
    orch.state.user_track_pin = None
    orch.state.fingerprint_anchor = None
    orch.state.dismissed_guesses = {}
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    orch.state.sonos_source = "vinyl"
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    orch._anchor_and_publish = lambda payload: payload

    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda wav, release_filter, **kw: [
        Hit(ref_id=1, release_id=31427573, track_position="C10", hits=60, track_position_s=0.0),
    ])

    result = _run(orch._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    orch.bcast.publish.assert_awaited_once()
    published = orch.bcast.publish.await_args.args[0]
    assert published["release_id"] == 31427573
    assert published["track_position"] == "C10"


# ---------------------------------------------------------------------------
# 20. Blind path: position change clears old anchor before setting new one
#     (anchor-release-on-different-position feature)
# ---------------------------------------------------------------------------


def test_blind_different_position_clears_old_anchor(
    orch_cold_start, monkeypatch, tmp_path,
):
    """Blind hit for C11 while anchor is on C10 → stale anchor released, new anchor on C11.

    Models the live-session bug 2026-05-18 18:40 where the kiosk left column
    (title) stayed on Pitiful while the right column (tracklist) jumped to Leo
    because the old C10 anchor was not cleared before the new C11 anchor was set.

    After this fix:
    - Old anchor (C10) is explicitly cleared.
    - New anchor (C11) is set if hits ≥ strong threshold.
    - Published payload is fully consistent with C11.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    # Pre-existing anchor on C10 (Pitiful), set by an earlier blind strong hit.
    orch_cold_start.state.fingerprint_anchor = {
        "release_id": 31427573,
        "track_position": "C10",
        "monotonic_ts": 1_000_000.0,
        "hits": 136,
        "duration_seconds": 240,
    }

    # New blind hit: same release, different position C11 (user moved needle).
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=11, release_id=31427573, track_position="C11", hits=122, track_position_s=0.0),
    ])
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE))

    import asyncio
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_500.0
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    with mock.patch("nowplaying.orchestrator._class.history") as hist:
        hist.record_play = mock.AsyncMock()
        result = _run(orch_cold_start._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    published = orch_cold_start.bcast.publish.await_args.args[0]

    # Payload must reflect C11, not the stale C10 anchor.
    assert published["track_position"] == "C11"
    assert published["title"] == "Another"  # C11 title from _DISCOGS_RELEASE

    # New anchor must be on C11, old C10 anchor must be gone.
    new_anchor = orch_cold_start.state.fingerprint_anchor
    assert new_anchor is not None
    assert new_anchor["track_position"] == "C11", (
        "Anchor must be on new position C11 after blind hit for a different track"
    )
    assert new_anchor["release_id"] == 31427573
    assert new_anchor["monotonic_ts"] == 1_000_500.0

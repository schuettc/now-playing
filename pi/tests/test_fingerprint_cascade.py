"""F3: Integration tests for the Orchestrator's fingerprint-cascade hooks.

These exercise `_try_fingerprint_fallback`, `_schedule_coverage_promotion`,
and the Shazam-hit-no-promotion invariant. We don't drive the full heartbeat
handler (it has too many other dependencies); the unit tests here ensure the
cascade logic is correct in isolation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest import mock

import pytest

from nowplaying.vinyl.fingerprint import Hit


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def orch_with_lock(tmp_path):
    """Orchestrator with FP enabled and a locked album."""
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
            {"track_position": "D2", "title": "Twenty Four Hours"},
            {"track_position": "D3", "title": "The Eternal"},
        ],
    }
    orch.state.idle_task = None
    orch.state.unmatched_streak = 5
    orch.state.predicted_position = {"track_position": "D2"}
    # Default: no pin set. Pin-driven promotion tests opt in by
    # overriding this attribute on the fixture.
    orch.state.user_track_pin = None
    # Default: no fingerprint anchor. Anchor-driven coverage promotion
    # (added 2026-05-19) reads this; tests that need the anchor path
    # active opt in by overriding.
    orch.state.fingerprint_anchor = None
    # Default: empty dismissed-guess set. Required because
    # `_compute_track_guess` consults it via `_guess_is_dismissed`
    # which does arithmetic on the timestamp values — a MagicMock
    # lookup would fail with a TypeError mid-test.
    orch.state.dismissed_guesses = {}
    # Track-guess hook needs an `llm` attr; disabled by default so the
    # heuristic fallback path is exercised. See docs/features/llm-track-guess/.
    orch.state.pending_guess = None
    orch.state.track_started_at = None
    from nowplaying.llm import LLMAssist
    orch.llm = LLMAssist()
    orch.llm.enabled = False
    orch.bcast = mock.MagicMock()
    orch.bcast.publish = mock.AsyncMock()
    # Stamp track_started_at via _anchor_and_publish — but the stub
    # just returns the payload as-is. Avoid invoking real logic.
    orch._anchor_and_publish = lambda payload: payload
    return orch


# ── _try_fingerprint_fallback ────────────────────────────────────────────


def test_fallback_skips_when_disabled(orch_with_lock):
    orch_with_lock.fingerprint_enabled = False
    result = _run(orch_with_lock._try_fingerprint_fallback(Path("/fake"), "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()


def test_fallback_skips_when_no_lock(orch_with_lock):
    orch_with_lock.state.last_vinyl = None
    result = _run(orch_with_lock._try_fingerprint_fallback(Path("/fake"), "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()


def test_fallback_skips_when_no_release_id(orch_with_lock):
    orch_with_lock.state.last_vinyl["release_id"] = None
    result = _run(orch_with_lock._try_fingerprint_fallback(Path("/fake"), "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()


def test_fallback_no_hits_returns_false(orch_with_lock, monkeypatch, tmp_path):
    """fingerprint.match returns [] → fallback skips, caller falls through."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()
    # Streak NOT reset on miss.
    assert orch_with_lock.state.unmatched_streak == 5
    # Track-guess hook: with LLM disabled and a heuristic predicted_position
    # set in the fixture (D2), the helper populates pending_guess with
    # the heuristic fallback. See docs/features/llm-track-guess/.
    assert orch_with_lock.state.pending_guess == {
        "position": "D2",
        "title": "Twenty Four Hours",
        "confidence": "low",
        "source": "heuristic",
    }


def test_fallback_hit_publishes_with_correct_title(orch_with_lock, monkeypatch, tmp_path):
    """On hit, payload has match_method='fingerprint', updated title from
    the tracklist, reset streak, cleared prediction."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    # hits=80 — above the MIN_FINGERPRINT_HITS=60 threshold so the gate passes.
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D3", hits=80, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is True
    # publish was called once.
    assert orch_with_lock.bcast.publish.await_count == 1
    published = orch_with_lock.bcast.publish.await_args.args[0]
    assert published["match_method"] == "fingerprint"
    assert published["track_position"] == "D3"
    assert published["side"] == "D"
    assert published["title"] == "The Eternal"  # from tracklist, not stale D1 title
    assert published["release_id"] == 12345
    # State invariants after fingerprint hit.
    assert orch_with_lock.state.unmatched_streak == 0
    assert orch_with_lock.state.predicted_position is None


def test_fallback_hit_falls_back_to_previous_title_if_tracklist_missing(
    orch_with_lock, monkeypatch, tmp_path,
):
    """If the locked album has no tracklist, the payload keeps the previous
    title rather than crashing."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    orch_with_lock.state.last_vinyl["tracklist"] = None
    from nowplaying.vinyl import fingerprint as fp
    # hits=80 — above the MIN_FINGERPRINT_HITS=60 threshold so the gate passes.
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D3", hits=80, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is True
    published = orch_with_lock.bcast.publish.await_args.args[0]
    # Title stays as last_vinyl's (no tracklist to look up from).
    assert published["title"] == "Heart and Soul"


# ── _build_fingerprint_payload duration threading ───────────────────────


def test_build_fingerprint_payload_threads_matched_duration():
    """The F3 overlay must set the MATCHED track's duration_seconds, not
    inherit the locked payload's stale (previous-track) duration. The
    builder copies locked_payload (carrying the prior track's duration) and
    swaps in a new track_position — so it must overwrite duration too, or
    the scrobble path uses the wrong track's length."""
    from nowplaying.orchestrator.fingerprint import _build_fingerprint_payload
    locked_payload = {
        "release_id": 12345,
        "artist": "Green Day",
        "album": "American Idiot",
        "track_position": "A1",
        "title": "American Idiot",
        "duration_seconds": 174,  # stale: belongs to A1, the previous track
        "tracklist": [
            {"position": "A1", "title": "American Idiot", "duration_seconds": 174},
            {"position": "A5", "title": "Are We The Waiting", "duration_seconds": 162},
        ],
    }
    top = Hit(ref_id=1, release_id=12345, track_position="A5", hits=80, track_position_s=0.0)
    payload = _build_fingerprint_payload(locked_payload, top, "vinyl")
    assert payload["track_position"] == "A5"
    assert payload["title"] == "Are We The Waiting"
    assert payload["duration_seconds"] == 162  # matched track, not stale 174


# ── Confidence gates (threshold + margin) ───────────────────────────────


def test_fallback_below_threshold_returns_false(orch_with_lock, monkeypatch, tmp_path):
    """Hit below MIN_FINGERPRINT_HITS (60) is rejected as if no match.

    Observed live 2026-05-18: Leo false positives scored 15–51 hits while the
    correct Pitiful match scored 136.  A hit at 51 must not publish.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D3", hits=51, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()
    # Streak must NOT be reset on a rejected match.
    assert orch_with_lock.state.unmatched_streak == 5


def test_fallback_insufficient_margin_returns_false(orch_with_lock, monkeypatch, tmp_path):
    """Near-tie (top=80, runner_up=70) is rejected because top < 2 * runner_up.

    Both hits exceed MIN_FINGERPRINT_HITS=60 so the threshold gate passes, but
    80 < 2*70=140 fails the margin gate.  A margin this small is statistically
    meaningless on a sparse DB.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D3", hits=80, track_position_s=0.0),
        Hit(ref_id=2, release_id=12345, track_position="D2", hits=70, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is False
    orch_with_lock.bcast.publish.assert_not_called()
    assert orch_with_lock.state.unmatched_streak == 5


def test_fallback_passes_threshold_and_margin(orch_with_lock, monkeypatch, tmp_path):
    """top=136, runner_up=15 — well above threshold and 9× the runner_up.

    Models the Pitiful correct match from the 2026-05-18 live session.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D3", hits=136, track_position_s=0.0),
        Hit(ref_id=2, release_id=12345, track_position="D2", hits=15, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is True
    assert orch_with_lock.bcast.publish.await_count == 1
    published = orch_with_lock.bcast.publish.await_args.args[0]
    assert published["track_position"] == "D3"
    assert orch_with_lock.state.unmatched_streak == 0


def test_fallback_single_hit_above_threshold_passes(orch_with_lock, monkeypatch, tmp_path):
    """Single hit at 80 (no runner-up) — margin check is skipped, gate passes.

    When a release has refs for only one track, the margin check would
    always false-reject.  Single-hit-above-threshold is sufficient.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        Hit(ref_id=1, release_id=12345, track_position="D1", hits=80, track_position_s=0.0),
    ])
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))
    assert result is True
    assert orch_with_lock.bcast.publish.await_count == 1
    published = orch_with_lock.bcast.publish.await_args.args[0]
    assert published["track_position"] == "D1"
    assert orch_with_lock.state.unmatched_streak == 0


# ── _schedule_coverage_promotion (coverage-driven pin path) ─────────────
#
# These tests verify the synchronous gate checks. The async integration
# tests are in test_orchestrator_coverage_promotion.py.


def test_pin_promotion_skipped_when_disabled(orch_with_lock, monkeypatch):
    """fingerprint_enabled=False → no task scheduled."""
    from nowplaying.vinyl import promotion as prom
    orch_with_lock.fingerprint_enabled = False
    orch_with_lock.state.user_track_pin = {
        "release_id": 12345, "track_position": "D2",
        "monotonic_ts": 100.0, "duration_seconds": None,
    }
    monkeypatch.setattr(prom, "should_promote_for_coverage", lambda *a, **kw: True)
    captured: list = []
    monkeypatch.setattr(asyncio, "create_task", lambda c: captured.append(c) or mock.MagicMock())
    _run(orch_with_lock._schedule_coverage_promotion(b"wav", -20.0))
    assert captured == []


def test_pin_promotion_skipped_when_no_pin(orch_with_lock, monkeypatch):
    """No pin → no task scheduled."""
    from nowplaying.vinyl import promotion as prom
    orch_with_lock.state.user_track_pin = None
    monkeypatch.setattr(prom, "should_promote_for_coverage", lambda *a, **kw: True)
    captured: list = []
    monkeypatch.setattr(asyncio, "create_task", lambda c: captured.append(c) or mock.MagicMock())
    _run(orch_with_lock._schedule_coverage_promotion(b"wav", -20.0))
    assert captured == []


def test_pin_promotion_skipped_when_pin_ttl_expired(orch_with_lock, monkeypatch):
    """Expired pin (now - monotonic_ts > duration + buffer) → no task."""
    from nowplaying.vinyl import promotion as prom
    # Build a pin whose TTL is definitely expired.
    orch_with_lock.state.user_track_pin = {
        "release_id": 12345, "track_position": "D2",
        "monotonic_ts": -1e9, "duration_seconds": 10,
    }
    monkeypatch.setattr(prom, "should_promote_for_coverage", lambda *a, **kw: True)
    captured: list = []

    async def driver():
        loop_create = asyncio.get_running_loop().create_task

        def capture(coro):
            captured.append(coro)
            return loop_create(asyncio.sleep(0))

        monkeypatch.setattr(asyncio, "create_task", capture)
        await orch_with_lock._schedule_coverage_promotion(b"wav", -20.0)

    _run(driver())
    assert captured == []


def test_pin_promotion_skipped_when_no_track_position(orch_with_lock, monkeypatch):
    """Pin present but track_position empty → no task."""
    from nowplaying.vinyl import promotion as prom
    orch_with_lock.state.user_track_pin = {
        "release_id": 12345, "track_position": "",
        "monotonic_ts": 100.0, "duration_seconds": None,
    }
    monkeypatch.setattr(prom, "should_promote_for_coverage", lambda *a, **kw: True)
    captured: list = []
    monkeypatch.setattr(asyncio, "create_task", lambda c: captured.append(c) or mock.MagicMock())
    _run(orch_with_lock._schedule_coverage_promotion(b"wav", -20.0))
    assert captured == []


def test_pin_promotion_creates_task_with_correct_args(
    orch_with_lock, monkeypatch,
):
    """Valid pin + clip → maybe_promote called with pin's release_id,
    pos, and elapsed-since-pin as track_position_s."""
    from nowplaying.vinyl import promotion as prom
    from nowplaying.orchestrator.streaming_idle import MUSIC_DB
    captured: dict = {}

    async def fake_maybe_promote(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(prom, "maybe_promote", fake_maybe_promote)
    # Coverage check: always report a gap so the task fires.
    monkeypatch.setattr(prom, "should_promote_for_coverage", lambda *a, **kw: True)

    async def driver():
        now = asyncio.get_running_loop().time()
        orch_with_lock.state.user_track_pin = {
            "release_id": 99,
            "track_position": "B3",
            "monotonic_ts": now - 7.5,
            "duration_seconds": None,
        }
        await orch_with_lock._schedule_coverage_promotion(
            b"wavbytes", MUSIC_DB + 5.0,
        )
        # Yield until the fire-and-forget task completes.
        for _ in range(50):
            if captured:
                return
            await asyncio.sleep(0.01)

    _run(driver())
    assert captured["release_id"] == 99
    assert captured["track_position"] == "B3"
    # Elapsed is approximately 7.5s (loop has barely advanced).
    assert 7.0 <= captured["track_position_s"] <= 8.5
    assert captured["wav_bytes"] == b"wavbytes"
    # No shazam_result / llm / recent_history kwargs (removed in
    # `promotion-on-confirmation`).
    assert "shazam_result" not in captured
    assert "llm" not in captured
    assert "recent_history" not in captured


def test_fallback_no_hits_schedules_coverage_promotion(
    orch_with_lock, monkeypatch, tmp_path,
):
    """`_try_fingerprint_fallback` no-hit branch invokes the coverage-driven
    promotion helper (previously: _maybe_schedule_pin_promotion)."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [])
    invoked: list = []

    async def capture(wav_bytes, level_db):
        invoked.append((wav_bytes, level_db))

    orch_with_lock._schedule_coverage_promotion = capture
    result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl", -20.0))
    assert result is False
    assert len(invoked) == 1
    assert invoked[0][0] == b"fake"


# ── _handle_non_shazam_heartbeat routing ─────────────────────────────────


def test_unmatched_method_routes_to_fingerprint_fallback(
    orch_with_lock, monkeypatch, tmp_path,
):
    """Regression test for P0 bug: method='unmatched' must invoke
    `_try_fingerprint_fallback`, not skip it.

    Before the fix, `_handle_non_shazam_heartbeat` checked `if method is None:`
    but recognize_proto always returns `match_method="unmatched"` on a Shazam
    miss. This meant `_try_fingerprint_fallback` (and therefore
    `_maybe_schedule_pin_promotion`) was never called on a real Shazam miss,
    so fp_refs stayed empty no matter how many times a user pinned a track.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    fallback_called: list = []

    async def fake_fallback(cp, src, level_db=0.0):
        fallback_called.append((cp, src, level_db))
        return False  # simulate no-hit so caller continues to unmatched path

    orch_with_lock._try_fingerprint_fallback = fake_fallback
    # _handle_unmatched_heartbeat needs level_db routed through it; stub it.
    orch_with_lock._handle_unmatched_heartbeat = mock.AsyncMock()

    _run(orch_with_lock._handle_non_shazam_heartbeat(
        "unmatched", clip, "vinyl", -20.0,
    ))

    assert len(fallback_called) == 1, (
        "_try_fingerprint_fallback must be called when method='unmatched'"
    )
    assert fallback_called[0][:2] == (clip, "vinyl")


def test_none_method_also_routes_to_fingerprint_fallback(
    orch_with_lock, monkeypatch, tmp_path,
):
    """Legacy callers that return method=None still invoke the fallback."""
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")
    fallback_called: list = []

    async def fake_fallback(cp, src, level_db=0.0):
        fallback_called.append((cp, src, level_db))
        return False

    orch_with_lock._try_fingerprint_fallback = fake_fallback
    orch_with_lock._handle_unmatched_heartbeat = mock.AsyncMock()

    _run(orch_with_lock._handle_non_shazam_heartbeat(
        None, clip, "vinyl", -20.0,
    ))

    assert len(fallback_called) == 1


# ── Shazam-hit → no promotion (removed dead code) ────────────────────────


def test_shazam_hit_does_not_call_maybe_promote(orch_with_lock, monkeypatch, tmp_path):
    """Confident Shazam hit with a known release_id MUST NOT trigger
    fingerprint promotion. `_schedule_fingerprint_promotion` was deleted;
    this test ensures the invariant is not accidentally reintroduced.

    Acceptance criterion from idea.md:
    > a confident Shazam hit on a known track produces NO
    > future: <Task...> exception=... lines and adds NO rows to fp_refs.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    promote_calls: list = []

    async def fake_maybe_promote(**kwargs):
        promote_calls.append(kwargs)
        return True

    from nowplaying.vinyl import promotion as prom
    monkeypatch.setattr(prom, "maybe_promote", fake_maybe_promote)

    # Stub out history and bcast so _publish_shazam_match can run
    orch_with_lock.bcast.publish = mock.AsyncMock()

    shazam_result = {
        "title": "The Nurse Who Loved Me",
        "artist": "A Perfect Circle",
        "release_id": 31427573,
        "track_position": "C13",
        "track_position_s": 5.0,
        "match_method": "shazam",
    }

    async def driver():
        with mock.patch("nowplaying.orchestrator._class.history") as hist:
            hist.record_play = mock.AsyncMock()
            # Simulate the rid-is-not-None fast path (no shazam-only gate needed)
            await orch_with_lock._publish_shazam_match(
                shazam_result, "vinyl", clip, -20.0,
            )
            # Let any fire-and-forget tasks drain
            for _ in range(20):
                if promote_calls:
                    break
                await asyncio.sleep(0.01)

    _run(driver())

    assert promote_calls == [], (
        "Shazam-hit-driven promotion was removed; maybe_promote must not be "
        "called from the Shazam-hit path. Got calls: %r" % promote_calls
    )


# ── Position-change guard (anchor-release-on-different-position) ─────────
#
# These tests cover the bug where a strong fingerprint hit for a DIFFERENT
# track position was folded into the stale last_vinyl dict, producing a
# Frankenstein payload (title from old track, track_position from new hit).
# Live evidence 2026-05-18 18:40: anchor on Pitiful (C10), Leo match (C11)
# published title='Pitiful' but track_position=C11.


_DISCOGS_RELEASE_D = {
    "id": 12345,
    "artist": "Joy Division",
    "title": "Closer",
    "year": 1980,
    "label": "Factory",
    "catno": "FACT 25",
    "tracks": [
        {"position": "D1", "side": "D", "title": "Heart and Soul", "duration_seconds": 330},
        {"position": "D2", "side": "D", "title": "Twenty Four Hours", "duration_seconds": 240},
        {"position": "D3", "side": "D", "title": "The Eternal", "duration_seconds": 365},
    ],
}


def test_confirmation_different_position_releases_anchor_and_rebuilds_payload(
    orch_with_lock, monkeypatch, tmp_path,
):
    """Confirmation path: anchor on D1, hit comes in for D3 at hits=80 (≥ 60).

    Expected:
    - Old anchor cleared and re-set on D3
    - Published payload built fresh from catalog: title='The Eternal' (not 'Heart and Soul')
    - Both columns of the kiosk would now agree on D3 / The Eternal
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    # Anchor is on D1 (the old track that was playing).
    orch_with_lock.state.fingerprint_anchor = {
        "release_id": 12345,
        "track_position": "D1",
        "monotonic_ts": 900.0,
        "hits": 73,
        "duration_seconds": 330,
    }

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        # New hit: same release, different position — user moved the needle.
        # hits=100 >> MIN_FINGERPRINT_HITS_ANCHORED * STRONG_FINGERPRINT_ANCHOR_MULTIPLIER
        # (60 * 0.5 = 30) so the new anchor will be set.
        Hit(ref_id=3, release_id=12345, track_position="D3", hits=100, track_position_s=0.0),
    ])
    # _build_blind_fingerprint_payload will call discogs_catalog.get_release.
    from nowplaying.discogs import catalog as cat
    monkeypatch.setattr(cat, "get_release", lambda rid: dict(_DISCOGS_RELEASE_D))

    import asyncio
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_001.0
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    with mock.patch("nowplaying.orchestrator._class.history") as hist:
        hist.record_play = mock.AsyncMock()
        result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    assert orch_with_lock.bcast.publish.await_count == 1
    published = orch_with_lock.bcast.publish.await_args.args[0]

    # Payload must be fully consistent with the new track.
    assert published["track_position"] == "D3"
    assert published["title"] == "The Eternal", (
        "Title must come from the new track's catalog record, not the stale anchor"
    )
    assert published["match_method"] == "fingerprint"
    assert published["release_id"] == 12345

    # Anchor must be re-set on D3 (hits=100 ≥ 60*1.5=90 strong threshold).
    new_anchor = orch_with_lock.state.fingerprint_anchor
    assert new_anchor is not None
    assert new_anchor["track_position"] == "D3", (
        "Anchor must be re-set on the new position after a strong position-change hit"
    )
    assert new_anchor["release_id"] == 12345


def test_confirmation_same_position_keeps_anchor(
    orch_with_lock, monkeypatch, tmp_path,
):
    """Confirmation path: anchor on D1, hit for D1 again (same position).

    The overlay path fires, NOT the fresh-payload path.
    Anchor is refreshed (monotonic_ts updated) to reset its TTL.
    Published title comes from the locked tracklist (Heart and Soul).
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    # Anchor and last_vinyl both on D1.
    orch_with_lock.state.fingerprint_anchor = {
        "release_id": 12345,
        "track_position": "D1",
        "monotonic_ts": 900.0,
        "hits": 73,
        "duration_seconds": 330,
    }

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        # Same position as anchor — strong confirmation hit.
        Hit(ref_id=1, release_id=12345, track_position="D1", hits=100, track_position_s=0.0),
    ])

    import asyncio
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_002.0
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    with mock.patch("nowplaying.orchestrator._class.history") as hist:
        hist.record_play = mock.AsyncMock()
        result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    published = orch_with_lock.bcast.publish.await_args.args[0]
    # Same-position path: overlay on locked last_vinyl, title from tracklist.
    assert published["track_position"] == "D1"
    assert published["title"] == "Heart and Soul"

    # Anchor must be refreshed (not cleared) — hits=100 ≥ 60*1.5=90.
    refreshed = orch_with_lock.state.fingerprint_anchor
    assert refreshed is not None
    assert refreshed["track_position"] == "D1"
    assert refreshed["monotonic_ts"] == 1_000_002.0, (
        "Anchor monotonic_ts must be refreshed on strong same-position confirmation"
    )


def test_confirmation_no_anchor_uses_overlay_path_with_correct_title(
    orch_with_lock, monkeypatch, tmp_path,
):
    """When no anchor is set, the confirmation path uses the overlay
    (_build_fingerprint_payload) which looks up the title from the locked
    tracklist.  The tracklist key bug fix (checking both "position" and
    "track_position") ensures this works even when last_vinyl was built by
    the blind path (tracklist uses "position" key).

    This covers the reviewer's finding: without the key fix, a blind-built
    last_vinyl would cause the overlay to miss the tracklist lookup and
    return the old title from the previous track.
    """
    clip = tmp_path / "clip.wav"
    clip.write_bytes(b"fake")

    # No anchor (prior hit was below strong threshold).
    orch_with_lock.state.fingerprint_anchor = None

    # Simulate last_vinyl built by the blind path: tracklist uses "position" key.
    orch_with_lock.state.last_vinyl = {
        "title": "Heart and Soul",  # old title — must be overwritten
        "track_position": "D1",
        "side": "D",
        "release_id": 12345,
        "artist": "Joy Division",
        "album": "Closer",
        "tracklist": [
            # Blind-path format: "position" key (not "track_position")
            {"position": "D1", "title": "Heart and Soul"},
            {"position": "D2", "title": "Twenty Four Hours"},
            {"position": "D3", "title": "The Eternal"},
        ],
    }

    from nowplaying.vinyl import fingerprint as fp
    monkeypatch.setattr(fp, "match", lambda *a, **kw: [
        # Hit is for D3 on same album — normal intra-album advance.
        Hit(ref_id=3, release_id=12345, track_position="D3", hits=80, track_position_s=0.0),
    ])

    import asyncio
    fake_loop = mock.MagicMock()
    fake_loop.time.return_value = 1_000_003.0
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    with mock.patch("nowplaying.orchestrator._class.history") as hist:
        hist.record_play = mock.AsyncMock()
        result = _run(orch_with_lock._try_fingerprint_fallback(clip, "vinyl"))

    assert result is True
    published = orch_with_lock.bcast.publish.await_args.args[0]
    # Overlay path: title updated from tracklist (both "position" and
    # "track_position" key formats are handled by _build_fingerprint_payload).
    assert published["track_position"] == "D3"
    assert published["title"] == "The Eternal", (
        "Title must be looked up from tracklist using either 'position' or "
        "'track_position' key — was the dual-key fix applied to _build_fingerprint_payload?"
    )


# ── matched track title (cleaning delegated to publish choke point) ──────


def test_build_fingerprint_payload_uses_matched_track_title():
    # Builder now returns the RAW matched title; _apply_clean_display_title
    # at the publish choke point handles cleaning for display.
    from nowplaying.orchestrator.fingerprint import _build_fingerprint_payload
    locked_payload = {
        "release_id": 1, "artist": "The Beatles", "album": "Blue",
        "track_position": "A1", "title": "x",
        "tracklist": [
            {"position": "A2", "title": "Penny Lane (2017 Mix)",
             "clean_title": "Penny Lane", "duration_seconds": 163},
        ],
    }
    top = Hit(ref_id=1, release_id=1, track_position="A2", hits=80, track_position_s=0.0)
    payload = _build_fingerprint_payload(locked_payload, top, "vinyl")
    assert payload["title"] == "Penny Lane (2017 Mix)"
    assert payload["duration_seconds"] == 163
    assert payload["track_position"] == "A2"


def test_build_fingerprint_payload_uses_raw_title_when_no_clean():
    from nowplaying.orchestrator.fingerprint import _build_fingerprint_payload
    locked_payload = {
        "release_id": 1, "track_position": "A1", "title": "x",
        "tracklist": [{"position": "A2", "title": "Bury Me", "duration_seconds": 200}],
    }
    top = Hit(ref_id=1, release_id=1, track_position="A2", hits=80, track_position_s=0.0)
    payload = _build_fingerprint_payload(locked_payload, top, "vinyl")
    assert payload["title"] == "Bury Me"

"""Tests for the fingerprint promotion module.

Covers the three layered gates: cross-cohort audio-similarity guard,
static cohort cap, static spacing on `track_position_s`. The fingerprint
engine itself is exercised by `test_fingerprint.py`; the orchestrator's
pin-state-driven scheduling is exercised by `test_main_pin_driven.py`.
"""
from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nowplaying.vinyl import fingerprint, promotion


def _make_wav(seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    sr = 44100
    t = np.linspace(0, 12.0, sr * 12, endpoint=False)
    audio = np.sin(2 * np.pi * (440 + seed) * t) + 0.5 * np.sin(2 * np.pi * (880 + seed * 2) * t)
    audio += 0.1 * rng.standard_normal(len(t))
    audio /= np.max(np.abs(audio)) + 1e-9
    stereo = np.stack([audio, audio], axis=1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, stereo, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "fp.db"
    fingerprint.init_db(db)
    return db


def _run(coro):
    return asyncio.run(coro)


# ── Pin-driven happy path ───────────────────────────────────────────────


def test_pin_driven_promotion_writes_ref(tmp_db: Path):
    """Empty DB + valid clip → one row lands in fp_refs."""
    result = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=5.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    ))
    assert result is True
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM fp_refs WHERE release_id=100 AND track_position='A1'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_pin_driven_promotion_skips_empty_track_position(tmp_db: Path):
    """Without a track_position the ref has no cohort identity."""
    r = _run(promotion.maybe_promote(
        release_id=100, track_position="", track_position_s=0.0,
        wav_bytes=_make_wav(), db_path=tmp_db,
    ))
    assert r is False


# ── Static cohort cap ──────────────────────────────────────────────────


def test_pin_driven_promotion_static_gate_caps_cohort(tmp_db: Path, monkeypatch):
    """Fill the cohort cap; the next promotion is rejected by the static gate.

    Uses a short track (duration_s=30s → cap=30 floor) but with
    _MIN_COHORT_CAP monkeypatched to 3 to keep the test fast.
    """
    monkeypatch.setattr(promotion, "_MIN_COHORT_CAP", 3)
    monkeypatch.setattr(promotion, "MIN_REF_SPACING_S", 0.1)
    for i in range(3):
        assert _run(promotion.maybe_promote(
            release_id=100, track_position="A1",
            track_position_s=float(i * 100),
            wav_bytes=_make_wav(seed=i), db_path=tmp_db,
        )) is True
    # Cap reached → next rejected.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=1000.0,
        wav_bytes=_make_wav(seed=99), db_path=tmp_db,
    )) is False


# ── Static spacing ─────────────────────────────────────────────────────


def test_pin_driven_promotion_static_gate_spacing(tmp_db: Path):
    """A new ref within MIN_REF_SPACING_S of an existing one is rejected."""
    r1 = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=20.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    ))
    assert r1 is True
    # Within 12s (5s gap) → reject.  Enforces one ref per 15s heartbeat window.
    r2 = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=25.0,
        wav_bytes=_make_wav(seed=2), db_path=tmp_db,
    ))
    assert r2 is False
    # 15s away (> 12s threshold) → accept.
    r3 = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=35.0,
        wav_bytes=_make_wav(seed=3), db_path=tmp_db,
    ))
    assert r3 is True


def test_regular_spacing_rejects_within_12s(tmp_db: Path):
    """Boundary test for the 12s spacing threshold (MIN_REF_SPACING_S=12.0).

    A ref 9s after an existing one is rejected; one 13s after is accepted.
    This ensures the 15s heartbeat window has at most one ref even under
    capture jitter.
    """
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=20.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    )) is True
    # 9s gap — less than MIN_REF_SPACING_S=12 → reject.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=29.0,
        wav_bytes=_make_wav(seed=2), db_path=tmp_db,
    )) is False
    # 13s gap — greater than MIN_REF_SPACING_S=12 → accept.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=33.0,
        wav_bytes=_make_wav(seed=3), db_path=tmp_db,
    )) is True


def test_cohort_cap_30_accepts_up_to_30_refs(tmp_db: Path):
    """With the floor cap of 30, exactly 30 well-spaced refs are accepted
    and the 31st is rejected for a short track (duration_s=None → floor).

    Uses 15s spacing (> 12s threshold) to isolate the cap gate.
    """
    assert promotion.max_refs_for_duration(None) == 30, (
        "This test validates the floor — monkeypatch not used"
    )
    for i in range(30):
        result = _run(promotion.maybe_promote(
            release_id=999, track_position="D1",
            track_position_s=float(i * 15),
            wav_bytes=_make_wav(seed=i + 500), db_path=tmp_db,
            duration_s=None,
        ))
        assert result is True, f"ref {i} (t={i * 15}s) should be accepted"
    # 31st ref — cohort now full → reject.
    assert _run(promotion.maybe_promote(
        release_id=999, track_position="D1", track_position_s=450.0,
        wav_bytes=_make_wav(seed=600), db_path=tmp_db,
        duration_s=None,
    )) is False


# ── Cohort isolation ───────────────────────────────────────────────────


def test_promotion_different_cohorts_independent(tmp_db: Path):
    """Different (release, track_position) → separate cohorts; cap applies independently."""
    for i in range(2):
        assert _run(promotion.maybe_promote(
            release_id=100, track_position="A1", track_position_s=float(i * 30),
            wav_bytes=_make_wav(seed=i), db_path=tmp_db,
        )) is True
    for i in range(2):
        assert _run(promotion.maybe_promote(
            release_id=100, track_position="A2", track_position_s=float(i * 30),
            wav_bytes=_make_wav(seed=i + 10), db_path=tmp_db,
        )) is True
    for i in range(2):
        assert _run(promotion.maybe_promote(
            release_id=200, track_position="A1", track_position_s=float(i * 30),
            wav_bytes=_make_wav(seed=i + 20), db_path=tmp_db,
        )) is True


# ── Cross-cohort guard ─────────────────────────────────────────────────


def test_first_cohort_bypasses_guard(tmp_db: Path, monkeypatch):
    """Zero existing refs for a release → guard bypasses without consulting
    fingerprint.match. (We verify by patching match to raise.)"""
    def boom(*args, **kwargs):
        raise RuntimeError("guard should not call match on first cohort")
    monkeypatch.setattr(fingerprint, "match", boom)
    r = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    ))
    assert r is True


def test_guard_refuses_when_audio_matches_different_cohort(
    tmp_db: Path, monkeypatch, caplog,
):
    """With refs already present for position A1, a pin for A2 using the
    SAME audio is refused (cross-cohort guard catches the user error)."""
    # Seed: a ref for A1 using wav_seed=1.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    )) is True
    # Force the guard to see a strong match for A1 when the user tries A2.
    Hit = fingerprint.Hit
    def fake_match(wav, release_id, min_hits=10, *, db_path=None):
        return [Hit(ref_id=1, release_id=release_id, track_position="A1",
                    hits=999, track_position_s=0.0)]
    monkeypatch.setattr(fingerprint, "match", fake_match)
    with caplog.at_level(logging.INFO, logger="nowplaying.promotion"):
        r = _run(promotion.maybe_promote(
            release_id=100, track_position="A2", track_position_s=0.0,
            wav_bytes=_make_wav(seed=1), db_path=tmp_db,
        ))
    assert r is False
    assert any(
        "audio-matches-different-cohort" in rec.getMessage()
        for rec in caplog.records
    )
    # No row should have landed for A2.
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM fp_refs WHERE release_id=100 AND track_position='A2'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_guard_allows_when_audio_matches_same_cohort(tmp_db: Path, monkeypatch):
    """With refs for A1, a NEW ref for A1 (well-spaced) is allowed even if
    the guard's fingerprint.match returns a hit — same cohort is fine."""
    # Seed: a ref for A1.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    )) is True
    # Add A2 so "other position" count > 0 (so guard actually runs).
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A2", track_position_s=0.0,
        wav_bytes=_make_wav(seed=2), db_path=tmp_db,
    )) is True
    # Now mock match to return a strong A1 hit (same cohort as the target).
    Hit = fingerprint.Hit
    def fake_match(wav, release_id, min_hits=10, *, db_path=None):
        return [Hit(ref_id=1, release_id=release_id, track_position="A1",
                    hits=999, track_position_s=0.0)]
    monkeypatch.setattr(fingerprint, "match", fake_match)
    # New A1 ref well-spaced from the existing one → should succeed.
    r = _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=50.0,
        wav_bytes=_make_wav(seed=3), db_path=tmp_db,
    ))
    assert r is True


def test_guard_allows_when_audio_does_not_match(tmp_db: Path, monkeypatch):
    """With refs for A1, pinning A2 using unrelated audio is allowed
    (no match returned by fingerprint.match)."""
    # Seed: a ref for A1.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    )) is True
    # Mock match to return no hits (audio unrelated to existing refs).
    monkeypatch.setattr(fingerprint, "match", lambda *a, **kw: [])
    r = _run(promotion.maybe_promote(
        release_id=100, track_position="A2", track_position_s=0.0,
        wav_bytes=_make_wav(seed=2), db_path=tmp_db,
    ))
    assert r is True


def test_guard_failure_allows_promotion(tmp_db: Path, monkeypatch, caplog):
    """If the guard helper raises (transient DB error), allow the
    promotion to proceed and log a warning. Defensive, not gatekeeping."""
    # Seed: a ref for A1 so the guard would actually run.
    assert _run(promotion.maybe_promote(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=_make_wav(seed=1), db_path=tmp_db,
    )) is True
    def boom(*args, **kwargs):
        raise RuntimeError("simulated transient")
    monkeypatch.setattr(fingerprint, "match", boom)
    with caplog.at_level(logging.WARNING, logger="nowplaying.promotion"):
        r = _run(promotion.maybe_promote(
            release_id=100, track_position="A2", track_position_s=0.0,
            wav_bytes=_make_wav(seed=42), db_path=tmp_db,
        ))
    assert r is True
    assert any("guard failed" in rec.getMessage() for rec in caplog.records)


# ── add_ref failure ────────────────────────────────────────────────────


def test_promotion_logs_error_on_add_ref_failure(tmp_db: Path, monkeypatch, caplog):
    """add_ref exception → returns False + logs a warning, doesn't propagate."""
    def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")
    monkeypatch.setattr(fingerprint, "add_ref", boom)
    with caplog.at_level(logging.WARNING, logger="nowplaying.promotion"):
        r = _run(promotion.maybe_promote(
            release_id=100, track_position="A1", track_position_s=0.0,
            wav_bytes=_make_wav(), db_path=tmp_db,
        ))
    assert r is False
    assert any("simulated DB failure" in rec.getMessage() for rec in caplog.records)


# ── max_refs_for_duration unit tests ──────────────────────────────────────


def test_max_refs_for_duration_none_returns_floor():
    """None / missing duration → conservative fallback of 30."""
    assert promotion.max_refs_for_duration(None) == 30


def test_max_refs_for_duration_449s_floor():
    """449s: ceil(449/15) = 30, so floor applies → 30."""
    assert promotion.max_refs_for_duration(449.0) == 30


def test_max_refs_for_duration_450s_floor():
    """450s: ceil(450/15) = 30, exactly at floor → 30."""
    assert promotion.max_refs_for_duration(450.0) == 30


def test_max_refs_for_duration_451s_above_floor():
    """451s: ceil(451/15) = ceil(30.066…) = 31, above floor → 31."""
    assert promotion.max_refs_for_duration(451.0) == 31


def test_max_refs_for_duration_echoes():
    """1380s (Pink Floyd 'Echoes'): ceil(1380/15) = 92."""
    assert promotion.max_refs_for_duration(1380.0) == 92


# ── Adaptive cap integration tests ────────────────────────────────────────


def test_adaptive_cap_long_track_accepts_beyond_30(tmp_db: Path, monkeypatch):
    """A long track (duration_s=451s → cap=31) accepts a 31st ref that
    would be rejected at the short-track floor of 30."""
    monkeypatch.setattr(promotion, "MIN_REF_SPACING_S", 0.1)
    for i in range(31):
        result = _run(promotion.maybe_promote(
            release_id=200, track_position="B1",
            track_position_s=float(i * 100),
            wav_bytes=_make_wav(seed=i + 200),
            db_path=tmp_db,
            duration_s=451.0,
        ))
        assert result is True, f"ref {i} should be accepted (cap=31, got False)"
    # 32nd ref — now full → reject.
    assert _run(promotion.maybe_promote(
        release_id=200, track_position="B1", track_position_s=3200.0,
        wav_bytes=_make_wav(seed=999), db_path=tmp_db,
        duration_s=451.0,
    )) is False


def test_adaptive_cap_missing_duration_uses_floor(tmp_db: Path, monkeypatch):
    """duration_s=None falls back to floor=30; the 31st ref is rejected."""
    monkeypatch.setattr(promotion, "_MIN_COHORT_CAP", 3)
    monkeypatch.setattr(promotion, "MIN_REF_SPACING_S", 0.1)
    for i in range(3):
        assert _run(promotion.maybe_promote(
            release_id=300, track_position="C1",
            track_position_s=float(i * 100),
            wav_bytes=_make_wav(seed=i + 300),
            db_path=tmp_db,
            duration_s=None,
        )) is True
    assert _run(promotion.maybe_promote(
        release_id=300, track_position="C1", track_position_s=400.0,
        wav_bytes=_make_wav(seed=998), db_path=tmp_db,
        duration_s=None,
    )) is False

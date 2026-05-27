"""F2: Tests for the standalone fingerprint engine (no integration yet).

Synthetic WAV generation is used so tests run anywhere without
checked-in audio fixtures. Each test gets its own temp DB via the
tmp_path fixture.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nowplaying.vinyl import fingerprint as fp


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_wav(seconds: float = 12.0, freq: float = 440.0, sr: int = 44100,
              stereo: bool = True, seed: int = 0) -> bytes:  # skylos: ignore SKY-L029 — test helper; all args are keyword-defaulted, positional call sites are intentionally rare
    """Generate a synthetic WAV. Each call produces deterministic audio
    so identical inputs yield identical fingerprints."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    # Sine sweep with a unique base + harmonics so different seeds
    # produce different fingerprints. Add a touch of pink-ish noise
    # to ensure non-trivial peak finding.
    base = np.sin(2 * np.pi * (freq + 50 * seed) * t)
    base += 0.4 * np.sin(2 * np.pi * (2 * freq + 100 * seed) * t)
    base += 0.2 * rng.standard_normal(len(t))
    base /= np.max(np.abs(base)) + 1e-9
    if stereo:
        audio = np.stack([base, base], axis=1)
    else:
        audio = base
    buf = io.BytesIO()
    sf.write(buf, audio.astype(np.float32), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "fp.db"
    fp.init_db(db)
    return db


# ── Algorithm tests ─────────────────────────────────────────────────────


def test_fingerprint_returns_list_of_hash_offset_tuples():
    wav = _make_wav(seconds=4.0, seed=1)
    result = fp._fingerprint(wav)
    assert isinstance(result, list)
    assert result, "expected at least one hash for a 4s synthetic clip"
    h, off = result[0]
    assert isinstance(h, str) and len(h) == fp.FINGERPRINT_REDUCTION
    assert isinstance(off, int) and off >= 0


def test_fingerprint_is_deterministic():
    wav = _make_wav(seed=2)
    a = fp._fingerprint(wav)
    b = fp._fingerprint(wav)
    assert a == b


def test_fingerprint_handles_mono_and_stereo_identically():
    stereo = _make_wav(seed=3, stereo=True)
    mono = _make_wav(seed=3, stereo=False)
    # Same source signal, different channel layout → identical fingerprints.
    assert fp._fingerprint(stereo) == fp._fingerprint(mono)


def test_fingerprint_resamples_44100_to_22050():
    # Different SR, otherwise-identical signal. Resample should produce
    # the same fingerprint as if recorded at 22050 directly.
    wav_44k = _make_wav(seed=4, sr=44100, stereo=False)
    wav_22k = _make_wav(seed=4, sr=22050, stereo=False)
    a = fp._fingerprint(wav_44k)
    b = fp._fingerprint(wav_22k)
    # Resampling introduces numerical noise that perturbs peak
    # coordinates. The hashes will overlap only where peaks land on
    # identical (freq, time) bins post-resample. We assert non-trivial
    # overlap to confirm the pre-processing step works at all.
    overlap = set(h for h, _ in a) & set(h for h, _ in b)
    assert len(overlap) >= 3, f"only {len(overlap)} hashes overlap between sample rates"


# ── add_ref / match round-trip ──────────────────────────────────────────


def test_add_ref_then_match_round_trip(tmp_db: Path):
    wav = _make_wav(seed=5)
    ref_id = fp.add_ref(
        release_id=100, track_position="A1", track_position_s=0.0,
        wav_bytes=wav, db_path=tmp_db,
    )
    assert ref_id > 0
    hits = fp.match(wav, release_filter=100, min_hits=10, db_path=tmp_db)
    assert hits, "expected at least one hit when matching ref against itself"
    assert hits[0].ref_id == ref_id
    assert hits[0].release_id == 100
    assert hits[0].track_position == "A1"
    assert hits[0].hits >= 10


def test_match_filters_by_release(tmp_db: Path):
    wav_a = _make_wav(seed=10)
    wav_b = _make_wav(seed=20)
    fp.add_ref(100, "A1", 0.0, wav_a, db_path=tmp_db)
    fp.add_ref(200, "A1", 0.0, wav_b, db_path=tmp_db)
    # Query wav_a but ask for release 200 → no hits.
    hits = fp.match(wav_a, release_filter=200, min_hits=10, db_path=tmp_db)
    assert hits == []
    # Same query, release 100 → ref_a back.
    hits = fp.match(wav_a, release_filter=100, min_hits=10, db_path=tmp_db)
    assert len(hits) >= 1
    assert hits[0].release_id == 100


def test_match_below_min_hits_returns_empty(tmp_db: Path):
    wav_ref = _make_wav(seconds=2.0, seed=30)
    wav_other = _make_wav(seconds=2.0, seed=999)
    fp.add_ref(100, "A1", 0.0, wav_ref, db_path=tmp_db)
    hits = fp.match(wav_other, release_filter=100, min_hits=1000, db_path=tmp_db)
    assert hits == []


# ── Concurrency + persistence ───────────────────────────────────────────


def test_concurrent_add_ref_from_threads(tmp_db: Path):
    """8 threads each add a distinct ref → all rows present, no corruption."""
    barrier = threading.Barrier(8)

    def add(idx: int) -> int:
        wav = _make_wav(seed=100 + idx)
        barrier.wait()
        return fp.add_ref(
            release_id=300, track_position=f"A{idx + 1}",
            track_position_s=float(idx), wav_bytes=wav, db_path=tmp_db,
        )

    threads = [threading.Thread(target=add, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify all 8 refs landed.
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        n_refs = conn.execute(
            "SELECT COUNT(*) FROM fp_refs WHERE release_id = 300"
        ).fetchone()[0]
        assert n_refs == 8
        n_hashes = conn.execute(
            "SELECT COUNT(*) FROM fp_hashes"
        ).fetchone()[0]
        assert n_hashes > 0
    finally:
        conn.close()


def test_delete_refs_cascades_hashes(tmp_db: Path):
    wav = _make_wav(seed=50)
    ref_id = fp.add_ref(100, "A1", 0.0, wav, db_path=tmp_db)
    deleted = fp.delete_refs([ref_id], db_path=tmp_db)
    assert deleted == 1
    # fp_hashes rows cascade-deleted.
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM fp_hashes WHERE ref_id = ?", (ref_id,),
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_add_ref_unique_constraint_returns_existing_id(tmp_db: Path):
    wav = _make_wav(seed=60)
    first = fp.add_ref(100, "A1", 12.34, wav, db_path=tmp_db)
    second = fp.add_ref(100, "A1", 12.34, _make_wav(seed=61), db_path=tmp_db)
    assert first == second  # UNIQUE(release_id, track_position, track_position_s)

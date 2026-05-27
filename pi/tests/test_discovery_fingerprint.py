"""Round-trip tests for the MBID-keyed discovered fingerprint store.

Mirrors ``test_fingerprint.py``'s template but exercises
``nowplaying.discovery.fingerprint`` against ``discovered.sqlite``.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from nowplaying.discovery import fingerprint as dfp
from nowplaying.discovery import schema as discovery_schema


def _make_wav(seconds: float = 12.0, seed: int = 0) -> bytes:
    sr = 44100
    rng = np.random.default_rng(seed)
    t = np.linspace(0, seconds, int(sr * seconds), endpoint=False)
    base = np.sin(2 * np.pi * (440 + 50 * seed) * t)
    base += 0.4 * np.sin(2 * np.pi * (880 + 100 * seed) * t)
    base += 0.2 * rng.standard_normal(len(t))
    base /= np.max(np.abs(base)) + 1e-9
    audio = np.stack([base, base], axis=1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "discovered.sqlite"
    discovery_schema.init_db(db)
    return db


def test_add_ref_then_match_round_trip(tmp_db: Path):
    wav = _make_wav(seed=1)
    ref_id = dfp.add_ref(
        mbid="harvest-mb-1", track_position="A1", track_position_s=0.0,
        wav_bytes=wav, db_path=tmp_db,
    )
    assert ref_id > 0
    hits = dfp.match(wav, mbid_filter="harvest-mb-1", min_hits=10, db_path=tmp_db)
    assert hits, "expected at least one hit when matching ref against itself"
    assert hits[0].ref_id == ref_id
    assert hits[0].mbid == "harvest-mb-1"
    assert hits[0].release_id is None
    assert hits[0].track_position == "A1"
    assert hits[0].hits >= 10


def test_match_filters_by_mbid(tmp_db: Path):
    wav_a = _make_wav(seed=10)
    wav_b = _make_wav(seed=20)
    dfp.add_ref("mb-A", "A1", 0.0, wav_a, db_path=tmp_db)
    dfp.add_ref("mb-B", "A1", 0.0, wav_b, db_path=tmp_db)
    # Query wav_a but ask for mb-B → no hits.
    hits = dfp.match(wav_a, mbid_filter="mb-B", min_hits=10, db_path=tmp_db)
    assert hits == []
    # Same query, mb-A → hit back.
    hits = dfp.match(wav_a, mbid_filter="mb-A", min_hits=10, db_path=tmp_db)
    assert hits
    assert hits[0].mbid == "mb-A"


def test_blind_match_returns_all_store_hits(tmp_db: Path):
    wav_a = _make_wav(seed=30)
    wav_b = _make_wav(seed=40)
    dfp.add_ref("mb-A", "A1", 0.0, wav_a, db_path=tmp_db)
    dfp.add_ref("mb-B", "A1", 0.0, wav_b, db_path=tmp_db)
    # Blind scan with wav_a as the query should return the mb-A ref.
    hits = dfp.match(wav_a, mbid_filter=None, min_hits=10, db_path=tmp_db)
    assert hits
    assert hits[0].mbid == "mb-A"


def test_add_ref_unique_constraint_returns_existing_id(tmp_db: Path):
    wav = _make_wav(seed=50)
    first = dfp.add_ref("mb-X", "A1", 12.34, wav, db_path=tmp_db)
    # Same composite key with different audio → idempotent: existing id back.
    second = dfp.add_ref("mb-X", "A1", 12.34, _make_wav(seed=51), db_path=tmp_db)
    assert first == second


def test_match_missing_db_returns_empty(tmp_path: Path):
    # No init_db: the file doesn't exist.
    missing = tmp_path / "nowhere.sqlite"
    hits = dfp.match(_make_wav(seed=60), mbid_filter="x", db_path=missing)
    assert hits == []


def test_delete_refs_cascades_hashes(tmp_db: Path):
    wav = _make_wav(seed=70)
    ref_id = dfp.add_ref("mb-D", "A1", 0.0, wav, db_path=tmp_db)
    deleted = dfp.delete_refs([ref_id], db_path=tmp_db)
    assert deleted == 1
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM fp_hashes WHERE ref_id = ?", (ref_id,),
        ).fetchone()[0]
        assert n == 0
    finally:
        conn.close()


def test_schema_creates_fp_tables(tmp_db: Path):
    """init_db() must materialise fp_refs + fp_hashes in discovered.sqlite."""
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    try:
        names = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
    finally:
        conn.close()
    assert "fp_refs" in names
    assert "fp_hashes" in names

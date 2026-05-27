"""Tests for the clear-learned-fingerprints feature:

  - vinyl.fingerprint helpers: ``count_refs_for_track``, ``delete_refs_for_track``
  - POST /control/clear-fingerprints endpoint
  - PublishEnrichmentMixin._attach_learned_fingerprint_count

All DB tests hit a real sqlite file (per the project's testing memory:
mock-vs-prod divergence has burned us before).
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

_PI_ROOT = Path(__file__).resolve().parents[1]
if str(_PI_ROOT) not in sys.path:
    sys.path.insert(0, str(_PI_ROOT))

from nowplaying.control.clear_fingerprints import clear_fingerprints  # noqa: E402
from nowplaying.vinyl import fingerprint as fp  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────


def _seed_refs(db_path: Path, rows: list[tuple[int, str, float]]) -> None:
    """Insert (release_id, track_position, track_position_s) rows directly.
    Bypasses ``add_ref`` so we don't have to synthesize audio for every test.
    """
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO fp_refs (release_id, track_position, track_position_s, created_at) "
            "VALUES (?, ?, ?, '2026-05-22T00:00:00Z')",
            rows,
        )


def _mk_request(db_path: Path, body):
    app = {"fp_db_path": db_path}
    req = MagicMock(spec=web.Request)
    req.app = app
    req.json = AsyncMock(return_value=body)
    return req


def _decode(resp: web.Response) -> dict:
    return json.loads(resp.body.decode())


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fp_db(tmp_path: Path) -> Path:
    db = tmp_path / "fingerprint.db"
    fp.init_db(db)
    return db


# ── count_refs_for_track ────────────────────────────────────────────────


def test_count_refs_returns_zero_when_db_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist.db"
    assert fp.count_refs_for_track(123, "A1", db_path=missing) == 0


def test_count_refs_returns_zero_for_empty_cohort(fp_db: Path):
    assert fp.count_refs_for_track(123, "A1", db_path=fp_db) == 0


def test_count_refs_counts_only_matching_cohort(fp_db: Path):
    _seed_refs(fp_db, [
        (123, "A1", 0.0),
        (123, "A1", 12.0),
        (123, "A1", 24.0),
        (123, "A2", 0.0),         # different track on same release
        (456, "A1", 0.0),         # same track on different release
    ])
    assert fp.count_refs_for_track(123, "A1", db_path=fp_db) == 3
    assert fp.count_refs_for_track(123, "A2", db_path=fp_db) == 1
    assert fp.count_refs_for_track(456, "A1", db_path=fp_db) == 1


# ── delete_refs_for_track ───────────────────────────────────────────────


def test_delete_refs_returns_zero_when_db_missing(tmp_path: Path):
    missing = tmp_path / "does-not-exist.db"
    assert fp.delete_refs_for_track(123, "A1", db_path=missing) == 0


def test_delete_refs_returns_zero_for_empty_cohort(fp_db: Path):
    assert fp.delete_refs_for_track(123, "A1", db_path=fp_db) == 0


def test_delete_refs_removes_only_matching_cohort(fp_db: Path):
    _seed_refs(fp_db, [
        (123, "A1", 0.0),
        (123, "A1", 12.0),
        (123, "A2", 0.0),
        (456, "A1", 0.0),
    ])
    cleared = fp.delete_refs_for_track(123, "A1", db_path=fp_db)
    assert cleared == 2
    assert fp.count_refs_for_track(123, "A1", db_path=fp_db) == 0
    assert fp.count_refs_for_track(123, "A2", db_path=fp_db) == 1
    assert fp.count_refs_for_track(456, "A1", db_path=fp_db) == 1


def test_delete_refs_cascades_fp_hashes(fp_db: Path):
    """ON DELETE CASCADE on the FK should drop fp_hashes rows."""
    _seed_refs(fp_db, [(123, "A1", 0.0)])
    with sqlite3.connect(fp_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        ref_id = conn.execute(
            "SELECT id FROM fp_refs WHERE release_id=123 AND track_position='A1'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO fp_hashes (hash, ref_id, offset) VALUES (?, ?, ?)",
            ("deadbeef", ref_id, 0),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM fp_hashes").fetchone()[0] == 1
    fp.delete_refs_for_track(123, "A1", db_path=fp_db)
    with sqlite3.connect(fp_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fp_hashes").fetchone()[0] == 0


# ── POST /control/clear-fingerprints ────────────────────────────────────


def test_endpoint_clears_cohort_and_returns_count(fp_db: Path):
    _seed_refs(fp_db, [
        (123, "A1", 0.0),
        (123, "A1", 12.0),
        (123, "A2", 0.0),
    ])
    req = _mk_request(fp_db, {"release_id": 123, "track_position": "A1"})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 200
    body = _decode(resp)
    assert body == {"ok": True, "cleared": 2}
    assert fp.count_refs_for_track(123, "A1", db_path=fp_db) == 0
    assert fp.count_refs_for_track(123, "A2", db_path=fp_db) == 1


def test_endpoint_returns_zero_when_no_refs(fp_db: Path):
    req = _mk_request(fp_db, {"release_id": 999, "track_position": "B2"})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 200
    body = _decode(resp)
    assert body == {"ok": True, "cleared": 0}


def test_endpoint_rejects_missing_release_id(fp_db: Path):
    req = _mk_request(fp_db, {"track_position": "A1"})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 400
    body = _decode(resp)
    assert body["ok"] is False
    assert "release_id" in body["error"]


def test_endpoint_rejects_missing_track_position(fp_db: Path):
    req = _mk_request(fp_db, {"release_id": 123})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 400
    assert _decode(resp)["ok"] is False


def test_endpoint_rejects_empty_track_position(fp_db: Path):
    req = _mk_request(fp_db, {"release_id": 123, "track_position": ""})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 400


def test_endpoint_rejects_non_int_release_id(fp_db: Path):
    req = _mk_request(fp_db, {"release_id": "123", "track_position": "A1"})
    resp = _run(clear_fingerprints(req))
    assert resp.status == 400


def test_endpoint_rejects_malformed_json(fp_db: Path):
    app = {"fp_db_path": fp_db}
    req = MagicMock(spec=web.Request)
    req.app = app
    req.json = AsyncMock(side_effect=ValueError("bad json"))
    resp = _run(clear_fingerprints(req))
    assert resp.status == 400


# ── PublishEnrichmentMixin._attach_learned_fingerprint_count ────────────


def _patch_count_to_db(monkeypatch, db_path: Path):
    """Rewire the mixin's ``_fp.count_refs_for_track`` to hit a test DB.

    The mixin calls ``_fp.count_refs_for_track(release_id, track_position)``
    without a ``db_path`` arg — relying on ``DEFAULT_DB_PATH``. Monkeypatching
    the module-level constant doesn't change the function's default arg
    (Python binds defaults at def time). So we replace the helper itself
    with one that forwards to ``db_path``.
    """
    from nowplaying.orchestrator import _publish_enrichment

    original = fp.count_refs_for_track

    def _bound(*args, **kwargs):
        kwargs["db_path"] = db_path
        return original(*args, **kwargs)

    monkeypatch.setattr(_publish_enrichment._fp, "count_refs_for_track", _bound)
    return _publish_enrichment


def test_attach_count_stamps_field_when_refs_exist(fp_db: Path, monkeypatch):
    mod = _patch_count_to_db(monkeypatch, fp_db)
    _seed_refs(fp_db, [(123, "A1", 0.0), (123, "A1", 12.0)])
    mixin = mod.PublishEnrichmentMixin()
    payload = {"release_id": 123, "track_position": "A1"}
    mixin._attach_learned_fingerprint_count(payload)
    assert payload["learned_fingerprint_count"] == 2


def test_attach_count_stamps_zero_when_no_refs(fp_db: Path, monkeypatch):
    mod = _patch_count_to_db(monkeypatch, fp_db)
    mixin = mod.PublishEnrichmentMixin()
    payload = {"release_id": 123, "track_position": "A1"}
    mixin._attach_learned_fingerprint_count(payload)
    assert payload["learned_fingerprint_count"] == 0


def test_attach_count_skips_payload_without_release_id(fp_db: Path, monkeypatch):
    mod = _patch_count_to_db(monkeypatch, fp_db)
    mixin = mod.PublishEnrichmentMixin()
    payload = {"track_position": "A1"}
    mixin._attach_learned_fingerprint_count(payload)
    assert "learned_fingerprint_count" not in payload


def test_attach_count_skips_payload_without_track_position(fp_db: Path, monkeypatch):
    mod = _patch_count_to_db(monkeypatch, fp_db)
    mixin = mod.PublishEnrichmentMixin()
    payload = {"release_id": 123}
    mixin._attach_learned_fingerprint_count(payload)
    assert "learned_fingerprint_count" not in payload

"""Local audio fingerprint engine — Shazam-style landmark hashing.

Standalone module: no integration with recognize / promotion / kiosk
yet (F3 wires it into the cascade). Implements the algorithm from
dejavu (https://github.com/worldveil/dejavu, MIT-licensed) directly
against SQLite — no library vendoring required since the core
algorithm fits in ~200 lines.

Algorithm summary:
  1. Decode WAV → downmix to mono → resample to 22050 Hz.
  2. Compute spectrogram (scipy.signal.spectrogram, Hanning window).
  3. Log-transform amplitudes.
  4. Peak finding via scipy.ndimage.maximum_filter with a binary-
     structure neighborhood, thresholded at amp_min.
  5. For each peak, pair with up to `fan_value` subsequent peaks
     within a time-delta range; hash each pair as
     `sha1(f1|f2|t_delta).hexdigest()[:20]`. Result: list of
     (hash, offset) pairs per clip.

Match against the locked album's refs:
  1. Fingerprint the query.
  2. SELECT ref_id, offset FROM fp_hashes WHERE hash IN (...).
  3. Group hits by (ref_id, query_offset - ref_offset). The mode
     of the alignment delta within each ref is the score.
  4. Top-scoring ref above `min_hits` wins.

See `docs/features/fingerprint-engine/plan.md` for design rationale.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import sqlite3
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import numpy as np
import soundfile as sf
from scipy.ndimage import (
    binary_erosion,
    generate_binary_structure,
    iterate_structure,
    maximum_filter,
)
from scipy.signal import resample_poly, spectrogram

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "pi" / "data" / "fingerprint.db"

# Algorithm constants — dejavu's published defaults. Tunable here if the
# Pi-perf bench reveals accuracy or latency issues on real vinyl.
DEFAULT_FS = 22050
DEFAULT_WINDOW_SIZE = 4096
DEFAULT_OVERLAP_RATIO = 0.5
DEFAULT_FAN_VALUE = 5
DEFAULT_AMP_MIN = 10
PEAK_NEIGHBORHOOD_SIZE = 10
MIN_HASH_TIME_DELTA = 0
MAX_HASH_TIME_DELTA = 200
FINGERPRINT_REDUCTION = 20  # hex chars of sha1 kept per hash
CONNECTIVITY_MASK = 2  # diamond → square neighborhood (3x faster, same accuracy)

_DB_LOCK = threading.Lock()


class Hit(NamedTuple):
    ref_id: int
    release_id: int | None
    track_position: str
    hits: int
    track_position_s: float
    # MBID-keyed hits come from the discovered-release store
    # (``nowplaying.discovery.fingerprint``); Discogs-keyed hits set
    # ``release_id`` instead. Exactly one of the two is always set.
    mbid: str | None = None


# ── Database helpers ────────────────────────────────────────────────────


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None,
    )
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create the fingerprint schema if missing. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK, contextlib.closing(_connect(db_path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS fp_refs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                track_position TEXT NOT NULL,
                track_position_s REAL NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(release_id, track_position, track_position_s)
            );
            CREATE INDEX IF NOT EXISTS idx_fp_refs_release
              ON fp_refs(release_id);

            CREATE TABLE IF NOT EXISTS fp_hashes (
                hash TEXT NOT NULL,
                ref_id INTEGER NOT NULL,
                offset INTEGER NOT NULL,
                FOREIGN KEY(ref_id) REFERENCES fp_refs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_fp_hashes_hash
              ON fp_hashes(hash);
            CREATE INDEX IF NOT EXISTS idx_fp_hashes_ref
              ON fp_hashes(ref_id);
        """)


# ── Algorithm ───────────────────────────────────────────────────────────


def _decode_mono_22050(wav_bytes: bytes) -> np.ndarray:
    """Decode WAV → mono float32 → resample to DEFAULT_FS.

    The capture pipeline records 44100 Hz stereo; dejavu's constants
    assume 22050 Hz mono. Mismatched sample rate scales landmark
    frequencies and breaks hash matching across sources.
    """
    audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=True)  # skylos: ignore SKY-P401 — wav_bytes is an in-memory capture clip (~12s @ 44.1kHz stereo ≈ 2 MB), bounded by the heartbeat capture window; streaming the WAV header+PCM piecewise isn't supported by soundfile and would defeat the spectrogram's full-buffer FFT.
    # Downmix stereo (or N-channel) to mono by averaging channels.
    if audio.shape[1] > 1:
        audio = audio.mean(axis=1)
    else:
        audio = audio[:, 0]
    # Resample to 22050 Hz if needed.
    if sr != DEFAULT_FS:
        # resample_poly is faster + lower-artifact than resample for
        # integer ratios. Fall back to general resample for odd rates.
        from math import gcd
        g = gcd(int(sr), DEFAULT_FS)
        up = DEFAULT_FS // g
        down = int(sr) // g
        audio = resample_poly(audio, up, down).astype(np.float32)
    return audio


def _fingerprint(wav_bytes: bytes) -> list[tuple[str, int]]:
    """Compute a list of (hash, time_offset) pairs for one clip.

    Returns the raw fingerprint suitable for storing in fp_hashes or
    querying against. See module docstring for algorithm details.
    """
    audio = _decode_mono_22050(wav_bytes)
    if audio.size == 0:
        return []
    # Spectrogram. `noverlap` matches dejavu's wsize * overlap_ratio
    # formula. `mode='psd'` returns power-spectral-density values
    # equivalent to what dejavu reads from matplotlib's mlab.specgram.
    _, _, spec = spectrogram(
        audio,
        fs=DEFAULT_FS,
        window="hann",
        nperseg=DEFAULT_WINDOW_SIZE,
        noverlap=int(DEFAULT_WINDOW_SIZE * DEFAULT_OVERLAP_RATIO),
        mode="psd",
        scaling="spectrum",
    )
    # Log transform with zero-guard, then normalize to a 0..100
    # dynamic range so DEFAULT_AMP_MIN is interpreted as a percentage
    # of the spectrogram's range rather than an absolute dB value.
    # Dejavu's original code assumed mlab.specgram's scaling, which
    # differs from scipy.signal.spectrogram by orders of magnitude.
    # Normalization makes the threshold scaling-invariant.
    spec = 10.0 * np.log10(spec, out=np.full_like(spec, -300.0), where=(spec > 0))
    spec_min = spec.min()
    spec_max = spec.max()
    # Reject low-energy / silent clips. If the peak log-spec value is
    # below -60 dB, the audio is too quiet for meaningful peaks —
    # normalization would amplify line noise into false peaks (Gemini
    # impl-review should-fix). -60 dB is well below any musical signal
    # and above pure-silence floor.
    if spec_max < -60.0:
        return []
    if spec_max - spec_min < 1e-9:
        return []  # silent/constant input
    spec = (spec - spec_min) / (spec_max - spec_min) * 100.0
    peaks = _find_peaks_2d(spec, amp_min=DEFAULT_AMP_MIN)
    return _hash_peaks(peaks, fan_value=DEFAULT_FAN_VALUE)


def _find_peaks_2d(arr: np.ndarray, amp_min: int) -> list[tuple[int, int]]:
    """Return (freq_bin, time_bin) peak coordinates above amp_min."""
    struct = generate_binary_structure(2, CONNECTIVITY_MASK)
    neighborhood = iterate_structure(struct, PEAK_NEIGHBORHOOD_SIZE)
    local_max = maximum_filter(arr, footprint=neighborhood) == arr
    background = arr == 0
    eroded_background = binary_erosion(
        background, structure=neighborhood, border_value=1,
    )
    detected = local_max != eroded_background
    amps = arr[detected].flatten()
    freqs, times = np.where(detected)
    mask = amps > amp_min
    return list(zip(freqs[mask].tolist(), times[mask].tolist()))


def _hash_peaks(
    peaks: list[tuple[int, int]],
    fan_value: int = DEFAULT_FAN_VALUE,
) -> list[tuple[str, int]]:
    """Pair each peak with up to fan_value subsequent peaks; hash each pair."""
    if not peaks:
        return []
    # Sort by time so the fan-out walks forward in time.
    peaks_sorted = sorted(peaks, key=lambda p: p[1])
    hashes: list[tuple[str, int]] = []
    n = len(peaks_sorted)
    for i in range(n):
        f1, t1 = peaks_sorted[i]
        for j in range(1, fan_value):  # skylos: ignore SKY-P403 — Shazam-style landmark fan-out: pair each peak with the next `fan_value` peaks. Inner loop is bounded by DEFAULT_FAN_VALUE (5), so total work is O(n * fan_value) = O(n), not O(n²). Algorithm-inherent and intentionally local.
            if i + j >= n:
                break
            f2, t2 = peaks_sorted[i + j]
            t_delta = t2 - t1
            if not (MIN_HASH_TIME_DELTA <= t_delta <= MAX_HASH_TIME_DELTA):
                continue
            digest = hashlib.sha1(
                f"{f1}|{f2}|{t_delta}".encode("utf-8")
            ).hexdigest()[:FINGERPRINT_REDUCTION]
            hashes.append((digest, t1))
    return hashes


# ── Public API ──────────────────────────────────────────────────────────


def _insert_ref_with_hashes(
    cur: sqlite3.Cursor,
    release_id: int,
    track_position: str,
    track_position_s: float,
    now: str,
    fingerprint_data: list[tuple[str, int]],
) -> int:
    """Insert the fp_refs row and its fp_hashes inside an open transaction.

    Returns the ref_id (new or existing). Caller is responsible for
    BEGIN/COMMIT/ROLLBACK; this helper just performs the writes.
    """
    cur.execute(
        """
        INSERT OR IGNORE INTO fp_refs
          (release_id, track_position, track_position_s, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (release_id, track_position, track_position_s, now),
    )
    if cur.rowcount == 0:
        # Existing row — look up its id; don't re-insert hashes.
        row = cur.execute(
            """
            SELECT id FROM fp_refs
            WHERE release_id = ? AND track_position = ?
              AND track_position_s = ?
            """,
            (release_id, track_position, track_position_s),
        ).fetchone()
        return int(row[0])
    ref_id = int(cur.lastrowid)
    if fingerprint_data:
        cur.executemany(
            "INSERT INTO fp_hashes (hash, ref_id, offset) VALUES (?, ?, ?)",
            [(h, ref_id, off) for h, off in fingerprint_data],
        )
    return ref_id


def add_ref(
    release_id: int,
    track_position: str,
    track_position_s: float,
    wav_bytes: bytes,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Fingerprint a clip and store it as a ref for the given release.

    Returns the new ref_id, or the existing ref_id if a row with the same
    (release_id, track_position, track_position_s) already exists (the
    UNIQUE constraint makes the call idempotent for re-promotions).
    """
    fingerprint_data = _fingerprint(wav_bytes)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _DB_LOCK, contextlib.closing(_connect(db_path)) as conn:
        cur = conn.cursor()
        # Wrap the two-table write in an explicit transaction so a
        # mid-call crash can't leave an orphaned fp_refs row with no
        # associated hashes (Gemini impl-review should-fix).
        cur.execute("BEGIN")
        try:
            ref_id = _insert_ref_with_hashes(
                cur, release_id, track_position, track_position_s,
                now, fingerprint_data,
            )
        except Exception:
            cur.execute("ROLLBACK")
            raise
        cur.execute("COMMIT")
        return ref_id


# SQLite has a SQLITE_LIMIT_VARIABLE_NUMBER cap (default 999 pre-3.32, 32766
# after). A 12s clip can produce 3000+ unique hashes, which would crash naive
# single-IN-clause queries on older SQLite builds. Chunking the IN-clause
# keeps us safely under the 999 floor with headroom.
_SQLITE_VAR_CHUNK = 800


def _fetch_ref_hash_rows(
    conn: sqlite3.Connection,
    release_filter: int,
    query_hashes: list[str],
) -> list[tuple]:
    """Pull (ref_id, hash, offset) rows matching any query hash, in chunks.

    Scoped to a single release_id — used by the confirmation path when the
    orchestrator already has a locked album. For blind scan (no release lock)
    use :func:`_fetch_ref_hash_rows_blind` which omits the release filter
    entirely; passing ``None`` here would produce ``WHERE release_id = NULL``
    (always false in SQLite) and silently return zero rows.
    """
    rows: list[tuple] = []
    for i in range(0, len(query_hashes), _SQLITE_VAR_CHUNK):
        chunk = query_hashes[i:i + _SQLITE_VAR_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(chunk); all real values flow through bound parameters below
            f"SELECT fh.ref_id, fh.hash, fh.offset "
            f"FROM fp_hashes fh "
            f"JOIN fp_refs fr ON fr.id = fh.ref_id "
            f"WHERE fr.release_id = ? AND fh.hash IN ({placeholders})"
        )
        rows.extend(conn.execute(query, (release_filter, *chunk)).fetchall())
    return rows


def _fetch_ref_hash_rows_blind(
    conn: sqlite3.Connection,
    query_hashes: list[str],
) -> list[tuple]:
    """Pull (ref_id, hash, offset) rows matching any query hash across ALL
    refs — used by the blind fingerprint scan when there is no locked release.

    Uses a separate SQL statement that omits the ``release_id = ?`` predicate
    entirely (rather than passing ``None`` through :func:`_fetch_ref_hash_rows`,
    which would silently match zero rows due to SQLite's NULL semantics).
    The ``idx_fp_hashes_hash`` index covers hash lookups without a release
    filter, so per-chunk query performance is equivalent to the scoped path.
    """
    rows: list[tuple] = []
    for i in range(0, len(query_hashes), _SQLITE_VAR_CHUNK):
        chunk = query_hashes[i:i + _SQLITE_VAR_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(chunk); all real values flow through bound parameters below
            f"SELECT fh.ref_id, fh.hash, fh.offset "
            f"FROM fp_hashes fh "
            f"WHERE fh.hash IN ({placeholders})"
        )
        rows.extend(conn.execute(query, chunk).fetchall())
    return rows


def _score_ref_alignments(
    rows: list[tuple],
    query_offsets_by_hash: dict[str, list[int]],
    min_hits: int,
) -> list[tuple[int, int]]:
    """Vote per (ref_id, alignment_delta); return refs scoring >= min_hits.

    The dominant delta within each ref's hits is that ref's alignment score.
    Result is sorted by score descending.
    """
    votes: dict[int, Counter] = defaultdict(Counter)
    for ref_id, h, ref_off in rows:
        for q_off in query_offsets_by_hash[h]:  # skylos: ignore SKY-P403 — Shazam-style alignment vote: for each ref-side hit we tally (q_off - ref_off) deltas across all query-side offsets of the same hash. Inner loop length is the per-hash collision count (typically 1-2); total work is O(rows), not O(N²). Algorithm-inherent.
            votes[ref_id][q_off - ref_off] += 1
    ref_scores: list[tuple[int, int]] = []
    for ref_id, deltas in votes.items():
        top_delta_count = deltas.most_common(1)[0][1]
        if top_delta_count >= min_hits:
            ref_scores.append((ref_id, top_delta_count))
    ref_scores.sort(key=lambda t: t[1], reverse=True)
    return ref_scores


def _hydrate_top_refs(
    conn: sqlite3.Connection,
    ref_scores: list[tuple[int, int]],
) -> list[Hit]:
    """Look up fp_refs metadata for the scored ref_ids and build Hit rows."""
    ref_ids = [r for r, _ in ref_scores]
    placeholders = ",".join("?" * len(ref_ids))
    query = (  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(ref_ids); all real values flow through bound parameters below
        f"SELECT id, release_id, track_position, track_position_s "
        f"FROM fp_refs WHERE id IN ({placeholders})"
    )
    meta_rows = conn.execute(query, ref_ids).fetchall()
    meta = {r[0]: r for r in meta_rows}
    hits: list[Hit] = []
    for ref_id, score in ref_scores:
        m = meta.get(ref_id)
        if m is None:
            continue
        hits.append(Hit(
            ref_id=int(m[0]),
            release_id=int(m[1]),
            track_position=str(m[2]),
            hits=score,
            track_position_s=float(m[3]),
        ))
    return hits


def match(
    wav_bytes: bytes,
    release_filter: int | None,
    min_hits: int = 10,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[Hit]:
    """Match a clip against fingerprint refs. Returns Hits sorted by
    alignment-vote count descending. Only refs with at least min_hits
    aligned hashes are returned.

    ``release_filter``:
    - ``int`` — scoped confirmation scan: only refs for that release_id
      are queried. Used by the F3 confirmation path when the orchestrator
      has a locked album.
    - ``None`` — blind scan: ALL refs in fp_refs are candidates. Used by
      the F4 blind-fingerprint-discovery path when Shazam missed and no
      album lock exists. The ``_fetch_ref_hash_rows_blind`` SQL branch is
      used in this case (never pass ``None`` to ``_fetch_ref_hash_rows`` —
      that would silently return zero rows due to SQLite NULL semantics).
    """
    query_fp = _fingerprint(wav_bytes)
    if not query_fp:
        return []
    # Build a hash→offset map for the query side.
    query_offsets_by_hash: dict[str, list[int]] = defaultdict(list)
    for h, off in query_fp:
        query_offsets_by_hash[h].append(off)
    query_hashes = list(query_offsets_by_hash.keys())
    if not query_hashes:
        return []
    with contextlib.closing(_connect(db_path)) as conn:
        if release_filter is None:
            rows = _fetch_ref_hash_rows_blind(conn, query_hashes)
        else:
            rows = _fetch_ref_hash_rows(conn, release_filter, query_hashes)
        ref_scores = _score_ref_alignments(rows, query_offsets_by_hash, min_hits)
        if not ref_scores:
            return []
        return _hydrate_top_refs(conn, ref_scores)


def delete_refs(ref_ids: list[int], db_path: Path = DEFAULT_DB_PATH) -> int:
    """Delete refs by id. fp_hashes rows cascade via the FK constraint.
    Returns the number of fp_refs rows deleted."""
    if not ref_ids:
        return 0
    with _DB_LOCK:
        conn = _connect(db_path)
        try:
            placeholders = ",".join("?" * len(ref_ids))
            query = f"DELETE FROM fp_refs WHERE id IN ({placeholders})"  # skylos: ignore SKY-D211 — placeholders is a "?,?,..." template built from len(ref_ids); all real values flow through bound parameters below
            cur = conn.execute(query, ref_ids)
            return cur.rowcount
        finally:
            conn.close()


def count_refs_for_track(
    release_id: int,
    track_position: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Count learned fingerprint refs for one (release_id, track_position).

    Returns 0 when the DB file doesn't exist yet — keeps the WS publish
    path safe on fresh installs where no fingerprints have been promoted.
    """
    if not db_path.exists():
        return 0
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM fp_refs WHERE release_id = ? AND track_position = ?",
            (release_id, track_position),
        ).fetchone()
        return int(row[0]) if row else 0


def delete_refs_for_track(
    release_id: int,
    track_position: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    """Delete all learned fingerprint refs for one (release_id, track_position).

    fp_hashes rows cascade via the FK constraint. Returns the number of
    fp_refs rows deleted (0 when no refs exist for the cohort).
    """
    if not db_path.exists():
        return 0
    with _DB_LOCK, contextlib.closing(_connect(db_path)) as conn:
        cur = conn.execute(
            "DELETE FROM fp_refs WHERE release_id = ? AND track_position = ?",
            (release_id, track_position),
        )
        return cur.rowcount

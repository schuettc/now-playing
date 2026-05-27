"""Pin-driven promotion of user-confirmed clips into the fingerprint DB.

Called fire-and-forget from the orchestrator heartbeat handler on every
Shazam-miss + fingerprint-miss heartbeat where the user has an active
track pin. The pin is ground truth (the user explicitly confirmed
identity via /api/identify or the kiosk tracklist); promotion writes
fingerprints for that track so the next play matches locally.

Layered gates inside `maybe_promote`:

1. Cross-cohort audio-similarity guard. If the release already has
   refs for a position other than the target, fingerprint-match the
   incoming clip against the release. If audio matches a *different*
   position strongly, refuse — the user-supplied label disagrees with
   the existing audio. First cohort on a release bypasses this gate
   (nothing to compare against).
2. Static cohort cap (`MAX_REFS_PER_COHORT = 30`).
3. Static spacing on `track_position_s` (`MIN_REF_SPACING_S = 12s`).
4. Atomic `fingerprint.add_ref` with a UNIQUE constraint as a final
   line of defense.

Shazam-hit auto-promotion was removed (epic
`confirmed-fingerprint-coverage`): populating the DB from Shazam hits
covers the tracks Shazam already knows — the wrong population. The DB
exists to remember what Shazam can't.
"""
from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from pathlib import Path

from nowplaying.vinyl import fingerprint

log = logging.getLogger("nowplaying.promotion")

# Promotion gate constants. Tuned against live 2026-05-18 *Fantastic Planet*
# data (feature `fingerprint-cohort-coverage-tuning`):
#   - spacing=12s enforces one ref per 15s heartbeat window with 3s slop for
#     capture jitter; the previous 10s minimum allowed two refs in the same
#     window, causing irregular coverage gaps on C10 (Pitiful, 286s).
#   - The per-cohort ref cap is now duration-adaptive; see
#     `max_refs_for_duration` below.
MIN_REF_SPACING_S = 12.0

_MIN_COHORT_CAP = 30  # floor: preserves today's short-track behaviour


def max_refs_for_duration(duration_s: float | None) -> int:
    """Return the cohort ref cap for a track of the given duration.

    Formula: ``max(30, ceil(duration_s / 15))``.

    This guarantees full 15s-spaced coverage is always *reachable* on
    long tracks (e.g. 1380s "Echoes" → 92 refs) while leaving short-track
    behaviour unchanged (floor = 30, covers ≤ 450s).

    ``None`` / missing duration → conservative fallback of 30.
    """
    if duration_s is None:
        return _MIN_COHORT_CAP
    return max(_MIN_COHORT_CAP, math.ceil(duration_s / 15))

# Cross-cohort guard sensitivity — minimum `Hit.hits` count for a match
# against an EXISTING cohort to override the user-supplied target
# position and refuse the write. Lower → stricter (more refusals);
# higher → laxer.
#
# Empirically validated (2026-05-18): with 30-ref cohorts the true-positive
# signal grows (correct-cohort clips score in the 100s of hits against dense
# refs), while a false-positive cross-cohort collision still needs 5 aligned
# hashes by accident — statistically very unlikely for distinct audio. Value
# intentionally stays conservative; the static cap + spacing gates are the
# authoritative bound on DB growth. Calibration log lines (see
# `_cross_cohort_guard_passes`) surface near-threshold hits for future tuning.
GUARD_THRESHOLD = 5


async def maybe_promote(
    release_id: int,
    track_position: str,
    track_position_s: float,
    wav_bytes: bytes,
    *,
    duration_s: float | None = None,
    db_path: Path = fingerprint.DEFAULT_DB_PATH,
) -> bool:
    """Promote a user-confirmed clip into the fingerprint DB.

    Returns True if a new ref landed, False if any gate rejected it
    or any underlying error logged a warning.

    Gates in order: cross-cohort guard → cohort cap → spacing → add_ref.

    ``duration_s`` is the current track's duration in seconds; used to
    compute the adaptive cohort cap.  ``None`` falls back to the
    conservative floor of 30.

    Fire-and-forget from the heartbeat handler — never raises.
    """
    if not track_position:
        log.info(
            "promotion: release=%s pos=<empty> result=skipped reason=no-track-position",
            release_id,
        )
        return False
    # Cross-cohort guard — refuse to write a ref labeled position Y
    # for a release whose existing refs strongly fingerprint-match
    # the incoming audio at a DIFFERENT position. Defensive; never
    # blocks first-cohort bootstrap.
    if not await _cross_cohort_guard_passes(
        release_id, track_position, wav_bytes, db_path=db_path,
    ):
        return False
    # Cohort + spacing gates (duration-adaptive cap, always run).
    if not await asyncio.to_thread(
        _cohort_gate_passes, release_id, track_position, track_position_s, db_path,
        duration_s,
    ):
        return False
    try:
        # add_ref does the fingerprint + INSERT atomically. Its UNIQUE
        # constraint is the second line of defense against duplicates
        # racing past the gate.
        await asyncio.to_thread(
            fingerprint.add_ref,
            release_id, track_position, track_position_s, wav_bytes, db_path,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "promotion: release=%s pos=%s result=error reason=%r",
            release_id, track_position, e,
        )
        return False
    log.info(
        "promotion: release=%s pos=%s track_position_s=%.1f result=added",
        release_id, track_position, track_position_s,
    )
    return True


async def _cross_cohort_guard_passes(
    release_id: int,
    target_position: str,
    wav_bytes: bytes,
    *,
    db_path: Path,
) -> bool:
    """Refuse promotion when the audio fingerprint-matches a different
    cohort on the same release.

    Returns True (allow) when:
      - The release has no refs for any other position (first-cohort
        bootstrap — nothing to compare).
      - `fingerprint.match` returns no hits at `min_hits=GUARD_THRESHOLD`.
      - The top hit's `track_position` equals the target (same cohort,
        which is expected — let the static gates decide).
      - Any error occurs (defensive: a transient DB issue shouldn't
        block a legitimate promotion).

    Returns False (refuse) only when there's a strong match for a
    position other than the target — i.e. the user-supplied label
    contradicts existing audio evidence on this release.
    """
    try:  # skylos: ignore SKY-L004 — wide catch-all is the documented behavior per docstring "any error → allow (defensive)"; narrowing the scope would lose that guarantee
        # First, cheap precheck: any refs for OTHER positions on this
        # release? If not, bypass the (more expensive) match call.
        others = await asyncio.to_thread(
            _count_other_position_refs, release_id, target_position, db_path,
        )
        if others == 0:
            return True
        hits = await asyncio.to_thread(
            fingerprint.match, wav_bytes, release_id, GUARD_THRESHOLD,
            db_path=db_path,
        )
        if not hits:
            return True
        top = hits[0]
        if top.track_position == target_position:
            return True
        # `top.hits` is always >= GUARD_THRESHOLD here (match() filters by
        # min_hits). The refused log below already records the hits count,
        # which is sufficient for threshold calibration over time.
        log.info(
            "promotion: release=%s pos=%s result=refused "
            "reason=audio-matches-different-cohort "
            "(pinned=%s matched=%s hits=%d threshold=%d)",
            release_id, target_position,
            target_position, top.track_position, top.hits, GUARD_THRESHOLD,
        )
        return False
    except Exception as e:  # noqa: BLE001
        log.warning(
            "promotion: guard failed release=%s pos=%s reason=%r",
            release_id, target_position, e,
        )
        return True


def _count_other_position_refs(
    release_id: int, target_position: str, db_path: Path,
) -> int:
    """Count refs for `release_id` whose track_position is NOT
    `target_position`. Returns 0 on error (treated as bypass by the
    guard's exception handling)."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM fp_refs
            WHERE release_id = ? AND track_position != ?
            """,
            (release_id, target_position),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _cohort_gate_passes(
    release_id: int,
    track_position: str,
    track_position_s: float,
    db_path: Path,
    duration_s: float | None = None,
) -> bool:
    """Return True if a new ref for this cohort would pass cap + spacing
    gates. Reads existing track_position_s values for the cohort and
    rejects when at cap or too close to an existing ref.

    ``duration_s`` is used to compute the adaptive cohort cap via
    ``max_refs_for_duration``.  ``None`` → conservative floor of 30.
    """
    cap = max_refs_for_duration(duration_s)
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT track_position_s FROM fp_refs
                WHERE release_id = ? AND track_position = ?
                """,
                (release_id, track_position),
            ).fetchall()
    except sqlite3.OperationalError as e:
        # First-ever promotion: DB may not have the schema yet because
        # init_db is called on enable. If we got here without that,
        # something is misconfigured — log and skip rather than try to
        # init_db here (avoids a startup ordering dependency).
        log.warning(
            "promotion: release=%s pos=%s result=error reason=db-not-ready (%r)",
            release_id, track_position, e,
        )
        return False
    existing_positions = [row[0] for row in rows]
    if len(existing_positions) >= cap:
        log.info(
            "promotion: release=%s pos=%s result=skipped reason=cohort-full (%d/%d)",
            release_id, track_position, len(existing_positions), cap,
        )
        return False
    for existing_s in existing_positions:
        if abs(existing_s - track_position_s) < MIN_REF_SPACING_S:
            log.info(
                "promotion: release=%s pos=%s result=skipped reason=too-close-to-existing "
                "(this=%.1f existing=%.1f spacing=%.1f)",
                release_id, track_position, track_position_s,
                existing_s, MIN_REF_SPACING_S,
            )
            return False
    return True


# Target spacing for coverage-driven promotion. Matches the adaptive cap
# derivation ``ceil(duration_s / 15)``: using the same denominator guarantees
# full coverage is always reachable — the cap equals exactly the number of
# 15s-spaced slots across the duration.
COVERAGE_SPACING_S = 15.0


def should_promote_for_coverage(
    release_id: int,
    track_position: str,
    track_position_s: float,
    *,
    spacing_s: float = COVERAGE_SPACING_S,
    duration_s: float | None = None,
    db_path: Path = fingerprint.DEFAULT_DB_PATH,
) -> bool:
    """Return True if a new ref should be promoted at ``track_position_s``
    to fill a coverage gap.

    Spatial check: queries existing fp_refs for the cohort
    (``release_id`` + ``track_position``) and returns True when no ref
    exists within ``± spacing_s / 2`` seconds of ``track_position_s``.

    Also returns False (no promotion needed) when the cohort has already
    reached ``max_refs_for_duration(duration_s)``.

    Intended to be called via ``asyncio.to_thread`` from the orchestrator.
    Independent of fingerprint hit/miss outcome — purely spatial.

    Returns False on any DB error (defensive: caller treats False as "skip
    this heartbeat").
    """
    if not track_position:
        return False
    cap = max_refs_for_duration(duration_s)
    half_window = spacing_s / 2.0
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                """
                SELECT track_position_s FROM fp_refs
                WHERE release_id = ? AND track_position = ?
                """,
                (release_id, track_position),
            ).fetchall()
    except Exception as e:  # noqa: BLE001
        log.warning(
            "coverage-check: release=%s pos=%s result=error reason=%r",
            release_id, track_position, e,
        )
        return False
    existing_positions = [row[0] for row in rows]
    if len(existing_positions) >= cap:
        log.debug(
            "coverage-check: release=%s pos=%s result=skip reason=cohort-full (%d/%d)",
            release_id, track_position, len(existing_positions), cap,
        )
        return False
    for existing_s in existing_positions:
        if abs(existing_s - track_position_s) <= half_window:
            log.debug(
                "coverage-check: release=%s pos=%s result=skip "
                "reason=ref-exists-nearby (this=%.1f existing=%.1f window=±%.1f)",
                release_id, track_position,
                track_position_s, existing_s, half_window,
            )
            return False
    log.debug(
        "coverage-check: release=%s pos=%s track_position_s=%.1f result=gap-found",
        release_id, track_position, track_position_s,
    )
    return True


async def schedule_backfill_promotions(
    release_id: int,
    track_position: str,
    audible_edge_unix_ts: int,
    pin_unix_ts: int,
    duration_s: float | None = None,
    db_path: Path = fingerprint.DEFAULT_DB_PATH,
) -> int:
    """Retroactively promote heartbeat clips captured between the last
    audible-edge and the pin click moment.

    Closes the "user clicks N seconds after needle drop, refs from those
    first N seconds never captured" gap. Reads clip files from
    ``CLIPS_DIR``, parses unix-timestamp prefixes, schedules
    ``maybe_promote`` for each clip in the window with
    ``track_position_s = clip_ts - audible_edge_ts``.

    The existing per-clip gates inside ``maybe_promote`` (cross-cohort
    guard, spacing, cap) handle any bad-pin or duplicate cases. This
    function is fire-and-forget; returns the count of clips scheduled.

    Clip filenames are ``<unix_ts>_heartbeat.wav`` (capture-side
    convention; see ``vinyl/hygiene.py``).
    """
    # Local import to avoid coupling promotion.py to hygiene.py at
    # module load time — both already import fingerprint.
    from nowplaying.vinyl.hygiene import CLIPS_DIR
    if not track_position:
        return 0
    if not CLIPS_DIR.exists():
        return 0
    if pin_unix_ts < audible_edge_unix_ts:
        return 0
    scheduled = 0
    for clip_file in sorted(CLIPS_DIR.iterdir()):
        if not clip_file.is_file():
            continue
        # Parse the unix-ts prefix. Filename shape is
        # "<unix_ts>_heartbeat.wav" — split on first underscore.
        stem = clip_file.stem
        try:
            clip_ts_str = stem.split("_", 1)[0]
            clip_ts = int(clip_ts_str)
        except (ValueError, IndexError):
            continue
        if clip_ts < audible_edge_unix_ts or clip_ts > pin_unix_ts:
            continue
        try:
            wav_bytes = clip_file.read_bytes()
        except OSError as e:
            log.warning(
                "backfill: read failed clip=%s: %r", clip_file.name, e,
            )
            continue
        clip_position_s = float(clip_ts - audible_edge_unix_ts)
        log.info(
            "backfill: scheduling release=%s pos=%s clip=%s track_position_s=%.1f",
            release_id, track_position, clip_file.name, clip_position_s,
        )
        asyncio.create_task(
            maybe_promote(
                release_id=release_id,
                track_position=track_position,
                track_position_s=clip_position_s,
                wav_bytes=wav_bytes,
                duration_s=duration_s,
                db_path=db_path,
            ),
        )
        scheduled += 1
    log.info(
        "backfill: release=%s pos=%s scheduled=%d window=[%d,%d]",
        release_id, track_position, scheduled, audible_edge_unix_ts, pin_unix_ts,
    )
    return scheduled

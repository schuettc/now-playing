"""Backfill `tracks.duration_seconds` by re-fetching tracklists from Discogs.

A 2026-05-12 Pi audit found ~50% of catalog tracks (2341 / 4695) had
`duration_seconds IS NULL`. Most are multi-disc / box-set releases where
Discogs nests playable tracks inside `sub_tracks`. The original sync has
been patched (`iter_leaf_tracks` recurses into sub_tracks), but existing rows
still need a fix.

Earlier versions of this backfill tried to re-parse `releases.raw_detail_json`
from the local cache, but that cache is projected down to 8 fields and does
not contain a tracklist. The fix is to call the live Discogs REST API.

This script:
  - finds every release with at least one null-duration track,
  - fetches GET /releases/<id> from the Discogs API,
  - parses durations using `_parse_discogs_duration` + `iter_leaf_tracks`
    (recurses into sub_tracks),
  - UPDATEs `tracks.duration_seconds` for each match (release_id, position),
  - if the per-release endpoint returns zero parseable durations and the
    release JSON has a `master_id`, fetches GET /masters/<master_id> and
    retries the UPDATE against the master's tracklist (UPDATE-only — never
    INSERT, since compilations / deluxe editions can diverge in position).

Resumable + idempotent: only NULL rows are queried each invocation. Re-running
after a successful pass is a no-op.

Run:
    pi/.venv/bin/python pi/scripts/backfill_track_durations.py
    pi/.venv/bin/python pi/scripts/backfill_track_durations.py --limit 10

Env (from pi/.env):
    DISCOGS_TOKEN
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Re-use the hardened parser + leaf-iterator from the sync module so the
# backfill semantics exactly match what a fresh sync would have produced.
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from discogs_sync import (  # noqa: E402
    _parse_discogs_duration,
    iter_leaf_tracks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PI_DIR = REPO_ROOT / "pi"
DB_PATH = PI_DIR / "data" / "discogs.sqlite"

USER_AGENT = "now-playing/0.1 (+https://github.com/schuettc/now-playing)"
API_URL = "https://api.discogs.com/releases/{id}"
MASTER_API_URL = "https://api.discogs.com/masters/{id}"
# Discogs authenticated cap is 60/min; 1.1 s gives margin and keeps us polite.
SLEEP_BETWEEN_REQUESTS_S = 1.1
RATE_LIMIT_BACKOFF_S = 60.0


def log(msg: str) -> None:
    print(msg, flush=True)


def find_releases_with_nulls(con: sqlite3.Connection) -> list[int]:
    rows = con.execute(
        "SELECT DISTINCT release_id FROM tracks "
        "WHERE duration_seconds IS NULL "
        "ORDER BY release_id"
    ).fetchall()
    return [r[0] for r in rows]


def count_null_tracks(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM tracks WHERE duration_seconds IS NULL"
    ).fetchone()[0]


def _fetch_json(
    session: requests.Session, url: str, token: str, label: str
) -> dict | None:
    """GET ``url`` with Discogs auth. Returns parsed JSON, or None on any HTTP failure.

    On HTTP 429 we sleep ``RATE_LIMIT_BACKOFF_S`` and retry exactly once. A
    second 429 is logged and the request is skipped. 404 / 5xx / network
    errors are logged and skipped without crashing. ``label`` is a short
    identifier (e.g. ``"release=123"`` or ``"master=456"``) used purely for
    log lines.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": f"Discogs token={token}",
    }

    for attempt in (1, 2):
        try:
            r = session.get(url, headers=headers, timeout=30)  # skylos: ignore SKY-D216 — admin script; url is built from hardcoded api.discogs.com templates
        except requests.RequestException as e:
            log(f"  {label} WARN network: {e!r}")
            return None

        if r.status_code == 200:
            try:
                return r.json()
            except ValueError as e:
                log(f"  {label} WARN bad json: {e!r}")
                return None
        if r.status_code == 404:
            log(f"  {label} WARN 404 not found upstream")
            return None
        if r.status_code == 429:
            if attempt == 1:
                log(
                    f"  {label} 429 rate-limited; "
                    f"sleeping {RATE_LIMIT_BACKOFF_S:.0f}s and retrying once"
                )
                time.sleep(RATE_LIMIT_BACKOFF_S)
                continue
            log(f"  {label} WARN 429 after retry; skipping")
            return None
        # 5xx and anything else: log and skip.
        log(f"  {label} WARN HTTP {r.status_code}; skipping")
        return None

    return None


def fetch_release_detail(  # skylos: ignore — deliberate thin wrapper documenting the /releases/<id> URL; clone with fetch_master_detail is intentional
    session: requests.Session, release_id: int, token: str
) -> dict | None:
    """GET /releases/<id>. Returns parsed JSON, or None on any HTTP failure."""
    return _fetch_json(
        session,
        API_URL.format(id=release_id),
        token,
        f"release={release_id}",
    )


def fetch_master_detail(
    session: requests.Session, master_id: int, token: str
) -> dict | None:
    """GET /masters/<id>. Returns parsed JSON, or None on any HTTP failure.

    Used as a fallback when the per-release endpoint returns empty `duration`
    strings — the canonical track times often live on the master.
    """
    return _fetch_json(
        session,
        MASTER_API_URL.format(id=master_id),
        token,
        f"master={master_id}",
    )


def update_release_durations(
    con: sqlite3.Connection, release_id: int, detail: dict
) -> int:
    """Walk the tracklist, parse durations, UPDATE matching rows.

    Returns the number of rows actually changed by UPDATE statements.
    """
    tracklist = detail.get("tracklist") or []
    updated = 0
    for position, _title, duration_str in iter_leaf_tracks(tracklist):
        dur = _parse_discogs_duration(duration_str)
        if dur is None:
            continue
        cur = con.execute(
            "UPDATE tracks SET duration_seconds = ? "
            "WHERE release_id = ? AND position = ? AND duration_seconds IS NULL",
            (dur, release_id, position),
        )
        if cur.rowcount:
            updated += cur.rowcount
    return updated


def main() -> None:  # skylos: ignore — admin/backfill script; CLI orchestration with retry/rate-limit branches isn't worth decomposing
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--limit", type=int, default=None,
        help="cap the number of releases processed (useful for smoke testing)",
    )
    p.add_argument(
        "--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS_S,
        help=f"seconds between API requests (default {SLEEP_BETWEEN_REQUESTS_S})",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="fetch and parse but skip the UPDATE statements",
    )
    args = p.parse_args()

    load_dotenv(PI_DIR / ".env")
    token = os.environ.get("DISCOGS_TOKEN")
    if not token or token in ("replace_me", ""):
        sys.exit("DISCOGS_TOKEN not set in pi/.env")

    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    con = sqlite3.connect(DB_PATH, isolation_level=None)
    try:  # skylos: ignore SKY-L004 — admin script; the per-release control flow inside the try IS the algorithm (dry-run vs. apply branches with master-fallback). Splitting hurts readability and the operation is fundamentally one transactional best-effort sweep.
        release_ids = find_releases_with_nulls(con)
        if args.limit is not None:
            release_ids = release_ids[: args.limit]
        total = len(release_ids)
        starting_nulls = count_null_tracks(con)
        log(
            f"backfill: {total} releases have at least one null-duration "
            f"track ({starting_nulls} null rows total)"
        )
        if not total:
            return

        session = requests.Session()  # skylos: ignore SKY-L008 — admin script; session lives for the whole sweep and is implicitly cleaned up at process exit.
        total_updated = 0
        releases_with_updates = 0
        releases_via_master = 0
        for i, release_id in enumerate(release_ids, 1):
            detail = fetch_release_detail(session, release_id, token)
            if detail is None:
                log(f"[{i}/{total}] release={release_id} skipped")
            else:
                if args.dry_run:
                    # Count what *would* update without applying.
                    tracklist = detail.get("tracklist") or []
                    would = sum(
                        1
                        for _pos, _title, dur in iter_leaf_tracks(tracklist)
                        if _parse_discogs_duration(dur) is not None
                    )
                    if would > 0:
                        log(
                            f"[{i}/{total}] release={release_id} "
                            f"would parse {would} durations (dry-run)"
                        )
                    else:
                        # Try master fallback in dry-run mode too so operators
                        # can see whether the real run will find times.
                        master_id = detail.get("master_id")
                        if not master_id:
                            log(
                                f"[{i}/{total}] release={release_id} "
                                f"no master for fallback (dry-run)"
                            )
                        else:
                            time.sleep(args.sleep)
                            master_detail = fetch_master_detail(
                                session, int(master_id), token
                            )
                            if master_detail is None:
                                log(
                                    f"[{i}/{total}] release={release_id} "
                                    f"master={master_id} skipped (dry-run)"
                                )
                            else:
                                m_tracklist = master_detail.get("tracklist") or []
                                m_would = sum(
                                    1
                                    for _p, _t, dur in iter_leaf_tracks(m_tracklist)
                                    if _parse_discogs_duration(dur) is not None
                                )
                                log(
                                    f"[{i}/{total}] release={release_id} "
                                    f"master={master_id} would parse "
                                    f"{m_would} durations via master (dry-run)"
                                )
                else:
                    updated = update_release_durations(con, release_id, detail)
                    if updated:
                        total_updated += updated
                        releases_with_updates += 1
                        log(
                            f"[{i}/{total}] release={release_id} "
                            f"updated {updated} durations"
                        )
                    else:
                        # Per-release endpoint returned no parseable durations.
                        # Fall back to the master's canonical tracklist if we
                        # have a master_id on the release.
                        master_id = detail.get("master_id")
                        if not master_id:
                            log(
                                f"[{i}/{total}] release={release_id} "
                                f"no master for fallback"
                            )
                        else:
                            # Second API call ⇒ second sleep before issuing it.
                            time.sleep(args.sleep)
                            master_detail = fetch_master_detail(
                                session, int(master_id), token
                            )
                            if master_detail is None:
                                log(
                                    f"[{i}/{total}] release={release_id} "
                                    f"master={master_id} skipped"
                                )
                            else:
                                m_updated = update_release_durations(
                                    con, release_id, master_detail
                                )
                                if m_updated:
                                    total_updated += m_updated
                                    releases_with_updates += 1
                                    releases_via_master += 1
                                    log(
                                        f"[{i}/{total}] release={release_id} "
                                        f"master={master_id} updated "
                                        f"{m_updated} durations via master"
                                    )
                                else:
                                    log(
                                        f"[{i}/{total}] release={release_id} "
                                        f"no times in release or master"
                                    )
            if i < total:
                time.sleep(args.sleep)

        n_remaining = count_null_tracks(con)
        log(
            f"backfill complete: {total_updated} rows updated across "
            f"{releases_with_updates} releases "
            f"({releases_via_master} via master fallback); "
            f"{n_remaining} rows still null"
        )
    finally:
        con.close()


if __name__ == "__main__":
    main()

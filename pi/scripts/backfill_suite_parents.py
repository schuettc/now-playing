"""Backfill suite-parent track rows for releases that have multi-part
position leaves (``X1. I``, ``A2. II``, etc.).

Discogs models suites/medleys as a parent row with ``sub_tracks``. The
original ingest yielded only the playable leaves and dropped the
parent — but the parent's title is what Shazam returns when any
movement is playing, so reverse-lookup needs it.

This backfill re-hits Discogs for any release that has at least one
multi-part position leaf AND no parent row yet. The number of such
releases is small (a handful of albums per collection — concept
suites, prog medleys), so the Discogs API call budget is modest.

Run on the Pi:
    pi/.venv/bin/python pi/scripts/backfill_suite_parents.py

Honors ``DISCOGS_TOKEN`` from pi/.env.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))

import discogs_client  # noqa: E402

from discogs._db import log, open_db  # noqa: E402
from discogs._helpers import (  # noqa: E402
    _parse_discogs_duration,
    iter_suite_parents,
    position_to_side,
)

USER_AGENT = "now-playing/0.1 (+https://github.com/schuettc/schuettc/now-playing)"
# Match positions like "A2. I", "D1. III" (parent's children).
_MULTIPART_LEAF_RE = re.compile(r"^[A-Z]\d+\.\s*[IVX]+$")


def _try_load_env() -> None:
    """Best-effort .env loader. Avoids a hard dotenv dependency on the
    Pi — falls back to whatever os.environ already has if .env isn't
    present or readable."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _releases_with_multipart_leaves(con) -> list[int]:
    """Return release_ids that have at least one ``X1. I`` style leaf
    position and no existing ``is_suite_parent=1`` row."""
    rows = con.execute(
        "SELECT release_id, position FROM tracks WHERE is_suite_parent = 0",
    ).fetchall()
    by_release: dict[int, bool] = {}
    for release_id, position in rows:
        if _MULTIPART_LEAF_RE.match((position or "").strip()):
            by_release[release_id] = True
    if not by_release:
        return []
    existing_parents = {
        r[0] for r in con.execute(
            "SELECT DISTINCT release_id FROM tracks WHERE is_suite_parent = 1",
        ).fetchall()
    }
    return sorted(rid for rid in by_release if rid not in existing_parents)


def main() -> None:
    _try_load_env()
    token = os.environ.get("DISCOGS_TOKEN")
    if not token or token in ("replace_me", ""):
        sys.exit("DISCOGS_TOKEN not set in pi/.env")

    con = open_db()
    targets = _releases_with_multipart_leaves(con)
    log(f"{len(targets)} release(s) need suite-parent backfill")
    if not targets:
        return

    client = discogs_client.Client(USER_AGENT, user_token=token)
    total_inserted = 0
    for i, release_id in enumerate(targets, 1):
        try:
            rel = client.release(release_id)
            _ = rel.title  # force fetch
            tracklist = list(getattr(rel, "tracklist", []) or [])
        except Exception as e:  # noqa: BLE001
            log(f"  [{i}/{len(targets)}] release_id={release_id}: fetch failed — {e}")
            continue
        inserted = 0
        for position, title, duration_str in iter_suite_parents(tracklist):
            dur = _parse_discogs_duration(duration_str)
            con.execute(
                "INSERT OR REPLACE INTO tracks "
                "(release_id, position, side, title, duration_seconds, is_suite_parent) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (release_id, position, position_to_side(position), title, dur),
            )
            inserted += 1
        total_inserted += inserted
        log(f"  [{i}/{len(targets)}] release_id={release_id}: +{inserted} parent(s)")
        # Be polite to Discogs — their rate limit is 60/min authenticated.
        time.sleep(1.0)
    log(f"Done. {total_inserted} parent row(s) inserted.")


if __name__ == "__main__":
    main()

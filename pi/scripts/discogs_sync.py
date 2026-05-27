"""Phase 3a: Discogs collection sync to local SQLite.

Three idempotent passes:
  1. basic   — walk collection pages, upsert releases with summary fields
  2. details — for releases missing tracklist, fetch full release & insert tracks
  3. art     — for releases missing local cover image, download primary image

Default behavior: run all three. Re-runs skip already-synced data.

Run:
    uv run python pi/scripts/discogs_sync.py
    uv run python pi/scripts/discogs_sync.py --only basic
    uv run python pi/scripts/discogs_sync.py --limit 10        # for testing

Env (from pi/.env):
    DISCOGS_TOKEN
"""
from __future__ import annotations

# Re-export everything from the discogs package so that:
# 1. ``import discogs_sync; discogs_sync._parse_discogs_duration(...)`` keeps working
#    (tests and any downstream callers use the flat module name).
# 2. The CLI entrypoint (main / __main__) is still here.
from discogs import *  # noqa: F401,F403 — Why: thin shim; all public names live in discogs package

import argparse
import os
import sys

import discogs_client
from dotenv import load_dotenv

from discogs._db import PI_DIR, log, open_db
from discogs._passes import (
    pass_art,
    pass_basic,
    pass_details,
    pass_enrich_durations,
)

# Sentinel + norm-title are also referenced directly by test patches;
# the star-import above covers them but make them explicit for clarity.
from discogs._enrich import (  # noqa: F401 — Why: explicit re-export so ``discogs_sync._AMBIGUOUS_TITLE`` patches work
    _AMBIGUOUS_TITLE,
    _enrich_durations_from_musicbrainz_async,
    _norm_title,
    _resolve_best_matching_mbid,
)
from discogs._helpers import (  # noqa: F401 — Why: explicit re-export so ``discogs_sync._parse_discogs_duration`` patches work
    _parse_discogs_duration,
    iter_leaf_tracks,
    parse_duration,
)

USER_AGENT = "now-playing/0.1 (+https://github.com/schuettc/now-playing)"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--only", choices=["basic", "details", "art", "enrich-durations"],
        default=None,
    )
    p.add_argument("--limit", type=int, default=None, help="cap items per pass (for testing)")
    args = p.parse_args()

    load_dotenv(PI_DIR / ".env")
    con = open_db()

    # enrich-durations is the only pass that doesn't need a Discogs API
    # client (it operates locally on tracks + MusicBrainz). Skip the
    # token check so users can run this even without a fresh Discogs
    # session.
    if args.only == "enrich-durations":
        pass_enrich_durations(con, args.limit)
        n_null = con.execute(
            "SELECT COUNT(*) FROM tracks WHERE duration_seconds IS NULL",
        ).fetchone()[0]
        n_tr = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        log(f"DB: {n_tr} tracks ({n_null} still NULL durations)")
        return

    token = os.environ.get("DISCOGS_TOKEN")
    if not token or token in ("replace_me", ""):
        sys.exit("DISCOGS_TOKEN not set in pi/.env")
    client = discogs_client.Client(USER_AGENT, user_token=token)

    passes = ["basic", "details", "art"] if args.only is None else [args.only]
    for ph in passes:
        log(f"=== pass: {ph} ===")
        if ph == "basic":
            pass_basic(con, client, args.limit)
        elif ph == "details":
            pass_details(con, client, args.limit)
        elif ph == "art":
            pass_art(con, args.limit)

    # Summary
    n_rel = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    n_det = con.execute("SELECT COUNT(*) FROM releases WHERE detail_synced_at IS NOT NULL").fetchone()[0]
    n_art = con.execute("SELECT COUNT(*) FROM releases WHERE art_path IS NOT NULL").fetchone()[0]
    n_tr = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
    n_null = con.execute("SELECT COUNT(*) FROM tracks WHERE duration_seconds IS NULL").fetchone()[0]
    log(
        f"DB: {n_rel} releases ({n_det} with tracklist, {n_art} with art), "
        f"{n_tr} tracks ({n_null} NULL durations)",
    )


if __name__ == "__main__":
    main()

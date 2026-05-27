"""Pre-warm the MusicBrainz album-art cache for every release in the
local Discogs catalog.

The kiosk's /identify route is now an album-art grid, so a cold cache
means most cells start with the "no art" placeholder until each release
is played and the recognition cascade lazily fills its art entry. This
script walks `releases` in `pi/data/discogs.sqlite` and drives the same
`art_cache.maybe_cache(...)` helper used in production so the on-disk
result is identical: `pi/data/art/musicbrainz/<release_id>.jpg`.

Idempotent: skips any release whose JPG already exists on disk.
Resumable: interrupt and re-run; previously cached files are skipped.
No DB writes — pure filesystem side-effect.

Rate-limited to ~1 req/sec (default 1.1s) to respect MusicBrainz and the
Cover Art Archive. `maybe_cache` itself logs cache success to stderr; we
add stdout progress lines so the script reads cleanly when run by hand.

Run:
    pi/.venv/bin/python pi/scripts/preload_album_art.py
    pi/.venv/bin/python pi/scripts/preload_album_art.py --limit 25
    pi/.venv/bin/python pi/scripts/preload_album_art.py --sleep 2.0
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PI_DIR = REPO_ROOT / "pi"
DB_PATH = PI_DIR / "data" / "discogs.sqlite"
ART_DIR = PI_DIR / "data" / "art" / "musicbrainz"

# Make `nowplaying.*` importable when invoked as a plain script.
sys.path.insert(0, str(PI_DIR))

from nowplaying import art_cache  # noqa: E402

SLEEP_BETWEEN_REQUESTS_S = 1.1


def log(msg: str) -> None:
    print(msg, flush=True)


def find_releases(con: sqlite3.Connection) -> list[tuple[int, str, str]]:
    rows = con.execute(
        "SELECT id, artist, title FROM releases "
        "WHERE artist IS NOT NULL AND title IS NOT NULL "
        "ORDER BY id"
    ).fetchall()
    return [(int(r[0]), str(r[1]), str(r[2])) for r in rows]


async def cache_one(release_id: int, artist: str, album: str) -> None:
    """Invoke the shared helper; logging on the result is handled by caller."""
    await art_cache.maybe_cache(release_id, artist, album)


def main() -> None:  # skylos: ignore — admin preload script; argparse-driven CLI
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--limit", type=int, default=None,
        help="cap the number of releases processed (for testing)",
    )
    p.add_argument(
        "--sleep", type=float, default=SLEEP_BETWEEN_REQUESTS_S,
        help=f"seconds between API requests (default {SLEEP_BETWEEN_REQUESTS_S})",
    )
    args = p.parse_args()

    if not DB_PATH.exists():
        sys.exit(f"DB not found: {DB_PATH}")

    con = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        releases = find_releases(con)
    finally:
        con.close()

    # Filter out already-cached releases up front so progress numerators
    # reflect remaining work, not the total catalog size.
    pending: list[tuple[int, str, str]] = []
    skipped_cached = 0
    for rid, artist, title in releases:
        if (ART_DIR / f"{rid}.jpg").exists():
            skipped_cached += 1
            continue
        pending.append((rid, artist, title))

    if args.limit is not None:
        pending = pending[: args.limit]

    total = len(pending)
    log(
        f"preload: {len(releases)} releases in catalog; "
        f"{skipped_cached} already cached; {total} to attempt"
    )
    if not total:
        return

    cached_now = 0
    missed = 0
    for i, (rid, artist, title) in enumerate(pending, 1):
        out = ART_DIR / f"{rid}.jpg"
        try:
            asyncio.run(cache_one(rid, artist, title))
        except Exception as e:  # noqa: BLE001 — never let one row kill the run
            log(f"[{i}/{total}] ERROR release={rid} {artist} - {title}: {e!r}")
            # Still pace, because the failure may have made a request.
            if i < total:
                import time
                time.sleep(args.sleep)
            continue

        if out.exists():
            cached_now += 1
            log(f"[{i}/{total}] cached release={rid} {artist} - {title}")
        else:
            missed += 1
            log(f"[{i}/{total}] no-art release={rid} {artist} - {title}")

        if i < total:
            import time
            time.sleep(args.sleep)

    log(
        f"preload complete: cached {cached_now} new; "
        f"no-art on {missed}; {skipped_cached} pre-cached untouched"
    )


if __name__ == "__main__":
    main()

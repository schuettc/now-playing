"""Backfill tracks.clean_title across both catalogs.

Default: fill rows where clean_title IS NULL.
--reclean-regex: also re-clean rows whose clean_title_source='regex'
(use after adding ANTHROPIC_API_KEY to upgrade to LLM-cleaned titles).
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from nowplaying.titleclean import clean_title as _clean


def _load_env(env_path: Path | None = None) -> None:
    """Load pi/.env so ANTHROPIC_API_KEY is visible to LLMAssist when run
    as a standalone CLI (the long-running orchestrator loads it at startup;
    this script must do it explicitly)."""
    if env_path is None:
        env_path = Path(__file__).resolve().parents[1] / ".env"  # pi/.env
    load_dotenv(env_path)


async def backfill_db(db_path: Path, llm=None, reclean_regex: bool = False) -> int:
    """Backfill clean_title in one sqlite DB's tracks table. Returns rows updated."""
    con = sqlite3.connect(db_path)
    try:
        where = "clean_title_source = 'regex'" if reclean_regex else "clean_title IS NULL"
        rows = con.execute(f"SELECT rowid, title FROM tracks WHERE {where}").fetchall()
        updated = 0
        for rowid, title in rows:
            clean, source = await _clean(title or "", llm)
            con.execute(
                "UPDATE tracks SET clean_title = ?, clean_title_source = ? WHERE rowid = ?",
                (clean, source, rowid),
            )
            updated += 1
        con.commit()
        return updated
    finally:
        con.close()


def main() -> None:
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--discogs", type=Path, default=Path("pi/data/discogs.sqlite"))
    ap.add_argument("--discovered", type=Path, default=Path("pi/data/discovered.sqlite"))
    ap.add_argument("--reclean-regex", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="force regex even if a key is set")
    args = ap.parse_args()

    llm = None
    if not args.no_llm:
        from nowplaying.llm import LLMAssist

        llm = LLMAssist()

    async def run() -> None:
        d1 = await backfill_db(args.discogs, llm, args.reclean_regex)
        d2 = await backfill_db(args.discovered, llm, args.reclean_regex)
        print(f"backfilled discogs={d1} discovered={d2}")

    asyncio.run(run())


if __name__ == "__main__":
    main()

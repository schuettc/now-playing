"""Backfill tracks.clean_title across both catalogs.

Default: fill rows where clean_title IS NULL.
--reclean-regex: also re-clean rows whose clean_title_source='regex'
(use after a regex change to propagate updated cleaning logic).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from nowplaying.titleclean import clean_title as _clean


def backfill_db(db_path: Path, reclean_regex: bool = False) -> int:
    """Backfill clean_title in one sqlite DB's tracks table. Returns rows updated."""
    con = sqlite3.connect(db_path)
    try:
        where = "clean_title_source = 'regex'" if reclean_regex else "clean_title IS NULL"
        rows = con.execute(f"SELECT rowid, title FROM tracks WHERE {where}").fetchall()
        updated = 0
        for rowid, title in rows:
            clean, source = _clean(title or "")
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--discogs", type=Path, default=Path("pi/data/discogs.sqlite"))
    ap.add_argument("--discovered", type=Path, default=Path("pi/data/discovered.sqlite"))
    ap.add_argument("--reclean-regex", action="store_true")
    args = ap.parse_args()

    d1 = backfill_db(args.discogs, args.reclean_regex)
    d2 = backfill_db(args.discovered, args.reclean_regex)
    print(f"backfilled discogs={d1} discovered={d2}")


if __name__ == "__main__":
    main()

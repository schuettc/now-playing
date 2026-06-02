"""Discovered-release path — MusicBrainz-sourced tracklists for albums
not in the user's Discogs catalog.

Persists into ``pi/data/discovered.sqlite``, parallel to ``discogs.sqlite``.
See ``docs/features/musicbrainz-tracklist-discovery``.
"""
from __future__ import annotations

from nowplaying.discovery import fingerprint
from nowplaying.discovery.schema import (
    DISCOVERED_DB_PATH,
    init_db,
    open_ro,
    open_rw,
    set_track_duration_mbid,
)

__all__ = [
    "DISCOVERED_DB_PATH",
    "fingerprint",
    "init_db",
    "open_ro",
    "open_rw",
    "set_track_duration_mbid",
]

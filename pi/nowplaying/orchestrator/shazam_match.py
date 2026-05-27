"""Pure decision helper for the Shazam-vs-locked-album cross check."""
from __future__ import annotations


def _shazam_disagrees_with_lock(result: dict, locked: dict) -> bool:
    """Pure half of `_should_consult_llm_for_shazam`'s decision.

    Returns True only when the Shazam result conflicts with the locked
    album on both release_id AND artist — the case where an LLM judgment
    actually buys signal. Both blank artists short-circuit to False
    (nothing for the LLM to weigh).
    """
    same_release = (
        result.get("release_id") is not None
        and result.get("release_id") == locked.get("release_id")
    )
    result_artist = (result.get("artist") or "").strip()
    locked_artist = (locked.get("artist") or "").strip()
    if not result_artist and not locked_artist:
        return False
    same_artist = (
        bool(result_artist)
        and bool(locked_artist)
        and result_artist.casefold() == locked_artist.casefold()
    )
    return not (same_release or same_artist)

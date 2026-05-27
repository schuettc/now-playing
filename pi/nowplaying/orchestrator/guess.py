"""Pure helpers for the confirm-first track-guess flow.

The orchestrator stashes a best-guess track on `state.pending_guess`
when a Shazam-miss + fingerprint-miss heartbeat arrives with an album
lock; the kiosk surfaces it as a confirmation prompt. If the user
rejects, `(release_id, track_position)` lands in
`state.dismissed_guesses` and `_compute_track_guess` consults this
set to avoid re-emitting the same guess for `DISMISSED_GUESS_TTL_S`.

See docs/features/identify-guess-confirm/ and
docs/features/llm-track-guess/.
"""
from __future__ import annotations


# Time-to-live for entries in `state.dismissed_guesses`. After this
# many seconds since a user-reject, the same (rid, position) becomes
# eligible to be guessed again. 5 minutes balances "no re-emission
# this play of the album" against "fresh chances on a later replay."
DISMISSED_GUESS_TTL_S = 300


def _guess_is_dismissed(
    dismissed: dict[tuple[int, str], float],
    release_id: int,
    track_position: str,
    now_mono: float,
) -> bool:
    """Pure helper: True if `(release_id, track_position)` is in the
    dismissed set and within TTL. Evicts the looked-up key on read
    when stale; other stale entries persist until their own next
    lookup or until the next album-lock change clears the whole set.

    The dict is mutated in place — caller passes
    `state.dismissed_guesses` and gets opportunistic eviction for
    the queried key. Total set size is bounded by the album-change
    clear, which is invoked on source flip and idle.
    """
    key = (int(release_id), str(track_position))
    ts = dismissed.get(key)
    if ts is None:
        return False
    if now_mono - ts > DISMISSED_GUESS_TTL_S:
        del dismissed[key]
        return False
    return True

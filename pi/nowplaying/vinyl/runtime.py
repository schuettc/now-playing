"""Vinyl recognition runtime.

Spawns the existing capture script as a subprocess. Reads its JSONL on
stdout. When a "flowing" event emits a clip path, runs the recognition
cascade (ShazamIO → Discogs reverse-lookup, with optional Apple Music
text fallback when hints are supplied) and hands the result to a
callback.

Designed to be embedded in the orchestrator alongside the Sonos listener.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time as _time_module
from pathlib import Path
from typing import Any, Awaitable, Callable

REPO_ROOT = Path(__file__).resolve().parents[3]
PI_DIR = REPO_ROOT / "pi"
CAPTURE_SCRIPT = PI_DIR / "scripts" / "capture_proto.py"
PYTHON = PI_DIR / ".venv" / "bin" / "python"

log = logging.getLogger("nowplaying.vinyl")

ClipHandler = Callable[[Path, float], Awaitable[None]]
StateHandler = Callable[[str], Awaitable[None]]


async def _consume_stream(stream: asyncio.StreamReader, prefix: str) -> None:
    """Pipe captured stderr to our log so the operator sees device-status info."""
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        log.info("%s %s", prefix, text)


# Supervision backoff sequence (seconds). Cap is the last value; subsequent
# restarts stay at the cap. Reset to index 0 after a healthy run (> 30s).
SUPERVISION_BACKOFF = (1, 2, 5, 10, 30)
# Minimum run lifetime in seconds to consider a spawn "healthy" and reset
# the backoff clock. Matches the backoff cap so one full stable cycle clears
# the counter.
_BACKOFF_RESET_THRESHOLD_S = 30.0


class _BackoffClock:
    """Exponential backoff clock for capture restart sequences.

    Usage::

        clock = _BackoffClock()
        delay = clock.next()   # 1, 2, 5, 10, 30, 30, …
        clock.record_start()
        # … after a healthy run …
        clock.maybe_reset()    # resets index to 0 if run was long enough
    """

    def __init__(self, *, _now: Callable[[], float] = _time_module.monotonic) -> None:
        self._idx = 0
        self._start: float | None = None
        self._now = _now

    def next(self) -> float:
        """Return current delay and advance the index (capped at last value)."""
        delay = SUPERVISION_BACKOFF[min(self._idx, len(SUPERVISION_BACKOFF) - 1)]
        if self._idx < len(SUPERVISION_BACKOFF) - 1:
            self._idx += 1
        return float(delay)

    def record_start(self) -> None:
        """Mark the start of a new capture run."""
        self._start = self._now()

    def maybe_reset(self) -> None:
        """Reset backoff index if the last run was healthy (>= threshold)."""
        if self._start is not None:
            elapsed = self._now() - self._start
            if elapsed >= _BACKOFF_RESET_THRESHOLD_S:
                self._idx = 0
        self._start = None


# Shared reference to the running capture subprocess. Populated by
# run_capture so the orchestrator can send signals (SIGUSR1 / SIGUSR2)
# to adjust heartbeat cadence at runtime. Single capture process per
# orchestrator → single global is fine.
_capture_pid: int | None = None

# Exit code from the most recent run_capture invocation. Set in run_capture's
# finally block so run_capture_supervised can log it on unexpected death.
_last_exit_code: int | None = None


def signal_capture(sig: int) -> bool:
    """Send `sig` to the running capture subprocess. Returns True on
    success, False if no capture process is registered or the signal
    couldn't be delivered."""
    import os
    pid = _capture_pid
    if pid is None:
        return False
    try:
        os.kill(pid, sig)
        return True
    except OSError as e:
        log.warning("signal_capture(%s) failed: %r", sig, e)
        return False


def _log_started(ev: dict) -> None:
    log.info(
        "capture started: device=%s silence_db=%s heartbeat_s=%s",
        ev.get("device"),
        ev.get("silence_db"),
        ev.get("heartbeat_s"),
    )


async def _handle_heartbeat(ev: dict, on_clip: ClipHandler) -> None:
    """Dispatch a heartbeat event to on_clip. No-op if `clip` is missing."""
    rel = ev.get("clip")
    if not rel:
        return
    clip_path = REPO_ROOT / rel
    level_db = float(ev.get("level_db") or -120.0)
    # Instant clips (audible+3s flush) ship with `instant: True` in the
    # JSON event and a `_instant.wav` filename suffix. We log the
    # distinction for traceability; the orchestrator detects instant
    # clips via filename in on_heartbeat so the ClipHandler signature
    # stays stable.
    kind_tag = "instant" if ev.get("instant") else "heartbeat"
    log.info("capture %s: clip=%s level_db=%s", kind_tag, rel, ev.get("level_db"))
    try:
        await on_clip(clip_path, level_db)
    except Exception as e:
        log.exception("on_clip failed: %r", e)


async def _dispatch_event(
    ev: dict,
    on_clip: ClipHandler,
    on_state: StateHandler,
) -> bool:
    """Dispatch one parsed event. Returns True if the reader loop should
    stop (capture-side `stopped` event)."""
    kind = ev.get("event")
    if kind == "started":
        _log_started(ev)
    elif kind == "heartbeat":
        await _handle_heartbeat(ev, on_clip)
    elif kind == "silent":
        log.info("capture silent: level_db=%s", ev.get("level_db"))
        await on_state("silent")
    elif kind == "audible":
        log.info("capture audible: level_db=%s", ev.get("level_db"))
        await on_state("audible")
    elif kind == "stopped":
        log.info("capture stopped")
        return True
    return False


async def _read_events(
    stdout: asyncio.StreamReader,
    on_clip: ClipHandler,
    on_state: StateHandler,
) -> None:
    """Read JSONL events from capture stdout and dispatch by kind."""
    while True:
        line = await stdout.readline()
        if not line:
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            log.warning("capture: non-JSON line: %r", line)
            continue
        if await _dispatch_event(ev, on_clip, on_state):
            return


async def run_capture(
    on_clip: ClipHandler,
    on_state: StateHandler,
    stop: asyncio.Event,
    *,
    silence_db: float | None = None,
    start_paused: bool = False,
) -> None:
    """Run capture_proto.py and dispatch events.

    on_clip(clip_path, level_db) — called on each heartbeat with a 10s recorded clip and its measured level
    on_state(state)    — called with "silent" when sustained silence is detected
    """
    if not CAPTURE_SCRIPT.exists():
        raise FileNotFoundError(f"capture script missing: {CAPTURE_SCRIPT}")
    py = str(PYTHON) if PYTHON.exists() else sys.executable

    cmd: list[str] = [py, str(CAPTURE_SCRIPT)]
    if silence_db is not None:
        cmd += ["--silence-db", str(silence_db)]
    if start_paused:
        cmd += ["--start-paused"]

    log.info("starting capture: %s", " ".join(cmd))
    global _capture_pid
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _capture_pid = proc.pid
    stderr_task = asyncio.create_task(_consume_stream(proc.stderr, "[capture]"))

    assert proc.stdout is not None
    reader_task = asyncio.create_task(_read_events(proc.stdout, on_clip, on_state))
    try:
        done, pending = await asyncio.wait(
            {reader_task, asyncio.create_task(stop.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        global _last_exit_code
        _last_exit_code = proc.returncode
        _capture_pid = None
        stderr_task.cancel()


async def _probe_capture_health(stop: asyncio.Event) -> None:
    """Belt-and-suspenders health probe: every 60s confirm the capture PID
    is still alive in /proc. Only logs a WARNING on miss — does NOT restart
    (that's the watcher's job). Stops when ``stop`` fires.

    # Linux/Pi only — /proc is not available on macOS. The probe silently
    # no-ops on non-Linux platforms, which is fine since the Pi is the only
    # deployment target. Tests should not rely on probe log output on macOS.
    """
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=60.0)
            return  # stop fired
        except asyncio.TimeoutError:
            pass
        pid = _capture_pid
        if pid is None:
            continue
        proc_path = Path(f"/proc/{pid}/status")
        if not proc_path.exists():
            log.warning(
                "capture health probe: PID %s no longer in /proc "
                "(process may have been reaped between watcher cycles)",
                pid,
            )


async def run_capture_supervised(
    on_clip: ClipHandler,
    on_state: StateHandler,
    stop: asyncio.Event,
    *,
    get_start_paused: Callable[[], bool],
    silence_db: float | None = None,
    start_paused: bool = False,
) -> None:
    """Run ``run_capture`` in a supervision loop that restarts the capture
    subprocess with exponential backoff on unexpected exit.

    Args:
        on_clip: Forwarded to each ``run_capture`` invocation.
        on_state: Forwarded to each ``run_capture`` invocation.
        stop: Shared shutdown event. When set, the current ``run_capture``
            exits cleanly and the supervisor loop terminates — no restart.
        get_start_paused: Called on every restart (not the first spawn) to
            pick up the current pause intent from the orchestrator state,
            so the replacement child starts with the right emit mode without
            needing a post-spawn signal.
        silence_db: Forwarded to each ``run_capture`` invocation.
        start_paused: Used only for the **first** spawn; subsequent restarts
            use ``get_start_paused()`` instead.
    """
    clock = _BackoffClock()
    is_first = True
    health_task: asyncio.Task | None = asyncio.create_task(
        _probe_capture_health(stop)
    )
    try:  # skylos: ignore SKY-L004 SKY-L025 — Why: outer try guards health_task cancellation cleanup; inner try is a separate capture-subprocess restart loop — the two concerns can't share a single try without conflating normal restarts with health-task teardown
        while not stop.is_set():
            spawn_paused = start_paused if is_first else get_start_paused()
            is_first = False
            clock.record_start()
            try:
                await run_capture(
                    on_clip, on_state, stop,
                    silence_db=silence_db,
                    start_paused=spawn_paused,
                )
            except Exception as exc:
                log.error(
                    "capture subprocess raised unexpectedly (%r); "
                    "will restart after backoff",
                    exc,
                )
            clock.maybe_reset()

            if stop.is_set():
                break

            # Unexpected exit or exception — log and backoff before restarting.
            exit_code = _last_exit_code
            log.warning(
                "capture subprocess exited unexpectedly (exit_code=%s); "
                "will restart after backoff",
                exit_code,
            )
            delay = clock.next()
            log.info("capture supervisor: backoff %.0fs before restart", delay)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                # stop fired during backoff — exit cleanly
                break
            except asyncio.TimeoutError:
                pass
            log.info("capture supervisor: restarting capture subprocess")
    finally:
        if health_task is not None:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass


def to_now_playing_vinyl(result: dict[str, Any]) -> dict[str, Any]:
    """Translate recognize_proto.recognize() output → kiosk NowPlaying payload."""
    payload: dict[str, Any] = {
        "ts": result.get("ts"),
        "state": "PLAYING",
        "source": "vinyl",
        "title": result.get("title"),
        "artist": result.get("artist"),
        "album": result.get("album"),
        "year": result.get("year"),
        "label": result.get("label"),
        "catno": result.get("catno"),
        "track_position": result.get("track_position"),
        "release_id": result.get("release_id"),
        "match_method": result.get("match_method"),
        "match_confidence": result.get("match_confidence"),
        "tracklist": result.get("tracklist"),
        "alternate_releases": result.get("alternate_releases"),
        "track_started_at": result.get("track_started_at"),
        "anchor_source": result.get("anchor_source"),
    }
    rid = result.get("release_id")
    if rid is not None:
        from nowplaying.orchestrator._publish_enrichment import _art_url_for_release
        payload["art_url"] = _art_url_for_release(int(rid))
    else:
        # Shazam-only path: propagate wrapper-extracted art_url so the
        # kiosk gets Apple's CDN image when there's no Discogs scan.
        shazam_art = result.get("art_url")
        if shazam_art:
            payload["art_url"] = shazam_art
    # MB / Apple identifiers — emitted only when the recognize result
    # actually carried them. Optional fields; kiosk treats them additively.
    if result.get("release_mbid"):
        payload["release_mbid"] = result.get("release_mbid")
    if result.get("albumadamid"):
        payload["albumadamid"] = result.get("albumadamid")
    if payload["track_position"]:
        payload["side"] = payload["track_position"][:1]
    # Thread the matched track's duration so the Last.fm scrobble path can
    # apply the 50%-of-duration rule. Without it _should_scrobble falls back
    # to the >=240s leg and sub-4-minute tracks never scrobble.
    for tr in (result.get("tracklist") or []):
        if (tr.get("position") or tr.get("track_position")) == payload["track_position"]:
            if tr.get("duration_seconds") is not None:
                payload["duration_seconds"] = tr["duration_seconds"]
            break
    return payload

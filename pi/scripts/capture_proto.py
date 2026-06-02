"""Continuous capture from UFO202 with heartbeat-based emission.

Streams audio from the UFO202 in real time. Maintains a rolling 10-second
buffer. Every HEARTBEAT_S seconds, if the rolling RMS is above the silence
floor, writes the buffer to a clip file and emits a "heartbeat" event.

When RMS stays below the silence floor for SILENT_S seconds, emits a
"silent" event so the orchestrator can start its idle timer.

On each silent->audible transition we additionally:
  (a) schedule a one-shot "instant" clip flush at audible+3s — the 10s
      rolling buffer at that moment is ~7s of leading silence + ~3s of
      song audio, plenty for Shazam. This is what gets a recognition on
      screen within 2-5s of needle drop. Filename suffix `_instant.wav`
      so the orchestrator can relax its level-db gate for the clip
      (which will naturally have low RMS due to the silence padding).
  (b) override heartbeat cadence to 5s for the next 30s — gives Shazam
      3-4 extra shots at song-start.

Run:
    uv run python pi/scripts/capture_proto.py
"""
from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import sounddevice as sd

REPO_ROOT = Path(__file__).resolve().parents[2]
PI_DIR = REPO_ROOT / "pi"
DATA_DIR = PI_DIR / "data"
CLIPS_DIR = DATA_DIR / "clips"

# Instant-recognize-on-audible knobs. See module docstring for rationale.
INSTANT_RECOGNIZE_DELAY_S = 3.0
INSTANT_FAST_HEARTBEAT_S = 5.0
INSTANT_FAST_HEARTBEAT_DURATION_S = 30.0


def find_ufo202() -> int | None:
    for idx, dev in enumerate(sd.query_devices()):
        if "CODEC" in dev["name"] and dev["max_input_channels"] > 0:
            return idx
    return None


def db(x: float) -> float:
    return 20.0 * np.log10(x) if x > 1e-9 else -120.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def write_clip(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    int16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(int16.shape[1])
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(int16.tobytes())


def main() -> None:  # skylos: ignore — prototype script (argparse + signal-wiring inflates CC; not a production code path)
    p = argparse.ArgumentParser()
    p.add_argument("--device", type=int, default=None)
    p.add_argument("--rate", type=int, default=44100)
    p.add_argument("--silence-db", type=float, default=-34.0,
                   help="hysteresis LOWER bound (dBFS RMS): the gate flips to "
                        "silent only when level falls below this, and heartbeats "
                        "are suppressed. Pairs with --resume-music-db (upper "
                        "bound); the gap between them is a no-man's-land that "
                        "prevents threshold flap. Default matches "
                        "nowplaying/vinyl/levels.py SILENCE_DB; the orchestrator "
                        "passes the canonical value from there at launch.")
    p.add_argument("--heartbeat-s", type=float, default=15.0,
                   help="seconds between heartbeat clip emissions when signal is present")
    p.add_argument("--silent-s", type=float, default=5.0,
                   help="seconds below silence-db before emitting a silent event")
    p.add_argument("--audible-debounce-s", type=float, default=30.0,
                   help="suppress repeat 'audible' IPC events that fire within "
                        "this window of the previous one, UNLESS a sustained "
                        "'silent' event was emitted in between. Prevents "
                        "level-flap at the silence threshold from spamming "
                        "audible events that reset per-side state in the "
                        "orchestrator without ever flushing a heartbeat clip.")
    p.add_argument("--buffer-s", type=float, default=12.0,
                   help="rolling buffer duration written on each heartbeat. "
                        "Shazam's backend silently rejects clips >=15s "
                        "(shazamio Issue #150), so 12s leaves a safe pad "
                        "under the cliff.")
    p.add_argument("--start-paused", action="store_true",
                   help="start with heartbeat emission paused (orchestrator can SIGCONT to resume)")
    p.add_argument("--resume-music-db", type=float, default=-30.0,
                   help="hysteresis UPPER bound (dBFS RMS): the gate flips to "
                        "audible only when level rises above this — and, after a "
                        "silent period, force-emits the first heartbeat here so "
                        "the first post-silence clip lands at song-start. Pairs "
                        "with --silence-db (lower bound). Default matches "
                        "nowplaying/vinyl/levels.py MUSIC_DB; the orchestrator "
                        "passes the canonical value from there at launch.")
    p.add_argument("--instant-delay-s", type=float, default=INSTANT_RECOGNIZE_DELAY_S,
                   help="seconds after a silent->audible transition at which to flush the "
                        "rolling buffer as an _instant.wav clip. The 12s buffer at that point "
                        "covers ~t=-9..+3 of the new song — enough for Shazam to identify it "
                        "within 2-5s of needle drop. Replaces the older audible+5s burst.")
    p.add_argument("--fast-heartbeat-s", type=float, default=INSTANT_FAST_HEARTBEAT_S,
                   help="override heartbeat cadence to this many seconds for the first "
                        "--fast-heartbeat-duration-s seconds after a silent->audible "
                        "transition. Gives Shazam extra song-start shots.")
    p.add_argument("--fast-heartbeat-duration-s", type=float,
                   default=INSTANT_FAST_HEARTBEAT_DURATION_S,
                   help="how long after audible to keep the fast heartbeat cadence before "
                        "reverting to --heartbeat-s.")
    args = p.parse_args()

    # ---------------------------------------------------------------------------
    # Device-open with exponential-backoff retry.
    #
    # On Pi boot the USB Audio CODEC (UFO202) takes a few seconds to be
    # enumerated and bound by ALSA after the orchestrator starts. If the
    # first open attempt hits that window the process would die silently.
    # We retry with a 1→2→5→10→10… s backoff for up to ~30 s so that a
    # transient boot-time race doesn't leave the system deaf until a manual
    # restart. Each attempt is logged to stderr so the systemd journal
    # records the wait.
    # ---------------------------------------------------------------------------
    _BACKOFF = [1, 2, 5, 10]  # seconds between successive retries
    _OPEN_DEADLINE = 30        # give up after ~this many seconds total
    dev: int | None = None
    _open_start = time.monotonic()
    _attempt = 0
    while True:
        candidate = args.device if args.device is not None else find_ufo202()
        if candidate is not None:
            dev = candidate
            break
        elapsed = time.monotonic() - _open_start
        if elapsed >= _OPEN_DEADLINE:
            sys.exit("UFO202 (CODEC) not found after retrying for ~30s; giving up")
        delay = _BACKOFF[min(_attempt, len(_BACKOFF) - 1)]
        print(
            f"[capture] device not found (attempt {_attempt + 1}, "
            f"elapsed {elapsed:.1f}s); retrying in {delay}s …",
            file=sys.stderr, flush=True,
        )
        time.sleep(delay)
        _attempt += 1

    info = sd.query_devices(dev)
    print(f"[capture] device [{dev}] {info['name']} rate={args.rate}", file=sys.stderr, flush=True)

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    block_s = 0.05
    block = int(args.rate * block_s)
    buffer_blocks = int(args.buffer_s / block_s)
    rolling: deque[np.ndarray] = deque(maxlen=buffer_blocks)
    rms_window_blocks = buffer_blocks
    rms_window: deque[float] = deque(maxlen=rms_window_blocks)

    last_heartbeat_at: float = 0.0
    silent_since: float | None = None
    silent_emitted: bool = False
    # Single-block-granularity below-floor flag. Flips True the moment a
    # block dips below `silence_db`; on the next above-floor block we fire
    # an `audible` IPC event regardless of how brief the dip was. This is
    # the side-flip path that doesn't satisfy the `--silent-s` sustained
    # silence threshold gating `silent_emitted`. See feature
    # audible-event-wiring-regression.
    was_below_floor: bool = False
    # Set True when audio crosses back above silence_db after a sustained
    # silent period. While True, the normal heartbeat cadence is bypassed:
    # the next music-level heartbeat (level >= --resume-music-db) fires as
    # soon as the rolling buffer is full. Clears once that heartbeat emits.
    resume_pending: bool = False
    # Monotonic timestamp at which the instant clip should be flushed.
    # Set to (now + --instant-delay-s) on each silent->audible transition.
    # The instant clip's 10s rolling buffer covers approximately
    # t=-7..+3 of the new song. Cleared after the instant fires (one-shot
    # per silent period). A second audible transition within the window
    # re-arms it.
    instant_due_at: float | None = None
    # Monotonic timestamp until which the heartbeat cadence is overridden
    # to --fast-heartbeat-s. Set to (now + --fast-heartbeat-duration-s) on
    # each silent->audible transition. After this expires, cadence
    # reverts to args.heartbeat_s. A second audible re-arms it.
    fast_heartbeat_until: float = 0.0
    # Monotonic timestamp of the most recent audible IPC event we emitted.
    # Used by the --audible-debounce-s gate to drop transient threshold-
    # crossing audible events that aren't preceded by a sustained-silence
    # event (= real side change).
    last_audible_at: float = -1e9

    audio_q: queue.Queue = queue.Queue()
    stop = threading.Event()
    # Runtime-mutable heartbeat interval — orchestrator can SIGUSR1 to drop
    # to FAST_HEARTBEAT_S (anticipated end-of-track), SIGUSR2 to revert.
    FAST_HEARTBEAT_S = 5.0
    current_heartbeat_s = [args.heartbeat_s]
    # Runtime emit gate — orchestrator can SIGHUP to suppress heartbeat
    # emission (no clip writes, no JSON events) when Sonos source isn't a
    # UFO202-listened source (streaming, TV, radio). SIGCONT resumes.
    # Audio stream + rolling buffer + RMS keep running so resumption is
    # instant. Silent events continue to fire so idle logic still works.
    emit_paused = [args.start_paused]

    def cb(indata, frames, _t, status):
        if status:
            print(f"[capture] stream status: {status}", file=sys.stderr, flush=True)
        audio_q.put(indata.copy())

    def handle_signal(signum, frame):
        stop.set()

    def handle_speedup(signum, frame):
        current_heartbeat_s[0] = FAST_HEARTBEAT_S
        print(
            f"[capture] heartbeat cadence -> {FAST_HEARTBEAT_S}s (SIGUSR1)",
            file=sys.stderr, flush=True,
        )

    def handle_revert(signum, frame):
        current_heartbeat_s[0] = args.heartbeat_s
        print(
            f"[capture] heartbeat cadence -> {args.heartbeat_s}s (SIGUSR2)",
            file=sys.stderr, flush=True,
        )

    def handle_pause(signum, frame):
        if not emit_paused[0]:
            emit_paused[0] = True
            print("[capture] heartbeat emit paused (SIGHUP)", file=sys.stderr, flush=True)

    def handle_resume(signum, frame):
        if emit_paused[0]:
            emit_paused[0] = False
            print("[capture] heartbeat emit resumed (SIGCONT)", file=sys.stderr, flush=True)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGUSR1, handle_speedup)
    signal.signal(signal.SIGUSR2, handle_revert)
    signal.signal(signal.SIGHUP, handle_pause)
    signal.signal(signal.SIGCONT, handle_resume)

    # ---------------------------------------------------------------------------
    # Stream-open with retry — the device may appear in the ALSA device list
    # before it is fully initialised (e.g. if the USB descriptors have been
    # read but the audio interface isn't yet ready). A short open-retry loop
    # covers this second race window without adding a fixed sleep.
    # ---------------------------------------------------------------------------
    _stream: sd.InputStream | None = None  # assigned on first successful open
    _stream_attempt = 0
    _stream_open_start = time.monotonic()
    while True:
        try:
            _stream = sd.InputStream(device=dev, samplerate=args.rate, channels=2,
                                     dtype="float32", blocksize=block, callback=cb)
            _stream.__enter__()
            break
        except Exception as exc:  # noqa: BLE001 — sd raises various Exception subclasses
            elapsed = time.monotonic() - _stream_open_start
            if elapsed >= _OPEN_DEADLINE:
                sys.exit(
                    f"Failed to open audio stream after ~30s "
                    f"(last error: {exc}); giving up"
                )
            delay = _BACKOFF[min(_stream_attempt, len(_BACKOFF) - 1)]
            print(
                f"[capture] stream open failed (attempt {_stream_attempt + 1}, "
                f"elapsed {elapsed:.1f}s): {exc}; retrying in {delay}s …",
                file=sys.stderr, flush=True,
            )
            # Device may have disappeared from ALSA — re-probe it before the
            # next open attempt so we pick up any re-enumerated index.
            if args.device is None:
                new_dev = find_ufo202()
                if new_dev is not None and new_dev != dev:
                    print(
                        f"[capture] device index changed {dev}→{new_dev} during open retry",
                        file=sys.stderr, flush=True,
                    )
                    dev = new_dev
                    info = sd.query_devices(dev)
            time.sleep(delay)
            _stream_attempt += 1

    emit({"ts": now_iso(), "event": "started",
          "device": info["name"], "silence_db": args.silence_db,
          "heartbeat_s": args.heartbeat_s})
    try:  # skylos: ignore SKY-L004 — Why: finally-only cleanup of _stream; all control flow belongs to the capture loop and can't be hoisted without fragmenting mutable scalar state across a function boundary
        while not stop.is_set():
            try:
                blk = audio_q.get(timeout=0.5)
            except queue.Empty:
                # Polling timeout — no audio in queue this tick; resume loop.
                continue
            rolling.append(blk)
            rms = float(np.sqrt(np.mean(blk * blk))) if blk.size else 0.0
            rms_window.append(rms)
            mean_rms = float(np.mean(rms_window))
            level_db = db(mean_rms)
            now = time.monotonic()

            if level_db < args.silence_db:
                silent_since = silent_since or now
                was_below_floor = True
                if not silent_emitted and now - silent_since >= args.silent_s:
                    emit({"ts": now_iso(), "event": "silent",
                          "level_db": round(level_db, 1)})
                    silent_emitted = True
            elif level_db >= args.resume_music_db:
                # Audio rose above the MUSIC (upper hysteresis) bound. Any
                # silent→audible level transition — even a sub-second dip from a fast
                # side-flip — fires the `audible` IPC event so main.py
                # can clear the previous side's album-lock and (when
                # idle) flip the kiosk to VinylIdentifying. The
                # `silent_emitted` (sustained-silence) gate that USED to
                # nest this block dropped fast side-flips on the floor
                # because they never reached --silent-s seconds of
                # silence. See feature audible-event-wiring-regression.
                if was_below_floor:
                    # Only treat this as a real silent→audible transition if
                    # either (a) we actually emitted a sustained-silent event
                    # in the gap (= side end / new record), or (b) it has
                    # been at least --audible-debounce-s since our last
                    # audible IPC. This drops the storm of fake transitions
                    # we'd otherwise get when a quiet song's level oscillates
                    # across the silence floor — see the Honey Pie incident
                    # 2026-05-14 where 40s of audible flapping kept resetting
                    # the instant-clip timer and Shazam never got called.
                    is_real_resume = (
                        silent_emitted
                        or (now - last_audible_at) >= args.audible_debounce_s
                    )
                    if is_real_resume:
                        resume_pending = True
                        last_audible_at = now
                        print(
                            f"[capture] emitting audible IPC event "
                            f"(level={level_db:.1f})",
                            file=sys.stderr, flush=True,
                        )
                        emit({"ts": now_iso(), "event": "audible",
                              "level_db": round(level_db, 1)})
                        # Arm the audible+3s instant clip and the 30s fast
                        # heartbeat window.
                        instant_due_at = now + args.instant_delay_s
                        fast_heartbeat_until = now + args.fast_heartbeat_duration_s
                        print(
                            "[capture] instant clip queued (audible+"
                            f"{args.instant_delay_s:.0f}s) + "
                            f"{args.fast_heartbeat_s:.0f}s heartbeat for "
                            f"{args.fast_heartbeat_duration_s:.0f}s",
                            file=sys.stderr, flush=True,
                        )
                was_below_floor = False
                silent_since = None
                silent_emitted = False
            else:
                # Hysteresis no-man's-land: SILENCE_DB <= level < MUSIC_DB.
                # Hold the current silent/audible state — flip neither way —
                # so a level hovering between the bounds can't flap.
                pass

            if emit_paused[0]:
                continue
            # Effective heartbeat interval: use --fast-heartbeat-s while
            # we're inside the post-audible window, else whatever
            # current_heartbeat_s[0] is (SIGUSR1/2-mutable).
            if now < fast_heartbeat_until:
                effective_heartbeat_s = min(
                    current_heartbeat_s[0], args.fast_heartbeat_s
                )
            else:
                effective_heartbeat_s = current_heartbeat_s[0]

            # Cadence guard, with two short-circuits:
            #   (1) `resume_pending` — post-silence resume heartbeat. Wait
            #       for the rolling buffer to fill with music-level audio
            #       and emit immediately, ignoring the cadence.
            #   (2) `instant_due_at` — audible+3s instant clip. Fires once,
            #       independent of the heartbeat cadence, with filename
            #       suffix `_instant.wav`. Resume-pending takes priority
            #       since it lands earlier; the instant then fills the
            #       ~t=-7..+3 window ~3s after audible.
            instant_ready = (
                instant_due_at is not None
                and now >= instant_due_at
                and len(rolling) >= buffer_blocks
                and level_db >= args.resume_music_db
            )
            if resume_pending:
                if len(rolling) < buffer_blocks:
                    continue
                if level_db < args.resume_music_db:
                    continue
                # fall through to clip emission
            elif instant_ready:
                # fall through to clip emission (instant path)
                pass
            else:
                if now - last_heartbeat_at < effective_heartbeat_s:
                    continue
                # Suppress heartbeats while in the below-floor (silent)
                # hysteresis state — which now persists through the
                # no-man's-land until level crosses the MUSIC_DB upper bound.
                if was_below_floor:
                    continue
                if len(rolling) < buffer_blocks:
                    continue

            clip = np.concatenate(list(rolling), axis=0)
            ts = int(time.time())
            # Distinguish the instant emit (only when instant path
            # triggered AND not preempted by resume_pending, which takes
            # priority). The orchestrator keys off `_instant.wav` to
            # relax its shazam-only level-db gate.
            is_instant = instant_ready and not resume_pending
            suffix = "_instant.wav" if is_instant else "_heartbeat.wav"
            clip_path = CLIPS_DIR / f"{ts}{suffix}"
            write_clip(clip_path, clip, args.rate)
            evt: dict = {
                "ts": now_iso(),
                "event": "heartbeat",
                "level_db": round(level_db, 1),
                "clip": str(clip_path.relative_to(REPO_ROOT)),
                "clip_seconds": round(clip.shape[0] / args.rate, 2),
            }
            if is_instant:
                evt["instant"] = True
            emit(evt)
            last_heartbeat_at = now
            # The post-silence resume heartbeat fires exactly once per
            # silent period. Subsequent heartbeats in the same audible
            # period use the (possibly fast-overridden) cadence.
            resume_pending = False
            # The instant clip fires exactly once per audible transition.
            # Clear regardless of which path emitted this clip so a
            # near-simultaneous resume + instant don't double-emit.
            instant_due_at = None

        emit({"ts": now_iso(), "event": "stopped"})
    finally:
        if _stream is not None:
            _stream.__exit__(None, None, None)


if __name__ == "__main__":
    main()

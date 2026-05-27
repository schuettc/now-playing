"""Phase 2 calibration: stream live RMS / peak from UFO202.

Run:
    uv run python pi/scripts/capture_calibrate.py [--device <idx>]

Prints one line per second:
    HH:MM:SS  rms=-XX.X dB  peak=-XX.X dB  bar=[####    ]

Use this to set the VAD threshold: watch idle (silence) and music sections,
pick a threshold ~10-15 dB above idle that's still well below music RMS.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import numpy as np
import sounddevice as sd


def find_ufo202() -> int | None:
    for idx, dev in enumerate(sd.query_devices()):
        if "CODEC" in dev["name"] and dev["max_input_channels"] > 0:
            return idx
    return None


def db(x: float) -> float:
    return 20.0 * np.log10(x) if x > 1e-9 else -120.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=None, help="input device index (default: auto-detect UFO202)")
    parser.add_argument("--rate", type=int, default=44100)
    parser.add_argument("--seconds", type=float, default=0.0, help="exit after N seconds (0 = run forever)")
    args = parser.parse_args()

    dev = args.device if args.device is not None else find_ufo202()
    if dev is None:
        sys.exit("No UFO202 (CODEC) input device found")
    info = sd.query_devices(dev)
    print(f"Using device [{dev}] {info['name']}  rate={args.rate}  channels=2", flush=True)
    print("(Ctrl-C to stop. Watch RMS as you toggle silence vs music.)", flush=True)

    block = int(args.rate * 0.05)  # 50ms blocks
    window_blocks = int(1.0 / 0.05)  # 1s window
    rms_buf: list[float] = []
    peak_buf: list[float] = []
    last_print = time.monotonic()
    started = last_print

    def cb(indata, frames, _t, status):
        nonlocal last_print
        if status:
            print(f"[stream status] {status}", file=sys.stderr, flush=True)
        # indata is float32, shape (frames, 2)
        x = indata.astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
        peak = float(np.max(np.abs(x))) if x.size else 0.0
        rms_buf.append(rms)
        peak_buf.append(peak)
        if len(rms_buf) > window_blocks:
            rms_buf.pop(0)
            peak_buf.pop(0)

        now = time.monotonic()
        if now - last_print >= 1.0:
            mean_rms = float(np.mean(rms_buf))
            max_peak = float(np.max(peak_buf))
            rms_db = db(mean_rms)
            peak_db = db(max_peak)
            # crude bar: -80..0 dB → 0..40 chars
            n = max(0, min(40, int((rms_db + 80) / 2)))
            bar = "#" * n + " " * (40 - n)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"{ts}  rms={rms_db:6.1f} dB  peak={peak_db:6.1f} dB  [{bar}]", flush=True)
            last_print = now

    with sd.InputStream(device=dev, samplerate=args.rate, channels=2, dtype="float32", blocksize=block, callback=cb):
        try:
            while True:
                if args.seconds and (time.monotonic() - started) >= args.seconds:
                    break
                sd.sleep(200)
        except KeyboardInterrupt:
            print("\n[calibrate] stopped", flush=True)


if __name__ == "__main__":
    main()

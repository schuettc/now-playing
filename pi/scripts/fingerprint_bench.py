#!/usr/bin/env python3
"""Standalone Pi-perf benchmark for the F2 fingerprint engine.

Generates 200 synthetic 12s WAV clips across 10 "albums" (20 refs each),
times add_ref + match, asserts p95 latency and accuracy gates.

Usage:
    uv run python scripts/fingerprint_bench.py

Exits non-zero on:
    - p95 match latency >= 5000ms (the per-heartbeat budget on Shazam miss)
    - top-1 match accuracy < 90%

Prints a non-blocking warning if p95 > 1000ms — that's a smoke signal
the implementation drifted toward inefficiency without breaking the
budget.
"""
from __future__ import annotations

import io
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

PI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PI_DIR))

from nowplaying.vinyl import fingerprint as fp  # noqa: E402


N_ALBUMS = 10
REFS_PER_ALBUM = 20
TOTAL_REFS = N_ALBUMS * REFS_PER_ALBUM
N_MATCH_QUERIES = 50
NOISE_STD = 0.05  # gaussian noise added to query clips
CLIP_SECONDS = 12.0
SAMPLE_RATE = 44100
WARN_P95_MS = 1000.0
HARD_P95_MS = 5000.0
MIN_ACCURACY = 0.90


def _make_clip(seed: int, with_noise: bool = False) -> bytes:  # skylos: ignore SKY-L029 — bench helper; both call sites use a literal True/False, kwarg overhead unnecessary
    """Generate a synthetic 12s WAV. `seed` controls the spectrum so
    different seeds produce different fingerprints; same seed produces
    the same clip (modulo noise)."""
    rng = np.random.default_rng(seed)
    n_samples = int(CLIP_SECONDS * SAMPLE_RATE)
    t = np.linspace(0, CLIP_SECONDS, n_samples, endpoint=False)
    f1 = 220.0 + (seed % 100) * 5
    f2 = 1.5 * f1 + (seed % 50) * 3
    f3 = 2.7 * f1 + (seed % 30) * 7
    audio = (
        np.sin(2 * np.pi * f1 * t)
        + 0.6 * np.sin(2 * np.pi * f2 * t)
        + 0.3 * np.sin(2 * np.pi * f3 * t)
    )
    # Pink-ish noise floor for non-trivial peak finding.
    audio += 0.05 * rng.standard_normal(n_samples)
    if with_noise:
        audio += NOISE_STD * rng.standard_normal(n_samples)
    audio /= np.max(np.abs(audio)) + 1e-9
    # Stereo, matching the capture pipeline's typical output.
    stereo = np.stack([audio, audio], axis=1).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, stereo, SAMPLE_RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def main() -> int:  # skylos: ignore SKY-C304 SKY-Q301 SKY-P403 — bench harness; intentionally a single linear function emitting phase-by-phase prints. Splitting would scatter the bench narrative for no test benefit (script is not unit-tested and never imported).
    print(f"[bench] generating {TOTAL_REFS} refs across {N_ALBUMS} albums ...")
    # Bench DB lives in tmpdir, not pi/data/. Burst-loading 200 refs
    # against the SD card hits sync-write contention (each INSERT
    # fsyncs WAL frames) that doesn't reflect production use, where
    # add_ref is called at heartbeat cadence (~once per 15s) on a
    # warm DB. tmpdir is tmpfs (RAM-backed on Pi OS) and isolates
    # the perf measurement to the algorithm + DB logic, not SD I/O.
    import tempfile
    db_path = Path(tempfile.gettempdir()) / "fingerprint_bench.db"
    if db_path.exists():
        db_path.unlink()
    fp.init_db(db_path)

    # Phase 1: fingerprint and store 200 refs. Each (album_id, track_idx)
    # gets a unique seed so the resulting fingerprints are distinct.
    add_durations_ms: list[float] = []
    ref_seeds: dict[int, int] = {}  # ref_id → seed (so we can re-generate the query clip later)
    for album in range(N_ALBUMS):
        release_id = 1000 + album
        for track_idx in range(REFS_PER_ALBUM):  # skylos: ignore SKY-P403 — bench sweep is inherently 2D (N_ALBUMS × REFS_PER_ALBUM); each iteration creates a distinct fingerprint, no dict-lookup alternative
            seed = album * 1000 + track_idx
            clip = _make_clip(seed)
            t0 = time.perf_counter()
            ref_id = fp.add_ref(
                release_id=release_id,
                track_position=f"A{track_idx + 1}",
                track_position_s=float(track_idx * 30),
                wav_bytes=clip,
                db_path=db_path,
            )
            add_durations_ms.append((time.perf_counter() - t0) * 1000)
            ref_seeds[ref_id] = seed

    print(
        f"[bench] add_ref × {TOTAL_REFS}: "
        f"total={sum(add_durations_ms) / 1000:.1f}s "
        f"mean={np.mean(add_durations_ms):.0f}ms "
        f"p95={np.percentile(add_durations_ms, 95):.0f}ms"
    )

    # Phase 2: match queries. For each query, pick a known ref, regenerate
    # the original clip with added noise, run match, check top-1.
    rng = np.random.default_rng(42)
    ref_ids = list(ref_seeds.keys())
    selected_refs = rng.choice(ref_ids, size=N_MATCH_QUERIES, replace=False)
    match_durations_ms: list[float] = []
    n_correct = 0
    for chosen_ref_id in selected_refs:
        chosen_ref_id = int(chosen_ref_id)
        seed = ref_seeds[chosen_ref_id]
        album = seed // 1000
        release_id = 1000 + album
        noisy_clip = _make_clip(seed, with_noise=True)
        t0 = time.perf_counter()
        hits = fp.match(
            noisy_clip, release_filter=release_id, min_hits=10, db_path=db_path,
        )
        match_durations_ms.append((time.perf_counter() - t0) * 1000)
        if hits and hits[0].ref_id == chosen_ref_id:
            n_correct += 1
        elif hits:
            # Wrong ref but still hit something — log for debugging.
            print(
                f"[bench]   miss: query seed={seed} expected ref_id={chosen_ref_id} "
                f"got ref_id={hits[0].ref_id} hits={hits[0].hits}"
            )
        else:
            print(f"[bench]   no match: query seed={seed} expected ref_id={chosen_ref_id}")

    p50 = np.percentile(match_durations_ms, 50)
    p95 = np.percentile(match_durations_ms, 95)
    p99 = np.percentile(match_durations_ms, 99)
    accuracy = n_correct / N_MATCH_QUERIES
    print(
        f"\n[bench] match × {N_MATCH_QUERIES}: "
        f"p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms"
    )
    print(f"[bench] accuracy: {n_correct}/{N_MATCH_QUERIES} = {accuracy:.1%}")

    # Gate evaluation.
    failed = False
    if p95 >= HARD_P95_MS:
        print(f"\nFAIL: p95={p95:.0f}ms exceeds hard ceiling {HARD_P95_MS:.0f}ms")
        failed = True
    elif p95 > WARN_P95_MS:
        print(
            f"\nWARN: p95={p95:.0f}ms exceeds {WARN_P95_MS:.0f}ms warning threshold "
            "(within hard budget but suggests an inefficiency)"
        )
    if accuracy < MIN_ACCURACY:
        print(f"\nFAIL: accuracy={accuracy:.1%} below floor {MIN_ACCURACY:.1%}")
        failed = True

    if failed:
        return 1
    print("\nPASS: all gates met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

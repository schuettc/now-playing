"""Tiny disk I/O helpers used inside asyncio.to_thread."""
from __future__ import annotations


def _read_bytes(path) -> bytes:
    """Tiny helper: read a file's bytes. Used inside asyncio.to_thread
    so disk I/O doesn't block the event loop in the fingerprint cascade
    auto-promote and fallback paths.

    `path` is always an internal capture-pipeline path (clip_path from
    the capture state machine), never user input. Callers: capture
    cascade auto-promote + fallback only.
    """
    with open(path, "rb") as f:  # skylos: ignore SKY-D215 — Why: path is always an internal capture-pipeline clip_path set by the capture state machine, never user input
        return f.read()  # skylos: ignore SKY-P401 — capture clips are bounded (≤12s @ 44.1kHz stereo ~ 2MB); loading whole file is correct, no streaming benefit

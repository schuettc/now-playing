#!/usr/bin/env python3
"""Build a unified timeline from a capture-session directory.

Inputs (all under --dir):
    events.log     filtered orchestrator events, one per line, with ISO timestamps
    markers.log    operator narrations, "HH:MM:SS USER: text"
    shots/         screenshot PNGs named "<HHMMSS>_<kind>_<slug>.png"

Output: a markdown table written to stdout (or --out PATH) with one row per
event, marker, or publish-triggered screenshot, sorted by wall-clock time.

Usage:
    build_timeline.py --dir /tmp/session-bside-2 > timeline.md
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path

_PI_DIR = Path(__file__).resolve().parent.parent
if str(_PI_DIR) not in sys.path:
    sys.path.insert(0, str(_PI_DIR))

from nowplaying._io_safe import safe_read_bytes  # noqa: E402 — Why: sys.path must be set before this import

_TIMELINE_MAX_BYTES = 64 * 1024 * 1024  # 64 MiB — bounded by session log size

EVENT_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+")
MARKER_TS = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+USER:\s+(.*)$")
SHOT_TS = re.compile(r"^(\d{2})(\d{2})(\d{2})_([a-z]+)(?:_(.+))?\.png$")


@dataclass
class Row:
    when: time
    kind: str  # event | user | shot
    text: str


def parse_event(line: str) -> Row | None:
    m = EVENT_TS.match(line)
    if not m:
        return None
    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S,%f")
    body = line[m.end():].strip()
    return Row(when=dt.time(), kind="event", text=body)


def parse_marker(line: str) -> Row | None:
    m = MARKER_TS.match(line.strip())
    if not m:
        return None
    t = datetime.strptime(m.group(1), "%H:%M:%S").time()
    return Row(when=t, kind="user", text=m.group(2))


def parse_shot(name: str) -> Row | None:
    m = SHOT_TS.match(name)
    if not m:
        return None
    h, mi, s, kind, slug = m.groups()
    t = time(int(h), int(mi), int(s))
    label = f"shot ({kind})"
    if slug:
        label = f"shot ({kind}: {slug.replace('_', ' ')})"
    return Row(when=t, kind="shot", text=label + f" — {name}")


def _rows_from_log(path: Path, parse_fn) -> list[Row]:
    """Read a log file and return parsed rows, skipping lines that don't match."""
    if not path.exists():
        return []
    lines = safe_read_bytes(path, max_bytes=_TIMELINE_MAX_BYTES).decode("utf-8", errors="replace").splitlines()
    return [r for line in lines for r in (parse_fn(line),) if r]


def _rows_from_shots(shots_dir: Path) -> list[Row]:
    """Return rows parsed from shot filenames in the shots directory."""
    if not shots_dir.exists():
        return []
    return [r for p in shots_dir.iterdir() for r in (parse_shot(p.name),) if r]


def collect(dir_path: Path) -> list[Row]:
    rows: list[Row] = (
        _rows_from_log(dir_path / "events.log", parse_event)
        + _rows_from_log(dir_path / "markers.log", parse_marker)
        + _rows_from_shots(dir_path / "shots")
    )
    rows.sort(key=lambda r: (r.when, 0 if r.kind == "user" else 1))
    return rows


def format_markdown(rows: list[Row]) -> str:
    out = ["| time | kind | what |", "|---|---|---|"]
    for r in rows:
        text = r.text.replace("|", "\\|")
        out.append(f"| {r.when.isoformat()} | {r.kind} | {text} |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rows = collect(args.dir)
    md = format_markdown(rows)
    if args.out:
        args.out.write_text(md)
    else:
        print(md)


if __name__ == "__main__":
    main()

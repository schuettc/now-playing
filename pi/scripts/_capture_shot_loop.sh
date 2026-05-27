#!/usr/bin/env bash
# Inner loop: baseline kiosk screenshot every 30s.
# Invoked by capture_session.sh start. Not meant to be run directly.

set -uo pipefail

DIR="${1:?dir required}"
INTERVAL="${BASELINE_INTERVAL:-30}"
WAYLAND_RUNTIME="${WAYLAND_RUNTIME:-/run/user/1000}"
WAYLAND_SOCK="${WAYLAND_SOCK:-wayland-0}"

while true; do
    ts=$(date +%H%M%S)
    XDG_RUNTIME_DIR="$WAYLAND_RUNTIME" WAYLAND_DISPLAY="$WAYLAND_SOCK" \
        grim "$DIR/shots/${ts}_baseline.png" 2>>"$DIR/shotloop.err" || true
    sleep "$INTERVAL"
done

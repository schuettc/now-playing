#!/bin/bash
# Run the Vite dev server on your Mac while pulling live data + album art
# from a real Pi backend.
#
# Usage:
#   ./kiosk/scripts/dev-against-pi.sh                      # → nowplaying-pi.local:8080
#   ./kiosk/scripts/dev-against-pi.sh 192.168.4.200:8080   # → explicit host:port
#   ./kiosk/scripts/dev-against-pi.sh otherpi.local:8080   # → another Pi
#
# Why this exists:
# - The kiosk talks to its backend over TWO independent channels: a
#   WebSocket for now-playing data (VITE_WS_URL) and plain HTTP for
#   album art + the art-candidate picker (/art/*, /api/*). On the real
#   kiosk both are same-origin, so relative URLs just work. On a Vite
#   dev server (:5173) they don't — you must point BOTH at the Pi.
# - Set only VITE_WS_URL and you get live track data but BROKEN art:
#   /art/<id> and /api/art-candidates hit Vite, get index.html back, and
#   the picker shows "No alternative art found." Setting both fixes it.
# - Set NEITHER and the app falls back to a mock fixture (no live data).

set -e

HOST_PORT="${1:-nowplaying-pi.local:8080}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIOSK_DIR="$(dirname "$SCRIPT_DIR")"
cd "$KIOSK_DIR"

export VITE_WS_URL="ws://${HOST_PORT}/ws"
export VITE_DEV_PROXY="http://${HOST_PORT}"

echo "kiosk dev → backend ${HOST_PORT}"
echo "  VITE_WS_URL=${VITE_WS_URL}"
echo "  VITE_DEV_PROXY=${VITE_DEV_PROXY}"
echo "  open http://localhost:5173/"

exec npm run dev

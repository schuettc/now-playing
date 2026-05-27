#!/bin/bash
# Launch a Chrome window at exactly 1920×1080 to preview the kiosk
# layout as it would render on the Waveshare FHD display.
#
# Usage:
#   ./kiosk/scripts/preview-fhd.sh                       # → Pi at nowplaying-pi.local:8080
#   ./kiosk/scripts/preview-fhd.sh http://localhost:5173 # → local Vite dev server
#
# Notes:
# - Uses Chrome's --app mode so there's no browser chrome (URL bar,
#   bookmarks, tabs) eating into the viewport. The window content
#   area is exactly 1920×1080.
# - --user-data-dir isolates the preview from your main Chrome
#   profile so it doesn't share cookies / extensions.
# - On a 4K or 5K display this opens a 1920×1080 window pixel-for-
#   pixel matching the kiosk. On a 13" laptop the window will be
#   bigger than the screen and you'll need to scroll or zoom out
#   (Cmd+- to fit).

set -e

URL="${1:-http://nowplaying-pi.local:8080/}"
PROFILE_DIR="${TMPDIR:-/tmp}/kiosk-preview-chrome"

mkdir -p "$PROFILE_DIR"

exec /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --app="$URL" \
  --window-size=1920,1080 \
  --window-position=0,0 \
  --user-data-dir="$PROFILE_DIR" \
  --disable-features=Translate \
  --no-default-browser-check \
  --no-first-run

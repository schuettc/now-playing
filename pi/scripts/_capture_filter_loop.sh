#!/usr/bin/env bash
# Inner loop: tail raw.log, filter to events.log, snap screenshot on publish.
# Invoked by capture_session.sh start. Not meant to be run directly.

set -uo pipefail

DIR="${1:?dir required}"
WAYLAND_RUNTIME="${WAYLAND_RUNTIME:-/run/user/1000}"
WAYLAND_SOCK="${WAYLAND_SOCK:-wayland-0}"

tail -F "$DIR/raw.log" 2>/dev/null | while IFS= read -r line; do
    # Strip "May 20 09:47:29 nowplaying-pi python[10596]: " prefix.
    # Keep the ISO timestamp emitted by the app's own logger.
    stripped=$(printf '%s\n' "$line" \
        | sed -E 's/^[A-Z][a-z]+ +[0-9]+ +[0-9:]+ +[^ ]+ +python\[[0-9]+\]: +//')

    case "$stripped" in
        *"recognize: method=shazam"*|\
        *"recognize: method=unmatched"*|\
        *"fingerprint: matched"*|\
        *"fingerprint: position changed"*|\
        *"fingerprint: anchor set"*|\
        *"fingerprint: no match"*|\
        *"fingerprint: below threshold"*|\
        *"predicted:"*|\
        *"publish:"*|\
        *"state-decay"*|\
        *"NEEDS_ID"*|\
        *"promotion:"*|\
        *"sonos: state="*|\
        *"capture silent"*|\
        *"idle timer"*)
            printf '%s\n' "$stripped" >> "$DIR/events.log"
            ;;
    esac

    # Event-triggered screenshot on real publishes (not redundant ones)
    case "$stripped" in
        *"publish: clients="*)
            case "$stripped" in *"redundant"*) continue ;; esac
            # Extract title between single quotes: title='Pillowhead'
            title=$(printf '%s' "$stripped" \
                | sed -nE "s/.*title='([^']*)'.*/\1/p" \
                | tr -c '[:alnum:]' '_' \
                | sed 's/_*$//; s/^_*//')
            [[ -z "$title" ]] && title="publish"
            ts=$(date +%H%M%S)
            XDG_RUNTIME_DIR="$WAYLAND_RUNTIME" WAYLAND_DISPLAY="$WAYLAND_SOCK" \
                grim "$DIR/shots/${ts}_publish_${title}.png" 2>>"$DIR/shotloop.err" || true
            ;;
    esac
done

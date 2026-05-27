#!/usr/bin/env bash
# Live capture harness for observation sessions on the Pi.
#
# What it does:
#   - Tails journalctl -u nowplaying-orchestrator into raw.log
#   - Filters to "interesting" events (recognize/publish/predicted/fingerprint/
#     promotion/sonos/state-decay/NEEDS_ID) into events.log
#   - Takes a baseline kiosk screenshot every 30s
#   - Takes an event-triggered screenshot ~1s after each non-redundant
#     publish line, named with the published title
#   - Writes a markers.log file that the operator can append to from anywhere
#
# Usage:
#   capture_session.sh start [--dir DIR]    # default DIR=/tmp/session-<timestamp>
#   capture_session.sh stop  [--dir DIR]    # kills background loops, prints summary
#   capture_session.sh mark "text" --dir DIR  # append USER marker w/ Pi wall-clock
#   capture_session.sh status --dir DIR     # show recent events + screenshot count
#
# Artifacts in $DIR:
#   raw.log         full journalctl output
#   events.log      filtered "interesting" lines
#   markers.log     operator-narrated milestones (USER: ...)
#   shots/          screenshot PNGs, sorted by HHMMSS prefix
#   pids            background process IDs (used by stop)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILTER_LOOP="$SCRIPT_DIR/_capture_filter_loop.sh"
SHOT_LOOP="$SCRIPT_DIR/_capture_shot_loop.sh"

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//' >&2
    exit 1
}

resolve_dir() {
    local default="/tmp/session-$(date +%Y%m%d-%H%M%S)"
    local dir="$default"
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dir) dir="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    echo "$dir"
}

cmd_start() {
    local dir
    dir=$(resolve_dir "$@")
    mkdir -p "$dir/shots"
    : > "$dir/raw.log"
    : > "$dir/events.log"
    : > "$dir/markers.log"
    : > "$dir/shotloop.err"

    nohup journalctl -u nowplaying-orchestrator -f --since now \
        > "$dir/raw.log" 2>&1 &
    local journal_pid=$!

    nohup "$FILTER_LOOP" "$dir" >/dev/null 2>>"$dir/shotloop.err" &
    local filter_pid=$!

    nohup "$SHOT_LOOP" "$dir" >/dev/null 2>>"$dir/shotloop.err" &
    local shot_pid=$!

    printf "journal_pid=%s\nfilter_pid=%s\nshot_pid=%s\n" \
        "$journal_pid" "$filter_pid" "$shot_pid" > "$dir/pids"

    echo "started"
    echo "dir=$dir"
    echo "journal=$journal_pid filter=$filter_pid shot=$shot_pid"
}

cmd_stop() {
    local dir
    dir=$(resolve_dir "$@")
    [[ -f "$dir/pids" ]] || { echo "no pids file in $dir" >&2; exit 1; }
    # shellcheck disable=SC1090
    source "$dir/pids"
    kill "$journal_pid" "$filter_pid" "$shot_pid" 2>/dev/null || true
    pkill -P "$filter_pid" 2>/dev/null || true
    pkill -P "$shot_pid" 2>/dev/null || true
    sleep 1
    echo "stopped"
    echo "raw.log:    $(wc -l < "$dir/raw.log") lines"
    echo "events.log: $(wc -l < "$dir/events.log") lines"
    echo "markers:    $(wc -l < "$dir/markers.log") entries"
    echo "shots:      $(ls -1 "$dir/shots" | wc -l)"
}

cmd_mark() {
    local text=""
    local dir=""
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dir) dir="$2"; shift 2 ;;
            *) text="$1"; shift ;;
        esac
    done
    [[ -n "$dir" && -n "$text" ]] || { echo "usage: mark \"text\" --dir DIR" >&2; exit 1; }
    local ts
    ts=$(date "+%H:%M:%S")
    printf "%s USER: %s\n" "$ts" "$text" >> "$dir/markers.log"
    echo "$ts marked: $text"
}

cmd_status() {
    local dir
    dir=$(resolve_dir "$@")
    echo "=== last 20 events ==="
    tail -20 "$dir/events.log" 2>/dev/null || echo "(no events yet)"
    echo
    echo "=== markers ==="
    cat "$dir/markers.log" 2>/dev/null || true
    echo
    echo "=== shots ==="
    ls -1 "$dir/shots" 2>/dev/null | wc -l
}

action="${1:-}"
shift || true
case "$action" in
    start)  cmd_start "$@" ;;
    stop)   cmd_stop "$@" ;;
    mark)   cmd_mark "$@" ;;
    status) cmd_status "$@" ;;
    *)      usage ;;
esac

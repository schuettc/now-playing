#!/usr/bin/env bash
# Live monitor for LLM-assist activity on the Pi.
#
# Tails journalctl on the orchestrator and color-codes the lines that
# matter for the four LLM hooks (judge_shazam_result, judge_advance,
# decide_track_change, judge_track_guess) plus the orchestrator context
# (heartbeats, publishes, sonos events) so it's possible to correlate
# a hook firing with the audio state that triggered it.
#
# Usage:
#   pi/scripts/monitor_llm.sh             # remote tail via ssh nowplaying-pi
#   pi/scripts/monitor_llm.sh local       # local tail (when run on the Pi)
#
# Prints a header explaining the color key. Ctrl-C to stop.

set -euo pipefail

mode="${1:-remote}"

# ANSI colors (no-op if NO_COLOR is set or stdout isn't a TTY).
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_HOOK=$'\033[1;35m'    # magenta bold — LLM verdict lines
  C_FAIL=$'\033[1;31m'    # red bold     — LLM call failures
  C_GATE=$'\033[33m'      # yellow       — gating / hold decisions
  C_SHAZ=$'\033[36m'      # cyan         — Shazam-relevance
  C_GUESS=$'\033[32m'     # green        — track-guess
  C_TC=$'\033[1;36m'      # bright cyan  — track-change-llm
  C_ADV=$'\033[1;32m'     # bright green — advance-track
  C_CTX=$'\033[90m'       # grey         — orchestrator context (heartbeat, publish, sonos)
  C_BOOT=$'\033[1;33m'    # yellow bold  — boot lines (features:, post-resubscribe)
  C_RESET=$'\033[0m'
else
  C_HOOK="" C_FAIL="" C_GATE="" C_SHAZ="" C_GUESS=""
  C_TC="" C_ADV="" C_CTX="" C_BOOT="" C_RESET=""
fi

cat <<EOF
${C_BOOT}── monitor_llm — Now Playing LLM-assist live tail ──────────────${C_RESET}
  ${C_BOOT}features:${C_RESET}             boot indicator (look for llm=on)
  ${C_SHAZ}shazam-relevance:${C_RESET}    judge_shazam_result verdicts
  ${C_ADV}advance-track:${C_RESET}        judge_advance verdicts
  ${C_TC}track-change-llm:${C_RESET}     decide_track_change verdicts
  ${C_GUESS}track-guess:${C_RESET}          judge_track_guess verdicts + heuristic fallback
  ${C_FAIL}llm: ... call failed:${C_RESET} API errors / timeouts / parse failures
  ${C_CTX}heartbeat / publish / sonos:${C_RESET} orchestrator context (grey)
  Ctrl-C to stop.
─────────────────────────────────────────────────────────────────
EOF

# All the patterns that matter. We tail journalctl and filter to these so
# the signal-to-noise is high during a listening session.
PATTERN='features: |shazam-relevance:|advance-track:|track-change-llm:|track-guess:|llm: .*call failed|post-resubscribe reconcile|heartbeat |publish: |sonos: state='

stream_cmd=(journalctl -u nowplaying-orchestrator -f --no-pager --output cat)

if [[ "$mode" == "local" ]]; then
  "${stream_cmd[@]}"
else
  ssh nowplaying-pi "${stream_cmd[*]}"
fi | grep --line-buffered -E "$PATTERN" | while IFS= read -r line; do
  case "$line" in
    *"features: "*)              printf '%s%s%s\n' "$C_BOOT"  "$line" "$C_RESET" ;;
    *"call failed"*)             printf '%s%s%s\n' "$C_FAIL"  "$line" "$C_RESET" ;;
    *"shazam-relevance:"*)       printf '%s%s%s\n' "$C_SHAZ"  "$line" "$C_RESET" ;;
    *"track-change-llm:"*)       printf '%s%s%s\n' "$C_TC"    "$line" "$C_RESET" ;;
    *"advance-track:"*)          printf '%s%s%s\n' "$C_ADV"   "$line" "$C_RESET" ;;
    *"track-guess:"*)            printf '%s%s%s\n' "$C_GUESS" "$line" "$C_RESET" ;;
    *"post-resubscribe"*)        printf '%s%s%s\n' "$C_BOOT"  "$line" "$C_RESET" ;;
    *)                           printf '%s%s%s\n' "$C_CTX"   "$line" "$C_RESET" ;;
  esac
done

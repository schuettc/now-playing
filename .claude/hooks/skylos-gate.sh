#!/usr/bin/env bash
set -euo pipefail

# PreToolUse Bash gate for skylos. Mirrors fallow-gate.sh:
#   - Same Bash-command matcher (git commit/push/merge/pull, gh pr merge)
#   - Runs `skylos pi/ --danger --quality --secrets --json` against staged
#     diff vs origin/main and blocks on verdict=fail.
#   - Fails open (exit 0 + stderr notice) when skylos / jq aren't on PATH
#     so contributors without the tooling don't get deadlocked.
#
# Why this exists separately from the pre-commit hook:
#   .git/hooks/pre-commit only fires on `git commit`. PR merges via the
#   GitHub UI or `gh pr merge` bypass it entirely. This hook fires
#   pre-tool-use, so it catches every git/gh action a Claude session
#   takes. Closes the merge-path gap that let the previous Phase 3
#   skylos cleanup regress on D-feature merges.

if ! command -v jq >/dev/null 2>&1; then
  echo "skylos-gate: jq not on PATH, skipping audit." >&2
  exit 0
fi

INPUT="$(cat)"
CMD="$(jq -r '.tool_input.command // empty' <<<"$INPUT")"

# Match the same patterns fallow-gate uses.
if ! printf '%s\n' "$CMD" | grep -Eq '(^|[[:space:];|&()])(git[[:space:]]+(commit|push|merge|pull)|gh[[:space:]]+pr[[:space:]]+merge)([[:space:]]|$)'; then
  exit 0
fi

# Skylos is invoked via uvx (no global install needed). If uvx isn't
# present, fail open.
if ! command -v uvx >/dev/null 2>&1; then
  echo "skylos-gate: uvx not on PATH, skipping audit." >&2
  exit 0
fi

PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
cd "$PROJECT_ROOT"

# Choose base ref. Prefer origin/main; fall back to local main; fall
# back to skipping (nothing to diff against — fresh repo).
BASE_REF=""
if git rev-parse --verify origin/main >/dev/null 2>&1; then
  BASE_REF="origin/main"
elif git rev-parse --verify main >/dev/null 2>&1; then
  BASE_REF="main"
else
  echo "skylos-gate: no base ref (origin/main or main) found, skipping audit." >&2
  exit 0
fi

# Bail early if nothing in pi/ has changed since base — saves the
# whole skylos invocation for the common case of TypeScript-only work.
if git diff --quiet "$BASE_REF" -- pi/ 2>/dev/null; then
  exit 0
fi

TMP_JSON="$(mktemp)"
TMP_ERR="$(mktemp)"
cleanup() { rm -f "$TMP_JSON" "$TMP_ERR"; }
trap cleanup EXIT

if uvx skylos pi --danger --quality --secrets --baseline \
     --diff-base "$BASE_REF" --diff "$BASE_REF" \
     --json -o "$TMP_JSON" >"$TMP_ERR" 2>&1; then
  STATUS=0
else
  STATUS=$?
fi

# Skylos's --json output puts findings in `definitions.danger`, `quality`,
# etc. The gate verdict is: any new HIGH-or-CRITICAL danger finding, OR
# any new HIGH-or-CRITICAL quality finding, blocks.
NEW_DANGER=0
NEW_QUALITY_CRITICAL=0
if [ -s "$TMP_JSON" ]; then
  NEW_DANGER=$(jq -r '
    (.danger // [])
    | map(select(.severity == "HIGH" or .severity == "CRITICAL"))
    | length
  ' "$TMP_JSON" 2>/dev/null || echo 0)
  NEW_QUALITY_CRITICAL=$(jq -r '
    (.quality // [])
    | map(select(.severity == "CRITICAL" or .severity == "HIGH"))
    | length
  ' "$TMP_JSON" 2>/dev/null || echo 0)
fi

if [ "${NEW_DANGER:-0}" -gt 0 ] || [ "${NEW_QUALITY_CRITICAL:-0}" -gt 0 ]; then
  {
    echo "skylos-gate: blocked — $NEW_DANGER danger + $NEW_QUALITY_CRITICAL HIGH/CRITICAL quality findings introduced vs $BASE_REF"
    echo "(full JSON below; fix the findings or use a per-site '# skylos: ignore SKY-XXX' suppression with a WHY comment)"
    echo
    cat "$TMP_JSON"
  } >&2
  exit 2
fi

# Non-zero skylos exit without findings → infrastructure issue, fail
# open with a notice so a transient skylos error doesn't deadlock.
if [ "$STATUS" -ne 0 ] && [ "${NEW_DANGER:-0}" -eq 0 ] && [ "${NEW_QUALITY_CRITICAL:-0}" -eq 0 ]; then
  ERR_LINE="$(sed -n '1p' "$TMP_ERR" 2>/dev/null || true)"
  echo "skylos-gate: skylos exited $STATUS without findings (${ERR_LINE:-no stderr}), skipping." >&2
fi

exit 0

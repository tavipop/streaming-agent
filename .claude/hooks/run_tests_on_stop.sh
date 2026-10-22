#!/usr/bin/env bash
# Stop: before Claude finishes, run the offline test suite if Python files changed.
# Exit 2 sends the failure output back to Claude so it keeps working until tests pass.
set -uo pipefail

payload=$(cat)
# Avoid loops: if we already blocked once this turn, let Claude stop.
if python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1]).get("stop_hook_active") else 1)' "$payload" 2>/dev/null; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Only run if there are uncommitted Python changes (or this isn't a git repo yet).
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git status --porcelain -- '*.py' | grep -q . || exit 0
fi

if ! out=$(uv run --quiet pytest -q -x 2>&1); then
  echo "Unit tests are failing. Fix them before finishing:" >&2
  echo "$out" | tail -40 >&2
  exit 2
fi
exit 0

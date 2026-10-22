#!/usr/bin/env bash
# PostToolUse: auto-fix and format any Python file Claude just edited.
# Reads the hook payload (JSON) on stdin. Never blocks: formatting is best-effort.
set -uo pipefail

file=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)

[[ "$file" == *.py ]] || exit 0
[[ -f "$file" ]] || exit 0

cd "${CLAUDE_PROJECT_DIR:-.}"
uv run --quiet ruff check --fix --quiet "$file" >/dev/null 2>&1
uv run --quiet ruff format --quiet "$file" >/dev/null 2>&1
exit 0

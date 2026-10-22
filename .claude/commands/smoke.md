---
description: Run the live end-to-end streaming test against the real API
allowed-tools: Bash(uv run pytest -m live:*)
---

Run `uv run pytest -m live -v -s`. This calls the real Claude API (costs a little).
If it is skipped, tell me `ANTHROPIC_API_KEY` isn't set. If it fails, diagnose whether the
problem is in `events.py` mapping, `build_options()`, or the environment (CLI missing, auth),
and propose a fix.

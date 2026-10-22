---
description: Run lint, format check, type check and unit tests; fix anything that fails
allowed-tools: Bash(uv run ruff:*), Bash(uv run mypy:*), Bash(uv run pytest:*), Read, Edit
---

Run, in order, and stop to fix problems before moving on:

1. `uv run ruff check --fix .`
2. `uv run ruff format .`
3. `uv run mypy`
4. `uv run pytest -q`

Fix root causes (don't silence with `# type: ignore` or `noqa` unless the cause is an
untyped third-party API, and say so). Finish with a one-line summary per step.

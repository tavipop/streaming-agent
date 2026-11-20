---
description: Run quality gates 1–6 from CLAUDE.md (check-only); `/check fix` auto-fixes lint/format first
argument-hint: [fix]
allowed-tools: Bash(uv lock --check:*), Bash(uv run ruff:*), Bash(uv run mypy:*), Bash(uv run pytest:*), Read
---

Arguments: $ARGUMENTS

## Optional auto-fix (only when the argument is `fix`)
Before the gates, run `uv run ruff check --fix .` then `uv run ruff format .`. This is a development
convenience, not part of the gate verdict. Report which files it changed.

## Gates (always, check-only, in order)
Run every command even if an earlier one fails, so the report shows the true state:

1. Lockfile: `uv lock --check`
2. Format: `uv run ruff format --check .`
3. Lint: `uv run ruff check .`
4. Types: `uv run mypy`
5. Unit tests: `uv run pytest`
6. Coverage: `uv run pytest --cov=streaming_agent --cov-report=term-missing` (≥ 85% overall)

Gates 7 (live) and 8 (QA) are not part of `/check`; see CLAUDE.md.

Rules (CLAUDE.md "Quality gates"): don't add `# noqa` / `# type: ignore`, skip/xfail/delete tests,
lower thresholds, or edit ruff/mypy/pytest config to get green. Report failures with their root cause;
don't silence them.

Finish with a table: gate, PASS/FAIL, one-line note (e.g. test counts, coverage %).

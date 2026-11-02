# streaming-agent

A Python agent built on the **Claude Agent SDK** (`claude-agent-sdk`) with token-level
streaming (`include_partial_messages=True`).

> **Terminology.** "The agent" / "runtime agent" means the product in `src/streaming_agent/`.
> "Developer" and "QA" mean the Claude Code subagents in `.claude/agents/` that build it.
> They never load at runtime because `build_options()` sets `setting_sources=[]`.

## Commands (always via uv)

- Install / sync deps: `uv sync`
- Run the agent REPL: `uv run streaming-agent`
- Run the REST API: `./build.sh run_api` (OpenAPI docs at `/docs`)
- Local tracing UI: `./build.sh phoenix`
- Unit tests (offline): `uv run pytest`
- Live API test: `uv run pytest -m live` (needs `ANTHROPIC_API_KEY`)
- Lint + format (auto-fix, for development): `uv run ruff check --fix . && uv run ruff format .`
- Type check: `uv run mypy`
- Everything: `/check`

## Layout

- `src/streaming_agent/config.py` – `AgentConfig`, env-driven (`AGENT_MODEL`, `AGENT_MAX_TURNS`, …).
  `AGENT_MAX_TURNS` (default 20, positive int) caps agent-loop turns per user message.
- `src/streaming_agent/agent.py` – `StreamingAgent` wraps `ClaudeSDKClient`; `build_options()` is the single place SDK options are set
- `src/streaming_agent/events.py` – pure mapping: raw SDK messages → `AgentEvent` (`TextDelta`, `ToolCallStarted`, `ToolCallFinished`, `TurnFinished`)
- `src/streaming_agent/tools.py` – custom tools (`@tool`) served via an in-process MCP server
- `src/streaming_agent/observability.py` – OpenTelemetry tracing: `TurnTracer` builds turn / `chat` /
  `execute_tool` spans from the SDK message stream; `setup_tracing()` picks the exporter from env
  (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `AGENT_TRACE_CONSOLE=1`); `AGENT_TRACE_CONTENT=1` records content.
- `src/streaming_agent/cli.py` – terminal REPL; only renders `AgentEvent`s
- `src/streaming_agent/server.py` – FastAPI REST front end:
  `POST /sessions`, `POST /sessions/{id}/messages` (JSON `ChatResponse` envelope with `message`/`error`/
  `error_source`/`stop_reason`, or SSE with `Accept: text/event-stream`), `DELETE /sessions/{id}`. Auth via `X-API-Key`
  against `AGENT_API_KEYS` (unset = off). Also `AGENT_API_HOST/PORT`, `AGENT_CORS_ORIGINS`.
- `src/streaming_agent/sessions.py` – `SessionManager`: one `StreamingAgent` (= one CLI process) per session;
  `AGENT_MAX_SESSIONS`, `AGENT_SESSION_TTL` (idle reaper), `AGENT_TURN_TIMEOUT`. Failed turns discard the session.
- `tests/` – offline unit tests; `test_agent_live.py` is marked `live`
- `.claude/agents/` – `developer.md`, `qa.md` (development workflow, see below)

## Conventions

- Python 3.11+, full type hints, `mypy --strict` must pass.
- Keep streaming logic in `events.py` pure (no I/O) so it's testable with fake messages.
  Front ends consume `AgentEvent`s and never touch raw SDK types.
- New tool: add an `@tool` function in `tools.py`, append to `TOOLS`, add a unit test calling
  `tool.handler(...)`. It is auto-allowed as `mcp__agent_tools__<name>`. Use `/add-tool`.
- New SDK option: change `build_options()` only, and cover it in `tests/test_config.py`.
- New env setting: add it to `AgentConfig` (not ad-hoc `os.environ` reads elsewhere), give it a safe
  default, test the default and an override, and list it in the Layout section above.
- REST contract: don't change routes, status codes, or the `ChatResponse` / SSE shape without explicit
  user approval. Additive fields are OK; mention them in the summary.
- Observability: new behaviour that spans a turn or tool call should appear in `TurnTracer` spans.
  Never record prompt/response content unless `AGENT_TRACE_CONTENT=1`.
- Unit tests must not hit the network. Anything needing the API gets `@pytest.mark.live`.
- Tests: one behaviour per test, descriptive names, fake SDK messages instead of real clients,
  no real sleeps (inject clocks / timeouts).
- No dead code, commented-out code, or stray `print()` outside `cli.py`.
- Never read, print or commit `.env` or API keys.

## Quality gates (Definition of Done)

A change is done only when every gate passes. Gates use **check-only** commands (no `--fix`),
so a gate reports the true state. `/check` must run this same set.

| # | Gate              | Command                                                        | Pass criteria |
|---|-------------------|----------------------------------------------------------------|---------------|
| 1 | Lockfile          | `uv lock --check`                                              | Lockfile in sync with `pyproject.toml` |
| 2 | Format            | `uv run ruff format --check .`                                 | No files to reformat |
| 3 | Lint              | `uv run ruff check .`                                          | Zero findings |
| 4 | Types             | `uv run mypy`                                                  | Zero errors (`--strict`) |
| 5 | Unit tests        | `uv run pytest`                                                | 100% pass, offline, no `live` tests selected |
| 6 | Coverage          | `uv run pytest --cov=streaming_agent --cov-report=term-missing` | ≥ 85% overall, no drop; new/changed code covered <!-- TODO: confirm threshold --> |
| 7 | Live test         | `uv run pytest -m live`                                        | Pass, **only when** the change touches `agent.py`, `build_options()`, `events.py`, or `tools.py` **and** `ANTHROPIC_API_KEY` is already set in the environment. Otherwise report "not run" with the reason. |
| 8 | Functional QA     | `qa` subagent                                                  | Verdict = PASS |

Never skip, `xfail`, or delete tests, add `# noqa` / `# type: ignore`, lower thresholds, or edit ruff/mypy/pytest
config to get green without explicit user approval. If a gate already fails on `main` without your change, report it
rather than working around it.

## Development workflow (developer → QA)

Two subagents live in `.claude/agents/`:

- **developer** evaluates the requirement, returns clarification questions, implements with tests, and runs every quality gate.
- **qa** independently verifies each acceptance criterion against real behaviour. It never edits `src/` or `tests/`.

The main session orchestrates:

1. Start every feature or bug fix by delegating to **developer** with the full requirement text.
2. Subagents can't talk to the user. When developer returns `CLARIFICATIONS NEEDED`, ask the user
   those questions (AskUserQuestion when the options are clear), then resume developer with the answers.
   Never answer on the user's behalf.
3. Invoke **qa** only after developer reports `READY_FOR_QA` with gates 1–7 green (or 7 justified as not run).
4. Give qa the original requirement, the agreed clarifications, and developer's change list, not developer's
   opinion of correctness.
5. On QA `FAIL`, pass the defect list to developer verbatim and repeat. After 3 failed rounds, stop and report to the user.
6. Finish with: decisions made, files changed, gate table, QA verdict, open risks.

Shortcut: `/feature <requirement>` runs this loop.

### Requirement format (recommended)

```markdown
## Story
As a <role>, I want <capability>, so that <benefit>.

## Acceptance criteria
- AC1: Given <context>, when <action>, then <outcome>.

## Out of scope
- ...
```

If acceptance criteria are missing, developer derives them and gets them confirmed before implementing.

## Guardrails

- Don't commit or push unless the user asks. <!-- TODO: branch / commit message convention -->
- Stay in the requirement's scope; note unrelated issues instead of fixing them.
- Don't add dependencies without saying why in the summary (`uv add` / `uv add --dev`, never hand-edit the lockfile).

## Gotchas

- `include_partial_messages=True` yields `StreamEvent`s with *raw* API events
  (`content_block_delta` / `text_delta`, `content_block_start` / `tool_use`, …) **in addition to**
  full `AssistantMessage`s — don't render text twice.
- `receive_response()` ends after the `ResultMessage` for the current turn; use it per turn.
- Hitting `max_turns` yields `ResultMessage(subtype="error_max_turns", is_error=True)`. It is mapped to
  `TurnFinished.max_turns_reached` and treated as a graceful stop: REST 200 with `stop_reason="max_turns"`,
  session kept, turn span not ERROR (unless a model error also occurred).
- `setting_sources=[]` keeps the runtime agent from loading *this repo's* `.claude/` config
  (including the developer/QA subagents). Don't remove it.
- The SDK drives the Claude Code CLI under the hood; it must be installed (`claude --version`).
- Each session is a separate CLI process: tests for `SessionManager` must use fakes, never spawn real agents.
- When unsure about an SDK API, check the installed package
  (`uv run python -c "import claude_agent_sdk, inspect; ..."`) rather than guessing.

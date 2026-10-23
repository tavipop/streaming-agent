# streaming-agent

A Python agent built on the **Claude Agent SDK** (`claude-agent-sdk`) with token-level
streaming (`include_partial_messages=True`).

## Commands (always via uv)

- Install / sync deps: `uv sync`
- Run the agent REPL: `uv run streaming-agent`
- Unit tests (offline): `uv run pytest`
- Live API test: `uv run pytest -m live` (needs `ANTHROPIC_API_KEY`)
- Lint + format: `uv run ruff check --fix . && uv run ruff format .`
- Type check: `uv run mypy`
- Everything: `/check`

## Layout

- `src/streaming_agent/config.py` – `AgentConfig`, env-driven (`AGENT_MODEL`, `AGENT_MAX_TURNS`, …)
- `src/streaming_agent/agent.py` – `StreamingAgent` wraps `ClaudeSDKClient`; `build_options()` is the single place SDK options are set
- `src/streaming_agent/events.py` – pure mapping: raw SDK messages → `AgentEvent` (`TextDelta`, `ToolCallStarted`, `ToolCallFinished`, `TurnFinished`)
- `src/streaming_agent/tools.py` – custom tools (`@tool`) served via an in-process MCP server
- `src/streaming_agent/observability.py` – OpenTelemetry tracing: `TurnTracer` builds turn / `chat` /
  `execute_tool` spans from the SDK message stream; `setup_tracing()` picks the exporter from env
  (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `AGENT_TRACE_CONSOLE=1`); `AGENT_TRACE_CONTENT=1` records content.
  Local UI: `./build.sh phoenix`.
- `src/streaming_agent/cli.py` – terminal REPL; only renders `AgentEvent`s
- `src/streaming_agent/server.py` – FastAPI REST front end (`./build.sh run_api`, docs at `/docs`):
  `POST /sessions`, `POST /sessions/{id}/messages` (JSON `ChatResponse` envelope with `message`/`error`/
  `error_source`, or SSE with `Accept: text/event-stream`), `DELETE /sessions/{id}`. Auth via `X-API-Key`
  against `AGENT_API_KEYS` (unset = off). Also `AGENT_API_HOST/PORT`, `AGENT_CORS_ORIGINS`.
- `src/streaming_agent/sessions.py` – `SessionManager`: one `StreamingAgent` (= one CLI process) per session;
  `AGENT_MAX_SESSIONS`, `AGENT_SESSION_TTL` (idle reaper), `AGENT_TURN_TIMEOUT`. Failed turns discard the session.
- `tests/` – offline unit tests; `test_agent_live.py` is marked `live`

## Conventions

- Python 3.11+, full type hints, `mypy --strict` must pass.
- Keep streaming logic in `events.py` pure (no I/O) so it's testable with fake messages.
  Front ends consume `AgentEvent`s and never touch raw SDK types.
- New tool: add an `@tool` function in `tools.py`, append to `TOOLS`, add a unit test calling
  `tool.handler(...)`. It is auto-allowed as `mcp__agent_tools__<name>`. Use `/add-tool`.
- New SDK option: change `build_options()` only, and cover it in `tests/test_config.py`.
- Unit tests must not hit the network. Anything needing the API gets `@pytest.mark.live`.
- Never read, print or commit `.env` or API keys.

## Gotchas

- `include_partial_messages=True` yields `StreamEvent`s with *raw* API events
  (`content_block_delta` / `text_delta`, `content_block_start` / `tool_use`, …) **in addition to**
  full `AssistantMessage`s — don't render text twice.
- `receive_response()` ends after the `ResultMessage` for the current turn; use it per turn.
- `setting_sources=[]` keeps the runtime agent from loading *this repo's* `.claude/` config.
- The SDK drives the Claude Code CLI under the hood; it must be installed (`claude --version`).
- When unsure about an SDK API, check the installed package
  (`uv run python -c "import claude_agent_sdk, inspect; ..."`) rather than guessing.

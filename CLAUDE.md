# streaming-agent

A Python agent built on the **Claude Agent SDK** (`claude-agent-sdk`) with token-level
streaming (`include_partial_messages=True`).

> **Terminology.** "The agent" / "runtime agent" means the product in `src/streaming_agent/`.
> "Developer", "reviewer" and "QA" mean the Claude Code subagents in `.claude/agents/` that build it.
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
- Gates 1–6, check-only: `/check` (`/check fix` auto-fixes lint/format first)

## Layout

- `src/streaming_agent/config.py` – `AgentConfig`, env-driven (`AGENT_MODEL`, `AGENT_MAX_TURNS`, …).
  `AGENT_MAX_TURNS` (default 20, positive int) caps agent-loop turns per user message.
- `src/streaming_agent/agent.py` – `StreamingAgent` wraps `ClaudeSDKClient`; `build_options()` is the single place SDK options are set
- `src/streaming_agent/events.py` – pure mapping: raw SDK messages → `AgentEvent` (`TextDelta`, `ToolCallStarted`, `ToolCallFinished`, `TurnFinished`)
- `src/streaming_agent/tools.py` – custom tools (`@tool`) served via an in-process MCP server;
  `enabled_tools(config)` adds the graph tools `graph_schema` / `graph_query` only when `NEO4J_URI` is set
- `src/streaming_agent/graph.py` – Neo4j retail-graph RAG (no SDK types): `GraphClient` wraps
  `langchain_neo4j.Neo4jGraph` (lazy connect on first use, one per process, READ session), read-only
  Cypher validation, row capping, system-prompt guidance, span attributes. Settings: `NEO4J_URI`
  (unset = graph off), `NEO4J_USERNAME` (default `neo4j`), `NEO4J_PASSWORD`, `NEO4J_DATABASE`
  (default `neo4j`), `AGENT_GRAPH_QUERY_TIMEOUT` (default 10 s), `AGENT_GRAPH_MAX_ROWS` (default 50).
  The DB must already exist, hold the data and have APOC (needed for the schema).
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
- `.claude/agents/` – `developer.md`, `reviewer.md`, `qa.md` (development workflow, see below), plus
  `sdk-reviewer.md` (SDK-specific review) and `test-writer.md`
- `.claude/commands/` – `/feature` (full workflow), `/open-pr` (commit, push, open/update PR), `/check` (gates 1–6), …
- `.github/` – `workflows/ci.yml` (gate 1–6 commands on every PR and push to `main`, see Quality gates),
  `workflows/claude-code-review.yml` (Claude review on PRs), `workflows/claude.yml` (`@claude` mentions),
  `pull_request_template.md`

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

CI (`.github/workflows/ci.yml`) runs the gate 1–5 commands and the gate 6 command with `--cov-fail-under=85` on every
PR and push to `main`. CI enforces only the 85% floor. The rest of gate 6 ("no drop", "new/changed code covered") is
checked locally: developer reports coverage against the baseline, and reviewer checks that report. Gates 7–8 are local only.

Never skip, `xfail`, or delete tests, add `# noqa` / `# type: ignore`, lower thresholds, or edit ruff/mypy/pytest
config to get green without explicit user approval. If a gate already fails on `main` without your change, report it
rather than working around it.

## Development workflow (developer → review → QA → PR)

Subagents in `.claude/agents/`:

- **developer** evaluates the requirement, returns clarification questions, implements with tests, and runs every quality gate.
- **reviewer** does a read-only code review of the diff against the requirement and these conventions
  (`APPROVE` / `CHANGES_REQUESTED`). **sdk-reviewer** adds an SDK-specific review when the change touches
  `agent.py`, `build_options()`, `events.py` or `tools.py`.
- **qa** independently verifies each acceptance criterion against real behaviour. It never edits `src/` or `tests/`.

The main session orchestrates:

1. Check the working tree. On `main` with changes, ask the user first. On this requirement's own feature branch,
   resume: its changes are in-progress work, and developer is told so. On any other branch (clean or not), ask the
   user whether to switch to `main` and branch from there or to continue on the current branch.
2. Delegate to **developer** with the full requirement text, Phases 1–2 only (analysis and plan, no file edits).
   The plan-only reply lists the ACs, files to change, tests per AC, live-gate applicability, risks, and the
   proposed Conventional Commit type/scope and branch slug.
3. Subagents can't talk to the user. When developer returns `CLARIFICATIONS NEEDED`, ask the user
   those questions (AskUserQuestion when the options are clear), then resume developer with the answers.
   Never answer on the user's behalf.
4. Once nothing blocking is open and before any file is changed, create the feature branch if on `main`
   (see "Pull requests"), using the slug and type from the plan. Then resume developer to implement.
5. After developer reports `READY_FOR_QA` with gates 1–7 green (or 7 justified as not run), invoke **reviewer**
   (and **sdk-reviewer** when applicable) with the requirement, clarifications, developer's change list and gate table.
   Review passes only if reviewer returns `APPROVE` **and** sdk-reviewer (when run) reports no findings.
   sdk-reviewer has no verdict, and its checklist only covers correctness, SDK misuse, tool safety and missing
   offline tests, so every sdk-reviewer finding is blocking. Pass blocking findings to developer verbatim and
   repeat. Developer may rebut a finding with evidence; the reviewer re-checks it.
6. Invoke **qa** only after review passes. Give qa the original requirement, the agreed clarifications, and
   developer's change list, not developer's or reviewers' opinion of correctness.
7. On QA `FAIL`, pass the defect list to developer verbatim, then repeat review and QA. Review and QA rejections share
   one cap: after 3 failed rounds in total, stop and report to the user. No PR is opened.
8. When QA is `PASS` and gates 1–8 are green (gate 7 may be NOT RUN with a justified reason), run `/open-pr` (see "Pull requests").
9. Finish with: decisions made, files changed, gate table, review verdict, QA verdict, PR URL, open risks.

Shortcut: `/feature <requirement>` runs this loop.

## Pull requests

`/open-pr` is the only route by which Claude commits, pushes and opens PRs. It runs as the last `/feature` step,
or standalone when the user asks.

- **Precondition**: every gate green (gate 7 may be NOT RUN with a justified reason), review passed (workflow
  step 5), QA `PASS`, evidenced by the `/feature` summary, or standalone by a fresh `/check` plus the user's
  explicit review/QA outcome. Otherwise no commit, push or PR.
- **Branch**: `<type>/<kebab-slug>`, plus the ticket id when there is one (`feat/item-3-graph-row-cap`).
  `<type>` is one of `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `ci`. Never commit or push to `main`
  directly (`.claude/hooks/block_push_to_main.py` blocks pushes to protected branches).
- **Commits**: [Conventional Commits](https://www.conventionalcommits.org/), e.g. `feat(graph): cap query rows`.
  The subject is imperative and ≤ 72 chars; the body says why; then the attribution trailer. Stage explicit paths only
  (never `git add -A`), never `.env*`, `tmp/` or untracked files unrelated to the change.
- **Attribution**: if the session gives attribution lines for commits and PRs (system instructions), use those
  verbatim. They take precedence because they name the exact model. Otherwise use these defaults:
  commit trailer `Co-Authored-By: Claude <noreply@anthropic.com>`; PR body footer
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **Confirmation**: before anything is written, show the user the mode (create PR / update PR / local commit only
  when `gh` or `origin` is unavailable), branch, commit message, file list, and PR title/body, and ask once.
  If the user declines, change nothing.
- **PR**: ready for review, base `main`, title = commit subject, body from `.github/pull_request_template.md`
  with every section filled in (summary, AC → tests, clarifications, changes, quality gates 1–8, review verdict,
  QA verdict, decisions & risks, manual verification), ending with the attribution footer. Label by type (`feat` → `enhancement`, `fix` → `bug`, `docs` → `documentation`), assignee `@me`,
  no reviewers requested. If an open PR exists for the branch, push and update its body/comment instead of
  creating a duplicate.
- **Failures**: if `gh` isn't authenticated, `origin` is missing, or push/PR creation fails, stop without
  retrying, keep the local branch and commit, and report the exact manual commands.
- **After opening**: CI (gate 1–6 commands, 85% coverage floor) and the Claude Code Review workflow run on the PR. Merging is the user's call.
- **Recommended repo setting** (not managed from here): protect `main` with the required status check
  `quality-gates` (GitHub matches it by the CI job name; it is shown as "CI / quality-gates" in the PR UI) and at
  least one approving review.

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

- Don't commit, push or open PRs unless the user asks or confirms the `/open-pr` step; follow "Pull requests"
  for branch and commit conventions.
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

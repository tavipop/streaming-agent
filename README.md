# streaming-agent

A Python agent on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/streaming-output)
that streams its output token by token, with custom tools and a Claude Code harness.

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) and the Claude Code CLI (`npm i -g @anthropic-ai/claude-code`),
which the SDK drives under the hood.

```bash
uv sync
cp .env.example .env        # add ANTHROPIC_API_KEY
chmod +x .claude/hooks/*.sh
git init && git add -A && git commit -m "Initial scaffold"
```

## Use

```bash
uv run streaming-agent      # interactive streaming REPL
uv run pytest               # offline unit tests
uv run pytest -m live       # real API end-to-end test
```

In code:

```python
from streaming_agent import StreamingAgent
from streaming_agent.events import TextDelta

async with StreamingAgent() as agent:
    async for event in agent.send("What time is it in UTC?"):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
```

## Working on it with Claude Code

Run `claude` in the project root. The harness gives it:

| Piece | What it does |
|---|---|
| `CLAUDE.md` | Project map, commands, conventions, SDK gotchas |
| `.claude/settings.json` | Pre-approved uv/pytest/ruff/mypy commands; `.env` blocked; live API runs & commits ask first |
| PostToolUse hook | `ruff --fix` + `ruff format` on every Python file Claude edits |
| Stop hook | Runs unit tests before Claude finishes; failures send it back to fix them |
| `/check` | Lint, format, types, tests — and fix |
| `/add-tool <name — purpose>` | Adds a custom tool + tests following project conventions |
| `/smoke` | Live end-to-end streaming test |
| `sdk-reviewer` subagent | Reviews diffs for streaming bugs, SDK misuse, unsafe tool permissions |

"""Interactive terminal REPL that prints the agent's output as it streams."""

from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from streaming_agent.agent import StreamingAgent
from streaming_agent.config import AgentConfig
from streaming_agent.events import (
    AgentEvent,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
)
from streaming_agent.observability import setup_tracing, tracing_target

DIM, RESET = "\033[2m", "\033[0m"


def render(event: AgentEvent) -> None:
    match event:
        case TextDelta(text):
            sys.stdout.write(text)
        case ToolCallStarted(name):
            sys.stdout.write(f"\n{DIM}→ calling {name}…{RESET}")
        case ToolCallFinished(name, args):
            sys.stdout.write(f"\n{DIM}✓ {name}({args}){RESET}\n")
        case TurnFinished(is_error, turns, cost, _):
            status = "error" if is_error else "done"
            cost_s = f", ${cost:.4f}" if cost is not None else ""
            sys.stdout.write(f"\n{DIM}[{status} · {turns} turn(s){cost_s}]{RESET}\n")
    sys.stdout.flush()


async def repl() -> None:
    config = AgentConfig.from_env()
    print(f"Streaming agent · model={config.model} · Ctrl-D to quit\n")
    async with StreamingAgent(config) as agent:
        while True:
            try:
                prompt = await asyncio.to_thread(input, "you> ")
            except EOFError:
                print()
                return
            if not prompt.strip():
                continue
            sys.stdout.write("agent> ")
            async for event in agent.send(prompt):
                render(event)


def main() -> None:
    load_dotenv()  # so OTEL_* / AGENT_TRACE_* from .env are visible to setup_tracing
    provider = setup_tracing()
    if provider is not None:
        print(f"{DIM}tracing → {tracing_target()}{RESET}")
    try:
        asyncio.run(repl())
    except KeyboardInterrupt:
        print()
    finally:
        if provider is not None:
            provider.shutdown()  # flush buffered spans


if __name__ == "__main__":
    main()

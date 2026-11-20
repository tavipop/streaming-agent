"""End-to-end test against the real API. Run with:  uv run pytest -m live"""

import os
from dataclasses import replace

import pytest

from streaming_agent import AgentConfig, StreamingAgent
from streaming_agent.events import TextDelta, TurnFinished

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


async def test_streams_text_and_finishes() -> None:
    async with StreamingAgent(AgentConfig.from_env()) as agent:
        events = [e async for e in agent.send("Use the add tool to compute 2 + 2.")]

    assert any(isinstance(e, TextDelta) for e in events), "expected streamed text deltas"
    assert isinstance(events[-1], TurnFinished)
    assert not events[-1].is_error


async def test_max_turns_reached_is_flagged() -> None:
    config = replace(AgentConfig.from_env(), max_turns=1)
    async with StreamingAgent(config) as agent:
        prompt = "Use the add tool to compute 2 + 2, then add 3 to the result."
        events = [e async for e in agent.send(prompt)]

    assert isinstance(events[-1], TurnFinished)
    assert events[-1].max_turns_reached


async def test_session_continues_after_max_turns() -> None:
    config = replace(AgentConfig.from_env(), max_turns=1)
    async with StreamingAgent(config) as agent:
        prompt = "Use the add tool to compute 2 + 2, then add 3 to the result."
        first = [e async for e in agent.send(prompt)]
        second = [e async for e in agent.send("Reply with just the word OK. Do not use tools.")]

    assert isinstance(first[-1], TurnFinished) and first[-1].max_turns_reached
    assert isinstance(second[-1], TurnFinished)
    assert not second[-1].is_error
    assert not second[-1].max_turns_reached

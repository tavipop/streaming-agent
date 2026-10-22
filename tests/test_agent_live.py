"""End-to-end test against the real API. Run with:  uv run pytest -m live"""

import os

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

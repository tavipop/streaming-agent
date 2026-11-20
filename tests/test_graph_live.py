"""Retail-graph RAG against the real API and an existing Neo4j. Run with:  uv run pytest -m live

Needs ANTHROPIC_API_KEY and NEO4J_URI (plus NEO4J_USERNAME/PASSWORD/DATABASE as required)
pointing at a populated database with APOC. Nothing is written to the database.
"""

import os
import re

import pytest

from streaming_agent import AgentConfig, StreamingAgent, graph, tools
from streaming_agent.events import AgentEvent, TextDelta, ToolCallFinished, TurnFinished

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
    pytest.mark.skipif(not os.getenv("NEO4J_URI"), reason="NEO4J_URI not set"),
]

GRAPH_QUERY = "mcp__agent_tools__graph_query"


async def _ask(prompt: str) -> list[AgentEvent]:
    async with StreamingAgent(AgentConfig.from_env()) as agent:
        return [e async for e in agent.send(prompt)]


def _answer(events: list[AgentEvent]) -> str:
    final = events[-1]
    assert isinstance(final, TurnFinished) and not final.is_error
    return final.result or "".join(e.text for e in events if isinstance(e, TextDelta))


def _tool_calls(events: list[AgentEvent]) -> list[str]:
    return [e.name for e in events if isinstance(e, ToolCallFinished)]


async def test_schema_lists_customer() -> None:
    settings = tools.graph_settings(AgentConfig.from_env())
    assert settings is not None
    print("Settings: ", settings)
    result = await graph.GraphClient(settings).schema()
    print(result)
    assert result is not None
    assert "Customer" in result["schema"]
    for prop in ("id", "first_name", "last_name", "email", "country", "age", "sex"):
        assert prop in result["schema"], prop


async def test_count_question_is_answered_from_the_graph() -> None:
    settings = tools.graph_settings(AgentConfig.from_env())
    assert settings is not None
    counted = await graph.GraphClient(settings).run(
        "MATCH (c:Customer) WHERE c.country = 'RO' RETURN count(c) AS n"
    )
    expected = counted["rows"][0]["n"]

    events = await _ask("How many customers are from RO? Answer with the number.")

    assert GRAPH_QUERY in _tool_calls(events)
    assert str(expected) in re.findall(r"\d+", _answer(events))


async def test_question_outside_the_schema_is_explained() -> None:
    events = await _ask("Which products did customers order last week?")
    answer = _answer(events).lower()
    assert any(word in answer for word in ("order", "product")), answer


async def test_unrelated_question_does_not_query_the_graph() -> None:
    events = await _ask("What is the capital of France? Reply in one word.")
    assert GRAPH_QUERY not in _tool_calls(events)
    assert "paris" in _answer(events).lower()

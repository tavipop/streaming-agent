import json
from typing import Any

import pytest

from graph_fakes import SCHEMA, FakeGraph, fake_client
from streaming_agent import news, tools
from streaming_agent.config import AgentConfig


async def test_add() -> None:
    result = await tools.add.handler({"a": 2, "b": 3.5})
    assert result["content"][0]["text"] == "5.5"


async def test_current_time_is_iso() -> None:
    result = await tools.current_time.handler({})
    assert "T" in result["content"][0]["text"]


def test_allowed_tool_names_are_namespaced() -> None:
    names = tools.allowed_tool_names(tools.TOOLS)
    assert f"mcp__{tools.SERVER_NAME}__add" in names
    assert len(names) == len(tools.TOOLS)


async def test_search_geopolitical_news_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    article = {
        "title": "t",
        "url": "https://example.com",
        "domain": "example.com",
        "source_country": "",
        "published": "2026-10-20T00:00:00Z",
    }

    async def fake_fetch(query: str, timespan: str, max_results: int) -> list[dict[str, str]]:
        assert (query, timespan, max_results) == ("sanctions", "7d", 20)
        return [article]

    monkeypatch.setattr(news, "fetch_articles", fake_fetch)
    result = await tools.search_geopolitical_news.handler({"query": "sanctions"})
    assert "is_error" not in result
    assert json.loads(result["content"][0]["text"]) == [article]


async def test_search_geopolitical_news_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_fetch(*args: object, **kwargs: object) -> None:
        raise news.NewsSearchError("rate limited")

    monkeypatch.setattr(news, "fetch_articles", failing_fetch)
    result = await tools.search_geopolitical_news.handler({"query": "sanctions"})
    assert result["is_error"] is True
    assert "rate limited" in result["content"][0]["text"]


async def test_validate_news_events_ok_and_error() -> None:
    event = {
        "event_date": "2026-10-20",
        "event_summary": "s",
        "details": "d",
        "references": ["https://example.com"],
    }
    ok = await tools.validate_news_events.handler({"events": [event]})
    assert json.loads(ok["content"][0]["text"]) == [event]

    bad = await tools.validate_news_events.handler({"events": [{**event, "references": []}]})
    assert bad["is_error"] is True


def _graph_tools(backend: FakeGraph) -> dict[str, Any]:
    return {t.name: t for t in tools.graph_tools(fake_client(backend))}


def test_graph_tools_disabled_without_neo4j_uri() -> None:
    assert tools.enabled_tools(AgentConfig()) == tools.TOOLS


def test_graph_tools_enabled_with_neo4j_uri() -> None:
    enabled = tools.enabled_tools(AgentConfig(neo4j_uri="bolt://graph:7687"))
    names = tools.allowed_tool_names(enabled)
    assert names[: len(tools.TOOLS)] == tools.allowed_tool_names(tools.TOOLS)
    assert names[len(tools.TOOLS) :] == [
        "mcp__agent_tools__graph_schema",
        "mcp__agent_tools__graph_query",
    ]


async def test_graph_schema_tool_returns_schema() -> None:
    result = await _graph_tools(FakeGraph())["graph_schema"].handler({})
    assert "is_error" not in result
    assert json.loads(result["content"][0]["text"])["schema"] == SCHEMA


async def test_graph_query_tool_returns_rows_as_json() -> None:
    backend = FakeGraph(rows=[{"email": "ana@example.com"}])
    result = await _graph_tools(backend)["graph_query"].handler(
        {"cypher": "MATCH (c:Customer) RETURN c.email AS email LIMIT 1"}
    )
    assert "is_error" not in result
    assert json.loads(result["content"][0]["text"])["rows"] == [{"email": "ana@example.com"}]


async def test_graph_query_tool_flags_rejected_query_as_error() -> None:
    result = await _graph_tools(FakeGraph())["graph_query"].handler(
        {"cypher": "CREATE (c:Customer)"}
    )
    assert result["is_error"] is True
    assert "read-only" in json.loads(result["content"][0]["text"])["error"]


async def test_graph_query_tool_requires_string_cypher() -> None:
    result = await _graph_tools(FakeGraph())["graph_query"].handler({"cypher": 42})
    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "cypher must be a string"


async def test_graph_query_tool_turns_unexpected_errors_into_results() -> None:
    backend = FakeGraph(error=RuntimeError("Cannot perform operations - connection closed"))
    result = await _graph_tools(backend)["graph_query"].handler(
        {"cypher": "MATCH (c:Customer) RETURN c LIMIT 1"}
    )
    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error_class"] == "RuntimeError"

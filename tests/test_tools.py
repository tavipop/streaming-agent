import json

import pytest

from streaming_agent import news, tools


async def test_add() -> None:
    result = await tools.add.handler({"a": 2, "b": 3.5})
    assert result["content"][0]["text"] == "5.5"


async def test_current_time_is_iso() -> None:
    result = await tools.current_time.handler({})
    assert "T" in result["content"][0]["text"]


def test_allowed_tool_names_are_namespaced() -> None:
    names = tools.allowed_tool_names()
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

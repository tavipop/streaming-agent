from typing import Any

import httpx
import pytest

from streaming_agent import news

SAMPLE = {
    "articles": [
        {
            "url": "https://example.com/a",
            "title": "Ceasefire talks resume",
            "seendate": "20261020T123000Z",
            "domain": "example.com",
            "sourcecountry": "France",
        },
        {"title": "no url, dropped"},
    ]
}

VALID_EVENT: dict[str, Any] = {
    "event_date": "2026-10-20",
    "event_summary": "Ceasefire talks resume.",
    "details": "Delegations met in Geneva.",
    "references": ["https://example.com/a"],
}


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_parse_gdelt_maps_fields_and_dates() -> None:
    articles = news.parse_gdelt(SAMPLE)
    assert articles == [
        {
            "title": "Ceasefire talks resume",
            "url": "https://example.com/a",
            "domain": "example.com",
            "source_country": "France",
            "published": "2026-10-20T12:30:00Z",
        }
    ]


def test_parse_gdelt_empty() -> None:
    assert news.parse_gdelt({}) == []


async def test_fetch_articles_sends_params_and_parses() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        return httpx.Response(200, json=SAMPLE)

    async with _client(handler) as client:
        articles = await news.fetch_articles("ceasefire", "3d", 500, client=client)
    assert len(articles) == 1
    assert seen["query"] == "ceasefire"
    assert seen["timespan"] == "3d"
    assert seen["maxrecords"] == str(news.MAX_RECORDS)
    assert seen["format"] == "json"


async def test_fetch_articles_empty_body_means_no_results() -> None:
    async with _client(lambda r: httpx.Response(200, text="")) as client:
        assert await news.fetch_articles("ceasefire", client=client) == []


async def test_fetch_articles_plain_text_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="The specified phrase is too short.")

    async with _client(handler) as client:
        with pytest.raises(news.NewsSearchError, match="too short"):
            await news.fetch_articles("ab", client=client)


async def test_fetch_articles_http_error() -> None:
    async with _client(lambda r: httpx.Response(500)) as client:
        with pytest.raises(news.NewsSearchError, match="HTTP 500"):
            await news.fetch_articles("ceasefire", client=client)


async def test_fetch_articles_rejects_bad_timespan() -> None:
    with pytest.raises(news.NewsSearchError, match="timespan"):
        await news.fetch_articles("ceasefire", timespan="yesterday")


def test_validate_events_ok() -> None:
    assert news.validate_events([VALID_EVENT]) == [VALID_EVENT]


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"event_date": "20 Oct 2026"}, "event_date"),
        ({"event_summary": "  "}, "event_summary"),
        ({"references": []}, "references"),
        ({"references": ["not a url"]}, "non-URL"),
        ({"extra": 1}, "unexpected fields"),
    ],
)
def test_validate_events_rejects(patch: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        news.validate_events([{**VALID_EVENT, **patch}])


def test_validate_events_missing_field() -> None:
    event = {k: v for k, v in VALID_EVENT.items() if k != "details"}
    with pytest.raises(ValueError, match="missing fields"):
        news.validate_events([event])


def test_validate_events_not_a_list() -> None:
    with pytest.raises(ValueError, match="list"):
        news.validate_events({"event_date": "2026-10-20"})

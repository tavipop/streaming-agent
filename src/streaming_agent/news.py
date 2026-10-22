"""Geopolitical news search via the GDELT DOC 2.0 API, plus validation of the event schema.

No SDK types here: tools.py wraps these functions as agent tools.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, TypedDict

import httpx

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_RECORDS = 75
TIMEOUT_SECONDS = 15.0
_TIMESPAN_RE = re.compile(r"^\d+(min|h|d|w|m)$")
EVENT_FIELDS = ("event_date", "event_summary", "details", "references")


class Article(TypedDict):
    title: str
    url: str
    domain: str
    source_country: str
    published: str


class NewsEvent(TypedDict):
    event_date: str
    event_summary: str
    details: str
    references: list[str]


class NewsSearchError(Exception):
    """The news search failed (network, HTTP status or a GDELT error message)."""


def _iso_seendate(raw: str) -> str:
    """GDELT `20261020T123000Z` -> `2026-10-20T12:30:00Z`; unknown formats pass through."""
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return raw


def parse_gdelt(payload: dict[str, Any]) -> list[Article]:
    articles = payload.get("articles") or []
    return [
        Article(
            title=str(a.get("title", "")),
            url=str(a.get("url", "")),
            domain=str(a.get("domain", "")),
            source_country=str(a.get("sourcecountry", "")),
            published=_iso_seendate(str(a.get("seendate", ""))),
        )
        for a in articles
        if a.get("url")
    ]


async def fetch_articles(
    query: str,
    timespan: str = "7d",
    max_results: int = 20,
    *,
    client: httpx.AsyncClient | None = None,
) -> list[Article]:
    if not query.strip():
        raise NewsSearchError("query must not be empty")
    if not _TIMESPAN_RE.match(timespan):
        raise NewsSearchError(f"invalid timespan {timespan!r}; use e.g. '24h', '3d', '1w'")
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": str(max(1, min(max_results, MAX_RECORDS))),
        "timespan": timespan,
        "sort": "datedesc",
    }
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await http.get(GDELT_URL, params=params)
    except httpx.HTTPError as exc:
        raise NewsSearchError(f"request to GDELT failed: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        raise NewsSearchError(f"GDELT returned HTTP {response.status_code}")
    if not response.text.strip():
        return []  # GDELT answers an empty body when nothing matches
    try:
        payload = response.json()
    except ValueError as exc:
        # GDELT reports bad queries and rate limits as plain text.
        raise NewsSearchError(f"GDELT error: {response.text.strip()[:300]}") from exc
    if not isinstance(payload, dict):
        raise NewsSearchError("unexpected GDELT response shape")
    return parse_gdelt(payload)


def validate_events(events: object) -> list[NewsEvent]:
    """Check the agent's event list against the output schema; raise ValueError if invalid."""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    result: list[NewsEvent] = []
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise ValueError(f"events[{i}] must be an object")
        missing = [f for f in EVENT_FIELDS if f not in ev]
        extra = sorted(set(ev) - set(EVENT_FIELDS))
        if missing or extra:
            raise ValueError(f"events[{i}]: missing fields {missing}, unexpected fields {extra}")
        event_date = ev["event_date"]
        try:
            date.fromisoformat(event_date)
        except (TypeError, ValueError):
            raise ValueError(f"events[{i}].event_date must be YYYY-MM-DD") from None
        if not isinstance(ev["event_summary"], str) or not ev["event_summary"].strip():
            raise ValueError(f"events[{i}].event_summary must be a non-empty string")
        if not isinstance(ev["details"], str):
            raise ValueError(f"events[{i}].details must be a string")
        refs = ev["references"]
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"events[{i}].references must be a non-empty list of URLs")
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith(("http://", "https://")):
                raise ValueError(f"events[{i}].references contains a non-URL: {ref!r}")
        result.append(
            NewsEvent(
                event_date=event_date,
                event_summary=ev["event_summary"].strip(),
                details=ev["details"].strip(),
                references=list(refs),
            )
        )
    return result

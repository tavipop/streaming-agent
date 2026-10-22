"""Custom tools exposed to the agent through an in-process MCP server.

To add a tool: write an async function decorated with @tool, append it to TOOLS.
Its permission name becomes  mcp__<SERVER_NAME>__<tool name>  and is auto-allowed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from streaming_agent import news

SERVER_NAME = "agent_tools"


@tool("current_time", "Return the current UTC date and time in ISO 8601 format.", {})
async def current_time(args: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return {"content": [{"type": "text", "text": now}]}


@tool("add", "Add two numbers and return the sum.", {"a": float, "b": float})
async def add(args: dict[str, Any]) -> dict[str, Any]:
    total = float(args["a"]) + float(args["b"])
    return {"content": [{"type": "text", "text": str(total)}]}


def _text(text: str, *, is_error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


@tool(
    "search_geopolitical_news",
    "Search recent worldwide news articles (GDELT) about geopolitics: conflicts, diplomacy, "
    "elections, sanctions, treaties. Returns a JSON list of articles with title, url, domain, "
    "source_country and published (ISO UTC). Use specific multi-word queries, e.g. "
    "'sanctions Iran' or '(Ukraine OR Russia) ceasefire'.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "GDELT search query (keywords, OR, quotes)"},
            "timespan": {
                "type": "string",
                "description": "How far back to search, e.g. '24h', '3d', '1w'. Default '7d'.",
            },
            "max_results": {
                "type": "integer",
                "description": "Max articles to return (1-75). Default 20.",
            },
        },
        "required": ["query"],
    },
)
async def search_geopolitical_news(args: dict[str, Any]) -> dict[str, Any]:
    try:
        articles = await news.fetch_articles(
            str(args["query"]),
            timespan=str(args.get("timespan", "7d")),
            max_results=int(args.get("max_results", 20)),
        )
    except (news.NewsSearchError, KeyError, TypeError, ValueError) as exc:
        return _text(f"News search failed: {exc}", is_error=True)
    return _text(json.dumps(articles, ensure_ascii=False))


@tool(
    "validate_news_events",
    "Validate a list of geopolitical news events before giving it as the final answer. Each "
    "event must have exactly: event_date (YYYY-MM-DD), event_summary (one sentence), details "
    "(a few sentences) and references (non-empty list of source URLs). Returns the normalized "
    "JSON on success, or an error describing what to fix.",
    {
        "type": "object",
        "properties": {"events": {"type": "array", "items": {"type": "object"}}},
        "required": ["events"],
    },
)
async def validate_news_events(args: dict[str, Any]) -> dict[str, Any]:
    try:
        events = news.validate_events(args.get("events"))
    except ValueError as exc:
        return _text(f"Invalid events: {exc}", is_error=True)
    return _text(json.dumps(events, ensure_ascii=False, indent=2))


TOOLS: list[SdkMcpTool[Any]] = [current_time, add, search_geopolitical_news, validate_news_events]


def build_server() -> Any:
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=TOOLS)


def allowed_tool_names() -> list[str]:
    return [f"mcp__{SERVER_NAME}__{t.name}" for t in TOOLS]

"""Custom tools exposed to the agent through an in-process MCP server.

To add a tool: write an async function decorated with @tool, append it to TOOLS.
Its permission name becomes  mcp__<SERVER_NAME>__<tool name>  and is auto-allowed.
The Neo4j graph tools are added by enabled_tools() only when NEO4J_URI is configured.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server, tool

from streaming_agent import graph, news
from streaming_agent.config import AgentConfig

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


def _json_result(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return _text(text, is_error="error" in payload)


def graph_tools(client: graph.GraphClient) -> list[SdkMcpTool[Any]]:
    """The retail-graph RAG tools, bound to one GraphClient."""

    @tool(
        "graph_schema",
        "Return the schema of the retail Neo4j graph (node labels, properties with types, "
        "relationships) as JSON {schema, duration_ms}. Call it before writing Cypher.",
        {},
    )
    async def graph_schema(args: dict[str, Any]) -> dict[str, Any]:
        return _json_result(await client.schema())

    @tool(
        "graph_query",
        "Run ONE read-only Cypher query against the retail Neo4j graph. Use only labels and "
        "properties from graph_schema and always add a LIMIT. Returns JSON {row_count, "
        "returned, truncated, duration_ms, rows}, or {error, error_class} to fix and retry.",
        {
            "type": "object",
            "properties": {"cypher": {"type": "string", "description": "Read-only Cypher"}},
            "required": ["cypher"],
        },
    )
    async def graph_query(args: dict[str, Any]) -> dict[str, Any]:
        return _json_result(await client.run(args.get("cypher")))

    return [graph_schema, graph_query]


def graph_settings(config: AgentConfig) -> graph.GraphSettings | None:
    if config.neo4j_uri is None:
        return None
    return graph.GraphSettings(
        uri=config.neo4j_uri,
        username=config.neo4j_username,
        password=config.neo4j_password,
        database=config.neo4j_database,
        timeout_s=config.graph_query_timeout_s,
        max_rows=config.graph_max_rows,
    )


def enabled_tools(config: AgentConfig) -> list[SdkMcpTool[Any]]:
    settings = graph_settings(config)
    if settings is None:
        return list(TOOLS)
    return [*TOOLS, *graph_tools(graph.shared_client(settings))]


def build_server(enabled: list[SdkMcpTool[Any]]) -> Any:
    return create_sdk_mcp_server(name=SERVER_NAME, version="0.1.0", tools=enabled)


def allowed_tool_names(enabled: list[SdkMcpTool[Any]]) -> list[str]:
    return [f"mcp__{SERVER_NAME}__{t.name}" for t in enabled]

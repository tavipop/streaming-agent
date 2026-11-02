"""Configuration, loaded from environment variables (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import cast, get_args

from claude_agent_sdk import PermissionMode
from dotenv import load_dotenv

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, concise assistant. Use the tools available to you when they help "
    "answer the user's question.\n\n"
    "When asked for geopolitical news: call search_geopolitical_news (several focused queries "
    "if useful), group the articles into distinct events, then call validate_news_events and "
    "fix any errors it reports. Reply with ONLY the validated JSON array of objects with "
    "event_date, event_summary, details and references - no prose, no code fences."
)


@dataclass(frozen=True)
class AgentConfig:
    model: str = "claude-sonnet-5-5"
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_turns: int = 20
    # Built-in Claude Code tools the agent may use without asking. Custom tools from
    # tools.py are added automatically. Keep this list tight; widen it deliberately.
    allowed_tools: list[str] = field(default_factory=lambda: ["Read", "Glob", "Grep"])
    permission_mode: PermissionMode = "default"
    # Record prompts, model output and tool I/O in traces (metadata only when False).
    trace_content: bool = False
    # REST API (server.py): session lifetime/limits, per-turn timeout and accepted API keys
    # (empty = auth off, local dev only).
    session_ttl_s: float = 1800.0
    max_sessions: int = 20
    turn_timeout_s: float = 300.0
    api_keys: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls) -> AgentConfig:
        load_dotenv()
        defaults = cls()
        mode = os.getenv("AGENT_PERMISSION_MODE", defaults.permission_mode)
        valid = get_args(PermissionMode)
        if mode not in valid:
            raise ValueError(f"AGENT_PERMISSION_MODE must be one of {valid}, got {mode!r}")
        return cls(
            model=os.getenv("AGENT_MODEL", defaults.model),
            system_prompt=os.getenv("AGENT_SYSTEM_PROMPT", defaults.system_prompt),
            max_turns=_positive_int("AGENT_MAX_TURNS", defaults.max_turns),
            permission_mode=cast(PermissionMode, mode),
            trace_content=os.getenv("AGENT_TRACE_CONTENT", "0") == "1",
            session_ttl_s=float(os.getenv("AGENT_SESSION_TTL", str(defaults.session_ttl_s))),
            max_sessions=int(os.getenv("AGENT_MAX_SESSIONS", str(defaults.max_sessions))),
            turn_timeout_s=float(os.getenv("AGENT_TURN_TIMEOUT", str(defaults.turn_timeout_s))),
            api_keys=frozenset(
                k.strip() for k in os.getenv("AGENT_API_KEYS", "").split(",") if k.strip()
            ),
        )


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise ValueError(f"{name} must be a positive integer, got {raw!r}")
    return value

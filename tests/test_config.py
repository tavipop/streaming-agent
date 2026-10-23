import pytest

from streaming_agent.agent import build_options
from streaming_agent.config import AgentConfig


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("AGENT_MAX_TURNS", "3")
    cfg = AgentConfig.from_env()
    assert cfg.model == "claude-haiku-5-5"
    assert cfg.max_turns == 3


def test_options_have_streaming_enabled() -> None:
    opts = build_options(AgentConfig())
    assert opts.include_partial_messages is True
    assert any(t.startswith("mcp__") for t in opts.allowed_tools)


def test_news_tools_are_allowed() -> None:
    opts = build_options(AgentConfig())
    assert "mcp__agent_tools__search_geopolitical_news" in opts.allowed_tools
    assert "mcp__agent_tools__validate_news_events" in opts.allowed_tools


def test_trace_content_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_TRACE_CONTENT", raising=False)
    assert AgentConfig.from_env().trace_content is False
    monkeypatch.setenv("AGENT_TRACE_CONTENT", "1")
    assert AgentConfig.from_env().trace_content is True


def test_api_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_API_KEYS", raising=False)
    assert AgentConfig.from_env().api_keys == frozenset()
    monkeypatch.setenv("AGENT_API_KEYS", " k1, k2 ,,")
    monkeypatch.setenv("AGENT_MAX_SESSIONS", "3")
    monkeypatch.setenv("AGENT_TURN_TIMEOUT", "12.5")
    monkeypatch.setenv("AGENT_SESSION_TTL", "60")
    cfg = AgentConfig.from_env()
    assert cfg.api_keys == frozenset({"k1", "k2"})
    assert (cfg.max_sessions, cfg.turn_timeout_s, cfg.session_ttl_s) == (3, 12.5, 60.0)

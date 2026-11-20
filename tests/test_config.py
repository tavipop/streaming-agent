import pytest

from streaming_agent import tools
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


def test_max_turns_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("streaming_agent.config.load_dotenv", lambda: False)  # ignore local .env
    monkeypatch.delenv("AGENT_MAX_TURNS", raising=False)
    assert AgentConfig.from_env().max_turns == 20


def test_max_turns_is_passed_to_sdk() -> None:
    assert build_options(AgentConfig(max_turns=5)).max_turns == 5


@pytest.mark.parametrize("value", ["abc", "0", "-1", "2.5", ""])
def test_invalid_max_turns_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("AGENT_MAX_TURNS", value)
    with pytest.raises(ValueError, match=rf"AGENT_MAX_TURNS.*{value!r}"):
        AgentConfig.from_env()


GRAPH_VARS = (
    "NEO4J_URI",
    "NEO4J_USERNAME",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "AGENT_GRAPH_QUERY_TIMEOUT",
    "AGENT_GRAPH_MAX_ROWS",
)


@pytest.fixture
def no_graph_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr("streaming_agent.config.load_dotenv", lambda: False)  # ignore local .env
    for var in GRAPH_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_graph_settings_defaults(no_graph_env: pytest.MonkeyPatch) -> None:
    cfg = AgentConfig.from_env()
    assert cfg.neo4j_uri is None
    assert cfg.graph_enabled is False
    assert (cfg.neo4j_username, cfg.neo4j_password, cfg.neo4j_database) == ("neo4j", "", "neo4j")
    assert (cfg.graph_query_timeout_s, cfg.graph_max_rows) == (10.0, 50)


def test_graph_settings_from_env(no_graph_env: pytest.MonkeyPatch) -> None:
    no_graph_env.setenv("NEO4J_URI", "neo4j+s://graph.example.com")
    no_graph_env.setenv("NEO4J_USERNAME", "reader")
    no_graph_env.setenv("NEO4J_PASSWORD", "s3cret")
    no_graph_env.setenv("NEO4J_DATABASE", "retail")
    no_graph_env.setenv("AGENT_GRAPH_QUERY_TIMEOUT", "2.5")
    no_graph_env.setenv("AGENT_GRAPH_MAX_ROWS", "7")
    cfg = AgentConfig.from_env()
    assert cfg.graph_enabled is True
    assert (cfg.neo4j_uri, cfg.neo4j_username, cfg.neo4j_password, cfg.neo4j_database) == (
        "neo4j+s://graph.example.com",
        "reader",
        "s3cret",
        "retail",
    )
    assert (cfg.graph_query_timeout_s, cfg.graph_max_rows) == (2.5, 7)


def test_blank_neo4j_uri_disables_graph(no_graph_env: pytest.MonkeyPatch) -> None:
    no_graph_env.setenv("NEO4J_URI", "  ")
    assert AgentConfig.from_env().graph_enabled is False


@pytest.mark.parametrize("value", ["abc", "0", "-1", "nan", "inf", ""])
def test_invalid_graph_query_timeout_is_rejected(
    no_graph_env: pytest.MonkeyPatch, value: str
) -> None:
    no_graph_env.setenv("AGENT_GRAPH_QUERY_TIMEOUT", value)
    with pytest.raises(ValueError, match=rf"AGENT_GRAPH_QUERY_TIMEOUT.*{value!r}"):
        AgentConfig.from_env()


@pytest.mark.parametrize("value", ["abc", "0", "-1", "2.5", ""])
def test_invalid_graph_max_rows_is_rejected(no_graph_env: pytest.MonkeyPatch, value: str) -> None:
    no_graph_env.setenv("AGENT_GRAPH_MAX_ROWS", value)
    with pytest.raises(ValueError, match=rf"AGENT_GRAPH_MAX_ROWS.*{value!r}"):
        AgentConfig.from_env()


def test_neo4j_password_not_in_repr() -> None:
    assert "s3cret" not in repr(AgentConfig(neo4j_password="s3cret"))


def test_options_without_graph_are_unchanged() -> None:
    cfg = AgentConfig()
    opts = build_options(cfg)
    assert opts.system_prompt == cfg.system_prompt
    assert opts.allowed_tools == [*cfg.allowed_tools, *tools.allowed_tool_names(tools.TOOLS)]
    assert list(opts.mcp_servers) == [tools.SERVER_NAME]


def test_options_with_graph_add_tools_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_connect(**kwargs: object) -> None:
        raise AssertionError("must connect lazily, not while building options")

    monkeypatch.setattr("langchain_neo4j.Neo4jGraph", no_connect)
    cfg = AgentConfig(neo4j_uri="bolt://graph:7687", graph_max_rows=30)
    opts = build_options(cfg)
    assert "mcp__agent_tools__graph_schema" in opts.allowed_tools
    assert "mcp__agent_tools__graph_query" in opts.allowed_tools
    assert isinstance(opts.system_prompt, str)
    assert opts.system_prompt.startswith(cfg.system_prompt)
    assert "LIMIT (at most 30)" in opts.system_prompt

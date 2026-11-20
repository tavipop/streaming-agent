import json
from typing import Any

import pytest
from neo4j import READ_ACCESS
from neo4j.exceptions import AuthError, CypherSyntaxError, ServiceUnavailable

from graph_fakes import SCHEMA, SETTINGS, Connector, FakeGraph, fake_client
from streaming_agent import graph
from streaming_agent.graph import GraphQueryRejected

_client = fake_client


# ---- validate_read_only --------------------------------------------------------------


def test_read_query_is_accepted_and_trailing_semicolon_stripped() -> None:
    cypher = "MATCH (c:Customer) WHERE c.country = 'RO' RETURN count(c) AS n LIMIT 1;"
    assert graph.validate_read_only(f"  {cypher}  ") == cypher.rstrip(";")


@pytest.mark.parametrize(
    "cypher",
    [
        "CREATE (c:Customer {id: '9'})",
        "MERGE (c:Customer {id: '9'}) RETURN c",
        "MATCH (c:Customer) DETACH DELETE c",
        "MATCH (c:Customer) DELETE c",
        "MATCH (c:Customer) SET c.age = 1",
        "MATCH (c:Customer) REMOVE c.email",
        "DROP INDEX customer_id",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "MATCH (c) FOREACH (x IN [1] | SET c.n = x)",
        "match (c:Customer) set c.age = 1",
        "GRANT ROLE admin TO bob",
        "USE system MATCH (n) RETURN n",
        "CALL apoc.create.node(['X'], {}) YIELD node RETURN node",
        "CALL dbms.security.createUser('x', 'y')",
        "CALL apoc.cypher.run('CREATE (n)', {}) YIELD value RETURN value",
        "MATCH (c) CALL { WITH c RETURN c AS d } IN TRANSACTIONS RETURN d",
        # D1: backtick-quoted procedure names
        "CALL `apoc`.create.node(['X'], {}) YIELD node RETURN node",
        "CALL `apoc`.`create`.`node`(['X'], {}) YIELD node RETURN node",
        "CALL `dbms`.security.createUser('a', 'b', false)",
        "CALL `apoc.create.node`(['X'], {}) YIELD node RETURN node",
        "CALL apoc . create . node(['X'], {}) YIELD node RETURN node",
        "MATCH (c) CALL `db`.`labels`() YIELD label CREATE (x) RETURN label",
        # D2: GQL INSERT
        "INSERT (n:Customer {id: 'x'})",
        "MATCH (a) insert (a)-[:R]->(b)",
        # D3: admin commands, concurrent batching, writes hidden in function calls
        "STOP DATABASE neo4j",
        "START DATABASE neo4j",
        "ENABLE SERVER 'abc'",
        "SHOW USERS",
        "SHOW TRANSACTIONS",
        "SHOW SETTINGS",
        "EXPLAIN SHOW USERS",
        "CYPHER 5 MATCH (n) RETURN n",
        "(n) RETURN n",
        "MATCH (c) CALL { WITH c RETURN c AS d } IN CONCURRENT TRANSACTIONS RETURN d",
        "MATCH (c) CALL { WITH c RETURN c AS d } IN 4 CONCURRENT TRANSACTIONS RETURN d",
        "RETURN apoc.cypher.runFirstColumnSingle('CREATE (n) RETURN n', {})",
        "RETURN `apoc`.`cypher`.`runFirstColumnSingle`('CREATE (n) RETURN n', {})",
        "MATCH (c) RETURN apoc.periodic.submit('x', 'CREATE (n)')",
        # D6: a carriage return ends a // comment
        "MATCH (n) // note\rSET n.a = 1 RETURN n",
        "MATCH (n) // note\r\nDETACH DELETE n",
        # D7: keyword right after a digit
        "MATCH (n) WITH n LIMIT 1SET n.a = 1 RETURN n",
        "MATCH (n) WITH n LIMIT 1CREATE (m) RETURN m",
        "MATCH (n) WITH n LIMIT 1CALL apoc.create.node(['X'], {}) YIELD node RETURN node",
        "MATCH (n) WITH n LIMIT 1AS SET n.a = 1",
        # D9: the alias exemption can't be abused
        "MATCH (n) WITH n AS AS SET AS.a = 1 RETURN 1",
        "MATCH (n) WITH n AS `AS` SET AS.a = 1 RETURN 1",
        "MATCH (n) WHERE n.AS SET n.a = 1",
        "MATCH (n) WITH n AS x SET x.a = 1",
        # one quoted dotted name is still checked as a function / procedure
        "RETURN `apoc.cypher.runFirstColumnSingle`('CREATE (n) RETURN n', {})",
        "CALL `apoc.create.node`(['X'], {}) YIELD node RETURN node",
    ],
)
def test_write_and_admin_queries_are_rejected(cypher: str) -> None:
    with pytest.raises(GraphQueryRejected, match="read-only"):
        graph.validate_read_only(cypher)


def test_multiple_statements_are_rejected() -> None:
    with pytest.raises(GraphQueryRejected, match="single statement"):
        graph.validate_read_only("MATCH (n) RETURN n; MATCH (m) RETURN m")


def test_empty_query_is_rejected() -> None:
    with pytest.raises(GraphQueryRejected, match="empty"):
        graph.validate_read_only("  ;  ")


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (c:Customer) WHERE c.first_name = 'Create; Delete' RETURN c LIMIT 5",
        'MATCH (c:Customer) WHERE c.last_name = "Merge" RETURN c LIMIT 5',
        "MATCH (c:Customer) // set nothing, delete nothing\nRETURN c.id LIMIT 5",
        "MATCH (c:Customer) /* CREATE */ RETURN c.`set` AS s LIMIT 5",
        "MATCH (c:Customer) RETURN c.set, c.created LIMIT 5",
        "CALL db.labels() YIELD label RETURN label",
        "CALL db.schema.visualization()",
        "MATCH (c:Customer) CALL { WITH c RETURN c.age AS a } RETURN a LIMIT 5",
        "CALL `db`.`labels`() YIELD label RETURN label",
        "OPTIONAL MATCH (c:Customer) RETURN count(c) AS n",
        "WITH 'RO' AS code MATCH (c:Customer {country: code}) RETURN c.email LIMIT 5",
        "UNWIND ['RO', 'DE'] AS code RETURN code",
        "EXPLAIN MATCH (c:Customer) RETURN c LIMIT 1",
        "profile MATCH (c:Customer) RETURN c LIMIT 1",
        "MATCH (c:Customer) RETURN c.id UNION MATCH (c:Customer) RETURN c.id",
        "MATCH (c:Customer) RETURN toLower(c.email), date.truncate('month', date()) LIMIT 5",
        "MATCH (c:Customer) RETURN apoc.text.capitalize(c.first_name) LIMIT 5",
        "MATCH (c:`Customer`) WHERE c.age IN [30, 40] RETURN c LIMIT 5",
        # D8: leading comments
        "// customers from Romania\nMATCH (c:Customer {country: 'RO'}) RETURN count(c) AS n",
        "/* RO count */ MATCH (c:Customer {country: 'RO'}) RETURN count(c) AS n",
        "// note\r\nMATCH (c:Customer) RETURN c LIMIT 1",
        # D9: keyword aliases
        "MATCH (c:Customer) RETURN count(*) AS transactions",
        "MATCH (c:Customer) RETURN c.first_name AS `set`, c.last_name AS load LIMIT 5",
        "CALL db.labels() YIELD label AS merge RETURN count(*) AS n",
    ],
)
def test_keywords_in_strings_comments_and_properties_are_allowed(cypher: str) -> None:
    assert graph.validate_read_only(cypher) == cypher


# ---- result shaping / tracing helpers ------------------------------------------------


def test_shape_rows_truncates_to_max_rows() -> None:
    rows = [{"n": 1}, {"n": 2}, {"n": 3}]
    assert graph.shape_rows(rows, 2) == {
        "row_count": 3,
        "returned": 2,
        "truncated": True,
        "rows": [{"n": 1}, {"n": 2}],
    }


def test_shape_rows_not_truncated_within_limit() -> None:
    shaped = graph.shape_rows([{"n": 1}], 2)
    assert (shaped["row_count"], shaped["truncated"]) == (1, False)


def test_result_attributes_for_success() -> None:
    content = json.dumps({**graph.shape_rows([{"email": "a@b.c"}], 5), "duration_ms": 4.0})
    assert graph.result_attributes(content) == {
        "db.system.name": "neo4j",
        "db.response.returned_rows": 1,
        "agent.graph.duration_ms": 4.0,
    }


def test_result_attributes_for_error_from_content_blocks() -> None:
    content = [{"type": "text", "text": json.dumps(graph.error_result("x", "CypherSyntaxError"))}]
    attrs = graph.result_attributes(content)
    assert attrs["error.type"] == "CypherSyntaxError"
    assert "db.response.returned_rows" not in attrs


@pytest.mark.parametrize("content", ["not json", "[1, 2]", None])
def test_result_attributes_ignore_non_object_results(content: Any) -> None:
    assert graph.result_attributes(content) == {}


def test_prompt_guidance_mentions_tools_limit_and_no_invention() -> None:
    text = graph.prompt_guidance(25)
    assert "graph_schema" in text and "graph_query" in text
    assert "LIMIT (at most 25)" in text
    assert "never invent data" in text
    assert "ISO-3166 alpha-2" in text


def test_settings_repr_hides_password() -> None:
    assert "s3cret" not in repr(SETTINGS)


# ---- GraphClient ---------------------------------------------------------------------


async def test_query_runs_in_read_session_and_returns_rows() -> None:
    backend = FakeGraph(rows=[{"n": 3}])
    result = await _client(backend).run("MATCH (c:Customer) RETURN count(c) AS n")
    assert result == {
        "row_count": 1,
        "returned": 1,
        "truncated": False,
        "duration_ms": 250.0,
        "rows": [{"n": 3}],
    }
    assert backend.calls == [
        ("MATCH (c:Customer) RETURN count(c) AS n", {"default_access_mode": READ_ACCESS})
    ]


async def test_query_rows_are_capped_at_max_rows() -> None:
    backend = FakeGraph(rows=[{"n": i} for i in range(5)])
    result = await _client(backend).run("MATCH (c:Customer) RETURN c.id AS n")
    assert (result["row_count"], result["returned"], result["truncated"]) == (5, 2, True)
    assert len(result["rows"]) == 2


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (c:Customer) DETACH DELETE c",
        "CALL `apoc`.create.node(['X'], {}) YIELD node RETURN node",
        "INSERT (n:Customer {id: 'x'})",
        "STOP DATABASE neo4j",
    ],
)
async def test_rejected_query_never_reaches_the_database(cypher: str) -> None:
    backend = FakeGraph()
    connector = Connector(backend)
    result = await _client(backend, connector).run(cypher)
    assert result["error_class"] == "GraphQueryRejected"
    assert "read-only" in result["error"]
    assert backend.calls == []
    assert connector.calls == 0


async def test_cypher_error_is_returned_for_correction() -> None:
    backend = FakeGraph(error=CypherSyntaxError("Invalid input 'RETRN'"))
    result = await _client(backend).run("MATCH (c) RETRN c")
    assert result == {
        "error": "Invalid input 'RETRN'",
        "error_class": "CypherSyntaxError",
        "duration_ms": 250.0,
    }


@pytest.mark.parametrize("error", [ServiceUnavailable("down"), AuthError("bad credentials")])
async def test_lost_connection_reports_unavailable(error: Exception) -> None:
    result = await _client(FakeGraph(error=error)).run("MATCH (c) RETURN c LIMIT 1")
    assert result == {
        "error": "graph database unavailable",
        "error_class": type(error).__name__,
        "duration_ms": 250.0,
    }


async def test_connect_failure_reports_unavailable_without_details() -> None:
    backend = FakeGraph()
    client = _client(backend, Connector(backend, failures=1))
    result = await client.run("MATCH (c) RETURN c LIMIT 1")
    assert (result["error"], result["error_class"]) == ("graph database unavailable", "ValueError")
    assert "s3cret" not in json.dumps(result)


async def test_connect_is_retried_after_a_failure() -> None:
    backend = FakeGraph(rows=[{"n": 1}])
    connector = Connector(backend, failures=1)
    client = _client(backend, connector)
    await client.run("MATCH (c) RETURN c LIMIT 1")
    result = await client.run("MATCH (c) RETURN c LIMIT 1")
    assert result["rows"] == [{"n": 1}]
    assert connector.calls == 2


async def test_connects_lazily_and_only_once() -> None:
    backend = FakeGraph()
    connector = Connector(backend)
    client = _client(backend, connector)
    assert connector.calls == 0
    await client.schema()
    await client.run("MATCH (c) RETURN c LIMIT 1")
    assert connector.calls == 1


async def test_schema_is_returned_with_duration() -> None:
    result = await _client(FakeGraph()).schema()
    assert result == {"schema": SCHEMA, "duration_ms": 250.0}


async def test_schema_reports_unavailable() -> None:
    backend = FakeGraph()
    result = await _client(backend, Connector(backend, failures=1)).schema()
    assert result == {
        "error": "graph database unavailable",
        "error_class": "ValueError",
        "duration_ms": 250.0,
    }


def test_connect_neo4j_passes_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeNeo4jGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("langchain_neo4j.Neo4jGraph", FakeNeo4jGraph)
    assert isinstance(graph.connect_neo4j(SETTINGS), FakeNeo4jGraph)
    assert captured == {
        "url": "bolt://graph:7687",
        "username": "neo4j",
        "password": "s3cret",
        "database": "retail",
        "timeout": 7.5,
        "sanitize": True,
    }


def test_shared_client_is_reused_per_settings() -> None:
    assert graph.shared_client(SETTINGS) is graph.shared_client(SETTINGS)


async def test_rejected_query_reports_duration() -> None:
    result = await _client(FakeGraph()).run("CREATE (c:Customer)")
    assert result["duration_ms"] == 250.0


async def test_non_string_cypher_is_rejected() -> None:
    result = await _client(FakeGraph()).run(42)
    assert (result["error"], result["error_class"]) == (
        "cypher must be a string",
        "GraphQueryRejected",
    )


async def test_unexpected_error_becomes_error_result() -> None:
    error = RuntimeError("Cannot perform operations - Neo4j connection has been closed")
    result = await _client(FakeGraph(error=error)).run("MATCH (c) RETURN c LIMIT 1")
    assert result == {
        "error": "graph request failed unexpectedly",
        "error_class": "RuntimeError",
        "duration_ms": 250.0,
    }


async def test_unexpected_error_reconnects_on_next_call() -> None:
    backend = FakeGraph(error=RuntimeError("closed"))
    connector = Connector(backend)
    client = _client(backend, connector)
    await client.run("MATCH (c) RETURN c LIMIT 1")
    backend.error = None
    result = await client.run("MATCH (c) RETURN c LIMIT 1")
    assert "error" not in result
    assert connector.calls == 2


def test_keyword_alias_used_outside_as_is_still_rejected() -> None:
    # Conservative: only the `AS <keyword>` definition is exempt, not later bare uses.
    with pytest.raises(GraphQueryRejected, match="MERGE"):
        graph.validate_read_only("MATCH (c) WITH c AS merge RETURN merge")


async def test_carriage_return_comment_write_never_reaches_the_database() -> None:
    backend = FakeGraph()
    connector = Connector(backend)
    result = await _client(backend, connector).run("MATCH (n) // note\rSET n.a = 1 RETURN n")
    assert result["error_class"] == "GraphQueryRejected"
    assert (backend.calls, connector.calls) == ([], 0)

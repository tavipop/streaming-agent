"""Shared fakes for the Neo4j graph tests (no network, no real driver)."""

import itertools
from typing import Any

from streaming_agent.graph import GraphClient, GraphSettings

SETTINGS = GraphSettings(
    uri="bolt://graph:7687",
    username="neo4j",
    password="s3cret",
    database="retail",
    timeout_s=7.5,
    max_rows=2,
)
CUSTOMER_PROPERTIES = ("id", "first_name", "last_name", "email", "country", "age", "sex")
SCHEMA = (
    "Node properties:\nCustomer {id: STRING, first_name: STRING, last_name: STRING, "
    "email: STRING, country: STRING, age: INTEGER, sex: STRING}"
)


class FakeGraph:
    """Stands in for langchain_neo4j.Neo4jGraph; records queries, returns rows or raises."""

    def __init__(
        self, rows: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    @property
    def get_schema(self) -> str:
        return SCHEMA

    def query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append((query, session_params))
        if self.error is not None:
            raise self.error
        return self.rows


class Connector:
    """Counts connects; fails the first `failures` attempts."""

    def __init__(self, backend: FakeGraph, failures: int = 0) -> None:
        self.backend = backend
        self.failures = failures
        self.calls = 0

    def __call__(self, settings: GraphSettings) -> FakeGraph:
        self.calls += 1
        if self.calls <= self.failures:
            raise ValueError("Could not connect to bolt://neo4j:s3cret@graph:7687")
        return self.backend


def fake_client(
    backend: FakeGraph, connector: Connector | None = None, settings: GraphSettings = SETTINGS
) -> GraphClient:
    """Client whose clock advances 0.25 s per reading, so every call takes 250 ms."""
    ticks = itertools.count(start=1.0, step=0.25)
    return GraphClient(settings, connect=connector or Connector(backend), clock=lambda: next(ticks))

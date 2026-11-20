"""Neo4j retail graph access for RAG: schema, read-only Cypher validation and result shaping.

No SDK types here: tools.py wraps GraphClient as the `graph_schema` / `graph_query` agent tools.
The runtime agent writes the Cypher itself from the schema; this module only guards and runs it.

Every tool result is a JSON object with `duration_ms`. Successful queries add `row_count`,
`returned`, `truncated` and `rows`; failures add `error` and `error_class`.
observability.py reads the metadata fields (never the rows) via `result_attributes()`.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from neo4j import READ_ACCESS
from neo4j.exceptions import AuthError, DriverError, Neo4jError

TOOL_NAMES = frozenset({"graph_schema", "graph_query"})
UNAVAILABLE_MESSAGE = "graph database unavailable"
UNEXPECTED_MESSAGE = "graph request failed unexpectedly"

# Read-only Cypher guard (the READ access-mode session is the second layer):
#  1. strings and comments (`//` ends at \r or \n) are blanked, backtick names unquoted
#     (`a b` -> a_b, dots kept), surrounding whitespace stripped;
#  2. the statement must start with a read clause (allow-list), optionally after EXPLAIN/PROFILE,
#     which rules out admin commands (SHOW, START/STOP DATABASE, ENABLE SERVER, ...);
#  3. write clauses and batching keywords are rejected anywhere, except as `.property`,
#     `:Label`, `$param` or an `AS alias`;
#  4. procedures (CALL x.y) and namespaced functions (x.y(...)) must be on allow-lists.
_READ_CLAUSES = frozenset({"MATCH", "OPTIONAL", "WITH", "UNWIND", "RETURN", "CALL"})
_FORBIDDEN = (
    "CREATE",
    "INSERT",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "DROP",
    "FOREACH",
    "LOAD",
    "GRANT",
    "DENY",
    "REVOKE",
    "ALTER",
    "RENAME",
    "TERMINATE",
    "USE",
    "TRANSACTIONS",
)
# A word starts here unless a letter/underscore precedes it. Digits don't count: identifiers
# can't start with one, so `LIMIT 1SET` may lex as `1` `SET` and is treated as a keyword.
_WORD_START = r"(?<![^\W\d])"
_IDENT = r"[^\W\d]\w*"
_FORBIDDEN_RE = re.compile(
    _WORD_START + r"(?<![.:$])(" + "|".join(_FORBIDDEN) + r")(?!\w)", re.IGNORECASE
)
# A keyword right after a real `AS` is an alias (`count(*) AS transactions`), never a clause.
# `x AS AS SET ...` aliases to `AS` and then SETs, so the `AS` must not itself be an alias.
_ALIAS_PREFIX_RE = re.compile(r"(?:^|[^\w.:$])AS\s+$", re.IGNORECASE)
_ALIASED_AS_PREFIX_RE = re.compile(r"(?:^|[^\w.:$])AS\s+AS\s+$", re.IGNORECASE)
_LEADING_RE = re.compile(rf"^(?:(?:EXPLAIN|PROFILE)\s+)?({_IDENT})?", re.IGNORECASE)
_NAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})*"
# `CALL name` is a procedure; `CALL {{` / `CALL (x) {{` subqueries don't match.
_PROCEDURE_RE = re.compile(rf"{_WORD_START}(?<![.$])CALL\s+({_NAME})", re.IGNORECASE)
_FUNCTION_RE = re.compile(
    rf"{_WORD_START}(?<![.$])({_IDENT}(?:\s*\.\s*{_IDENT})+)\s*\(", re.IGNORECASE
)
_READ_PROCEDURES = frozenset(
    name.lower()
    for name in (
        "db.labels",
        "db.relationshipTypes",
        "db.propertyKeys",
        "db.schema.visualization",
        "db.schema.nodeTypeProperties",
        "db.schema.relTypeProperties",
    )
)
_READ_FUNCTION_PREFIXES = (
    "date.",
    "datetime.",
    "localdatetime.",
    "localtime.",
    "time.",
    "duration.",
    "point.",
    "vector.",
    "apoc.text.",
    "apoc.coll.",
    "apoc.map.",
    "apoc.date.",
    "apoc.temporal.",
    "apoc.convert.",
    "apoc.math.",
    "apoc.number.",
    "apoc.agg.",
)
_LITERAL_RE = re.compile(
    r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`]|``)*`|//[^\r\n]*|/\*.*?\*/", re.DOTALL
)


class GraphQueryRejected(ValueError):
    """The Cypher is not a single read-only statement."""


class GraphUnavailableError(Exception):
    """Neo4j could not be reached or refused the credentials."""


class GraphBackend(Protocol):
    """What GraphClient needs from langchain_neo4j.Neo4jGraph; tests use fakes."""

    @property
    def get_schema(self) -> str: ...

    def query(
        self,
        query: str,
        params: dict[str, Any] | None = None,
        session_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class GraphSettings:
    uri: str
    username: str
    password: str = field(repr=False)
    database: str
    timeout_s: float
    max_rows: int


def prompt_guidance(max_rows: int) -> str:
    """System-prompt section added when the graph tools are enabled."""
    return (
        "Retail graph (Neo4j): questions about retail data such as customers are answered "
        "from the graph. First call graph_schema, then write ONE read-only Cypher query that "
        "uses only labels, relationship types and properties present in that schema, and run "
        f"it with graph_query. Always add a LIMIT (at most {max_rows}). Conventions: "
        "Customer.country is an ISO-3166 alpha-2 code (e.g. 'RO'), Customer.sex is 'M', 'F' "
        "or 'other', Customer.age is an integer. Never try to modify data.\n"
        "Answer only from rows returned by graph_query and never invent data. If the question "
        "needs data the schema does not contain, say which data is missing instead of "
        "querying. If graph_query returns an error, fix the query and retry at most 2 times, "
        "then explain in plain language that the question could not be answered. If no rows "
        "match, say that no matching data was found. If the graph database is unavailable, "
        "say so. Do not query the graph for questions unrelated to the retail data."
    )


def validate_read_only(cypher: str) -> str:
    """Return the stripped statement, or raise GraphQueryRejected if it could write."""
    statement = cypher.strip().rstrip(";").strip()
    if not statement:
        raise GraphQueryRejected("empty query")
    code = _LITERAL_RE.sub(_neutralize, statement).strip()
    if ";" in code:
        raise GraphQueryRejected("only a single statement is allowed")
    first = _LEADING_RE.match(code)
    leading = first.group(1) if first else None
    if leading is None or leading.upper() not in _READ_CLAUSES:
        raise GraphQueryRejected(
            f"read-only access: queries must start with MATCH, OPTIONAL MATCH, WITH, UNWIND, "
            f"RETURN or CALL, got {leading or code[:20]!r}"
        )
    for match in _FORBIDDEN_RE.finditer(code):
        if not _is_alias(code[: match.start()]):
            raise GraphQueryRejected(f"read-only access: {match.group(1).upper()} is not allowed")
    procedure_spans: set[int] = set()
    for proc in _PROCEDURE_RE.finditer(code):
        name = _dotted(proc.group(1))
        if name.lower() not in _READ_PROCEDURES:
            raise GraphQueryRejected(f"read-only access: procedure {name} is not allowed")
        procedure_spans.add(proc.start(1))
    for func in _FUNCTION_RE.finditer(code):
        name = _dotted(func.group(1))
        if func.start(1) in procedure_spans:
            continue
        if not name.lower().startswith(_READ_FUNCTION_PREFIXES):
            raise GraphQueryRejected(f"read-only access: function {name} is not allowed")
    return statement


def _is_alias(prefix: str) -> bool:
    return bool(_ALIAS_PREFIX_RE.search(prefix)) and not _ALIASED_AS_PREFIX_RE.search(prefix)


def _neutralize(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.startswith("`"):
        # Quoted name -> plain identifier so keyword checks still see it. Dots are kept, so
        # `apoc.cypher.run` (one quoted dotted name) is still checked as a procedure/function.
        return re.sub(r"[^\w.]", "_", token[1:-1].replace("``", "`")) or "_"
    if token.startswith(("'", '"')):
        return "''"
    return " "


def _dotted(name: str) -> str:
    return re.sub(r"\s+", "", name)


def shape_rows(rows: list[dict[str, Any]], max_rows: int) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "returned": min(len(rows), max_rows),
        "truncated": len(rows) > max_rows,
        "rows": rows[:max_rows],
    }


def error_result(message: str, error_class: str) -> dict[str, Any]:
    return {"error": message, "error_class": error_class}


def result_attributes(content: Any) -> dict[str, Any]:
    """Span attributes from a graph tool result (metadata only, never rows or schema)."""
    try:
        payload = json.loads(_result_text(content))
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    attrs: dict[str, Any] = {"db.system.name": "neo4j"}
    if isinstance(payload.get("row_count"), int):
        attrs["db.response.returned_rows"] = payload["row_count"]
    if isinstance(payload.get("duration_ms"), int | float):
        attrs["agent.graph.duration_ms"] = float(payload["duration_ms"])
    if isinstance(payload.get("error_class"), str):
        attrs["error.type"] = payload["error_class"]
    return attrs


def _result_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    return str(content)


def connect_neo4j(settings: GraphSettings) -> GraphBackend:
    """Open a Neo4jGraph (verifies connectivity and loads the schema; needs APOC)."""
    from langchain_neo4j import Neo4jGraph  # heavy import, only when the graph is used

    return Neo4jGraph(
        url=settings.uri,
        username=settings.username,
        password=settings.password,
        database=settings.database,
        timeout=settings.timeout_s,
        sanitize=True,
    )


class GraphClient:
    """Lazily connected, thread-safe access to the graph. Blocking calls run in a thread."""

    def __init__(
        self,
        settings: GraphSettings,
        *,
        connect: Callable[[GraphSettings], GraphBackend] = connect_neo4j,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.settings = settings
        self._connect = connect
        self._clock = clock
        self._backend: GraphBackend | None = None
        self._lock = threading.Lock()

    async def schema(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._timed, self._schema)

    async def run(self, cypher: object) -> dict[str, Any]:
        return await asyncio.to_thread(self._timed, self._run, cypher)

    def _timed(self, call: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        """Run a call, turning any failure into an error result; always add duration_ms."""
        start = self._clock()
        try:
            result = call(*args)
        except GraphQueryRejected as exc:
            result = error_result(str(exc), type(exc).__name__)
        except GraphUnavailableError as exc:
            result = error_result(UNAVAILABLE_MESSAGE, _cause_name(exc))
        except (AuthError, DriverError) as exc:
            result = error_result(UNAVAILABLE_MESSAGE, type(exc).__name__)
        except Neo4jError as exc:
            result = error_result(str(exc), type(exc).__name__)
        except Exception as exc:  # e.g. RuntimeError from a closed Neo4jGraph: reconnect next time
            with self._lock:
                self._backend = None
            result = error_result(UNEXPECTED_MESSAGE, type(exc).__name__)
        result["duration_ms"] = round((self._clock() - start) * 1000, 1)
        return result

    def _schema(self) -> dict[str, Any]:
        return {"schema": self._backend_or_connect().get_schema}

    def _run(self, cypher: object) -> dict[str, Any]:
        if not isinstance(cypher, str):
            raise GraphQueryRejected("cypher must be a string")
        statement = validate_read_only(cypher)
        rows = self._backend_or_connect().query(
            statement, session_params={"default_access_mode": READ_ACCESS}
        )
        return shape_rows(rows, self.settings.max_rows)

    def _backend_or_connect(self) -> GraphBackend:
        with self._lock:
            if self._backend is None:
                try:
                    self._backend = self._connect(self.settings)
                except Exception as exc:  # any connect failure; retried on next call
                    raise GraphUnavailableError(UNAVAILABLE_MESSAGE) from exc
            return self._backend


def _cause_name(exc: BaseException) -> str:
    return type(exc.__cause__ or exc).__name__


@functools.cache
def shared_client(settings: GraphSettings) -> GraphClient:
    """One client (one driver pool) per process and settings, shared by all sessions."""
    return GraphClient(settings)

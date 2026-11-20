# ITEM-2: Integrate Neo4j

## Original request

I want to integrate neo4j to store graph data about retailer data, having the following nodes: customer(id, first_name, last_name, email, country, age, sex).
Later new nodes will be added to complete the shopping experience.

As part of the neo4j integration I want to have an utility functionality to take a user query and transform it to a new4j cypher.
Use this to implement a RAG system.

For technical stack, I want to use Neo4jGraph from langchain_neo4j to connect to neo4j db and get schema and execute queries.

When the user query cannot be properly translated to a cypher query, I want the LLM to return a
proper response.

## Story

As a retail analyst using the agent (CLI or REST), I want to ask natural-language questions about customer data
stored in Neo4j, so that I get accurate answers grounded in the graph without writing Cypher, and a clear explanation
when my question can't be answered from the data.

## Clarifications (agreed)

- **Cypher generation.** The runtime Claude agent writes the Cypher itself, using two tools: `graph_schema` and
  `graph_query(cypher)`. A pure helper module (`graph.py`) holds schema access, read-only validation, result shaping
  and the prompt guidance. There is no second LLM and no `langchain-anthropic`.
- **Exposure.** Agent tools only. No new REST route and no `ChatResponse` / SSE change.
- **Read-only.** Write and admin clauses are rejected before execution, **and** every query runs in a READ
  access-mode session.
- **Data setup.** Connect-only: an existing, populated Neo4j (with APOC) is assumed. There is no docker-compose
  service, seed command or constraint creation.
- **Data model.** Label `Customer`. `id`, `first_name`, `last_name`, `email` and `country` are strings, with
  `country` as an ISO-3166 alpha-2 code. `age` is an integer. `sex` is `M` / `F` / `other`.
- **Untranslatable or failed queries.** Explained in natural language only (off-topic, data missing from the schema,
  invalid Cypher after at most 2 retries, read-only rejection, no rows, DB unavailable). Tool errors don't fail the
  turn: REST stays 200 and the session is kept.
- **NEO4J_URI unset.** The graph tools are not registered and behaviour is unchanged. When it is set, the agent
  connects lazily on first use.
- **Settings.** `NEO4J_URI` (unset = off), `NEO4J_USERNAME` (default `neo4j`), `NEO4J_PASSWORD`, `NEO4J_DATABASE`
  (default `neo4j`), `AGENT_GRAPH_QUERY_TIMEOUT` (default 10 s), `AGENT_GRAPH_MAX_ROWS` (default 50).
- **PII and tracing.** All fields are returned unmasked. Cypher text and rows are recorded in spans only when
  `AGENT_TRACE_CONTENT=1`. Row count, duration and error class are always recorded.
- **System prompt.** A graph section is appended only when the graph tools are enabled.
- **Dependency.** `langchain-neo4j` was added (it brings in `neo4j`, `langchain-core`, `langchain-classic`,
  `langgraph`, `neo4j-graphrag`).

## Acceptance criteria

- **AC1 (config):**
  - Given the six settings are set, when the config loads, then `AgentConfig` holds them.
  - Given they are unset, then the defaults apply and the graph is disabled.
  - Given a timeout or row limit that is not positive or not a number, then a `ValueError` names the variable and
    the bad value.
- **AC2 (registration):**
  - Given `NEO4J_URI` is set, when `build_options()` runs, then `mcp__agent_tools__graph_schema` and
    `mcp__agent_tools__graph_query` are registered and allowed.
  - Given it is unset, then they are absent and the options are unchanged.
- **AC3 (schema):** Given a reachable graph, when `graph_schema` is called, then it returns the `Neo4jGraph` schema
  text, including `Customer` and its properties.
- **AC4 (query):** Given valid read-only Cypher, when `graph_query` is called, then it runs in a READ session with the
  configured timeout and returns JSON rows, truncated to `AGENT_GRAPH_MAX_ROWS`, with a `truncated` flag.
- **AC5 (write rejected):** Given Cypher with a write or admin clause, when `graph_query` is called, then it is
  rejected before execution with `is_error` and a "read-only" message, and nothing is sent to Neo4j.
- **AC6 (invalid Cypher):** Given Cypher that Neo4j rejects, when `graph_query` is called, then the tool returns
  `is_error` with the Neo4j error message so the model can correct it. The REST turn is not failed and the session
  is kept.
- **AC7 (unavailable):** Given Neo4j is unreachable or authentication fails, when either graph tool is called, then
  it returns `is_error` with "graph database unavailable", without credentials or a stack trace. REST stays 200 and
  the session is kept.
- **AC8 (untranslatable, live):**
  - Given a question about data not in the schema, then the agent says the data is not available and doesn't
    invent results.
  - Given a question unrelated to the retail data, then the agent answers without querying the graph.
- **AC9 (RAG answer, live):** Given an existing populated DB, when asked "How many customers are from RO?", then the
  agent fetches the schema, runs Cypher, and its answer matches the DB count.
- **AC10 (tracing):**
  - Given tracing is on, when a graph tool runs, then its `execute_tool` span has row count, duration and error
    class attributes.
  - Cypher text and rows are recorded only when `AGENT_TRACE_CONTENT=1`.
- **AC12 (front-end rule):** The CLI and REST still only see `AgentEvent`s. No route, status code or
  `ChatResponse` / SSE shape changes.

(AC11, local Neo4j setup and seeding, was dropped.)

## Out of scope

- Node types other than `Customer`, and relationships (planned for later).
- Vector or embedding search and hybrid GraphRAG.
- Write access through the agent, and data import.
- A new REST route.
- Local Neo4j setup (docker-compose / make target), seeding and constraint creation.

## Status

Implemented, pending QA.

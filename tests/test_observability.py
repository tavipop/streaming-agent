import json
from collections.abc import Iterator
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from streaming_agent import graph, observability
from streaming_agent.observability import TurnTracer

MODEL = "claude-sonnet-5-5"


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


def _tracer(
    exporter: InMemorySpanExporter, *, content: bool = False, max_turns: int | None = None
) -> TurnTracer:
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    ticks: Iterator[int] = iter(range(1_000, 1_000_000, 1_000))
    return TurnTracer(
        provider.get_tracer("test"),
        model=MODEL,
        record_content=content,
        max_turns=max_turns,
        clock=lambda: next(ticks),
    )


def _stream(event: dict[str, Any]) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s1", event=event)


def _llm_call(text: str, stop_reason: str, msg_id: str) -> list[Any]:
    return [
        _stream(
            {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "model": MODEL,
                    "usage": {"input_tokens": 100, "cache_read_input_tokens": 40},
                },
            }
        ),
        _stream({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}),
        _stream(
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason},
                "usage": {"output_tokens": 7},
            }
        ),
        _stream({"type": "message_stop"}),
    ]


def _turn_messages(tool_error: bool = False) -> list[Any]:
    tool_use = ToolUseBlock(id="t1", name="mcp__agent_tools__add", input={"a": 2, "b": 3})
    return [
        *_llm_call("Let me add.", "tool_use", "m1"),
        AssistantMessage(content=[TextBlock("Let me add."), tool_use], model=MODEL),
        UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="5.0", is_error=tool_error)]
        ),
        *_llm_call("It is 5.", "end_turn", "m2"),
        ResultMessage(
            subtype="success",
            duration_ms=1200,
            duration_api_ms=900,
            is_error=False,
            num_turns=2,
            session_id="s1",
            total_cost_usd=0.0123,
            usage={"input_tokens": 200, "output_tokens": 14},
            result="It is 5.",
        ),
    ]


def _run(tracer: TurnTracer, messages: list[Any]) -> None:
    tracer.start("add 2 and 3")
    for msg in messages:
        tracer.observe(msg)
    tracer.finish()


def _by_name(exporter: InMemorySpanExporter) -> dict[str, list[ReadableSpan]]:
    spans: dict[str, list[ReadableSpan]] = {}
    for span in exporter.get_finished_spans():
        spans.setdefault(span.name, []).append(span)
    return spans


def test_span_tree(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    spans = _by_name(exporter)

    assert set(spans) == {"agent.turn", f"chat {MODEL}", "execute_tool mcp__agent_tools__add"}
    (turn,) = spans["agent.turn"]
    assert len(spans[f"chat {MODEL}"]) == 2
    children = [*spans[f"chat {MODEL}"], *spans["execute_tool mcp__agent_tools__add"]]
    assert turn.context is not None
    for child in children:
        assert child.parent is not None
        assert child.parent.span_id == turn.context.span_id
        assert child.context is not None
        assert child.context.trace_id == turn.context.trace_id


def test_llm_span_attributes(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    first, second = sorted(_by_name(exporter)[f"chat {MODEL}"], key=lambda s: s.start_time or 0)
    attrs = first.attributes or {}
    assert attrs["gen_ai.system"] == "anthropic"
    assert attrs["gen_ai.response.model"] == MODEL
    assert attrs["gen_ai.response.id"] == "m1"
    assert attrs["gen_ai.usage.input_tokens"] == 100
    assert attrs["gen_ai.usage.output_tokens"] == 7
    assert attrs["gen_ai.usage.cache_read_input_tokens"] == 40
    assert attrs["llm.token_count.completion"] == 7
    assert tuple(attrs["gen_ai.response.finish_reasons"]) == ("tool_use",)
    assert (second.attributes or {})["gen_ai.response.finish_reasons"] == ("end_turn",)


def test_tool_span_ok_and_timed(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    (tool,) = _by_name(exporter)["execute_tool mcp__agent_tools__add"]
    attrs = tool.attributes or {}
    assert attrs["gen_ai.tool.name"] == "mcp__agent_tools__add"
    assert attrs["gen_ai.tool.call.id"] == "t1"
    assert tool.status.status_code != StatusCode.ERROR
    assert (tool.end_time or 0) > (tool.start_time or 0)


def test_tool_error_sets_error_status(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages(tool_error=True))
    (tool,) = _by_name(exporter)["execute_tool mcp__agent_tools__add"]
    assert tool.status.status_code == StatusCode.ERROR


def test_turn_span_has_result_metadata(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    (turn,) = _by_name(exporter)["agent.turn"]
    attrs = turn.attributes or {}
    assert attrs["session.id"] == "s1"
    assert attrs["agent.num_turns"] == 2
    assert attrs["agent.cost_usd"] == 0.0123
    assert attrs["agent.duration_ms"] == 1200
    assert attrs["gen_ai.usage.input_tokens"] == 200


def test_content_not_recorded_by_default(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    for span in exporter.get_finished_spans():
        attrs = span.attributes or {}
        assert "input.value" not in attrs
        assert "output.value" not in attrs


def test_content_recorded_when_enabled(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter, content=True), _turn_messages())
    spans = _by_name(exporter)
    turn_attrs = spans["agent.turn"][0].attributes or {}
    assert turn_attrs["input.value"] == "add 2 and 3"
    assert turn_attrs["output.value"] == "It is 5."
    tool_attrs = spans["execute_tool mcp__agent_tools__add"][0].attributes or {}
    assert tool_attrs["input.value"] == '{"a": 2, "b": 3}'
    assert tool_attrs["output.value"] == "5.0"
    outputs = {(s.attributes or {}).get("output.value") for s in spans[f"chat {MODEL}"]}
    assert outputs == {"Let me add.", "It is 5."}


def test_truncate() -> None:
    assert observability.truncate("abc", limit=5) == "abc"
    assert observability.truncate("x" * 10, limit=4).startswith("xxxx… [truncated 6]")


def test_finish_with_error_closes_open_spans(exporter: InMemorySpanExporter) -> None:
    tracer = _tracer(exporter)
    tracer.start("hi")
    tracer.observe(_llm_call("partial", "end_turn", "m1")[0])  # message_start only
    tracer.finish(KeyboardInterrupt())
    spans = _by_name(exporter)
    assert spans[f"chat {MODEL}"][0].status.status_code == StatusCode.ERROR
    assert spans["agent.turn"][0].status.status_code == StatusCode.ERROR


async def test_wrap_passes_messages_through(exporter: InMemorySpanExporter) -> None:
    messages = _turn_messages()

    async def source() -> Any:
        for m in messages:
            yield m

    tracer = _tracer(exporter)
    tracer.start("hi")
    assert [m async for m in tracer.wrap(source())] == messages
    tracer.finish()
    assert len(exporter.get_finished_spans()) == 4


def test_setup_tracing_off_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (*observability._ENDPOINT_VARS, "AGENT_TRACE_CONSOLE"):
        monkeypatch.delenv(var, raising=False)
    assert observability.tracing_target() is None
    assert observability.setup_tracing() is None


def test_tracing_target_prefers_otlp_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TRACE_CONSOLE", "1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://localhost:6006/v1/traces")
    assert observability.tracing_target() == "http://localhost:6006/v1/traces"
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert observability.tracing_target() == "console"


def _max_turns_result(**kw: Any) -> ResultMessage:
    args: dict[str, Any] = {
        "subtype": "error_max_turns",
        "duration_ms": 10,
        "duration_api_ms": 5,
        "is_error": True,
        "num_turns": 4,
        "session_id": "s1",
        "terminal_reason": "max_turns",
    }
    return ResultMessage(**(args | kw))


def test_max_turns_reached_is_traced_without_error(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter, max_turns=3), [_max_turns_result()])
    (turn,) = _by_name(exporter)["agent.turn"]
    attrs = turn.attributes or {}
    assert attrs["agent.max_turns"] == 3
    assert attrs["agent.max_turns_reached"] is True
    assert turn.status.status_code != StatusCode.ERROR


def test_normal_turn_is_not_flagged_as_max_turns(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter, max_turns=3), _turn_messages())
    attrs = _by_name(exporter)["agent.turn"][0].attributes or {}
    assert attrs["agent.max_turns_reached"] is False


def test_max_turns_content_not_recorded_by_default(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter, max_turns=3), [_max_turns_result(result="partial answer")])
    attrs = _by_name(exporter)["agent.turn"][0].attributes or {}
    assert "output.value" not in attrs


def test_model_error_with_max_turns_sets_error_status(exporter: InMemorySpanExporter) -> None:
    _run(
        _tracer(exporter, max_turns=3),
        [AssistantMessage(content=[], model=MODEL, error="server_error"), _max_turns_result()],
    )
    (turn,) = _by_name(exporter)["agent.turn"]
    assert turn.status.status_code == StatusCode.ERROR
    assert "server_error" in (turn.status.description or "")
    assert (turn.attributes or {})["agent.max_turns_reached"] is True


def test_model_error_without_error_result_sets_error_status(
    exporter: InMemorySpanExporter,
) -> None:
    _run(
        _tracer(exporter),
        [AssistantMessage(content=[], model=MODEL, error="rate_limit"), *_turn_messages()[-1:]],
    )
    (turn,) = _by_name(exporter)["agent.turn"]
    assert turn.status.status_code == StatusCode.ERROR


def test_max_turns_content_recorded_when_enabled(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter, content=True, max_turns=3), [_max_turns_result(result="partial")])
    attrs = _by_name(exporter)["agent.turn"][0].attributes or {}
    assert attrs["output.value"] == "partial"


def test_error_result_without_max_turns_sets_error_status(exporter: InMemorySpanExporter) -> None:
    result = _max_turns_result(subtype="error_during_execution", terminal_reason=None)
    _run(_tracer(exporter, max_turns=3), [result])
    (turn,) = _by_name(exporter)["agent.turn"]
    assert turn.status.status_code == StatusCode.ERROR
    assert turn.status.description == "error_during_execution"


GRAPH_TOOL = "mcp__agent_tools__graph_query"
CYPHER = "MATCH (c:Customer) RETURN c.email AS email LIMIT 1"


def _graph_messages(result: dict[str, Any], is_error: bool = False) -> list[Any]:
    tool_use = ToolUseBlock(id="g1", name=GRAPH_TOOL, input={"cypher": CYPHER})
    return [
        AssistantMessage(content=[tool_use], model=MODEL),
        UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="g1",
                    content=[{"type": "text", "text": json.dumps(result)}],
                    is_error=is_error,
                )
            ]
        ),
    ]


def test_graph_tool_span_has_row_count_and_duration(exporter: InMemorySpanExporter) -> None:
    result = {**graph.shape_rows([{"email": "ana@example.com"}], 50), "duration_ms": 12.5}
    _run(_tracer(exporter), _graph_messages(result))
    (span,) = _by_name(exporter)[f"execute_tool {GRAPH_TOOL}"]
    attrs = span.attributes or {}
    assert attrs["db.system.name"] == "neo4j"
    assert attrs["db.response.returned_rows"] == 1
    assert attrs["agent.graph.duration_ms"] == 12.5
    assert span.status.status_code != StatusCode.ERROR


def test_graph_tool_span_has_error_class(exporter: InMemorySpanExporter) -> None:
    result = {**graph.error_result("Invalid input", "CypherSyntaxError"), "duration_ms": 3.0}
    _run(_tracer(exporter), _graph_messages(result, is_error=True))
    (span,) = _by_name(exporter)[f"execute_tool {GRAPH_TOOL}"]
    assert (span.attributes or {})["error.type"] == "CypherSyntaxError"
    assert (span.attributes or {})["agent.graph.duration_ms"] == 3.0
    assert span.status.status_code == StatusCode.ERROR


def test_graph_cypher_and_rows_not_recorded_by_default(exporter: InMemorySpanExporter) -> None:
    result = {**graph.shape_rows([{"email": "ana@example.com"}], 50), "duration_ms": 1.0}
    _run(_tracer(exporter), _graph_messages(result))
    for span in exporter.get_finished_spans():
        recorded = json.dumps(dict(span.attributes or {}))
        assert "ana@example.com" not in recorded
        assert "MATCH" not in recorded


def test_graph_cypher_and_rows_recorded_when_enabled(exporter: InMemorySpanExporter) -> None:
    result = {**graph.shape_rows([{"email": "ana@example.com"}], 50), "duration_ms": 1.0}
    _run(_tracer(exporter, content=True), _graph_messages(result))
    (span,) = _by_name(exporter)[f"execute_tool {GRAPH_TOOL}"]
    attrs = span.attributes or {}
    assert CYPHER in str(attrs["input.value"])
    assert "ana@example.com" in str(attrs["output.value"])


def test_non_graph_tool_span_has_no_graph_attributes(exporter: InMemorySpanExporter) -> None:
    _run(_tracer(exporter), _turn_messages())
    (span,) = _by_name(exporter)["execute_tool mcp__agent_tools__add"]
    assert "db.system.name" not in (span.attributes or {})

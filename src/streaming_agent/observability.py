"""OpenTelemetry tracing for agent turns, model calls and tool executions.

Spans are built from the same SDK message stream the agent already consumes (no hooks):

    agent.turn                     one per user prompt
    ├── chat <model>               one per model API call  (message_start → message_stop)
    ├── execute_tool <name>        ToolUseBlock → matching ToolResultBlock
    └── ...

Attributes follow the OTel GenAI conventions (gen_ai.*) plus OpenInference (Phoenix).
Prompts, outputs and tool I/O are recorded only when record_content=True.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    StreamEvent,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Span, Status, StatusCode

from streaming_agent import graph
from streaming_agent.events import is_max_turns

CONTENT_LIMIT = 4000
_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")


def tracing_target() -> str | None:
    """Where spans will go: the OTLP endpoint, 'console', or None when tracing is off."""
    for var in _ENDPOINT_VARS:
        if os.getenv(var):
            return os.environ[var]
    if os.getenv("AGENT_TRACE_CONSOLE") == "1":
        return "console"
    return None


def setup_tracing() -> TracerProvider | None:
    """Install a global TracerProvider from env vars. Returns None (no-op tracing) if unset."""
    target = tracing_target()
    if target is None:
        return None
    service = os.getenv("OTEL_SERVICE_NAME", "streaming-agent")
    # Phoenix groups traces into projects by openinference.project.name.
    resource = Resource.create({"service.name": service, "openinference.project.name": service})
    provider = TracerProvider(resource=resource)
    if target == "console":
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # The exporter reads the endpoint (and headers) from the OTEL_* env vars itself.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def truncate(value: Any, limit: int = CONTENT_LIMIT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + f"… [truncated {len(text) - limit}]"


@dataclass
class _LlmCall:
    span: Span
    text: list[str] = field(default_factory=list)


class TurnTracer:
    """Tracing state for one agent turn. Feed it every SDK message via observe() or wrap()."""

    def __init__(
        self,
        tracer: trace.Tracer,
        *,
        model: str,
        record_content: bool = False,
        max_turns: int | None = None,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self._tracer = tracer
        self._model = model
        self._record_content = record_content
        self._max_turns = max_turns
        self._clock = clock
        self._turn: Span | None = None
        # Keyed by parent_tool_use_id: None is the main agent, anything else a subagent.
        self._llm: dict[str | None, _LlmCall] = {}
        self._tools: dict[str, Span] = {}
        self._tool_names: dict[str, str] = {}  # tool_use_id -> tool name, while running
        self._model_errors: list[str] = []  # AssistantMessage.error kinds seen this turn

    def start(self, prompt: str) -> None:
        self._turn = self._tracer.start_span(
            "agent.turn",
            start_time=self._clock(),
            attributes={"openinference.span.kind": "CHAIN", "gen_ai.request.model": self._model},
        )
        if self._max_turns is not None:
            self._turn.set_attribute("agent.max_turns", self._max_turns)
        if self._record_content:
            self._turn.set_attribute("input.value", truncate(prompt))

    async def wrap(self, messages: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Pass messages through unchanged, observing each one."""
        async for msg in messages:
            self.observe(msg)
            yield msg

    def observe(self, msg: Any) -> None:
        if isinstance(msg, StreamEvent):
            self._on_stream_event(msg.event, msg.parent_tool_use_id)
        elif isinstance(msg, AssistantMessage):
            if msg.error is not None:
                self._model_errors.append(msg.error)
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    self._start_tool(block, msg.parent_tool_use_id)
        elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
            for block in msg.content:
                if isinstance(block, ToolResultBlock):
                    self._end_tool(block)
        elif isinstance(msg, ResultMessage):
            self._on_result(msg)

    def finish(self, error: BaseException | None = None) -> None:
        """End the turn span, closing any spans still open (as errors if the turn failed)."""
        now = self._clock()
        dangling = [c.span for c in self._llm.values()] + list(self._tools.values())
        for span in dangling:
            if error is not None:
                span.set_status(Status(StatusCode.ERROR, "turn ended before span completed"))
            span.end(end_time=now)
        self._llm.clear()
        self._tools.clear()
        self._tool_names.clear()
        if self._turn is None:
            return
        if error is not None:
            self._turn.record_exception(error)
            self._turn.set_status(Status(StatusCode.ERROR, type(error).__name__))
        self._turn.end(end_time=now)
        self._turn = None

    # ---- internals -------------------------------------------------------------------

    def _parent_ctx(self, parent_tool_use_id: str | None) -> Any:
        parent = self._tools.get(parent_tool_use_id or "") or self._turn
        return trace.set_span_in_context(parent) if parent is not None else None

    def _on_stream_event(self, raw: dict[str, Any], parent_id: str | None) -> None:
        etype = raw.get("type")
        if etype == "message_start":
            message = raw.get("message", {})
            model = message.get("model") or self._model
            span = self._tracer.start_span(
                f"chat {model}",
                context=self._parent_ctx(parent_id),
                start_time=self._clock(),
                attributes={
                    "openinference.span.kind": "LLM",
                    "gen_ai.system": "anthropic",
                    "gen_ai.operation.name": "chat",
                    "gen_ai.request.model": self._model,
                    "gen_ai.response.model": model,
                    "gen_ai.response.id": message.get("id", ""),
                    "llm.model_name": model,
                    "llm.provider": "anthropic",
                },
            )
            _set_usage(span, message.get("usage") or {})
            self._llm[parent_id] = _LlmCall(span)
            return
        call = self._llm.get(parent_id)
        if call is None:
            return
        if etype == "content_block_delta" and self._record_content:
            delta = raw.get("delta", {})
            if delta.get("type") == "text_delta":
                call.text.append(delta.get("text", ""))
        elif etype == "message_delta":
            _set_usage(call.span, raw.get("usage") or {})
            stop_reason = raw.get("delta", {}).get("stop_reason")
            if stop_reason:
                call.span.set_attribute("gen_ai.response.finish_reasons", [stop_reason])
        elif etype == "message_stop":
            if self._record_content and call.text:
                call.span.set_attribute("output.value", truncate("".join(call.text)))
            call.span.end(end_time=self._clock())
            del self._llm[parent_id]

    def _start_tool(self, block: ToolUseBlock, parent_id: str | None) -> None:
        span = self._tracer.start_span(
            f"execute_tool {block.name}",
            context=self._parent_ctx(parent_id),
            start_time=self._clock(),
            attributes={
                "openinference.span.kind": "TOOL",
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": block.name,
                "gen_ai.tool.call.id": block.id,
                "tool.name": block.name,
            },
        )
        if self._record_content:
            span.set_attribute("input.value", truncate(block.input))
        self._tools[block.id] = span
        self._tool_names[block.id] = block.name

    def _end_tool(self, block: ToolResultBlock) -> None:
        span = self._tools.pop(block.tool_use_id, None)
        name = self._tool_names.pop(block.tool_use_id, "")
        if span is None:
            return
        if name.rpartition("__")[2] in graph.TOOL_NAMES:
            # Metadata only (row count, duration, error class); rows go in output.value below.
            span.set_attributes(graph.result_attributes(block.content))
        if block.is_error:
            span.set_status(Status(StatusCode.ERROR, "tool returned an error"))
        if self._record_content and block.content is not None:
            span.set_attribute("output.value", truncate(block.content))
        span.end(end_time=self._clock())

    def _on_result(self, msg: ResultMessage) -> None:
        if self._turn is None:
            return
        max_turns_reached = is_max_turns(msg)
        attrs: dict[str, Any] = {
            "session.id": msg.session_id,
            "agent.num_turns": msg.num_turns,
            "agent.duration_ms": msg.duration_ms,
            "agent.duration_api_ms": msg.duration_api_ms,
            "agent.is_error": msg.is_error,
            "agent.max_turns_reached": max_turns_reached,
        }
        if msg.total_cost_usd is not None:
            attrs["agent.cost_usd"] = msg.total_cost_usd
        self._turn.set_attributes(attrs)
        _set_usage(self._turn, msg.usage or {})
        if self._model_errors:  # a failed model call is an error even if the cap was also hit
            self._turn.set_status(Status(StatusCode.ERROR, ", ".join(self._model_errors)))
        elif msg.is_error and not max_turns_reached:  # hitting the limit alone is graceful
            self._turn.set_status(Status(StatusCode.ERROR, msg.subtype))
        if self._record_content and msg.result is not None:
            self._turn.set_attribute("output.value", truncate(msg.result))


def _set_usage(span: Span, usage: dict[str, Any]) -> None:
    """Copy Anthropic usage counters onto a span (GenAI + OpenInference names)."""
    names = {
        "input_tokens": ("gen_ai.usage.input_tokens", "llm.token_count.prompt"),
        "output_tokens": ("gen_ai.usage.output_tokens", "llm.token_count.completion"),
        "cache_read_input_tokens": (
            "gen_ai.usage.cache_read_input_tokens",
            "llm.token_count.prompt_details.cache_read",
        ),
        "cache_creation_input_tokens": (
            "gen_ai.usage.cache_creation_input_tokens",
            "llm.token_count.prompt_details.cache_write",
        ),
    }
    for key, attr_names in names.items():
        value = usage.get(key)
        if isinstance(value, int):
            for name in attr_names:
                span.set_attribute(name, value)

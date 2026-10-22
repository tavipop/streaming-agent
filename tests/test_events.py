from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

from streaming_agent.events import (
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
    TurnFinished,
    from_stream_event,
    to_agent_events,
)


def _se(event: dict[str, Any]) -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event=event)


def test_text_delta_is_mapped() -> None:
    raw = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}
    assert from_stream_event(raw) == TextDelta("Hi")


def test_tool_use_start_is_mapped() -> None:
    raw = {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "add"}}
    assert from_stream_event(raw) == ToolCallStarted("add")


def test_irrelevant_events_are_skipped() -> None:
    assert from_stream_event({"type": "message_start"}) is None
    raw = {"type": "content_block_delta", "delta": {"type": "input_json_delta"}}
    assert from_stream_event(raw) is None


async def test_full_turn_sequence() -> None:
    async def fake_stream() -> AsyncIterator[Any]:
        yield _se({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "4"}})
        yield AssistantMessage(
            content=[TextBlock("4"), ToolUseBlock(id="t1", name="add", input={"a": 2, "b": 2})],
            model="claude-sonnet-5-5",
        )
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
            total_cost_usd=0.001,
        )

    events = [e async for e in to_agent_events(fake_stream())]
    assert events == [
        TextDelta("4"),
        ToolCallFinished("add", {"a": 2, "b": 2}),
        TurnFinished(is_error=False, num_turns=1, cost_usd=0.001, session_id="s"),
    ]

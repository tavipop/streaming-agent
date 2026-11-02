from collections.abc import AsyncIterator
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

from streaming_agent.events import (
    LlmError,
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


async def test_errors_are_surfaced() -> None:
    async def fake_stream() -> AsyncIterator[Any]:
        yield AssistantMessage(content=[], model="m", error="rate_limit")
        yield ResultMessage(
            subtype="error_during_execution",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=1,
            session_id="s",
            result="API Error: 429",
            api_error_status=429,
        )

    events = [e async for e in to_agent_events(fake_stream())]
    assert events == [
        LlmError("rate_limit"),
        TurnFinished(
            is_error=True,
            num_turns=1,
            cost_usd=None,
            session_id="s",
            result="API Error: 429",
            error_detail="error_during_execution (API status 429)",
        ),
    ]


def _result(subtype: str, *, is_error: bool, terminal_reason: str | None = None) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=3,
        session_id="s",
        terminal_reason=terminal_reason,
    )


async def _finished(msg: ResultMessage) -> TurnFinished:
    async def fake_stream() -> AsyncIterator[Any]:
        yield msg

    (event,) = [e async for e in to_agent_events(fake_stream())]
    assert isinstance(event, TurnFinished)
    return event


async def test_max_turns_subtype_is_flagged() -> None:
    event = await _finished(_result("error_max_turns", is_error=True))
    assert event.max_turns_reached is True
    assert event.num_turns == 3


async def test_max_turns_terminal_reason_is_flagged() -> None:
    event = await _finished(_result("success", is_error=True, terminal_reason="max_turns"))
    assert event.max_turns_reached is True


async def test_normal_completion_is_not_flagged_as_max_turns() -> None:
    event = await _finished(_result("success", is_error=False, terminal_reason="completed"))
    assert event.max_turns_reached is False


async def test_other_errors_are_not_flagged_as_max_turns() -> None:
    event = await _finished(_result("error_during_execution", is_error=True))
    assert event.max_turns_reached is False

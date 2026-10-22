"""Turn raw SDK messages into a small, typed stream of UI events.

Keeping this pure (no I/O) makes the streaming logic easy to unit-test with fake
messages, and lets any front end (CLI, web socket, TUI) consume the same events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStarted:
    name: str


@dataclass(frozen=True)
class ToolCallFinished:
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class TurnFinished:
    is_error: bool
    num_turns: int
    cost_usd: float | None
    session_id: str


AgentEvent = TextDelta | ToolCallStarted | ToolCallFinished | TurnFinished


def from_stream_event(raw: dict[str, Any]) -> AgentEvent | None:
    """Map one raw Claude API streaming event to an AgentEvent (or None to skip)."""
    etype = raw.get("type")
    if etype == "content_block_delta":
        delta = raw.get("delta", {})
        if delta.get("type") == "text_delta":
            return TextDelta(delta.get("text", ""))
    elif etype == "content_block_start":
        block = raw.get("content_block", {})
        if block.get("type") == "tool_use":
            return ToolCallStarted(block.get("name", "?"))
    return None


async def to_agent_events(messages: AsyncIterator[Any]) -> AsyncIterator[AgentEvent]:
    """Consume SDK messages (with include_partial_messages=True) and yield AgentEvents."""
    async for msg in messages:
        if isinstance(msg, StreamEvent):
            ev = from_stream_event(msg.event)
            if ev is not None:
                yield ev
        elif isinstance(msg, AssistantMessage):
            # Text already arrived as deltas; surface completed tool calls with full input.
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    yield ToolCallFinished(block.name, dict(block.input))
        elif isinstance(msg, ResultMessage):
            yield TurnFinished(
                is_error=msg.is_error,
                num_turns=msg.num_turns,
                cost_usd=msg.total_cost_usd,
                session_id=msg.session_id,
            )

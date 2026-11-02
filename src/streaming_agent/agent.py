"""The agent: a thin wrapper around ClaudeSDKClient with streaming turned on."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
from opentelemetry import trace

from streaming_agent import tools
from streaming_agent.config import AgentConfig
from streaming_agent.events import AgentEvent, to_agent_events
from streaming_agent.observability import TurnTracer


def build_options(config: AgentConfig) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=config.model,
        system_prompt=config.system_prompt,
        max_turns=config.max_turns,
        permission_mode=config.permission_mode,
        mcp_servers={tools.SERVER_NAME: tools.build_server()},
        allowed_tools=[*config.allowed_tools, *tools.allowed_tool_names()],
        include_partial_messages=True,  # <- token-level streaming
        setting_sources=[],  # don't inherit this repo's .claude/ settings at runtime
    )


class StreamingAgent:
    """Multi-turn agent session. Use as an async context manager.

    async with StreamingAgent(config) as agent:
        async for event in agent.send("hello"):
            ...
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig.from_env()
        self._client = ClaudeSDKClient(options=build_options(self.config))

    async def __aenter__(self) -> StreamingAgent:
        await self._client.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.disconnect()

    async def send(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """Send one user message and stream back events until the turn finishes."""
        turn = TurnTracer(
            trace.get_tracer(__name__),
            model=self.config.model,
            record_content=self.config.trace_content,
            max_turns=self.config.max_turns,
        )
        turn.start(prompt)
        try:
            await self._client.query(prompt)
            messages: AsyncIterator[Any] = self._client.receive_response()
            async for event in to_agent_events(turn.wrap(messages)):
                yield event
        except BaseException as exc:
            turn.finish(exc)
            raise
        else:
            turn.finish()

    async def interrupt(self) -> None:
        await self._client.interrupt()

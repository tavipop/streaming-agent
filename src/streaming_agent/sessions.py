"""Server-side conversation sessions: one long-lived agent (Claude Code CLI process) each."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol

from streaming_agent.agent import StreamingAgent
from streaming_agent.config import AgentConfig
from streaming_agent.events import AgentEvent

log = logging.getLogger(__name__)

REAP_INTERVAL_S = 60.0


class Agent(Protocol):
    """What a session needs from an agent; StreamingAgent satisfies it, tests use fakes."""

    async def __aenter__(self) -> Any: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> Any: ...

    def send(self, prompt: str) -> AsyncIterator[AgentEvent]: ...


class SessionNotFound(KeyError):
    pass


class SessionLimitError(RuntimeError):
    pass


@dataclass
class Session:
    id: str
    agent: Agent
    last_used: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionManager:
    def __init__(
        self,
        config: AgentConfig,
        *,
        agent_factory: Callable[[AgentConfig], Agent] = StreamingAgent,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._factory = agent_factory
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._starting = 0  # sessions being connected, counted against max_sessions
        self._closing: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        return len(self._sessions)

    async def create(self) -> Session:
        if len(self._sessions) + self._starting >= self.config.max_sessions:
            raise SessionLimitError(f"session limit reached ({self.config.max_sessions})")
        self._starting += 1
        try:
            agent = self._factory(self.config)
            await agent.__aenter__()
        finally:
            self._starting -= 1
        session = Session(id=uuid.uuid4().hex, agent=agent, last_used=self._clock())
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> Session:
        try:
            session = self._sessions[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None
        session.last_used = self._clock()
        return session

    def touch(self, session: Session) -> None:
        session.last_used = self._clock()

    async def close(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            raise SessionNotFound(session_id)
        await _shutdown(session)

    def discard(self, session_id: str) -> None:
        """Drop a session now and shut its agent down in the background.

        For sessions in an unknown state (failed or abandoned turn); safe to call from code
        that is being cancelled, where awaiting the shutdown isn't possible.
        """
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        task = asyncio.get_running_loop().create_task(_shutdown(session))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            with contextlib.suppress(SessionNotFound):
                await self.close(session_id)
        if self._closing:
            await asyncio.gather(*self._closing, return_exceptions=True)

    async def reap_idle(self) -> list[str]:
        """Close sessions idle for longer than the TTL (never one mid-turn)."""
        cutoff = self._clock() - self.config.session_ttl_s
        idle = [
            s.id for s in self._sessions.values() if s.last_used < cutoff and not s.lock.locked()
        ]
        for session_id in idle:
            with contextlib.suppress(SessionNotFound):
                await self.close(session_id)
        return idle

    async def run_reaper(self, interval_s: float = REAP_INTERVAL_S) -> None:
        while True:
            await asyncio.sleep(interval_s)
            reaped = await self.reap_idle()
            if reaped:
                log.info("closed %d idle session(s)", len(reaped))


async def _shutdown(session: Session) -> None:
    try:
        await session.agent.__aexit__(None, None, None)
    except Exception:
        log.warning("error closing session %s", session.id, exc_info=True)

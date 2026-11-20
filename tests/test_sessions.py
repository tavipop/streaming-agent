from collections.abc import AsyncIterator
from typing import Any

import pytest

from streaming_agent.config import AgentConfig
from streaming_agent.events import AgentEvent
from streaming_agent.sessions import SessionLimitError, SessionManager, SessionNotFound


class FakeAgent:
    def __init__(self, config: AgentConfig, *, fail_on_exit: bool = False) -> None:
        self.entered = self.exited = False
        self.fail_on_exit = fail_on_exit

    async def __aenter__(self) -> "FakeAgent":
        self.entered = True
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.exited = True
        if self.fail_on_exit:
            raise RuntimeError("boom")

    async def send(self, prompt: str) -> AsyncIterator[AgentEvent]:
        return
        yield


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _manager(clock: Clock | None = None, **cfg: Any) -> SessionManager:
    return SessionManager(AgentConfig(**cfg), agent_factory=FakeAgent, clock=clock or Clock())


async def test_create_get_close() -> None:
    manager = _manager()
    session = await manager.create()
    agent = session.agent
    assert isinstance(agent, FakeAgent) and agent.entered
    assert manager.get(session.id) is session
    assert len(manager) == 1

    await manager.close(session.id)
    assert agent.exited
    with pytest.raises(SessionNotFound):
        manager.get(session.id)
    with pytest.raises(SessionNotFound):
        await manager.close(session.id)


async def test_session_limit() -> None:
    manager = _manager(max_sessions=2)
    await manager.create()
    await manager.create()
    with pytest.raises(SessionLimitError):
        await manager.create()


async def test_reaper_closes_only_idle_unlocked_sessions() -> None:
    clock = Clock()
    manager = _manager(clock, session_ttl_s=10)
    idle = await manager.create()
    busy = await manager.create()
    clock.now = 20
    fresh = await manager.create()
    await busy.lock.acquire()

    assert await manager.reap_idle() == [idle.id]
    assert manager.get(busy.id) is busy
    assert manager.get(fresh.id) is fresh


async def test_failing_shutdown_does_not_propagate() -> None:
    manager = SessionManager(AgentConfig(), agent_factory=lambda c: FakeAgent(c, fail_on_exit=True))
    session = await manager.create()
    await manager.close(session.id)  # logged, not raised
    other = await manager.create()
    manager.discard(other.id)
    await manager.close_all()
    assert len(manager) == 0

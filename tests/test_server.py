import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from claude_agent_sdk import CLIConnectionError

from streaming_agent.config import AgentConfig
from streaming_agent.events import (
    AgentEvent,
    LlmError,
    TextDelta,
    ToolCallFinished,
    TurnFinished,
)
from streaming_agent.server import ChatResponse, create_app
from streaming_agent.sessions import SessionManager

Script = Callable[[str], AsyncIterator[AgentEvent]]


def _done(result: str | None = "It is 5.", **kw: Any) -> TurnFinished:
    args: dict[str, Any] = {
        "is_error": False,
        "num_turns": 2,
        "cost_usd": 0.01,
        "session_id": "s",
        "result": result,
    }
    return TurnFinished(**(args | kw))


async def happy(prompt: str) -> AsyncIterator[AgentEvent]:
    yield TextDelta("Let me add. ")
    yield ToolCallFinished("mcp__agent_tools__add", {"a": 2, "b": 3})
    yield TextDelta("It is 5.")
    yield _done()


class FakeAgent:
    def __init__(self, script: Script) -> None:
        self.script = script
        self.prompts: list[str] = []
        self.closed = False

    async def __aenter__(self) -> "FakeAgent":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.closed = True

    def send(self, prompt: str) -> AsyncIterator[AgentEvent]:
        self.prompts.append(prompt)
        return self.script(prompt)


class Harness:
    def __init__(self, script: Script = happy, **cfg: Any) -> None:
        self.script = script
        self.agents: list[FakeAgent] = []
        config = AgentConfig(**cfg)
        self.manager = SessionManager(config, agent_factory=self._factory)
        app = create_app(config, manager=self.manager)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    def _factory(self, _: AgentConfig) -> FakeAgent:
        agent = FakeAgent(lambda p: self.script(p))
        self.agents.append(agent)
        return agent

    async def session(self, **headers: str) -> str:
        r = await self.client.post("/sessions", headers=headers)
        assert r.status_code == 201, r.text
        return str(r.json()["session_id"])

    async def send(self, sid: str, message: str = "add 2 and 3", **kw: Any) -> httpx.Response:
        return await self.client.post(f"/sessions/{sid}/messages", json={"message": message}, **kw)


def _envelope(r: httpx.Response) -> ChatResponse:
    return ChatResponse.model_validate(r.json())


async def test_happy_path() -> None:
    h = Harness()
    r = await h.send(await h.session())
    assert r.status_code == 200
    body = _envelope(r)
    assert body.message == "It is 5."
    assert body.error is False and body.error_source is None
    assert [(t.name, t.input) for t in body.tool_calls] == [
        ("mcp__agent_tools__add", {"a": 2, "b": 3})
    ]
    assert body.num_turns == 2 and body.cost_usd == 0.01


async def test_message_falls_back_to_streamed_text() -> None:
    async def no_result(prompt: str) -> AsyncIterator[AgentEvent]:
        yield TextDelta("Hel")
        yield TextDelta("lo")
        yield _done(result=None)

    h = Harness(no_result)
    assert _envelope(await h.send(await h.session())).message == "Hello"


async def test_multi_turn_reuses_session_agent() -> None:
    h = Harness()
    sid = await h.session()
    await h.send(sid, "first")
    await h.send(sid, "second")
    assert len(h.agents) == 1
    assert h.agents[0].prompts == ["first", "second"]


@pytest.mark.parametrize(
    ("events", "detail"),
    [
        ([_done(result=None, is_error=True, error_detail="error_max_turns")], "error_max_turns"),
        ([LlmError("rate_limit"), _done(result="API Error")], "rate_limit"),
    ],
)
async def test_llm_errors(events: list[AgentEvent], detail: str) -> None:
    async def script(prompt: str) -> AsyncIterator[AgentEvent]:
        for e in events:
            yield e

    h = Harness(script)
    sid = await h.session()
    r = await h.send(sid)
    assert r.status_code == 502
    body = _envelope(r)
    assert body.error is True and body.error_source == "llm"
    assert body.message is None
    assert detail in (body.error_detail or "")
    assert len(h.manager) == 1  # the session survives model errors


async def test_backend_error_discards_session() -> None:
    async def broken(prompt: str) -> AsyncIterator[AgentEvent]:
        yield TextDelta("partial")
        raise CLIConnectionError("cli died")

    h = Harness(broken)
    sid = await h.session()
    r = await h.send(sid)
    assert r.status_code == 500
    body = _envelope(r)
    assert body.error_source == "backend"
    assert body.error_detail == "agent backend error (CLIConnectionError)"
    assert (await h.send(sid)).status_code == 404


async def test_turn_timeout() -> None:
    async def slow(prompt: str) -> AsyncIterator[AgentEvent]:
        await asyncio.sleep(5)
        yield _done()

    h = Harness(slow, turn_timeout_s=0.05)
    r = await h.send(await h.session())
    assert r.status_code == 504
    assert _envelope(r).error_source == "backend"


async def test_concurrent_send_is_rejected() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def blocking(prompt: str) -> AsyncIterator[AgentEvent]:
        started.set()
        await release.wait()
        yield _done()

    h = Harness(blocking)
    sid = await h.session()
    first = asyncio.create_task(h.send(sid))
    await started.wait()
    r = await h.send(sid)
    assert r.status_code == 409
    assert _envelope(r).error_source == "client"
    release.set()
    assert (await first).status_code == 200


async def test_client_errors_use_envelope() -> None:
    h = Harness()
    r = await h.send("nope")
    assert r.status_code == 404
    assert _envelope(r).error_source == "client"

    r = await h.send(await h.session(), "   ")
    assert r.status_code == 422
    assert _envelope(r).error is True


async def test_session_limit() -> None:
    h = Harness(max_sessions=1)
    await h.session()
    r = await h.client.post("/sessions")
    assert r.status_code == 503
    assert _envelope(r).error_source == "client"


async def test_delete_session() -> None:
    h = Harness()
    sid = await h.session()
    assert (await h.client.delete(f"/sessions/{sid}")).status_code == 204
    assert h.agents[0].closed
    assert (await h.client.delete(f"/sessions/{sid}")).status_code == 404


async def test_api_key_auth() -> None:
    h = Harness(api_keys=frozenset({"k1"}))
    r = await h.client.post("/sessions")
    assert r.status_code == 401
    assert _envelope(r).error_source == "client"
    assert (await h.client.post("/sessions", headers={"X-API-Key": "bad"})).status_code == 401
    sid = await h.session(**{"X-API-Key": "k1"})
    assert (await h.send(sid)).status_code == 401
    assert (await h.send(sid, headers={"X-API-Key": "k1"})).status_code == 200
    assert (await h.client.get("/health")).status_code == 200


def _parse_sse(text: str) -> list[tuple[str, Any]]:
    events = []
    for chunk in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_sse_stream() -> None:
    h = Harness()
    r = await h.send(await h.session(), headers={"Accept": "text/event-stream"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert [name for name, _ in events] == ["text", "tool_call", "text", "result"]
    assert events[1][1] == {"name": "mcp__agent_tools__add", "input": {"a": 2, "b": 3}}
    final = ChatResponse.model_validate(events[-1][1])
    assert final.message == "It is 5." and final.error is False


async def test_sse_reports_backend_error_in_result() -> None:
    async def broken(prompt: str) -> AsyncIterator[AgentEvent]:
        raise CLIConnectionError("cli died")
        yield

    h = Harness(broken)
    sid = await h.session()
    r = await h.send(sid, headers={"Accept": "text/event-stream"})
    ((name, data),) = _parse_sse(r.text)
    assert name == "result"
    assert data["error"] is True and data["error_source"] == "backend"
    assert len(h.manager) == 0

"""REST API front end: server-side sessions, JSON responses and optional SSE streaming.

    POST   /sessions                 -> 201 {"session_id"}
    POST   /sessions/{id}/messages   -> ChatResponse (or text/event-stream when requested)
    DELETE /sessions/{id}            -> 204
    GET    /health

Every /sessions* response body is a ChatResponse envelope, errors included: `message` holds
the assistant's answer, `error` flags failures and `error_source` says whose
("llm" | "backend" | "client").
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from streaming_agent.config import AgentConfig
from streaming_agent.events import (
    AgentEvent,
    LlmError,
    TextDelta,
    ToolCallFinished,
    TurnFinished,
)
from streaming_agent.observability import setup_tracing
from streaming_agent.sessions import Session, SessionLimitError, SessionManager, SessionNotFound

log = logging.getLogger(__name__)

ErrorSource = Literal["llm", "backend", "client"]


# ---- wire models ----------------------------------------------------------------------


class ToolCall(BaseModel):
    name: str
    input: dict[str, Any]


class ChatResponse(BaseModel):
    session_id: str | None = None
    message: str | None = None
    error: bool = False
    error_source: ErrorSource | None = None
    error_detail: str | None = None
    tool_calls: list[ToolCall] = []
    num_turns: int | None = None
    cost_usd: float | None = None


class ChatRequest(BaseModel):
    message: str

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be empty")
        return value


class SessionCreated(BaseModel):
    session_id: str


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        source: ErrorSource,
        detail: str,
        session_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.response = ChatResponse(
            session_id=session_id, error=True, error_source=source, error_detail=detail
        )


# ---- turn -> response (pure) ----------------------------------------------------------


@dataclass
class TurnSummary:
    """Folds a turn's AgentEvents into the final ChatResponse."""

    text: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    llm_errors: list[str] = field(default_factory=list)
    finished: TurnFinished | None = None

    def add(self, event: AgentEvent) -> None:
        match event:
            case TextDelta(text):
                self.text.append(text)
            case ToolCallFinished(name, args):
                self.tool_calls.append(ToolCall(name=name, input=args))
            case LlmError(kind):
                self.llm_errors.append(kind)
            case TurnFinished():
                self.finished = event

    def to_response(self, session_id: str) -> tuple[int, ChatResponse]:
        base = ChatResponse(session_id=session_id, tool_calls=self.tool_calls)
        done = self.finished
        if done is not None:
            base.num_turns = done.num_turns
            base.cost_usd = done.cost_usd
        if self.llm_errors or (done is not None and done.is_error):
            details = [*self.llm_errors]
            if done is not None and done.error_detail:
                details.append(done.error_detail)
            base.error, base.error_source = True, "llm"
            base.error_detail = ", ".join(dict.fromkeys(details)) or "model call failed"
            return 502, base
        if done is None:
            base.error, base.error_source = True, "backend"
            base.error_detail = "turn ended without a result"
            return 500, base
        base.message = done.result if done.result is not None else "".join(self.text)
        return 200, base


async def collect(events: AsyncIterator[AgentEvent]) -> TurnSummary:
    summary = TurnSummary()
    async for event in events:
        summary.add(event)
    return summary


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---- app ------------------------------------------------------------------------------


def create_app(
    config: AgentConfig | None = None, *, manager: SessionManager | None = None
) -> FastAPI:
    config = config or AgentConfig.from_env()
    sessions = manager if manager is not None else SessionManager(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        provider = setup_tracing()
        reaper = asyncio.create_task(sessions.run_reaper())
        try:
            yield
        finally:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
            await sessions.close_all()
            if provider is not None:
                provider.shutdown()

    app = FastAPI(title="streaming-agent", lifespan=lifespan)
    origins = [o.strip() for o in os.getenv("AGENT_CORS_ORIGINS", "").split(",") if o.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"]
        )

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_api_key(key: str | None = Depends(api_key_header)) -> None:
        if not config.api_keys:
            return  # auth off (local dev)
        if key is None or not any(secrets.compare_digest(key, k) for k in config.api_keys):
            raise ApiError(401, "client", "missing or invalid API key")

    def get_session(session_id: str) -> Session:
        try:
            return sessions.get(session_id)
        except SessionNotFound:
            raise ApiError(404, "client", "unknown session", session_id) from None

    async def run_turn(session: Session, prompt: str) -> AsyncIterator[AgentEvent]:
        """The agent's events for one turn, under the turn timeout (TimeoutError past it)."""
        deadline = asyncio.get_running_loop().time() + config.turn_timeout_s
        stream = session.agent.send(prompt)
        try:
            while True:
                try:
                    async with asyncio.timeout_at(deadline):
                        event = await anext(stream)
                except StopAsyncIteration:
                    return
                yield event
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

    def failed(session: Session, exc: Exception) -> tuple[int, ChatResponse]:
        """Map a turn exception to a response; the session's state is unknown, so drop it."""
        sessions.discard(session.id)
        if isinstance(exc, TimeoutError):
            status, detail = 504, f"turn timed out after {config.turn_timeout_s:g}s"
        else:
            log.exception("turn failed in session %s", session.id, exc_info=exc)
            status, detail = 500, f"agent backend error ({type(exc).__name__})"
        return status, ChatResponse(
            session_id=session.id, error=True, error_source="backend", error_detail=detail
        )

    def busy(session: Session) -> ApiError:
        return ApiError(409, "client", "session is busy with another message", session.id)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(exc.response.model_dump(), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(str(e.get("msg", e)) for e in exc.errors()) or "invalid request"
        body = ChatResponse(error=True, error_source="client", error_detail=detail)
        return JSONResponse(body.model_dump(), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        body = ChatResponse(error=True, error_source="client", error_detail=str(exc.detail))
        return JSONResponse(body.model_dump(), status_code=exc.status_code)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "sessions": len(sessions)}

    @app.post(
        "/sessions",
        status_code=201,
        dependencies=[Depends(require_api_key)],
        responses={503: {"model": ChatResponse}},
    )
    async def create_session() -> SessionCreated:
        try:
            session = await sessions.create()
        except SessionLimitError as exc:
            raise ApiError(503, "client", str(exc)) from None
        except Exception as exc:
            log.exception("could not start a session")
            raise ApiError(500, "backend", f"could not start agent ({type(exc).__name__})") from exc
        return SessionCreated(session_id=session.id)

    @app.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(require_api_key)])
    async def delete_session(session_id: str) -> Response:
        try:
            await sessions.close(session_id)
        except SessionNotFound:
            raise ApiError(404, "client", "unknown session", session_id) from None
        return Response(status_code=204)

    @app.post(
        "/sessions/{session_id}/messages",
        dependencies=[Depends(require_api_key)],
        response_model=ChatResponse,
        responses={
            200: {"content": {"text/event-stream": {}}},
            **{s: {"model": ChatResponse} for s in (401, 404, 409, 422, 500, 502, 504)},
        },
    )
    async def send_message(session_id: str, body: ChatRequest, request: Request) -> Response:
        session = get_session(session_id)
        if session.lock.locked():
            raise busy(session)
        if "text/event-stream" in request.headers.get("accept", ""):
            return StreamingResponse(
                stream_turn(session, body.message),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        async with session.lock:
            summary = TurnSummary()
            try:
                async for event in run_turn(session, body.message):
                    summary.add(event)
            except Exception as exc:
                status, response = failed(session, exc)
            else:
                status, response = summary.to_response(session.id)
            sessions.touch(session)
        return JSONResponse(response.model_dump(), status_code=status)

    async def stream_turn(session: Session, prompt: str) -> AsyncIterator[str]:
        if session.lock.locked():  # lost a race with another request since the check
            yield sse("result", busy(session).response.model_dump())
            return
        async with session.lock:
            summary = TurnSummary()
            completed = False
            try:
                async for event in run_turn(session, prompt):
                    summary.add(event)
                    match event:
                        case TextDelta(text):
                            yield sse("text", {"text": text})
                        case ToolCallFinished(name, args):
                            yield sse("tool_call", {"name": name, "input": args})
                _, response = summary.to_response(session.id)
                completed = True
            except Exception as exc:
                _, response = failed(session, exc)
                completed = True
            finally:
                if not completed:  # client disconnected mid-turn: unread messages remain
                    sessions.discard(session.id)
            sessions.touch(session)
            yield sse("result", response.model_dump())

    return app


def main() -> None:
    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(
        create_app(),
        host=os.getenv("AGENT_API_HOST", "127.0.0.1"),
        port=int(os.getenv("AGENT_API_PORT", "8000")),
    )


if __name__ == "__main__":
    main()

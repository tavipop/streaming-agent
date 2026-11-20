from typing import Any

import pytest

from streaming_agent.cli import render
from streaming_agent.events import TurnFinished


def _finished(**kw: Any) -> TurnFinished:
    args: dict[str, Any] = {
        "is_error": False,
        "num_turns": 2,
        "cost_usd": 0.5,
        "session_id": "s",
    }
    return TurnFinished(**(args | kw))


def test_render_normal_turn(capsys: pytest.CaptureFixture[str]) -> None:
    render(_finished())
    out = capsys.readouterr().out
    assert "[done · 2 turn(s), $0.5000]" in out


def test_render_max_turns_reached(capsys: pytest.CaptureFixture[str]) -> None:
    render(_finished(is_error=True, num_turns=4, max_turns_reached=True))
    out = capsys.readouterr().out
    assert "[stopped: reached max turns · 4 turn(s), $0.5000]" in out
    assert "error" not in out


def test_render_error_turn(capsys: pytest.CaptureFixture[str]) -> None:
    render(_finished(is_error=True, cost_usd=None))
    assert "[error · 2 turn(s)]" in capsys.readouterr().out

# ITEM-1: Limit agent turns

## Original request

> I want to limit the number of turns the agent will make in the agent loop. I want to be able to configure this number.
> When the interaction with the agent reaches the maximum number of turns ensure gratefully end for the conversation.

## Story

As an operator of the agent, I want to cap how many agent-loop turns the agent makes per user message and configure that cap,
so that runaway tool loops are bounded and, when the cap is hit, the user gets a graceful reply instead of an error.

## Clarifications (agreed)

- **Turn scope.** A turn is one SDK agent-loop iteration (model call → tool use → model call …) within a single user message.
  Hitting the cap ends only the current reply. The session stays open, and the next message works normally.
- **REST contract (change approved).** When the cap is hit, the response is **200** (previously 502) with `error=false`,
  `error_source=null`, `error_detail=null`, and a new additive field `stop_reason="max_turns"`. On normal completion,
  `stop_reason` is `null`. This applies to the JSON response and to the SSE `result` event. Routes and SSE event names are unchanged.
- **`message` when the cap is hit.** It contains the text produced so far (the SDK `result` if present, else the streamed text).
  Only when there is no text, it contains the notice `Stopped after reaching the maximum of N turns.`, where N is the configured
  `max_turns`. The notice is not appended to existing text.
- **No closing model call.** Reaching the cap does not trigger an extra summarising request.
- **Model error precedence.** If a model error (`AssistantMessage.error`) occurs in the same reply as the cap, the response stays
  502 with `error_source="llm"`, and the turn span is ERROR.
- **Dev dependency.** `pytest-cov` was added so the coverage quality gate can run.

## Acceptance criteria

- **AC1:** Given `AGENT_MAX_TURNS=5`, when the config is loaded, then `max_turns == 5` and `build_options()` passes `max_turns=5`
  to the SDK. Given the variable is unset, the default is 20.
- **AC2:** Given `AGENT_MAX_TURNS` is not an integer or is less than 1, when the config is loaded, then a `ValueError` names
  `AGENT_MAX_TURNS` and the bad value.
- **AC3:** Given the SDK returns a `ResultMessage` with `subtype="error_max_turns"` or `terminal_reason="max_turns"`, when it is
  mapped, then `TurnFinished.max_turns_reached` is `True`. `events.py` stays pure.
- **AC4:** Given a REST JSON turn reaches the cap, when the response is built, then it is 200 with `error=false`, `message` set to
  the partial text or the fallback notice, `stop_reason="max_turns"` and `num_turns`. The session stays usable.
- **AC5:** Given an SSE turn reaches the cap, when it ends, then the final `result` event has the same envelope as AC4.
- **AC6:** Given a turn completes normally, then `stop_reason` is `null` and nothing else in the response changes.
- **AC7:** Given the CLI hits the cap, then it prints a stopped notice (`[stopped: reached max turns · …]`) instead of `[error …]`,
  and the REPL keeps running.
- **AC8:** Given tracing is on and the cap is hit, then the turn span has `agent.max_turns_reached=true` and
  `agent.max_turns=<limit>`, and its status is not ERROR unless a model error also occurred. Prompt and response content are
  recorded only when `AGENT_TRACE_CONTENT=1`.
- **AC9:** Given the cap was hit, when the next message is sent in the same session, then it succeeds.

## Out of scope

- Limiting the number of user messages per conversation.
- Closing the session or exiting the CLI when the cap is hit.
- A model-generated closing summary.

## Status

Implemented in `3bc2782`. QA verdict: PASS (2 rounds).
Open: AC9 against the real CLI is verified by SDK source analysis and offline tests only. Run `uv run pytest -m live`
(`test_session_continues_after_max_turns`) with `ANTHROPIC_API_KEY` set before release.

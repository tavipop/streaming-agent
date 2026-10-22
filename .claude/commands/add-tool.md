---
description: Add a new custom tool to the agent
argument-hint: <tool name> — <what it should do>
---

Add a new tool to the agent: **$ARGUMENTS**

1. In `src/streaming_agent/tools.py`, write an async function decorated with
   `@tool(name, description, input_schema)`. Keep the description specific — it is what the
   model reads to decide when to call the tool. Return `{"content": [{"type": "text", "text": ...}]}`;
   on failure return the same shape with `"is_error": True` instead of raising.
2. Append it to `TOOLS` (it is auto-allowed as `mcp__agent_tools__<name>`).
3. Add unit tests in `tests/test_tools.py` that call `<fn>.handler({...})` directly,
   covering the happy path and one bad input. No network in unit tests.
4. Run `/check`.

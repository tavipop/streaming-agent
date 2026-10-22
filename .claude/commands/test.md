---
description: Generate tests with the test-writer subagent
argument-hint: [file, function or "changes"]
---

Use the test-writer subagent to write tests for: $ARGUMENTS

If no target is given, cover the uncommitted changes (git diff).
When it finishes, show me its summary and any bugs it flagged.
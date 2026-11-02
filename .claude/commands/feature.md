---
description: Run a requirement through the developer → QA pipeline defined in CLAUDE.md
argument-hint: <requirement text or path to a spec file>
---

Run this requirement through the agent workflow in CLAUDE.md section 5:

$ARGUMENTS

Steps:
1. Delegate to the `developer` subagent with the full requirement.
2. If it returns CLARIFICATIONS NEEDED, ask me those questions, then resume `developer` with my answers.
3. When `developer` reports READY_FOR_QA with all gates green, delegate to the `qa` subagent with the
   requirement, the agreed clarifications, and the developer's report.
4. On QA FAIL, send the defects to `developer` and repeat QA. Stop after 3 failed rounds and report to me.
5. Finish with a summary: decisions made, files changed, quality-gate table, QA verdict, open risks.

---
description: Run a requirement through the developer → review → QA → PR pipeline defined in CLAUDE.md
argument-hint: <requirement text or path to a spec file>
---

Run this requirement through the "Development workflow" in CLAUDE.md:

$ARGUMENTS

Steps:
1. Working tree check. Run `git status --short` and `git branch --show-current`. Ignore known
   unrelated untracked files (e.g. `.claude/skills/coverage-audit/`).
   - On `main`/`master` with other changes: stop and ask me how to proceed.
   - On a feature branch for this requirement (resuming after an interruption or a stopped round):
     its changes are in-progress work. Continue on it and tell `developer` the branch already has work
     (`git status`, `git diff`), including any open review/QA findings.
   - On any other branch, clean or with changes: stop and ask me whether to switch to `main` and
     branch from there (`git switch main`; then step 4 applies) or to continue on the current branch.
2. Analysis. Delegate to the `developer` subagent with the full requirement, and tell it to run
   Phases 1–2 only (analysis and plan, no file edits) and return either CLARIFICATIONS NEEDED or a
   plan-only reply containing: the acceptance criteria, files to change, test cases per AC, whether
   the live gate applies, risks, and the proposed Conventional Commit type/scope and branch slug
   (`<type>/<kebab-slug>`, see "Pull requests" in CLAUDE.md).
3. If it returns CLARIFICATIONS NEEDED, ask me those questions and resume `developer` (still analysis
   only) with my answers until nothing blocking is open.
4. Branch. If on `main`/`master`, create the feature branch with `git switch -c`, using the branch
   slug and type from the plan-only reply. This happens
   after clarifications and before any file is changed. If resuming, keep the current branch.
5. Implementation. Resume `developer` to implement. Do not touch other feature files than the ones indicated.
6. Review. When `developer` reports READY_FOR_QA with gates 1–7 green (or 7 justified as not run),
   delegate to the `reviewer` subagent with the requirement, the agreed clarifications, and the
   developer's change list and quality-gate table. If the change touches `agent.py`,
   `build_options()`, `events.py` or `tools.py`, also run `sdk-reviewer`. Review passes only when:
   - `reviewer` returns APPROVE (its "Blocking" list is empty), and
   - `sdk-reviewer` (if run) reports no findings. `sdk-reviewer` has no verdict, and its checklist only
     covers streaming correctness, SDK misuse, tool safety and missing offline tests, so treat every
     finding it reports as blocking.
   Otherwise send all blocking findings to `developer` verbatim (the developer may rebut a finding with
   evidence; the reviewer re-checks it in the next round) and repeat the review.
7. QA. When review passes, delegate to the `qa` subagent with the requirement, the agreed
   clarifications, and the developer's change list (not the developer's or reviewers' opinion).
8. On QA FAIL, send the defects to `developer`, then repeat review and QA. Review and QA rejections
   share one cap: stop after 3 failed rounds in total and report to me. No PR is opened.
9. PR. When QA is PASS and every gate is green (gate 7 may be NOT RUN with a justified reason),
   run `/open-pr` with the evidence for its precondition (gate table, review and QA verdicts). It shows me the branch, commit
   message, file list and PR title/body, and only commits, pushes and opens (or updates) the PR after I confirm.
10. Finish with a summary: decisions made, files changed, quality-gate table, review verdict,
    QA verdict, PR URL (or the manual commands if the PR step could not complete), open risks.

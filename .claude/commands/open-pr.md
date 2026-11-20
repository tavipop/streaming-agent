---
description: Commit the finished change on a feature branch, push it and open (or update) a GitHub PR
argument-hint: [short description or path to the /feature summary]
---

Open a pull request for the current change, following "Pull requests" in CLAUDE.md.

$ARGUMENTS

Run this as the last step of `/feature`, or standalone when the user asks for a PR. Never run it
for a change that has a red quality gate (gate 7 may be NOT RUN with a justified reason), a QA verdict
other than PASS, or a failed review (see `/feature` step 6). In that case stop and report why.

Evidence for this precondition must come from one of:
- the `/feature` summary of this change (gate table, review verdict, QA verdict), or
- standalone: a fresh `/check` run with gates 1–6 PASS, plus the user's explicit statement of the
  review and QA outcome.

If neither is available, stop and report what is missing. Don't infer success.

## 1. Preflight (read-only)
1. `git status --short` and `git branch --show-current`.
2. Work out the file list: only files changed for this requirement (the developer's change list).
   Never include `.env*`, secrets, `tmp/`, or untracked files that aren't part of the change
   (e.g. `.claude/skills/coverage-audit/`). If unrelated tracked files are modified, stop and ask.
3. Branch: if the current branch is `main`/`master`, plan a new branch
   `<type>/<kebab-slug>` (add the ticket id when there is one, e.g. `feat/item-3-graph-row-cap`),
   where `<type>` is one of `feat|fix|chore|docs|refactor|test|ci`. Otherwise reuse the current branch.
4. Remote: `gh auth status` and `git remote get-url origin`. If either fails, the run will be
   **local commit only**: skip step 5 and, after confirmation, finish with section 4 (Fallback).
5. Existing PR: `gh pr list --head <branch> --state open --json number,url`, where `<branch>` is the
   branch from step 3 (the planned name for a new branch, never `main`). An open PR means update,
   not create.

## 2. Confirm once with the user
Show, and ask for a single yes/no before anything is written:
- the mode: **create PR**, **update PR #<n>**, or **local commit only** (with the reason: gh not
  authenticated / no `origin`)
- branch name (new or existing) and base `main`
- commit message (Conventional Commits, see CLAUDE.md), ending with the commit attribution trailer
- exact file list to stage
- PR title (= commit subject) and the PR body: every section of `.github/pull_request_template.md`
  filled in (summary, AC → tests, clarifications, changes, quality gates 1–8, review verdict,
  QA verdict, decisions and risks, manual verification), ending with the PR attribution footer
- label (`feat` → `enhancement`, `fix` → `bug`, `docs` → `documentation`, otherwise none) and assignee `@me`

The attribution trailer and footer are defined under "Pull requests" in CLAUDE.md.
If the user declines, change nothing and report what would have been done.

## 3. Execute (only after "yes")
1. `git switch -c <branch>` if a new branch is needed.
2. `git add -- <file list>` (explicit paths, never `git add -A` / `git add .`).
3. `git commit` with the agreed message (use a heredoc to keep the body intact).
4. Local commit only: go to section 4 (Fallback).
5. `git push -u origin <branch>`. Never push to `main`; the PreToolUse hook blocks it anyway.
6. Create PR: `gh pr create --base main --head <branch> --title "<title>" --body-file <file>
   --assignee @me [--label <label>]`. Write the body file to the session scratchpad, not the repo.
   Update PR: the push updates it. Refresh the body with `gh pr edit <number> --body-file <file>` and
   add a `gh pr comment` summarising what changed since the last push.
7. Report the PR URL, the branch, the commit SHA, and that the CI and Claude Code Review workflows
   will run on the PR.

## 4. Fallback (gh not authenticated, no `origin`, or push / PR creation fails)
Stop. Don't retry or prompt for credentials. Keep the local branch and commit. Report the error and
the exact commands for the user to run manually (`gh auth login`, `git push -u origin <branch>`,
`gh pr create ...` with the same title, body file, label and assignee).

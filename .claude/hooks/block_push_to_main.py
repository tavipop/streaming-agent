#!/usr/bin/env python3
"""PreToolUse hook: block any `git push` from Claude that would update a protected branch.

Catches:  git push origin main | git push (while on main) | git push origin HEAD:main
          git push -f origin +main | git push origin feature:refs/heads/main
          git push origin :main / --delete main | git push --all / --mirror
          git -C some/dir push ... | chained commands (a && git push ...)

Protected branches: $CLAUDE_PROTECTED_BRANCHES (comma-separated), default "main,master".
Exit 2 + stderr = block the tool call and tell Claude why. Exit 0 = allow.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

PROTECTED = {
    b.strip()
    for b in os.environ.get("CLAUDE_PROTECTED_BRANCHES", "main,master").split(",")
    if b.strip()
}

# git push options that take a separate value argument (`--repo origin`, `-o ci.skip`).
OPTS_WITH_VALUE = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
# Options that push every branch, protected ones included.
PUSH_ALL = {"--all", "--mirror", "--branches"}
# git global options that take a separate value (`git -C dir push`).
GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}

SEPARATORS = re.compile(r"&&|\|\||[;|\n&]")


def current_branch(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return out.stdout.strip() or None


def short(ref: str) -> str:
    return ref.removeprefix("refs/heads/")


def blocked_reason(tokens: list[str], cwd: str) -> str | None:
    """Return why this git invocation is blocked, or None if it's fine / not a push."""
    if not tokens or os.path.basename(tokens[0]) != "git":
        return None

    # Skip git's global options to find the subcommand.
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        if tokens[i] in GIT_GLOBAL_WITH_VALUE and i + 1 < len(tokens):
            if tokens[i] == "-C":
                cwd = os.path.join(cwd, tokens[i + 1])
            i += 2
        else:
            i += 1
    if i >= len(tokens) or tokens[i] != "push":
        return None

    positionals: list[str] = []
    args = tokens[i + 1 :]
    j = 0
    while j < len(args):
        arg = args[j]
        if arg == "--":
            positionals.extend(args[j + 1 :])
            break
        if arg in PUSH_ALL:
            branches = ", ".join(sorted(PROTECTED))
            return f"`git push {arg}` would push protected branches ({branches})"
        if arg in OPTS_WITH_VALUE:
            j += 2
            continue
        if arg.startswith("-"):
            j += 1
            continue
        positionals.append(arg)
        j += 1

    refspecs = positionals[1:]  # positionals[0] is the remote
    branch = current_branch(cwd)

    if not refspecs:
        if branch in PROTECTED:
            return f"`git push` with no refspec while on `{branch}` pushes to `{branch}`"
        return None

    for spec in refspecs:
        spec = spec.lstrip("+")
        src, _, dst = spec.partition(":")
        target = dst if dst else src
        if target == "HEAD":
            target = branch or "HEAD"
        if short(target) in PROTECTED:
            return f"refspec `{spec}` updates protected branch `{short(target)}`"
    return None


def check(command: str, cwd: str) -> str | None:
    for segment in SEPARATORS.split(command):
        if "push" not in segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Unparseable (e.g. unbalanced quotes): be conservative.
            if re.search(r"\bgit\b.*\bpush\b", segment) and any(
                re.search(rf"\b{re.escape(b)}\b", segment) for b in PROTECTED
            ):
                return "could not parse this push command safely"
            continue
        # Drop leading env assignments: `FOO=1 git push ...`
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
        reason = blocked_reason(tokens, cwd)
        if reason:
            return reason
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    reason = check(command, cwd)
    if reason:
        print(
            f"Blocked: {reason}. Pushing to protected branches is not allowed from Claude Code. "
            "Push to a feature branch (e.g. `git push -u origin HEAD:feature/<name>`) "
            "and open a pull request instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

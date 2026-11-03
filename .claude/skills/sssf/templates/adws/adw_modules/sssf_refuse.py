#!/usr/bin/env python3
"""SSSF's refuse mode for a repo's agent_rules, on Claude Code agents.

Claude Code agents run with --safe-mode, which ignores every hook, so the pi
extension's way (sssf_rules.ts) isn't open to them. Safe mode does keep the
CLAUDE_CODE_SHELL_PREFIX environment variable: Claude Code runs every Bash
call as `<prefix> '<its shell line>'`. agent_cc points that variable at this
script and passes the rules marked `on_match: refuse` in SSSF_REFUSE_RULES.

A command matching one of their `forbid` patterns is refused: the rule goes
to stderr, the script exits 126 and nothing runs. The agent reads "Exit code
126" and the refusal as the call's result, and the run goes on. Anything
else runs as Claude Code meant it to, through an operator's own prefix if
they had one (SSSF_NEXT_SHELL_PREFIX).

Claude Code wraps the agent's command in its own shell line, which ends
`&& eval '<command>' < /dev/null && pwd -P ...`. The patterns are searched in
that command. A line in another shape is searched whole, so a change in
Claude Code's wrapping still refuses, at worst on a false match. SSSF's
stream check stays the backstop either way.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

REFUSED = "SSSF refused this command"     # rules.REFUSED; the stream check looks for it
EXIT = 126
EVAL = re.compile(r"&& eval (.*) < /dev/null && pwd -P", re.S)


def command(line: str) -> str:
    """The agent's own command inside Claude Code's shell line."""
    found = EVAL.search(line)
    if found:
        try:
            return " ".join(shlex.split(found.group(1)))
        except ValueError:
            pass
    return line


def refusal(line: str, rules: list[dict]) -> str | None:
    text = command(line)
    for rule in rules:
        for pattern in rule.get("forbid") or []:
            try:
                hit = re.search(pattern, text)
            except re.error:
                continue
            if hit:
                return (f"{REFUSED}, by this repo's rule: {rule.get('rule')} (it matches "
                        f"{json.dumps(pattern)}). Nothing ran. Don't try it another way: do the "
                        "task without it, or say in your report that the task needs it.")
    return None


def main(argv: list[str]) -> int:
    line = argv[1] if len(argv) > 1 else ""
    try:
        rules = json.loads(os.environ.get("SSSF_REFUSE_RULES") or "[]")
    except json.JSONDecodeError:
        rules = []
    reason = refusal(line, rules)
    if reason:
        sys.stderr.write(reason + "\n")
        return EXIT
    following = os.environ.get("SSSF_NEXT_SHELL_PREFIX")
    if following:
        os.execvp(following, [following, line])
    os.execvp("bash", ["bash", "-c", line])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

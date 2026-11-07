#!/usr/bin/env python3
"""SSSF's refuse mode for a repo's agent_rules, and SSSF's built-in secret-file rule, on Claude Code agents.

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

SSSF's stream check counts this script's refusal by its text, since Claude
Code's stream gives it the same result as a command that printed it and exited
126. A forged one needs this script to miss a command the stream check
matches; both read the same command with Python `re`, so that needs a change in
how Claude Code wraps a command (the shape `commands()` unwraps) or a Bash call
that skips the prefix. `docs/open.md` in SSSF's source repo watches for it.

Claude Code wraps the agent's command in its own shell line, which ends
`&& eval '<command>' < /dev/null && pwd -P ...`. The patterns are searched in
that command. A line in another shape is searched whole, and so is each
`eval '...'` argument in it, unquoted, so a pattern anchored to the start of
a command still finds one. Keep this docstring free of backslashes: a
warning on stderr would come before the refusal and hide it. A change in Claude
Code's wrapping can still make a pattern miss, and SSSF's stream check stays
the backstop either way. SSSF's built-in secret-file rule comes marked
`builtin`, and its refusal names it as SSSF's.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

REFUSED = "SSSF refused this command"     # rules.REFUSED; the stream check reads it after the exit line
TAIL = ("Nothing ran. Don't try it another way: do the task without it, or say in your "
        "report that the task needs it.")
EXIT = 126
EVAL = re.compile(r"&& eval (.*) < /dev/null && pwd -P", re.S)
ANY_EVAL = re.compile(r"\beval\s+('(?:[^']|'\\'')*')")   # one single-quoted eval argument


def _unquote(word: str) -> str | None:
    try:
        return " ".join(shlex.split(word))
    except ValueError:
        return None


def commands(line: str) -> list[str]:
    """The agent's own command inside Claude Code's shell line or, for a line
    in another shape, the whole line and every eval argument in it."""
    found = EVAL.search(line)
    if found:
        own = _unquote(found.group(1))
        if own is not None:
            return [own]
    return [line, *filter(None, (_unquote(m.group(1)) for m in ANY_EVAL.finditer(line)))]


def refusal(line: str, rules: list[dict]) -> str | None:
    texts = commands(line)
    for rule in rules:
        for pattern in rule.get("forbid") or []:
            try:
                hit = any(re.search(pattern, text, re.M) for text in texts)
            except re.error:
                continue
            if hit:
                if rule.get("builtin"):
                    return f"{REFUSED}, by SSSF's secret-file rule: {rule.get('rule')} {TAIL}"
                return (f"{REFUSED}, by this repo's rule: {rule.get('rule')} (it matches "
                        f"{json.dumps(pattern)}). {TAIL}")
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

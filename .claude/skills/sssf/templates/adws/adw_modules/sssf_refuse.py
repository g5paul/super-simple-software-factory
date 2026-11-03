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
that command. A line in another shape is searched whole, and so is each
`eval '...'` argument in it, unquoted, so a pattern anchored to the start of
a command still finds one. Text a heredoc only feeds to a file, such as a plan written with cat, is
not run, so it is dropped before the search (executed below). A heredoc fed
to a shell or an interpreter is kept, since it runs. Keep this docstring free of backslashes: a
warning on stderr would come before the refusal and hide it. A change in Claude
Code's wrapping can still make a pattern miss, and SSSF's stream check stays
the backstop either way.
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
ANY_EVAL = re.compile(r"\beval\s+('(?:[^']|'\\'')*')")   # one single-quoted eval argument


# A heredoc operator, not a here-string: <<WORD, <<-WORD, <<'WORD', <<"WORD".
HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_.-]*)\2")
# Commands that run what a heredoc feeds them, so its body is searched.
RUNS_STDIN = {"sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "source", ".", "xargs",
              "python", "python3", "node", "bun", "deno", "perl", "ruby", "php", "osascript"}
WRAPPERS = {"env", "sudo", "nohup", "command", "exec", "time", "nice", "timeout", "uv", "npx",
            "bunx", "run"}


def _feeds_a_runner(before: str) -> bool:
    """Whether the command a heredoc belongs to runs its input, judged by the
    command word in front of the operator. Unknown means yes."""
    words = re.split(r"[;&|(){}]", before)[-1].split()
    words = [w for w in words if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)
             and not re.match(r"^-|^[0-9]+[smhd]?$", w)]
    while words and words[0].rsplit("/", 1)[-1] in WRAPPERS:
        words = words[1:]
    return not words or words[0].rsplit("/", 1)[-1] in RUNS_STDIN


def executed(command: str) -> str:
    """The command without the bodies of heredocs that only feed a file or a
    non-runner, such as `cat > plan.md <<EOF`. Those lines are text, not
    commands: a plan that says "never run X" must not count as running X."""
    out: list[str] = []
    pending: list[tuple[str, bool, bool]] = []   # (word, strips tabs, keep body)
    for line in command.split("\n"):
        if pending:
            word, tabs, keep = pending[0]
            if (line.lstrip("\t") if tabs else line) == word:
                pending.pop(0)
                out.append(line)
            elif keep:
                out.append(line)
            continue
        out.append(line)
        for found in HEREDOC.finditer(line):
            pending.append((found.group(3), found.group(1) == "-",
                            _feeds_a_runner(line[:found.start()])))
    return "\n".join(out)


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
    texts = [executed(text) for text in commands(line)]
    for rule in rules:
        for pattern in rule.get("forbid") or []:
            try:
                hit = any(re.search(pattern, text) for text in texts)
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

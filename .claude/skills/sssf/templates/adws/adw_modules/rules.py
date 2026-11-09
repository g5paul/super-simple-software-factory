"""The repo's agent_rules, enforced on the commands agents run.

A rule in repo.yaml is either a plain string, which every agent is told and
nothing checks, or a mapping with `forbid`: regexes searched in every shell
command an agent runs.

    agent_rules:
      - Keep commit subjects under 72 characters.
      - rule: Never run `git push`. Commit locally only.
        forbid: ['\\bgit\\b(\\s+-[cC]\\s+\\S+|\\s+--?[\\w-]+(=\\S+)?)*\\s+push\\b']

The check reads the stream as each call is announced, before its result
arrives. On a match, SSSF kills the agent and every process under it, so the
command is often stopped before it runs or part way through. Not always: the
harness may have started it already. So a breach stops the run and says the
command may have run, the same way `writes` reports a write it caught after
the fact.

`on_match: refuse` asks for the softer mode: the command is blocked before it
runs, the agent reads the rule as the tool's result, and the run goes on. On
pi, SSSF's own extension (sssf_rules.ts) blocks it. Claude Code agents run
with --safe-mode, which ignores every hook, so their Bash calls go through
SSSF's shell prefix (sssf_refuse.py) instead. The stream check stays as the
backstop: on pi a matching call counts as refused only when pi's result shows
it never ran the call (`blocked`) and reads as the refusal; on Claude Code when
its result is the prefix's refusal or Claude Code denied it itself (a
`permission_denied` event for its id). Anything else is a breach, and so is a
matching call still waiting for its result when the agent ends. The check
reads only the running harness's own event types (`agents.RULE_EVENT_TYPES`),
so a line of the other harness's type that a command writes to the stream
counts for nothing.

So every `forbid` pattern must read the same in both languages: the settings fail to load on Python syntax JavaScript reads differently or can't compile (`_python_only`).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

# The shell tool's name in each harness: Claude Code's `Bash`, pi's `bash`.
SHELL_TOOLS = {"Bash", "bash"}

# How sssf_rules.ts and sssf_refuse.py start a shell refusal. On pi the stream
# check reads this text only together with `blocked`; on Claude Code it reads it
# after the exit line, which Claude Code puts first. A pi file-tool
# refusal says "this <tool> call" instead, and never reaches the stream check:
# only shell calls are.
REFUSED = "SSSF refused this command"
BUILTIN_RULE = "SSSF's secret-file rule"   # how a refusal or breach names secret_files.shell_rule's rule
# A backslash-newline and any backslashes right before it. A shell drops the
# pair; the rest of the run matters when an outer level of quoting unescapes it
# first. The lookbehind keeps a long run of backslashes linear. sssf_refuse.py
# and sssf_rules.ts use the same pattern.
_CONTINUATION = re.compile(r"(?<!\\)\\+\n")
# The same and the tabs right after it. A `<<-` heredoc strips a body line's
# leading tabs before the inner shell joins the lines, so that shell reads no tab
# there. Outside a `<<-` body the shell reads that tab as a word break, so
# _CONTINUATION's text stays too. sssf_refuse.py and sssf_rules.ts use the same pattern.
_CONTINUATION_TABS = re.compile(r"(?<!\\)\\+\n\t*")
_CLAUDE_REFUSED = re.compile(r"Exit code 126\n" + re.escape(REFUSED))
# An agent refused this many times in one call is looping: stop the run.
MAX_REFUSALS = 3


class RefusalTally:
    """The refusals one send has counted, from the text guard and from agentsh's
    exec denials alike. Thread-safe: agentsh's denials arrive on a poll thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._total = 0

    def add(self) -> int:
        with self._lock:
            self._total += 1
            return self._total

    @property
    def total(self) -> int:
        with self._lock:
            return self._total

# Python regex syntax that pi's extension, which reads a pattern as
# new RegExp(pattern, "m") (sssf_rules.ts), can't compile or reads
# differently: (what it is, how to write it portably). Each one is shown
# disagreeing under bun in tests/test_rules_portable.py.
_ESCAPES = {
    "A": ("`\\A`", "use `^`; with the m flag both read it as the start of a line"),
    "Z": ("`\\Z`", "use `$`"),
    "z": ("`\\z`", "use `$`"),
    "a": ("`\\a`", "use `\\x07`"),
    "U": ("a `\\U` escape", "use `\\xhh`, `\\uhhhh` or the character itself"),
    "N": ("a `\\N{...}` escape", "use `\\xhh`, `\\uhhhh` or the character itself"),
}
_GROUPS = [   # what follows "(?", checked in order
    ("P<", "a named group `(?P<name>...)`", "use a plain group `(...)`"),
    ("P=", "a named backreference `(?P=name)`", "use a plain group and a numbered backreference such as `\\1`"),
    ("#", "a comment group `(?#...)`", "drop the comment"),
    ("(", "a conditional group `(?(...)...)`", "use alternation with `|`"),
    (">", "an atomic group `(?>...)`", "use `(?:...)`"),
]
_PORTABLE_GROUPS = (":", "=", "!", "<=", "<!")
_FLAGS = ("an inline flag group such as `(?i)` or `(?i:...)`",
          "spell the flag out, e.g. `[Gg]it` for i, no spaces for x, `[\\s\\S]` for s's `.`")
_POSSESSIVE = ("a possessive quantifier such as `++` or `*+`", "drop the trailing `+`")
_EMPTY_MIN = ("a quantifier with no minimum, `{,n}`", "write `{0,n}`")
_CLASS_BRACKET = ("a character class opening with `]`, such as `[]x]`", "escape it: `[\\]x]`")
_BRACES = re.compile(r"\{(\d*)(,\d*)?\}")


def _python_only(pattern: str) -> Optional[tuple[str, str]]:
    """(construct, portable spelling) of the first Python-only syntax in a valid pattern, or None."""
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\":
            if i + 1 < n and pattern[i + 1] in _ESCAPES:
                return _ESCAPES[pattern[i + 1]]
            i += 2
        elif in_class:
            in_class = c != "]"
            i += 1
        elif c == "[":
            i += 1
            if pattern[i:i + 1] == "^":
                i += 1
            if pattern[i:i + 1] == "]":
                return _CLASS_BRACKET
            in_class = True
        elif c == "(" and pattern[i + 1:i + 2] == "?":
            rest = pattern[i + 2:]
            if rest.startswith(_PORTABLE_GROUPS):
                i += 2
                continue
            for prefix, construct, fix in _GROUPS:
                if rest.startswith(prefix):
                    return construct, fix
            return _FLAGS
        elif c in "*+?":
            if pattern[i + 1:i + 2] == "+":
                return _POSSESSIVE
            i += 1
        elif c == "{":
            m = _BRACES.match(pattern, i)
            if not m:
                i += 1
                continue
            if not m.group(1) and m.group(2) is not None:
                return _EMPTY_MIN
            if m.group(1) and pattern[m.end():m.end() + 1] == "+":
                return _POSSESSIVE
            i = m.end()
        else:
            i += 1
    return None


class RuleBreach(RuntimeError):
    """An agent ran a command one of this repo's agent_rules, or SSSF's built-in secret-file rule, forbids."""

    def __init__(self, agent: str, rule: "AgentRule", pattern: str, command: str,
                 refusals: int = 0, *, builtin: bool = False):
        self.agent, self.rule, self.pattern, self.command = agent, rule.rule, pattern, command
        self.refusals = refusals
        self.builtin = builtin
        whose = BUILTIN_RULE if builtin else "this repo's rule"
        self.partial = None         # the AgentResult so far, set by the interface that killed it
        if refusals:
            super().__init__(
                f"SSSF refused {agent} {refusals} times for {whose}: {rule.rule} "
                f"(matched {pattern!r}; last `{command[:300]}`). Nothing refused ran. SSSF "
                "stopped the agent so it doesn't keep trying: give it another way, or do "
                "that step yourself.")
        else:
            super().__init__(
                f"{agent} ran `{command[:300]}`, which {whose} forbids: {rule.rule} "
                f"(matched {pattern!r}). SSSF killed the agent and everything it started, but "
                "the command may have run first: check what it did before resuming.")


class AgentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str                       # what every agent is told
    forbid: list[str] = []          # regexes; a command matching any one stops the run
    on_match: Literal["stop", "refuse"] = "stop"    # refuse: blocked before it runs, see above

    @field_validator("forbid")
    @classmethod
    def _compiles(cls, patterns: list[str]) -> list[str]:
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"forbid pattern {pattern!r} is not a valid regex: {error}")
            found = _python_only(pattern)
            if found:
                construct, fix = found
                raise ValueError(f"forbid pattern {pattern!r} uses {construct}, which pi's extension (sssf_rules.ts) "
                                 f"reads as a JavaScript regex and can't compile or reads differently, so pi wouldn't "
                                 f"refuse the command. Write it portably: {fix}.")
        return patterns


def text(rule: str | AgentRule) -> str:
    """The rule as the agent reads it."""
    return rule if isinstance(rule, str) else rule.rule


def commands(event: dict) -> list[str]:
    return [command for _, command in calls(event)]


def calls(event: dict) -> list[tuple[str, str]]:
    """Every shell call an event announces, as (call id, command), in either harness's stream.

    Claude Code announces a call as a `tool_use` block in an `assistant` event,
    a subagent's calls included. Pi announces it as a `toolCall` block at
    `message_end`, and again at `tool_execution_start`; seeing it twice is
    harmless, since the first match stops the run.
    """
    etype = event.get("type")
    found: list[tuple[str, str, dict]] = []
    if etype in ("assistant", "message_end"):
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                found.append((block.get("id"), block.get("name"), block.get("input") or {}))
            elif block.get("type") == "toolCall":
                found.append((block.get("id"), block.get("name"), block.get("arguments") or {}))
    elif etype == "tool_execution_start":
        found.append((event.get("toolCallId"), event.get("toolName"), event.get("args") or {}))
    return [(str(call_id or args.get("command")), str(args.get("command")))
            for call_id, tool, args in found
            if tool in SHELL_TOOLS and isinstance(args, dict) and args.get("command")]


def results(event: dict) -> list[tuple[str, str]]:
    """Every tool result an event carries, as (call id, text). Pi reports one at
    tool_execution_end and again as a toolResult message; Claude Code as a
    tool_result block in a user event."""
    etype = event.get("type")
    if etype == "tool_execution_end":
        result = event.get("result")
        content = result.get("content") if isinstance(result, dict) else result
        return [(str(event.get("toolCallId")), _text(content))]
    message = event.get("message") or {}
    if etype == "message_end" and message.get("role") == "toolResult":
        return [(str(message.get("toolCallId")), _text(message.get("content")))]
    if etype == "user":
        return [(str(block.get("tool_use_id")), _text(block.get("content")))
                for block in message.get("content") or []
                if isinstance(block, dict) and block.get("type") == "tool_result"]
    return []


def denied(event: dict) -> list[str]:
    """The call id in a Claude Code `permission_denied` system event: Claude
    Code denied that call itself, by its own permission rules, before it ran
    (claude 2.1.319 sends it right after the call's tool_use). Matched by id
    only; the event's `message` is never read."""
    if event.get("type") != "system" or event.get("subtype") != "permission_denied":
        return []
    call_id = event.get("tool_use_id")
    return [call_id] if isinstance(call_id, str) and call_id else []


def pi_result(event: dict) -> bool:
    """Whether an event is a pi tool result: `tool_execution_end`, or a
    `message_end` for a toolResult message. A Claude Code result (a `user`
    event) is not one."""
    return event.get("type") == "tool_execution_end" or (
        event.get("type") == "message_end"
        and (event.get("message") or {}).get("role") == "toolResult")


def blocked(event: dict) -> list[str]:
    """The call id in a pi result for a call pi never ran. pi 1.4.0's agent loop
    (pi-agent-core/dist/agent-loop.js) sets `durationMs` on every call it ran
    (executePreparedToolCall) and on none it didn't: those are prepareToolCall's
    immediate results (a `tool_call` block, validation error, unknown tool, abort)
    and the output-token-limit "was not executed" result. The agent can't set or
    clear it. Claude Code's stream has no such field, so this returns nothing for
    it and the stream check reads its results by text alone."""
    if not pi_result(event):
        return []
    body = event if event["type"] == "tool_execution_end" else event["message"]
    if body.get("isError") is True and "durationMs" not in body:
        return [str(body.get("toolCallId"))]
    return []


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") if isinstance(part, dict) else str(part)
                         for part in content)
    return str(content or "")


def refused(text: str) -> bool:
    """Whether a call's result text reads as SSSF's refusal, from either harness.
    It reads only the text, which the agent controls: on pi the stream check also
    needs `blocked`; on Claude Code the text alone counts (see sssf_refuse.py)."""
    return text.startswith(REFUSED) or bool(_CLAUDE_REFUSED.match(text))


def refusing(rules: list, builtin: Optional["AgentRule"] = None) -> list[dict]:
    """The refuse rules, as sssf_rules.ts and sssf_refuse.py read them from
    SSSF_REFUSE_RULES. `builtin` (secret_files.shell_rule's rule, by identity)
    is marked so the refusal names it as SSSF's."""
    return [{"rule": rule.rule, "forbid": rule.forbid, **({"builtin": True} if rule is builtin else {})}
            for rule in rules
            if isinstance(rule, AgentRule) and rule.on_match == "refuse" and rule.forbid]


def breach(rules: list, command: str) -> Optional[tuple[AgentRule, str]]:
    """The first enforced rule and pattern this command breaks, or None.

    It searches the whole command, heredoc bodies included. `^` and `$` match
    at every line, as a shell reads them, so a pattern anchored to a command's
    start finds one on any line. sssf_refuse.py and sssf_rules.ts search the
    same way. Text an agent means only as text belongs in a file written with
    its file tool, which no rule reads.

    It searches the command as written, again with backslash-newlines removed,
    as a shell reads them, and again with the tabs right after them removed too,
    as the inner shell of a `<<-` heredoc reads them. A match in any of the
    three counts.
    """
    texts = (command, _CONTINUATION.sub("", command), _CONTINUATION_TABS.sub("", command))
    for rule in rules:
        if isinstance(rule, AgentRule):
            for pattern in rule.forbid:
                if any(re.search(pattern, text, re.M) for text in texts):
                    return rule, pattern
    return None


def stop_tree(pid: int) -> None:
    """Kill a process and everything under it: the agent, the shell running
    the command, and whatever that shell started. Killing only the agent would
    leave its shell running the forbidden command."""
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="],
                                 capture_output=True, text=True).stdout
    except OSError:
        listing = ""
    children: dict[int, list[int]] = {}
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    tree, queue = [], [pid]
    while queue:
        current = queue.pop()
        tree.append(current)
        queue.extend(children.get(current, []))
    for each in tree:               # the agent first, so it starts nothing new
        try:
            os.kill(each, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

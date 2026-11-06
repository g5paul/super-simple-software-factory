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
backstop: a matching call whose result isn't that refusal, and that Claude Code
didn't deny itself (a `permission_denied` event for its id), ran, and is a
breach.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

# The shell tool's name in each harness: Claude Code's `Bash`, pi's `bash`.
SHELL_TOOLS = {"Bash", "bash"}

# How sssf_rules.ts and sssf_refuse.py start a shell refusal, so the stream check
# can tell it from output. Claude Code puts the exit line first. A pi file-tool
# refusal says "this <tool> call" instead, and never reaches the stream check:
# only shell calls are.
REFUSED = "SSSF refused this command"
BUILTIN_RULE = "SSSF's secret-file rule"   # how a refusal or breach names secret_files.shell_rule's rule
_CLAUDE_REFUSED = re.compile(r"Exit code 126\n" + re.escape(REFUSED))
# An agent refused this many times in one call is looping: stop the run.
MAX_REFUSALS = 3


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


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(part.get("text") or "") if isinstance(part, dict) else str(part)
                         for part in content)
    return str(content or "")


def refused(text: str) -> bool:
    """Whether a call's result is SSSF's refusal, from either harness."""
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
    """
    for rule in rules:
        if isinstance(rule, AgentRule):
            for pattern in rule.forbid:
                if re.search(pattern, command, re.M):
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

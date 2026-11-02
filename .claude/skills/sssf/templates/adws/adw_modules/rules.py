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
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

# The shell tool's name in each harness: Claude Code's `Bash`, pi's `bash`.
SHELL_TOOLS = {"Bash", "bash"}


class RuleBreach(RuntimeError):
    """An agent ran a command one of this repo's agent_rules forbids."""

    def __init__(self, agent: str, rule: "AgentRule", pattern: str, command: str):
        self.agent, self.rule, self.pattern, self.command = agent, rule.rule, pattern, command
        self.partial = None         # the AgentResult so far, set by the interface that killed it
        super().__init__(
            f"{agent} ran `{command[:300]}`, which this repo's rule forbids: {rule.rule} "
            f"(matched {pattern!r}). SSSF killed the agent and everything it started, but "
            "the command may have run first: check what it did before resuming.")


class AgentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str                       # what every agent is told
    forbid: list[str] = []          # regexes; a command matching any one stops the run

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
    """Every shell command an event announces, in either harness's stream.

    Claude Code announces a call as a `tool_use` block in an `assistant` event,
    a subagent's calls included. Pi announces it as a `toolCall` block at
    `message_end`, and again at `tool_execution_start`; seeing it twice is
    harmless, since the first match stops the run.
    """
    etype = event.get("type")
    found: list[tuple[str, dict]] = []
    if etype in ("assistant", "message_end"):
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                found.append((block.get("name"), block.get("input") or {}))
            elif block.get("type") == "toolCall":
                found.append((block.get("name"), block.get("arguments") or {}))
    elif etype == "tool_execution_start":
        found.append((event.get("toolName"), event.get("args") or {}))
    return [str(args.get("command")) for tool, args in found
            if tool in SHELL_TOOLS and isinstance(args, dict) and args.get("command")]


def breach(rules: list, command: str) -> Optional[tuple[AgentRule, str]]:
    """The first enforced rule and pattern this command breaks, or None."""
    for rule in rules:
        if isinstance(rule, AgentRule):
            for pattern in rule.forbid:
                if re.search(pattern, command):
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

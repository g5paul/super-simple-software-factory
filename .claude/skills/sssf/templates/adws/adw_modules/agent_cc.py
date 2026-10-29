"""Claude Code coding agent interface.

Runs `claude -p --output-format stream-json` and tails its JSONL stdout line
by line, forwarding each event to a callback WHILE the agent works, exactly
like agent_pi. Two things differ from pi and shape everything below:

- A session is created with `--session-id <uuid>` and continued with
  `--resume <uuid>`; pi's one create-or-continue flag has no equivalent. The
  first send records the session in a small ledger under `session_dir`, and
  every later send (JSON retries, gate corrections, a rejoining phase) finds
  it there and resumes.
- `total_cost_usd` in the final `result` event is CUMULATIVE over a resumed
  session, while its `usage` covers only the current send. The ledger keeps
  the last running total so each send is billed only its own difference.

Agents run isolated from the operator's own Claude Code setup: `--safe-mode`
drops their CLAUDE.md, skills, plugins, hooks, and MCP servers, so a factory
agent sees its system.md and nothing else. Auth stays on the operator's
subscription login unless SSSF_CLAUDE_AUTH=api_key.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import account
from .agent_pi import ToolCallTracker as _PiToolCallTracker
from .data_types import AgentRequest, AgentResult, UsageBreakdown
from .utils import operator_env

CLAUDE_PATH = os.environ.get("CLAUDE_CODE_PATH", "claude")
# subscription: strip API keys from the child so the operator's login pays,
# and refuse a run that would bill the API anyway. api_key: pass them through.
AUTH = os.environ.get("SSSF_CLAUDE_AUTH", "subscription")
API_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

# The roster speaks pi's tool names. Claude has no LS tool (it drops the name
# silently), so both of pi's listing tools land on Glob.
TOOL_NAMES = {"read": "Read", "bash": "Bash", "edit": "Edit", "write": "Write",
              "grep": "Grep", "find": "Glob", "ls": "Glob"}
# Claude's --effort has no off switch; the lowest it goes is low.
EFFORT = {"off": "low", "minimal": "low", "low": "low", "medium": "medium",
          "high": "high", "xhigh": "xhigh", "max": "max"}


def model_id(model: str) -> str:
    """`anthropic/claude-opus-5-5` -> `claude-opus-5-5`. Claude serves only
    Anthropic models, so any other provider is a config error."""
    if "/" not in model:
        return model
    provider, name = model.split("/", 1)
    if provider != "anthropic":
        raise ValueError(f"model {model!r}: claude_code runs anthropic/* models only, "
                         f"not {provider!r}")
    return name


def map_tools(tools: Optional[list[str]]) -> Optional[list[str]]:
    """Translate a roster tool list to Claude's names. None stays None (the
    default set). A capitalized name is already Claude's and passes through."""
    if tools is None:
        return None
    mapped = []
    for tool in tools:
        name = TOOL_NAMES.get(tool) or (tool if tool[:1].isupper() else None)
        if name is None:
            raise ValueError(f"tool {tool!r} has no Claude Code equivalent "
                             f"(known: {sorted(TOOL_NAMES)}, or a Claude tool name)")
        if name not in mapped:
            mapped.append(name)
    return mapped


def child_env() -> dict[str, str]:
    env = operator_env()
    if AUTH == "subscription":
        for var in API_KEY_VARS:
            env.pop(var, None)
    return env


def _text_of(content) -> str:
    """Tool result content is a string, or a list of blocks like a message."""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or []
                   if isinstance(part, dict) and part.get("type") == "text")


class ToolCallTracker(_PiToolCallTracker):
    """Folds Claude's stream into ONE normalized record per completed call.

    Claude announces a call as a `tool_use` block in an `assistant` event and
    answers it with a `tool_result` block in a `user` event, joined by id. The
    record is the same shape agent_pi emits, so the tracer and UI need nothing.
    """

    # Glob and Grep send both a pattern and a path; the pattern is the call.
    primary_args = ("command", "pattern", "file_path", "path", "query", "url")

    def observe(self, event: dict) -> list[dict]:
        etype = event.get("type", "")
        blocks = [b for b in (event.get("message", {}) or {}).get("content", []) or []
                  if isinstance(b, dict)]
        if etype == "assistant":
            for block in blocks:
                if block.get("type") == "tool_use":
                    self._announce(block.get("id"), block.get("name"), block.get("input"))
            return []
        if etype != "user":
            return []
        return [self._finish(block.get("tool_use_id"), None, None,
                             not block.get("is_error", False),
                             _text_of(block.get("content")))
                for block in blocks if block.get("type") == "tool_result"]


def _ledger(request: AgentRequest) -> Path:
    return Path(request.session_dir) / f"{request.session_id}.json"


def _command(request: AgentRequest, resume: bool) -> list[str]:
    cmd = [
        CLAUDE_PATH, "-p", request.prompt,
        "--output-format", "stream-json", "--verbose",
        "--model", model_id(request.model),
        "--effort", EFFORT.get(request.thinking, "medium"),
        "--resume" if resume else "--session-id", request.session_id,
        # Appended, not replaced: Claude Code's own prompt carries its tool
        # guidance; system.md adds the agent's identity on top.
        "--append-system-prompt", request.system_prompt,
        # Nobody is there to answer a permission prompt. What an agent may
        # change is enforced after the call by permissions.py, not here.
        "--permission-mode", "bypassPermissions",
        "--safe-mode", "--disable-slash-commands",
    ]
    tools = map_tools(request.tools)
    if tools is not None:
        cmd += ["--tools", ",".join(tools)]
    return cmd


def _context_tokens(usage: dict) -> int:
    return int(sum(usage.get(part) or 0 for part in
                   ("input_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "output_tokens")))


def _usage(usage: dict, cost: float) -> UsageBreakdown:
    """One send's usage. Claude reports only a total cost, so the
    per-component cost fields stay zero."""
    return UsageBreakdown(
        input_tokens=usage.get("input_tokens") or 0,
        output_tokens=usage.get("output_tokens") or 0,
        cache_read_tokens=usage.get("cache_read_input_tokens") or 0,
        cache_write_tokens=usage.get("cache_creation_input_tokens") or 0,
        reasoning_tokens=(usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0,
        total_tokens=_context_tokens(usage),
        total_cost=cost,
    )


def run(request: AgentRequest, on_event: Optional[Callable[[dict], None]] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None) -> AgentResult:
    """Run one non-interactive claude turn. Same contract as agent_pi.run."""
    ledger = _ledger(request)
    resume = ledger.is_file()
    prior_cost = json.loads(ledger.read_text()).get("cost", 0.0) if resume else 0.0
    cmd = _command(request, resume)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)

    result = AgentResult(session_id=request.session_id)
    final: dict = {}
    # stdin is DEVNULL for the same reason as agent_pi: the prompt is in argv,
    # and an inherited non-TTY stdin can leave the child waiting forever.
    process = subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1, cwd=request.cwd,
                               env=child_env())
    if on_spawn:
        on_spawn(process.pid)
    try:
        with raw_path.open("a") as raw:
            assert process.stdout is not None
            for line in process.stdout:
                raw.write(line)
                raw.flush()
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "system" and event.get("subtype") == "init":
                    source = event.get("apiKeySource")
                    if AUTH == "subscription" and source not in (None, "none"):
                        process.kill()
                        raise RuntimeError(
                            f"claude would bill the API (apiKeySource={source!r}) on a "
                            "subscription run; remove that key source or set "
                            "SSSF_CLAUDE_AUTH=api_key")
                    if not resume:
                        # The session exists from here on, so a send that dies
                        # mid-turn is still resumed next time, never re-created.
                        ledger.write_text(json.dumps({"cost": 0.0}))
                elif etype == "assistant" and not event.get("parent_tool_use_id"):
                    # One API message streams as one event per content block,
                    # each repeating the same usage: read occupancy, never sum it.
                    turn = _context_tokens(event.get("message", {}).get("usage") or {})
                    if turn:
                        result.context_tokens = turn
                elif etype == "rate_limit_event" and event.get("rate_limit_info"):
                    account.record(event["rate_limit_info"], source=request.cwd)
                elif etype == "result":
                    final = event
                if on_event:
                    on_event(event)
    finally:
        stderr = process.stderr.read() if process.stderr else ""
        result.returncode = process.wait()
        if on_exit:
            on_exit(process.pid)

    if not final:
        raise RuntimeError(f"claude exited {result.returncode} without a result: "
                           f"{stderr.strip()[-800:]}")
    cumulative = float(final.get("total_cost_usd") or 0.0)
    ledger.write_text(json.dumps({"cost": cumulative}))
    result.usage = _usage(final.get("usage") or {}, max(0.0, cumulative - prior_cost))
    result.tokens = result.usage.total_tokens
    result.cost = result.usage.total_cost
    models = final.get("modelUsage") or {}
    entry = models.get(model_id(request.model)) or max(
        models.values(), key=lambda m: m.get("contextWindow") or 0, default={})
    result.context_window = int(entry.get("contextWindow") or 0)
    if final.get("is_error"):
        raise RuntimeError(f"claude ended {final.get('subtype')!r}: "
                           f"{str(final.get('result') or stderr.strip())[-800:]}")
    result.text = str(final.get("result") or "")
    return result

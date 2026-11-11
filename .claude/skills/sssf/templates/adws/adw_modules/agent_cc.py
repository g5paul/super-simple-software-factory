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
agent sees its system.md and nothing else. Safe mode keeps environment
variables, so a repo's refuse rules reach the agent as a shell prefix,
sssf_refuse.py, not as a hook. Protected secret files (secret_files.py) are
denied to Read and Grep (and to Bash reads, by Claude Code itself) with
`--disallowedTools "Read(//<abs>)"`, one per
file, with the path's pattern characters escaped (deny_rule), which
--safe-mode and bypassPermissions keep; checked by hand on claude 2.1.319.
Claude Code applies those denies to any tool that reads the file, Bash
included, so it may deny `cat .env` before sssf_refuse.py runs. Each denial
arrives as a `permission_denied` system event, whose `message` is a string. The
forwarder (agents._event_forwarder, via rules.denied) counts that call as
refused, on a Claude Code stream only, by its `tool_use_id`, and the trace records it from its `tool_result`.
Keys .env names don't reach the agent (utils.agent_env). Under local_guard: agentsh,
Claude Code agents run from the run's own CLAUDE_CONFIG_DIR, sessions/<adw_id>/claude-config,
and sign in with CLAUDE_CODE_OAUTH_TOKEN. Under the text guard, auth stays on the operator's
subscription login unless SSSF_CLAUDE_AUTH=api_key.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import account, agentsh_guard, budget, prices, rules, sandbox, session_files, silence
from .agent_pi import ToolCallTracker as _PiToolCallTracker
from .data_types import AgentRequest, AgentResult, UsageBreakdown
from .utils import agent_env, mark_agent, operator_env

CLAUDE_PATH = os.environ.get("CLAUDE_CODE_PATH", "claude")
HARNESS = "claude_code"   # agents._event_forwarder reads only Claude Code's event types for rules
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


class NotLoggedIn(RuntimeError):
    pass


_logged_in = False


def check_login(guard=None) -> None:
    """Stop before the first agent when claude has no login it can use.

    In the operator container, the session signed in from its token but the
    shell that launched the ADW did not have it, and the planner ended "Not
    logged in" (sssf-sandbox a03e7b00). The check runs with the agents' own
    environment, once per process. Under an agentsh guard the agents sign in
    with a token, so the check does too, from a private config folder: the
    run's own folder is agent-writable, and this runs outside the wrap.
    """
    global _logged_in
    if _logged_in:
        return
    under_agentsh = guard is not None and guard.mode == "agentsh"
    folder = None
    if under_agentsh:
        # Not under /tmp or $TMPDIR: every agent policy allows those in full, and an agent could plant a
        # settings.json here before this runs outside the wrap. sssf-credentials denies this folder to agents.
        home = agentsh_guard.TOKEN_FILE.parent
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        folder = tempfile.TemporaryDirectory(prefix="sssf-claude-login-", dir=home)
    try:
        try:
            env = child_env(guard=guard, config_dir=folder.name) if folder else child_env()
        except agentsh_guard.AgentshRefused as error:
            raise NotLoggedIn(str(error)) from None
        result = subprocess.run([CLAUDE_PATH, "auth", "status"], capture_output=True,
                                text=True, check=False, env=env)
    finally:
        if folder:
            folder.cleanup()
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        status = {}
    if not status.get("loggedIn"):
        if under_agentsh:
            raise NotLoggedIn(
                "the Claude Code token did not sign in (claude auth status: "
                f"{status.get('authMethod', 'no answer')}). Run `claude setup-token` in a terminal "
                "and save CLAUDE_CODE_OAUTH_TOKEN=<token> in ~/.config/sssf-sandbox/token.env, "
                "mode 600, or export it where the ADW is launched.")
        raise NotLoggedIn(
            "claude has no login the agents can use (claude auth status: "
            f"{status.get('authMethod', 'no answer')}). Log in with `claude`, or, "
            "with a token, export CLAUDE_CODE_OAUTH_TOKEN where the ADW is "
            "launched; a Claude Code session does not pass its own token to "
            "the shells it runs.")
    _logged_in = True


def child_env(agent_name: str = "", adw_id: str = "", allowed=(), *, guard=None,
              config_dir: str = "") -> dict[str, str]:
    """The operator's environment less the keys .env set (utils.agent_env),
    except SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN and `allowed` (repo.yaml
    agent_env). Claude Code gets no provider keys beyond those. Under an agentsh
    `guard` it also gets `config_dir` as CLAUDE_CONFIG_DIR, the token (from the
    operator's environment, else the token file) and DISABLE_AUTOUPDATER=1, and
    raises AgentshRefused when there is no token."""
    env = mark_agent(agent_env(operator_env(), allowed), agent_name, adw_id)
    if AUTH == "subscription":
        for var in API_KEY_VARS:
            env.pop(var, None)
    agentsh = guard is not None and guard.mode == "agentsh"
    env.update(agentsh_guard.claude_env(guard, agentsh_guard.claude_token(env) if agentsh else "", config_dir))
    return env


# SSSF's refusing shell for agent_rules marked `on_match: refuse` (rules.py).
REFUSE_SHELL = Path(__file__).with_name("sssf_refuse.py")


def _with_refusals(env: dict[str, str], refuse_rules: list[dict], tree) -> dict[str, str]:
    """Run every Bash call through sssf_refuse.py when the repo has refuse rules.
    Claude Code takes the prefix as one path, not a command line, and a prefix
    it can't run fails every call, so the tree installs it first. An
    operator's own prefix still runs after SSSF's on this machine."""
    if not refuse_rules:
        return env
    tree.install(REFUSE_SHELL)
    env["SSSF_REFUSE_RULES"] = json.dumps(refuse_rules)
    previous = env.get("CLAUDE_CODE_SHELL_PREFIX")
    if previous and tree is sandbox.LOCAL:
        env["SSSF_NEXT_SHELL_PREFIX"] = previous
    env["CLAUDE_CODE_SHELL_PREFIX"] = str(REFUSE_SHELL)
    return env


def _text_of(content) -> str:
    """Tool result content is a string, or a list of blocks like a message."""
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or []
                   if isinstance(part, dict) and part.get("type") == "text")


def _message(event: dict) -> dict:
    """An event's `message`, or {} when it isn't a dict. Claude Code 2.1.319's
    permission_denied system event carries a string there, one per denied call."""
    message = event.get("message")
    return message if isinstance(message, dict) else {}


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
        blocks = [b for b in _message(event).get("content") or []
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


# Claude Code reads a Read(...) rule as a gitignore-style pattern. Checked by
# hand on claude 2.1.319 (bypassPermissions, --safe-mode) in `weird [x] dir`:
# the raw path let the agent read .env; each of these escaped denied it.
_RULE_SPECIAL = frozenset("[]*?!\\ ")


def deny_rule(path: str) -> str:
    """The Read deny for one absolute path, in Claude Code's //<abs> form,
    with gitignore pattern characters escaped so it matches only that file."""
    return "Read(/" + "".join("\\" + c if c in _RULE_SPECIAL else c for c in path) + ")"


def _command(request: AgentRequest, resume: bool) -> list[str]:
    cmd = [
        CLAUDE_PATH, "-p", request.prompt,
        "--output-format", "stream-json", "--verbose",
        # For each message's final output count, in message_delta: the usage
        # on an assistant event is a snapshot from the message's start.
        "--include-partial-messages",
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
    for path in request.secret_files:
        # One deny per file, in Claude Code's //<absolute path> form.
        cmd += ["--disallowedTools", deny_rule(path)]
    if request.max_cost is not None:
        cmd += ["--max-budget-usd", str(request.max_cost)]
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


def _partial(turns: dict[str, dict], output: dict[str, int]) -> AgentResult:
    """What a killed call used, from its messages' usage. Claude prices a call
    only in its final result, which a killed call never sends, so SSSF prices
    the tokens at list price (prices.py). A model the table doesn't list adds
    its tokens and no cost."""
    usage = UsageBreakdown()
    for message_id, message in turns.items():
        tokens = dict(message["usage"])
        tokens["output_tokens"] = max(tokens.get("output_tokens") or 0, output.get(message_id, 0))
        priced = prices.cost(str(message.get("model") or ""), tokens)
        usage.merge(_usage(tokens, priced or 0.0))
    return AgentResult(tokens=usage.total_tokens, cost=usage.total_cost, usage=usage,
                       cost_reported=False)


def _partial_from_final(final: dict, cost: float) -> AgentResult:
    usage = _usage(final.get("usage") or {}, cost)
    return AgentResult(tokens=usage.total_tokens, cost=usage.total_cost, usage=usage,
                       cost_reported=True)


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
    turns: dict[str, dict] = {}     # message id -> the message, for a call killed mid-turn
    output: dict[str, int] = {}     # message id -> its output tokens so far, from message_delta
    streaming: dict = {}            # parent_tool_use_id -> the message id now streaming
    background = 0                  # Claude Code's background tasks still running
    # sandbox.start gives the child a DEVNULL stdin, for the reason agent_pi gives.
    tree = sandbox.current()
    guard, config_dir = None, ""
    if tree is sandbox.LOCAL and request.local_guard is not None and request.local_guard.mode == "agentsh":
        # The run's own Claude Code folder, the same for every call and rejoin of the run. The link-safe
        # walk makes it a real folder, replacing an agent's link, file or FIFO in its place.
        guard = request.local_guard
        config_dir = os.path.abspath(Path(request.data_root) / "sessions" / request.adw_id
                                     / agentsh_guard.CLAUDE_HOME)
        session_files.write(Path(config_dir) / ".sssf", "", request.data_root)
    process = agentsh_guard.start(
        tree, HARNESS, cmd, cwd=request.cwd,
        env=_with_refusals(child_env(request.agent_name, request.adw_id, request.agent_env,
                                     guard=guard, config_dir=config_dir),
                           request.refuse_rules, tree),
        guard=request.local_guard, tally=request.refusal_tally)
    if on_spawn:
        on_spawn(process.pid)
    left_by = None

    def keep_spend(stop) -> None:
        if final:
            cumulative = float(final.get("total_cost_usd") or 0.0)
            session_files.write(ledger, json.dumps({"cost": cumulative}), request.data_root)
            stop.partial = _partial_from_final(final, max(0.0, cumulative - prior_cost))
        else:
            stop.partial = _partial(turns, output)

    with silence.watch(process, request.max_silence_seconds) as dog:
        try:
            with session_files.open_append(raw_path, request.data_root) as raw:
                assert process.stdout is not None
                for line in process.stdout:
                    dog.touch()
                    try:
                        event = json.loads(line) if line.strip() else None
                    except json.JSONDecodeError:
                        event = None
                    if isinstance(event, dict) and event.get("type") == "stream_event":
                        # Partial messages: only their usage is read. The rest is
                        # the text token by token, kept out of the raw log.
                        inner = event.get("event") or {}
                        stream = event.get("parent_tool_use_id")
                        if inner.get("type") == "message_start":
                            streaming[stream] = _message(inner).get("id")
                        elif inner.get("type") == "message_delta" and streaming.get(stream):
                            output[streaming[stream]] = (inner.get("usage") or {}).get("output_tokens") or 0
                            raw.write(line)
                            raw.flush()
                        continue
                    raw.write(line)
                    raw.flush()
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type")
                    if etype == "system" and event.get("subtype") == "background_tasks_changed":
                        background = len(event.get("tasks") or [])
                    if etype == "system" and event.get("subtype") == "init":
                        dog.reopen()
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
                            session_files.write(ledger, json.dumps({"cost": 0.0}), request.data_root)
                    if etype == "assistant":
                        # Per message, subagents' too: a killed call never sends the
                        # result event that totals them.
                        message = _message(event)
                        if message.get("id") and message.get("usage"):
                            turns[message["id"]] = message
                    if etype == "assistant" and not event.get("parent_tool_use_id"):
                        # One API message streams as one event per content block,
                        # each repeating the same usage: read occupancy, never sum it.
                        turn = _context_tokens(_message(event).get("usage") or {})
                        if turn:
                            result.context_tokens = turn
                    elif etype == "rate_limit_event" and event.get("rate_limit_info"):
                        account.record(event["rate_limit_info"], source=request.cwd)
                        if (AUTH == "subscription" and request.stop_on_usage_credits
                                and account.on_credits(event["rate_limit_info"])):
                            process.stop_tree()
                            stop = account.RateLimitStop(account.CREDITS)
                            stop.partial = _partial(turns, output)
                            raise stop
                    elif etype == "result":
                        final = event
                        dog.finished(waiting=background > 0)
                    if on_event:
                        try:
                            on_event(event)
                        except Exception as error:
                            # A forbidden command (rules.RuleBreach), or a tracer
                            # failure: either way the agent must not run on unwatched.
                            process.stop_tree()
                            if isinstance(error, rules.RuleBreach):
                                error.partial = _partial(turns, output)
                            raise
        except BaseException as error:
            left_by = error
            # Leaving mid-call, by a stop or by `just kill`: the agent must not
            # run on. On the host the kill reaches it too, but in a sandbox VM
            # nothing else does, and the wait below would last until it finished.
            process.stop_tree()
            raise
        finally:
            stderr = process.stderr.read() if process.stderr else ""
            result.returncode = process.wait()
            agentsh_guard.close(process, on_event)
            if on_exit:
                on_exit(process.pid)
            if isinstance(left_by, Exception) and not isinstance(left_by, rules.RuleBreach):
                # Three denials replace whatever other stop the call was leaving by.
                stop = agentsh_guard.breach(process, request.agent_name)
                if stop:
                    keep_spend(stop)
                    raise stop from left_by

    try:
        agentsh_guard.check_stopped(process, request.agent_name)
    except (rules.RuleBreach, agentsh_guard.AgentshRefused) as stop:
        # agentsh's denials reached the limit, or its service failed. The call keeps its spend.
        keep_spend(stop)
        raise
    if dog.hung:
        stop = silence.SilentStop(request.agent_name, request.max_silence_seconds / 60, request.adw_id)
        stop.partial = _partial(turns, output)
        raise stop
    result.leftovers_stopped = dog.fired
    if not final:
        raise RuntimeError(f"claude exited {result.returncode} without a result: "
                           f"{stderr.strip()[-800:]}")
    cumulative = float(final.get("total_cost_usd") or 0.0)
    session_files.write(ledger, json.dumps({"cost": cumulative}), request.data_root)
    result.usage = _usage(final.get("usage") or {}, max(0.0, cumulative - prior_cost))
    result.tokens = result.usage.total_tokens
    result.cost = result.usage.total_cost
    models = final.get("modelUsage") or {}
    result.models = sorted(models)
    entry = models.get(model_id(request.model)) or max(
        models.values(), key=lambda m: m.get("contextWindow") or 0, default={})
    result.context_window = int(entry.get("contextWindow") or 0)
    if final.get("subtype") == "error_max_budget_usd":
        # Not a crash: the call ended itself at its cap. It is never retried,
        # and what it spent still counts, so the result travels with it.
        raise budget.CallBudgetStop(
            f"the call reached its max_call_cost: {'; '.join(final.get('errors') or [])}.",
            result=result)
    if final.get("is_error"):
        raise RuntimeError(f"claude ended {final.get('subtype')!r}: "
                           f"{str(final.get('result') or stderr.strip())[-800:]}")
    result.text = str(final.get("result") or "")
    return result

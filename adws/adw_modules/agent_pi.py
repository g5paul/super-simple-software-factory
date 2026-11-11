"""Pi coding agent interface.

Runs `pi -p --mode json` and tails its JSONL stdout line by line, forwarding
each event to a callback WHILE the agent works (the streaming crack, solved
by construction). `--session-id` creates-or-continues, so running and
continuing an agent are the same call: same session id = same context window.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import socket
import stat
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from . import agentsh_guard, obs_relay, rules, sandbox, session_files, silence
from .account import RateLimitStop
from .data_types import AgentRequest, AgentResult
from .utils import agent_env, mark_agent, now_iso, operator_env

PI_PATH = os.environ.get("PI_PATH", "pi")
HARNESS = "pi"   # agents._event_forwarder reads only pi's event types for rules

# OpenAI's code for a ChatGPT plan that is out of usage, on the token-sharing
# route pi's `openai` sign-in uses (HTTP 429). Pi never retries it. The route
# reports no usage windows and no reset time, so this is the only signal.
USAGE_LIMIT = "subscription_sharing_usage_limit_exceeded"
USAGE_PAGE = "https://chatgpt.com/settings/usage"


class UsageLimitStop(RateLimitStop):
    """The ChatGPT plan behind pi's sign-in refused a call: it is out of usage.
    A RateLimitStop, so the run ends on its message, not a traceback."""


# Keys for the providers Pi can sign into with a subscription (ChatGPT,
# Claude). With SSSF_PI_AUTH=subscription they are kept from Pi, so a lapsed
# sign-in fails the call instead of falling back to billing the API.
SUBSCRIPTION_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
MODELS_JSON = os.environ.get("PI_MODELS_PATH",
                             str(Path.home() / ".pi" / "agent" / "models.json"))

RESULT_SNIPPET_CHARS = 20_000   # tool output rides along whole; clip only guards pathological cases
ARG_VALUE_CHARS = 20_000        # args too — the UI scrolls, it must not be handed cut-off data
LABEL_CHARS = 80                # "bash: <command>" shown as the event name

# The arg that identifies a call at a glance, in the order tools tend to use.
PRIMARY_ARGS = ("command", "path", "file_path", "pattern", "query", "url")


def _count(value: str) -> int:
    """Parse pi's compact model-list counts (`272K`, `1.0M`)."""
    suffixes = {"K": 1_000, "M": 1_000_000}
    suffix = value[-1:].upper()
    if suffix in suffixes:
        return int(float(value[:-1]) * suffixes[suffix])
    return int(value)


@lru_cache(maxsize=1)
def _pi_catalog() -> list[tuple[str, str, int]]:
    """Read pi's merged catalog, including built-in providers and custom models.

    pi runs here, on this machine, whatever tree the agents use. In the operator
    container it has no login of its own, so it gets the host broker's token as
    OPENAI_API_KEY (_in_container), as an agent call does; without it pi lists
    no openai models. A broker error raises SandboxFailed and nothing is cached.
    The cache holds the parsed rows only, never the environment."""
    env = _in_container(operator_env(), sandbox.LOCAL)
    try:
        result = subprocess.run(
            [PI_PATH, "--list-models"], capture_output=True, text=True,
            timeout=30, env=env, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.splitlines()[1:]:
        columns = line.split()
        if len(columns) < 3:
            continue
        try:
            rows.append((columns[0], columns[1], _count(columns[2])))
        except ValueError:
            continue
    return rows


def resolve_model(pattern: str) -> tuple[str, str]:
    """Resolve a model pattern to an explicit ``(provider, model_id)`` pair.

    Pi's catalog merges built-in models with ``~/.pi/agent/models.json``. Using
    that same merged view lets SSSF target direct providers such as
    ``openai/gpt-5.6-terra`` without re-registering built-in models locally.
    """
    catalog = [(provider, model_id) for provider, model_id, _ in _pi_catalog()]
    if "/" in pattern:
        provider, model_id = pattern.split("/", 1)
        if (provider, model_id) in catalog:
            return provider, model_id
    matches = [(provider, model_id) for provider, model_id in catalog
               if pattern == model_id or pattern in model_id]
    exact = [match for match in matches
             if match[1] == pattern or match[1].endswith("/" + pattern)]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"model pattern {pattern!r} not found in pi --list-models — "
                         "authenticate/register it or fix the config")
    raise ValueError(f"model pattern {pattern!r} is ambiguous: {matches}")


def _context_tokens(usage: dict) -> int:
    """Tokens occupying the window after a turn.

    Mirrors pi's own `calculateContextTokens` (coding-agent
    `core/compaction/compaction.ts`), which is what pi compacts against and
    shows in its footer: prefer the provider's `totalTokens`, else sum the
    parts. Cache reads count — cached prompt is still prompt.
    """
    total = usage.get("totalTokens") or 0
    if total:
        return int(total)
    return int(sum(usage.get(part) or 0
                   for part in ("input", "output", "cacheRead", "cacheWrite")))


def context_window(provider: str, model_id: str) -> int:
    """The model's context ceiling from pi's merged model catalog."""
    # models.json only exists once a custom provider is registered; built-in
    # providers alone are a valid setup, served by the catalog fallback below.
    registry_path = Path(MODELS_JSON)
    registry = json.loads(registry_path.read_text()) if registry_path.is_file() else {}
    for model in registry.get("providers", {}).get(provider, {}).get("models", []):
        if model.get("id") == model_id:
            return int(model.get("contextWindow") or 0)
    for listed_provider, listed_model, window in _pi_catalog():
        if listed_provider == provider and listed_model == model_id:
            return window
    return 0


def _text_of(container: dict) -> str:
    """Join the text blocks of anything pi shapes as {content: [...]} — a
    message or a tool result."""
    return "".join(part.get("text", "") for part in container.get("content", []) or []
                   if isinstance(part, dict) and part.get("type") == "text")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _label(tool: str, args: dict, keys: tuple[str, ...] = PRIMARY_ARGS) -> str:
    """One-line human name for a tool call: `bash: ls -la src`."""
    value = next((args[key] for key in keys
                  if isinstance(args.get(key), str) and args[key].strip()), "")
    if not value:
        value = next((v for v in args.values() if isinstance(v, str) and v.strip()), "")
    value = " ".join(str(value).split())
    return f"{tool}: {_clip(value, LABEL_CHARS)}" if value else tool


class ToolCallTracker:
    """Folds pi's tool stream into ONE normalized record per completed call.

    pi announces a call as a `toolCall` content block, then emits
    tool_execution_start / _update / _end for it. Only the end carries the
    result, so that is where a record is emitted — one trace event per real
    tool call, the moment it returns, instead of three shapeless ones.

    The record carries the call's real span (`started_at`/`ended_at`), which the
    tracer writes to columns so the UI can lay tool calls on a time axis without
    parsing every payload.
    """

    primary_args = PRIMARY_ARGS

    def __init__(self) -> None:
        self._open: dict[str, dict] = {}

    def observe(self, event: dict) -> list[dict]:
        """Returns the records for tool calls this event finished, often none."""
        etype = event.get("type", "")
        if etype == "message_end":
            for block in event.get("message", {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "toolCall":
                    self._announce(block.get("id"), block.get("name"),
                                   block.get("arguments"))
            return []
        if etype == "tool_execution_start":
            self._announce(event.get("toolCallId"), event.get("toolName"),
                           event.get("args"))
            return []
        if etype != "tool_execution_end":
            return []
        return [self._finish(event.get("toolCallId"), event.get("toolName"),
                             event.get("args"), not event.get("isError", False),
                             _text_of(event.get("result") or {}))]

    def _finish(self, call_id, tool, args, ok: bool, result_text: str) -> dict:
        """Close an announced call into its one normalized record."""
        call_id = str(call_id or "")
        opened = self._open.pop(call_id, {})
        tool = str(tool or opened.get("tool") or "tool")
        args = args or opened.get("args") or {}
        record = {
            "tool": tool,
            "tool_call_id": call_id,
            "args": {key: _clip(value, ARG_VALUE_CHARS) if isinstance(value, str) else value
                     for key, value in args.items()},
            "ok": ok,
            "label": _label(tool, args, self.primary_args),
        }
        if result_text:
            record["result_snippet"] = _clip(result_text, RESULT_SNIPPET_CHARS)
        record["ended_at"] = now_iso()
        if opened.get("clock"):
            record["duration_ms"] = int((time.monotonic() - opened["clock"]) * 1000)
        if opened.get("started_at"):
            record["started_at"] = opened["started_at"]
        return record

    def running(self) -> dict:
        """The latest call started and not yet returned, as {"label",
        "started_at"}, or {} when none is open. A silent stop names it: it's
        what the agent was waiting on."""
        if not self._open:
            return {}
        opened = max(self._open.values(), key=lambda call: call.get("clock") or 0)
        return {"label": _label(str(opened.get("tool") or "tool"), opened.get("args") or {},
                                self.primary_args),
                "started_at": opened.get("started_at")}

    def _announce(self, call_id, tool, args) -> None:
        """First sighting starts the clock; a later sighting only fills gaps."""
        if not call_id:
            return
        known = self._open.get(str(call_id), {})
        self._open[str(call_id)] = {
            "tool": tool or known.get("tool", ""),
            "args": args or known.get("args", {}),
            "started_at": known.get("started_at") or now_iso(),   # wall clock, for the row
            "clock": known.get("clock") or time.monotonic(),      # monotonic, for duration
        }


# SSSF's own extension for agent_rules marked `on_match: refuse` (rules.py).
REFUSE_EXTENSION = Path(__file__).with_name("sssf_rules.ts")

# What a pi agent in a remote VM needs left on its ChatGPT token when it
# starts: a call may take AGENT_SECONDS (30 minutes), plus a margin.
VM_TOKEN_MINUTES = 35

# In the operator container (container/run.sh), the host's pi_token_broker.py
# hands out the token through the socket this variable names.
CONTAINER_SOCKET_VAR = "SSSF_PI_TOKEN_SOCKET"


def _node_join(*parts: str) -> str:
    """Node's path.posix.join: drop empty parts, join with /, then fold ., ..
    and repeated / as text, the way normalize does. A .. at the root of an
    absolute path stays at the root; a relative path keeps a leading .. it
    can't pop. posixpath.normpath differs (it keeps a leading //)."""
    joined = "/".join(p for p in parts if p)
    if not joined:
        return "."
    absolute = joined.startswith("/")
    out: list[str] = []
    for seg in joined.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not absolute:
                out.append("..")
            continue
        out.append(seg)
    body = "/".join(out)
    return "/" + body if absolute else (body or ".")


# The file:// URLs whose fileURLToPath SSSF can match exactly: no host, no
# %-escape, ?, #, \, :, | or empty segment, only characters the URL parser
# leaves as they are. On these, URL dot-segment folding then join is the
# same as join alone.
_URL_SEGMENT = r"[A-Za-z0-9._~!$&'()*+,;=@-]+"
_PLAIN_FILE_URL = re.compile(rf"file://(/(?:{_URL_SEGMENT}(?:/{_URL_SEGMENT})*/?)?)")


def _utf8(value: str, name: str) -> str:
    """value, if Node would read the same bytes. Node decodes the environment as
    UTF-8 and turns a bad byte into U+FFFD, a different path."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise sandbox.SandboxFailed(
            f"{name} isn't valid UTF-8, so SSSF can't tell which dir pi reads its "
            "logins from; set it to a plain absolute path.") from None
    return value


def _node_homedir(env=None) -> str:
    """Node's os.homedir() (libuv uv_os_homedir): $HOME whenever HOME is set,
    even empty, else the passwd entry for the effective uid. env is the
    environment pi runs with; os.environ when None."""
    env = os.environ if env is None else env
    if "HOME" in env:
        return _utf8(env["HOME"], "HOME")
    try:
        return _utf8(pwd.getpwuid(os.geteuid()).pw_dir, "the passwd home dir")
    except KeyError:
        raise sandbox.SandboxFailed(
            "HOME isn't set and there's no passwd entry for this user, so SSSF can't "
            "tell where pi reads its logins; set HOME.") from None


def _pi_auth_json(env=None) -> Path:
    """Where pi reads its stored logins, worked out as pi 1.4.0 does:
    join(getAgentDir(), "auth.json"). getAgentDir (dist/config.js) is
    normalizePath(PI_CODING_AGENT_DIR) when that's set and non-empty
    (dist/utils/paths.js: "~" is the home dir, "~/rest" is join(home, rest), a
    file:// URL goes through fileURLToPath, anything else is as written; ~user
    is not expanded), else join(homedir(), ".pi", "agent"). Any value SSSF
    can't match exactly raises SandboxFailed. A relative result is returned as
    is, for check_stored_login to stop on. No other override: the check must
    read what pi reads. env is the environment pi runs with (the agent's, which
    may lack a key .env named); os.environ when None."""
    env = os.environ if env is None else env
    agent_dir = env.get("PI_CODING_AGENT_DIR")
    if not agent_dir:
        return Path(_node_join(_node_homedir(env), ".pi", "agent", "auth.json"))
    agent_dir = _utf8(agent_dir, "PI_CODING_AGENT_DIR")
    if agent_dir == "~":
        return Path(_node_join(_node_homedir(env), "auth.json"))
    if agent_dir.startswith("~/"):
        return Path(_node_join(_node_homedir(env), agent_dir[2:], "auth.json"))
    if agent_dir.startswith("file://"):
        url = _PLAIN_FILE_URL.fullmatch(agent_dir)
        if not url:
            raise sandbox.SandboxFailed(
                f"PI_CODING_AGENT_DIR is a file:// URL ({agent_dir!r}) that SSSF can't turn "
                "into a path exactly as pi does; write it as a plain absolute path.")
        return Path(_node_join(url.group(1), "auth.json"))
    return Path(_node_join(agent_dir, "auth.json"))


PI_AUTH_JSON: Optional[Path] = None   # tests monkeypatch this; None works it out per call
BROKER_TIMEOUT = 150            # more than the broker's own 120 s pi timeout


def vm_token() -> str:
    """A ChatGPT access token for one pi agent in a remote VM, from the host's
    sign-in (docs/research/pi-chatgpt-in-vm.md). pi refreshes the login under
    its own lock if it has to, so the login and its refresh token stay on this
    machine and the VM gets only an access token that expires within the hour.
    Never print it."""
    done = subprocess.run([PI_PATH, "auth", "print-bearer-token", "--provider", "openai",
                           "--min-expiry", f"{VM_TOKEN_MINUTES}m"],
                          capture_output=True, text=True, check=False, env=operator_env())
    token = done.stdout.strip()
    if done.returncode != 0 or not token:
        raise sandbox.SandboxFailed(
            "pi has no ChatGPT sign-in to hand a VM a token from (pi auth print-bearer-token "
            f"--provider openai exited {done.returncode}). Sign in with `pi`, then /login.")
    if token.startswith("sk-"):
        raise sandbox.SandboxFailed(
            "pi's openai credential is an API key, not the ChatGPT sign-in, and SSSF doesn't "
            "put API keys in a VM. Sign in to ChatGPT with `pi` and /login.")
    return token


def _read_regular(path: Path) -> bytes:
    """The whole of the regular file at path. O_NONBLOCK keeps a FIFO swapped in
    after the stat from holding the open, and fstat refuses anything but a
    regular file, which a read can't block on."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"{path} is not a regular file")
        with os.fdopen(os.dup(fd), "rb") as f:
            return f.read()
    finally:
        os.close(fd)


def _strip_json_comments(text: str) -> str:
    """Port of pi's stripJsonComments (dist/utils/json.js in pi 1.4.0): drop //
    line comments, then trailing commas, outside string literals."""
    text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*',
                  lambda m: m.group(0) if m.group(0)[0] == '"' else "", text)
    return re.sub(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])',
                  lambda m: m.group(1) if m.group(1) is not None else m.group(0), text)


def _load_agent_json(path: Path, what: str, *, comments: bool = False) -> Optional[dict]:
    """The parsed JSON object at path, None if the file is missing. Never blocks:
    the path is stat'ed first (following a symlink, as pi does), so a FIFO,
    socket, device or directory there stops the run without being opened. That
    stat stays even though _read_regular checks again, because opening a device
    can have effects of its own. With comments, parse as pi parses models.json:
    BOM, // comments and trailing commas removed first."""
    try:
        info = os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        info = None
    if info is not None and not stat.S_ISREG(info.st_mode):
        raise sandbox.SandboxFailed(
            f"{path} is not a regular file, so SSSF can't check it for {what}; "
            "remove it and start again.")
    data = None
    if info is not None:
        try:
            text = _read_regular(path).decode("utf-8")
            if comments:
                text = _strip_json_comments(text.removeprefix("\ufeff"))
            data = json.loads(text)
        except (OSError, ValueError):
            data = None
    if not isinstance(data, dict):
        raise sandbox.SandboxFailed(f"SSSF can't read {path} to check it for {what}; "
                                    "fix or remove it.")
    return data


def _models_json_credential(data: dict) -> Optional[str]:
    """The first field path in a models.json that gives pi's openai provider a
    credential, or None. Key names only, never a value. Raises ValueError for a
    shape pi's schema wouldn't take."""
    def has_headers(entry: dict) -> bool:
        return "headers" in entry and entry["headers"] != {}

    providers = data.get("providers", {})
    if not isinstance(providers, dict):
        raise ValueError("providers")
    openai = providers.get("openai")
    if openai is None:
        return None
    if not isinstance(openai, dict):
        raise ValueError("providers.openai")
    if "apiKey" in openai:
        return "providers.openai.apiKey"
    if has_headers(openai):
        return "providers.openai.headers"
    models = openai.get("models", [])
    if not isinstance(models, list) or not all(isinstance(m, dict) for m in models):
        raise ValueError("providers.openai.models")
    for i, model in enumerate(models):
        if has_headers(model):
            return f"providers.openai.models[{i}].headers"
    overrides = openai.get("modelOverrides", {})
    if not isinstance(overrides, dict) or not all(isinstance(o, dict) for o in overrides.values()):
        raise ValueError("providers.openai.modelOverrides")
    if any(has_headers(o) for o in overrides.values()):
        return "providers.openai.modelOverrides.<key>.headers"
    return None


def check_stored_login(path: Path = None, env=None) -> None:
    """Stop if the container's pi agent dir gives openai a credential of its own:
    pi would use it instead of the host's sign-in. Checks auth.json for an
    openai login, then models.json in the same dir for what pi 1.4.0 accepts as
    a credential: providers.openai.apiKey (a literal, $NAME or !command) and
    headers at the provider, model and modelOverrides level. Reads key names
    only; never a value.

    The dir is pi's own, worked out by _pi_auth_json from env, the environment
    pi will run with (os.environ when None).

    Never blocks, for either file (see _load_agent_json). A missing file passes;
    an unreadable one stops the run."""
    path = path or PI_AUTH_JSON or _pi_auth_json(env)
    if not path.is_absolute():
        raise sandbox.SandboxFailed(
            f"PI_CODING_AGENT_DIR puts pi's auth.json at {path}, a relative path, so SSSF "
            "can't tell which file pi will read; set it to an absolute path.")
    data = _load_agent_json(path, "a stored ChatGPT login")
    if data is not None and "openai" in data:
        raise sandbox.SandboxFailed(
            f"{path} has a ChatGPT login stored in the container (an openai entry). pi would use "
            "it instead of the host's sign-in, so remove that entry (or the file) and start "
            "again; the container gets its token from the host.")
    what = "an openai key of its own"
    models_path = path.parent / "models.json"
    models = _load_agent_json(models_path, what, comments=True)
    if models is None:
        return
    try:
        field = _models_json_credential(models)
    except ValueError:
        raise sandbox.SandboxFailed(f"SSSF can't read {models_path} to check it for {what}; "
                                    "fix or remove it.") from None
    if field:
        raise sandbox.SandboxFailed(
            f"{models_path} gives pi's openai provider a credential of its own ({field}). pi "
            "would use it instead of the host's sign-in, so remove that entry and start again; "
            "the container gets its token from the host.")


def container_token(socket_path: str) -> str:
    """A ChatGPT access token from the host's broker (container/pi_token_broker.py:
    one line, `OK <token>` or `ERR <message>`). Never put it in a message."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(BROKER_TIMEOUT)
            conn.connect(socket_path)
            data = b""
            while len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError as e:
        raise sandbox.SandboxFailed(
            f"the pi token broker at {socket_path} isn't answering ({type(e).__name__}); start "
            "the session with container/run.sh, which runs it on the host.") from None
    lines = data[:65536].decode("utf-8", "replace").splitlines()
    line = lines[0] if lines else ""
    if line.startswith("ERR "):
        raise sandbox.SandboxFailed(line[4:])
    token = line[3:].strip() if line.startswith("OK ") else ""
    if not token:
        raise sandbox.SandboxFailed(f"the pi token broker at {socket_path} gave no token.")
    if token.startswith("sk-"):
        raise sandbox.SandboxFailed(
            "pi's openai credential is an API key, not the ChatGPT sign-in, and SSSF doesn't "
            "put API keys in the container. Sign in to ChatGPT with `pi` on the host and /login.")
    return token


def _in_container(env: dict[str, str], tree) -> dict[str, str]:
    """In the operator container (SSSF_PI_TOKEN_SOCKET set) on a local tree: a
    fresh token from the host's broker as OPENAI_API_KEY. Otherwise unchanged.
    Serves the model catalog (_pi_catalog) as well as agent calls.
    obs stays on: the container reaches the host's obs server."""
    socket_path = os.environ.get(CONTAINER_SOCKET_VAR)
    if tree is not sandbox.LOCAL or not socket_path:
        return env
    check_stored_login(env=env)
    env["OPENAI_API_KEY"] = container_token(socket_path)
    return env


def _obs_target(env: dict[str, str], token: str, extensions) -> Optional[obs_relay.Target]:
    """Where a VM call's obs events go, or None when obs couldn't post
    (design decision 40): no roster extension, no OBS_AUTH_TOKEN on the
    host, or the operator turned obs off. SSSF doesn't try to tell which
    extension is obs."""
    roster = [e for e in extensions if str(e) != str(REFUSE_EXTENSION)]
    if not roster or not token or env.get("OBS_DISABLE") == "true":
        return None
    return obs_relay.Target(
        server=env.get("OBS_SERVER_URL") or obs_relay.DEFAULT_SERVER,
        token=token,
        name=env.get("OBS_NAME", ""),
        pool=env.get("OBS_POOL", ""),
        tags=[t.strip() for t in env.get("OBS_TAG", "").split(",") if t.strip()])


def _in_vm(env: dict[str, str], tree, refuse_rules: list[dict],
           extensions=()) -> dict[str, str]:
    """On a remote tree: the ChatGPT token, renamed to OPENAI_API_KEY in the VM
    (sandbox_remote), SSSF's refuse extension copied in, and obs off with the
    value the extension reads, OBS_DISABLE=true. The obs server's token stays
    on the host. When obs could post (_obs_target), the tree gets a relay for
    this call. Remote.start then points OBS_SERVER_URL at it and drops
    OBS_DISABLE, and the host posts the events with the token (design
    decisions 37 to 50). In the operator container, the token comes through a
    socket instead (_in_container)."""
    if tree is sandbox.LOCAL:
        return env
    token = env.pop("OBS_AUTH_TOKEN", "")   # never enters the VM; only the host relay holds it
    target = _obs_target(env, token, extensions)
    env["OBS_DISABLE"] = "true"             # the only value the obs extension reads as off
    env["SSSF_PI_TOKEN"] = vm_token()
    tree.install(REFUSE_EXTENSION)          # every pi agent loads it
    if target is not None:
        tree.relay_obs(target)              # for the next start only
    return env


def _with_refusals(env: dict[str, str], refuse_rules: list[dict],
                   secret_files=()) -> dict[str, str]:
    if refuse_rules:
        env["SSSF_REFUSE_RULES"] = json.dumps(refuse_rules)
    if secret_files:
        env["SSSF_SECRET_FILES"] = json.dumps(list(secret_files))
    return env


# The provider key names pi reads, from pi-ai's env-api-keys.js. AWS credentials
# are left out on purpose: they are general cloud credentials, so a repo that
# uses Bedrock lists them under `agent_env`. A key pi learns later goes in
# `agent_env` until this list catches up.
PI_PROVIDER_KEYS = (
    "AI_GATEWAY_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_OAUTH_TOKEN", "ANT_LING_API_KEY", "AZURE_OPENAI_API_KEY",
    "BASETEN_API_KEY", "CEREBRAS_API_KEY", "CLOUDFLARE_API_KEY",
    "COPILOT_GITHUB_TOKEN", "DEEPSEEK_API_KEY", "FIREWORKS_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_CLOUD_API_KEY", "GROQ_API_KEY", "HF_TOKEN",
    "KIMI_API_KEY", "META_API_KEY", "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY",
    "MISTRAL_API_KEY", "MOONSHOT_API_KEY", "NVIDIA_API_KEY", "OPENAI_API_KEY",
    "OPENCODE_API_KEY", "OPENROUTER_API_KEY", "QWEN_TOKEN_PLAN_API_KEY",
    "QWEN_TOKEN_PLAN_CN_API_KEY", "RADIUS_API_KEY", "TOGETHER_API_KEY",
    "TYPESAFE_API_KEY", "XAI_API_KEY", "XIAOMI_API_KEY",
    "XIAOMI_TOKEN_PLAN_AMS_API_KEY", "XIAOMI_TOKEN_PLAN_CN_API_KEY",
    "XIAOMI_TOKEN_PLAN_SGP_API_KEY", "ZAI_API_KEY", "ZAI_CODING_CN_API_KEY",
)


def child_env(agent_name: str = "", adw_id: str = "", allowed=()) -> dict[str, str]:
    """The operator's environment, less the keys .env set (utils.agent_env)
    except SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN, pi's provider keys
    (PI_PROVIDER_KEYS) and `allowed` (repo.yaml agent_env), and less the
    subscription providers' keys when SSSF_PI_AUTH=subscription. Read per call,
    since .env loads after import.

    The rest is for the pi-observability extension. OBS_NAME is the roster
    name, so the dashboard shows "builder" rather than an unnamed session.
    OBS_POOL puts each run in its own pool, sssf-<adw_id>, so the usage panel
    totals one run per row; an operator's own OBS_POOL wins. OBS_TAG gains
    "sssf" and "adw-<adw_id>", added to the operator's tags, not replacing them.
    SSSF_ADW_ID and SSSF_AGENT mark the agent for the repo's scripts (mark_agent).
    """
    env = mark_agent(agent_env(operator_env(), (*PI_PROVIDER_KEYS, *allowed)),
                     agent_name, adw_id)
    if agent_name:
        env["OBS_NAME"] = agent_name
    if adw_id:
        if not env.get("OBS_POOL"):
            env["OBS_POOL"] = f"sssf-{adw_id}"
        tags = [t.strip() for t in env.get("OBS_TAG", "").split(",") if t.strip()]
        tags += [t for t in ("sssf", f"adw-{adw_id}") if t not in tags]
        env["OBS_TAG"] = ",".join(tags)
    if os.environ.get("SSSF_PI_AUTH") == "subscription":
        for var in SUBSCRIPTION_KEY_VARS:
            env.pop(var, None)
    return env


def run(request: AgentRequest, on_event: Optional[Callable[[dict], None]] = None,
        on_spawn: Optional[Callable[[int], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None) -> AgentResult:
    """Run one non-interactive pi turn.

    `on_spawn(pid)` and `on_exit(pid)` bracket the child process so the caller
    can record it as killable — a hung coding agent is otherwise a pid you have
    to hunt for in `ps` while the run sits there.
    """
    provider, model_id = resolve_model(request.model)
    cmd = [
        PI_PATH, "-p", "--mode", "json",
        "--provider", provider, "--model", model_id,
        "--thinking", request.thinking,
        "--session-id", request.session_id,
        "--session-dir", request.session_dir,
        "--system-prompt", request.system_prompt,
    ]
    if request.tools:
        cmd += ["--tools", ",".join(request.tools)]
    for extension in request.extensions:
        cmd += ["-e", extension]
    # Last, so it checks a command after every other extension has had its
    # turn (design decision 24). A test pins this.
    cmd += ["-e", str(REFUSE_EXTENSION)]
    cmd.append(request.prompt)

    raw_path = Path(request.raw_output_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)

    result = AgentResult(session_id=request.session_id,
                      context_window=context_window(provider, model_id))
    # sandbox.start gives the child a DEVNULL stdin, deliberately. The prompt
    # travels in argv, so the child never needs stdin — but inheriting the
    # parent's means pi sees a non-TTY and can sit forever waiting for piped
    # input that will never arrive or EOF. That failure is silent and total: no
    # request goes out, no bytes come back, and the ADW blocks on a read loop
    # with nothing to read. Observed as a run that sat idle at 0% CPU with an
    # empty raw_output.jsonl.
    # How the last assistant turn ended. Pi retries some errors itself, so only
    # the last turn says whether the call failed.
    last_stop, last_error = None, ""
    tree = sandbox.current()
    env = _with_refusals(
        child_env(request.agent_name, request.adw_id, request.agent_env),
        request.refuse_rules, request.secret_files)
    env = _in_vm(env, tree, request.refuse_rules, request.extensions)
    env = _in_container(env, tree)
    process = agentsh_guard.start(tree, HARNESS, cmd, cwd=request.cwd, env=env,
                                  guard=request.local_guard, tally=request.refusal_tally, on_event=on_event)
    if on_spawn:
        on_spawn(process.pid)
    stderr = ""
    left_by = None
    try:
        with silence.watch(process, request.max_silence_seconds) as dog:
            try:
                with session_files.open_append(raw_path, request.data_root) as raw:
                    assert process.stdout is not None
                    for line in process.stdout:
                        dog.touch()
                        raw.write(line)
                        raw.flush()                      # events land on disk as they happen
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        except (ValueError, RecursionError):
                            if line.startswith("{"):
                                raise                    # pi's events are objects: one too deep or long to read stops pi
                            continue                     # an integer past Python's digit limit, or nested too deep
                        if not isinstance(event, dict):
                            continue                     # null, a list, a number or a string: no event, as in agent_cc
                        if event.get("type") == "agent_end":
                            dog.finished()
                        if event.get("type") == "message_end":
                            message = event.get("message", {})
                            if not isinstance(message, dict):
                                continue                 # announces nothing; the forwarder can't read it either
                            if message.get("role") == "assistant":
                                text = _text_of(message)
                                if text:
                                    result.text = text   # last assistant message wins
                                last_stop = message.get("stopReason")
                                last_error = message.get("errorMessage") or ""
                                usage = message.get("usage", {}) or {}
                                if not isinstance(usage, dict):
                                    usage = {}
                                turn = _context_tokens(usage)
                                result.tokens += turn
                                result.usage.add_turn(usage, turn)
                                # Occupancy is read off the last VALID assistant turn, the
                                # way pi does it — an aborted or errored turn reports usage
                                # you can't trust, so it must not overwrite a good reading.
                                if turn and message.get("stopReason") not in ("aborted", "error"):
                                    result.context_tokens = turn
                                result.cost += (usage.get("cost", {}) or {}).get("total", 0.0) or 0.0
                        # agentsh_ events come only from the guard (start/close); a stream copy is a command's forgery.
                        if on_event and not agentsh_guard.guard_event(event):
                            try:
                                on_event(event)
                            except rules.RuleBreach as error:
                                error.partial = result   # pi priced every turn so far
                                raise
            except BaseException as error:
                # Leaving mid-call, by a stop, a forbidden command, a tracer failure, an error in this loop
                # or `just kill`: the agent must not run on unwatched. Stopped here, inside the watch, as soon
                # as the loop is left. On the host the kill reaches it too, but in a sandbox VM nothing else
                # does, and the wait below would last until it finished.
                left_by = error
                process.stop_tree()
                raise
        # A clean end: the watch has closed, so pi's stderr is read, and pi waited for, after it, as before.
        stderr = process.stderr.read() if process.stderr else ""
    except BaseException as error:
        if left_by is None:
            # The watch's own setup or teardown, or the stderr read, failed or was interrupted.
            left_by = error
            process.stop_tree()
        raise
    finally:
        result.returncode = process.wait()
        agentsh_guard.close(process, on_event)
        if on_exit:
            on_exit(process.pid)
        if isinstance(left_by, Exception) and not isinstance(left_by, rules.RuleBreach):
            # Three denials replace whatever other stop the call was leaving by.
            stop = agentsh_guard.breach(process, request.agent_name)
            if stop:
                stop.partial = result           # pi priced every turn so far
                raise stop from left_by
    try:
        agentsh_guard.check_stopped(process, request.agent_name)
    except (rules.RuleBreach, agentsh_guard.AgentshRefused) as stop:
        stop.partial = result           # pi priced every turn so far
        raise
    if dog.hung:
        stop = silence.SilentStop(request.agent_name, request.max_silence_seconds / 60, request.adw_id)
        stop.partial = result           # pi priced every turn so far
        raise stop
    result.leftovers_stopped = dog.fired
    if last_stop == "error" and USAGE_LIMIT in last_error:
        raise UsageLimitStop(
            f"The ChatGPT plan behind pi's sign-in hit its usage limit ({USAGE_LIMIT}). "
            f"OpenAI gives no reset time; check {USAGE_PAGE}, then resume with "
            f"--adw-id {request.adw_id} once it has usage again.")
    if last_stop == "error":
        # Raised, not returned: an error turn's text, if any, came before the
        # error, and parsing it would send corrections into the same failure.
        raise RuntimeError(f"pi ended with an error: "
                           f"{(last_error or stderr.strip() or 'no message')[-800:]}")
    if result.returncode != 0 and not result.text:
        raise RuntimeError(f"pi exited {result.returncode}: {stderr.strip()[-800:]}")
    return result

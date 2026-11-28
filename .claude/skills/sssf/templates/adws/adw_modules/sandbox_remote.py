"""A run's tree in a remote VM (docs/designs/remote-sandboxes.md).

The ADW stays on the host. The VM holds a clone of the run's branch at the
same absolute path as the host worktree, and the run's context_handoff folder
at its host path too, so every path the run passes means the same thing on
both sides. Each command goes through `run` or `start` below:

- `run` is a check, git, or setup. It gets SSSF's own and obs variables, never the
  Claude token. `run`, `commit` and `start` refuse when the env a VM command would
  get holds an API key (ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN): `_env` checks it. No command gets OBS_AUTH_TOKEN (HELD), which would let code in
  the VM copy it into the clone and back to the host branch.
- `start` is an agent. A Claude Code agent gets the token from
  ~/.config/sssf-sandbox/token.env (a roster of pi agents only needs no file); a pi
  agent gets its ChatGPT token as OPENAI_API_KEY, never the Claude token. And the handoff folder is copied in before it and back out after it. A pi
  agent whose events obs should get (`relay_obs`) gets an obs relay of its own
  for the call, on the VM's loopback (obs_relay.py, sssf_obs_relay.cjs).
- After each `git commit`, the new commits come back to the host branch as a
  git bundle, so the host keeps them if the VM dies. The host judges every commit
  first (permissions.judge_commits) from objects its own `git bundle unbundle`
  hashed; a refused bundle lands nothing and is kept as sandbox-refused.bundle.
  A `git commit` run by `commit()` that exits 0 and leaves the VM's HEAD at the
  last sync stops the run (sandbox_commit_not_landed).
- At the end, uncommitted work is saved as a patch in the run's folder, not
  applied, and the VM is deleted.
- Verify runs in a VM of its own (`open_verify`, used through `sandbox.verifying`): the
  work a commit would hold (the real index plus `git add -A`, in a copy of the index) is
  taken out as one patch, judged on the host (`permissions.judge_commits`), and applied
  to the index of a new VM that holds the branch at the last sync; the VM's tree id must
  be the host's before any file is written, and every file is then checked out from that
  index (a clean checkout of the verified tree). `worktree_setup` runs there, then
  every check, and the VM is deleted. It starts no agent, lands no commit, and the build
  VM is not changed. The chain's first round binds it: from then on every commit that
  comes back (a sync or `close()`) must hold the tree of the last green round, except
  at a `wrote` path with the blob the chain hashed, or the bundle is refused; before the first green round nothing lands. A
  red or failed round keeps the last green tree, a green one replaces it, and the
  chain's own commit (`commit`) moves the binding to its tree. Only `end_verified`
  (`sandbox.verify_done`) ends it. Nothing the build VM reports decides any of this.

A provider (sandbox_vercel, sandbox_e2b, sandbox_openshell) makes the VM and hands back an
object with Vercel's SDK calls: run_process, create_process, stop, destroy.
Sessions are capped, so before a command that might not fit in what is left,
the VM is stopped and resumed with a fresh session and its files kept.
SSSF_SANDBOX_SESSION_MINUTES shortens the session, for tests.
"""

from __future__ import annotations

import ast
import base64
import dataclasses
import importlib
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import obs_relay, session_files
from .sandbox import SandboxFailed

TOKEN_FILE = Path.home() / ".config" / "sssf-sandbox" / "token.env"
MAJOR_MINOR = re.compile(r"\d+\.\d+", re.ASCII)
UV_DROPPED = ("UV_PROJECT_ENVIRONMENT",)   # set per run by worktree._own_environment: the run's, not the engineer's
ANSWER_MAX = 200   # how much of a bad Python answer a message keeps
PASSED = ("SSSF_", "OBS_")          # the host variables a VM command gets, with UV_PYTHON
HELD = ("OBS_AUTH_TOKEN",)          # named variables no VM command gets, whatever PASSED says
PI_NO_CACHE = {"JITI_FS_CACHE": "false"}   # must match agent_pi.NO_EXTENSION_CACHE (a test pins it)
PI_NO_COMPILE_CACHE = {"NODE_DISABLE_COMPILE_CACHE": "1"}   # must match agent_pi.NO_COMPILE_CACHE (a test pins it)
# The names Claude Code reads as an API key; they match agent_cc.API_KEY_VARS. A VM
# command whose env holds one is refused.
KEY_NAMES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
AGENT_SECONDS = 30 * 60             # what an agent call may need of a session
COMMAND_SECONDS = 60                # what a command with no timeout may need: git, a read
MARGIN_SECONDS = 30
# How long an SDK call may run past the time its command asked for. The
# SDKs wait on a running command with no timeout of their own, so a dropped
# connection held run 80481cd6 for 15.7 minutes, until the host's TCP gave up.
CALL_SECONDS = 120
# An agent's output stream has no deadline of its own either, and a dropped
# connection can hold it with nothing in flight for TCP to retry. After
# QUIET_SECONDS with no line the host asks the VM whether the agent still runs
# (decisions 28 to 35 in docs/designs/remote-sandboxes.md). Once the agent is
# gone or stopped, its stream gets DRAIN_SECONDS to end before it counts as dead.
QUIET_SECONDS = 120
DRAIN_SECONDS = 10
POLL_SECONDS = 1.0
# The obs relay (decisions 37 to 50): its folder in the VM, outside the clone,
# and how long it may take to say which port it listens on.
OBS_ROOT = "/tmp"
RELAY_START_SECONDS = 10
RELAY_SECONDS = 30          # each of the relay's own SDK calls
# The relay, then its follower on the relay's file as the live stream, in one
# session of their own: one pkill stops both. The relay's port line comes
# first. Both are the same Node script, which waits on the pipe before it
# writes more; `tail` died on the first full pipe, since node makes the
# shared pipe non-blocking. The relay's pid goes beside the session id, so
# the host can tell at the end whether it died during the call.
RELAY_SHELL = ('echo $$ > "$0"; node "$1" "$2" & echo $! > "$0.relay"; '
               'exec node "$1" --follow "$2"')
# Run at the end of a call: says whether the relay and its follower were
# still up, then stops both.
# A zombie counts as down: the relay's parent is the follower, which never
# reaps it. The state is the field after the last ")" in /proc/<pid>/stat.
RELAY_STOP = ('up() { p=$(cat "$1" 2>/dev/null) || return 1; '
              's=$(sed "s/.*) //" "/proc/$p/stat" 2>/dev/null | cut -d" " -f1); '
              '[ -n "$s" ] && [ "$s" != Z ]; }; '
              'r=down; up "$0.relay" && r=up; f=down; up "$0" && f=up; '
              'echo "relay=$r follower=$f"; pkill -KILL -s "$(cat "$0")"')
# The tools a VM must have, by what the roster runs (docs/designs/remote-sandboxes.md,
# "As built: the tool check"). `min` is the oldest version SSSF has run on, or
# the tool's own floor; a VM below it fails before any agent starts. `tested`
# is the newest major SSSF has run on: a newer one is noted in the trace, not
# refused, because Vercel updates its image on its own.
TOOLS = {
    "git":    {"command": "git --version"},
    "uv":     {"command": "uv --version"},
    "claude": {"command": "claude --version", "min": "2.1.312", "tested": 2, "for": "claude_code"},
    "node":   {"command": "node --version", "min": "22.19.0", "for": "pi"},   # pi's engines field
    "pi":     {"command": "NODE_DISABLE_COMPILE_CACHE=1 pi --version", "min": "1.4.0", "tested": 1, "for": "pi"},
}
MISSING = "missing"
FOUND = "found"
# The repo's own commands (quality.py's checks, repo.yaml's worktree_setup) are
# looked up too: the quality.py beside this module, read as text, never run.
QUALITY_FILE = Path(__file__).with_name("quality.py")
QUALITY_MAX = 1024 * 1024
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63}", re.ASCII)
# A constant script: names are positional arguments, never part of its text.
LOOKUP = 'for n in "$@"; do if command -v "$n" >/dev/null 2>&1; then echo y; else echo n; fi; done'
SKIPPED_MAX = 50
# The pi the 2026-11-03 ChatGPT live check ran. Both image scripts (the OpenShell
# Containerfile and scripts/e2b_template.py) read it.
PI_VERSION = "1.5.0"


def _version(text: str) -> tuple[int, ...] | None:
    found = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(n) for n in found.groups() if n is not None) if found else None


def harnesses(cfg) -> set[str]:
    """The coding agents a roster uses."""
    return {a.coding_agent for a in getattr(cfg, "agents", None) or []}


def tools_needed(cfg) -> list[str]:
    """The TOOLS a roster's agents need in the VM, in TOOLS order."""
    agents = harnesses(cfg)
    return [name for name, tool in TOOLS.items() if tool.get("for") in (None, *agents)]


# --- the repo's own commands, read as text ------------------------------------
# The tool check also looks up, in the VM, the command names quality.py's checks
# and repo.yaml's worktree_setup run. quality.py is parsed, never imported or
# called; whatever can't be judged gets a row in `skipped`.

def _row(source: str, what, why: str) -> dict[str, str]:
    return {"from": source, "what": str(what)[:120], "why": why}


def _argv_commands(value: ast.expr, source: str, commands: list, skipped: list) -> None:
    """Read one `argv=` value: commands are (source, text, is_shell) triples."""
    shown = ast.unparse(value)
    if isinstance(value, (ast.List, ast.Tuple)) and value.elts \
            and isinstance(value.elts[0], ast.Constant) and isinstance(value.elts[0].value, str):
        first = value.elts[0].value
        commands.append((source, first, False))
        if first in ("sh", "bash") and len(value.elts) >= 2:
            flag = value.elts[1]
            if len(value.elts) >= 3 and isinstance(flag, ast.Constant) and flag.value == "-c":
                third = value.elts[2]
                if not (isinstance(third, ast.Constant) and isinstance(third.value, str)):
                    skipped.append(_row(source, shown, "shell string isn't a literal"))
                elif first == "sh":
                    commands.append((source, third.value, True))
                else:
                    # bash builtins (pushd, shopt, let) are unknown to the sh the lookup runs under
                    skipped.append(_row(source, shown, "bash string: looked up under sh"))
            else:
                skipped.append(_row(source, shown, "shell flags aren't read"))
        return
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "_placeholder":
        skipped.append(_row(source, shown, "placeholder"))
    else:
        skipped.append(_row(source, shown, "argv isn't a literal list"))


def _argv_calls(node: ast.AST) -> list[ast.expr]:
    return [kw.value for call in ast.walk(node) if isinstance(call, ast.Call)
            for kw in call.keywords if kw.arg == "argv"]


def _holds_spec(node: ast.AST) -> bool:
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            func = call.func
            if any(kw.arg == "argv" for kw in call.keywords) \
                    or (isinstance(func, ast.Name) and func.id == "QualityCheckSpec") \
                    or (isinstance(func, ast.Attribute) and func.attr == "QualityCheckSpec"):
                return True
    return False


def quality_commands(path: Path) -> tuple[list[tuple[str, str, bool]], list[dict[str, str]]]:
    """The commands quality.py's blocks run, read as text: `(commands, skipped)`.
    A command is `(source, text, is_shell)`. Nothing is imported or run."""
    commands: list[tuple[str, str, bool]] = []
    skipped: list[dict[str, str]] = []
    try:
        if path.stat().st_size > QUALITY_MAX:
            return [], [_row("quality.py", path.name, "quality.py is over 1 MiB")]
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, RecursionError, MemoryError) as error:
        return [], [_row("quality.py", f"{path.name}: {type(error).__name__}", "quality.py couldn't be read")]
    defs: dict[str, ast.AST] = {}
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs[node.name] = node
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
    # A def under if/try/with/for/while/match may not be the one the host runs: not read.
    nested = {inner.name for node in tree.body
              if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
              for inner in ast.walk(node) if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))}
    entries = {name: defs[name] for name in ("run_quality", "run_tests") if name in defs}
    loaded: dict[str, None] = {}
    for entry in entries.values():
        for n in ast.walk(entry):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                loaded[n.id] = None
    listed: dict[str, ast.AST] = {}
    for name in loaded:
        if name in entries:
            continue
        if name in defs:
            listed[name] = defs[name]
        elif name in nested:
            skipped.append(_row(f"quality:{name}", name, "defined under if/try/with"))
        elif name in bound:
            skipped.append(_row(f"quality:{name}", name, "not a def"))
    for name, node in defs.items():
        if name not in entries and name not in listed and _holds_spec(node):
            skipped.append(_row(f"quality:{name}", name, "not named in run_quality or run_tests"))
    if not listed:
        skipped.append(_row("quality.py", path.name, "no block named in run_quality or run_tests"))
    for name, node in {**entries, **listed}.items():
        calls = _argv_calls(node)
        if not calls and name in listed:
            skipped.append(_row(f"quality:{name}", name, "no argv= list"))
        for value in calls:
            _argv_commands(value, f"quality:{name}", commands, skipped)
    return commands, skipped


SHELL_PLAIN = re.compile(r"[A-Za-z0-9_ \t./:@%+,=&|;-]*")
SHELL_BARE = {"export", ".", "source", "alias", "eval", "hash", "set", "unset"}


def _segments(text: str) -> list[list[str]] | None:
    """The word lists of a plain shell string's segments, or None when it isn't read."""
    if not SHELL_PLAIN.fullmatch(text) or "PATH" in text:
        return None
    segments = []
    for part in re.split(r"&&|\|\||;|\|", text):
        words = part.split()
        if not words or "&" in part:
            return None
        if "=" in words[0] or words[0] in SHELL_BARE:
            return None
        segments.append(words)
    return segments


def shell_words(text: str) -> list[str] | None:
    """The first word of each segment of a plain shell string; None when skipped."""
    segments = _segments(text)
    return None if segments is None else [words[0] for words in segments]


def _may_install(name: str, pool: list[str]) -> bool:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    return any(pattern.search(text) for text in pool)


def repo_commands(quality_file: Path, setup: list[str], known) -> tuple[dict[str, str], list[dict[str, str]]]:
    """`(names, skipped)`: the command names to look up in the VM, each mapped to
    the source that named it first (quality.py's first), and a row for what isn't."""
    commands, skipped = quality_commands(quality_file)
    pool: list[str] = []
    for index, entry in enumerate(setup):
        segments = _segments(str(entry))
        source = f"worktree_setup:{index}"
        if segments is None:
            pool.append(str(entry))
            skipped.append(_row(source, entry, "not a plain shell string"))
            continue
        pool.extend(word for words in segments for word in words[1:])
        commands.extend((source, words[0], False) for words in segments)
    names: dict[str, str] = {}
    for source, text, is_shell in commands:
        if is_shell:
            words = shell_words(text)
            if words is None:
                skipped.append(_row(source, text, "not a plain shell string"))
                continue
            # a shell string inside quality.py: its first words are commands too
            candidates = words
        else:
            candidates = [text]
        for name in candidates:
            if "/" in name:
                skipped.append(_row(source, name, "a path"))
            elif not NAME.fullmatch(name):
                skipped.append(_row(source, name, "not a plain command name"))
            elif name in known:
                continue
            elif _may_install(name, pool):
                skipped.append(_row(source, name, "worktree_setup may install it"))
            else:
                names.setdefault(name, source)
    return names, skipped


def judge_tools(found: dict[str, str]) -> tuple[list[str], list[str]]:
    """What stops the run, and what is only noted, for the versions found."""
    problems, notes = [], []
    for name, text in found.items():
        tool, version = TOOLS[name], _version(text)
        if text == MISSING or version is None:
            problems.append(f"{name} is {'missing' if text == MISSING else f'unreadable ({text[:60]!r})'}")
            continue
        if "min" in tool and version < _version(tool["min"]):
            problems.append(f"{name} is {'.'.join(map(str, version))}, and SSSF needs {tool['min']} or later")
        elif "tested" in tool and version[0] > tool["tested"]:
            notes.append(f"{name} {'.'.join(map(str, version))} is a newer major than SSSF has run on "
                         f"({tool['tested']}.x); the run goes on")
    return problems, notes


CPU_READ = ("awk -v hz=\"$(getconf CLK_TCK)\" "
            "'/^cpu /{printf \"%.2f\\n\", ($2+$3+$4+$7+$8+$9)/hz; exit}' /proc/stat")


def require_sdk(provider: str, module: str, requirement: str) -> None:
    """Make sure the provider's SDK can be imported, before the run starts.

    The chains don't declare it, so a run without --sandbox never fetches it.
    A sandbox run without it starts again once, under `uv run --with`. The start again
    happens in session.ensure's `sandbox.prepare`, before `worktree.enter`, so the working
    folder is the launch folder. From inside a worktree it refuses: uv would read that
    folder's uv.toml, .python-version and .env, which a VM run's agent can commit."""
    try:
        importlib.import_module(module)
    except ImportError:
        if os.environ.get("SSSF_SANDBOX_SDK"):
            raise SystemExit(f"--sandbox {provider} needs {requirement}, and uv could not add it")
        from . import worktree
        try:
            inside = worktree.in_run_worktree()
        except worktree.WorktreeError as error:
            inside = False
            if worktree.checkout_here():
                raise SystemExit(f"SSSF won't fetch {requirement} from {Path.cwd()}: git can't "
                                 f"answer there ({error}). Launch from the main checkout.") from None
        if inside:
            raise SystemExit(f"SSSF won't fetch {requirement} from inside the worktree {Path.cwd()}, "
                             "because uv would read that folder's config. Launch from the main "
                             f"checkout, {worktree.main_checkout()}.")
        os.environ["SSSF_SANDBOX_SDK"] = "1"
        os.execvp("uv", ["uv", "run", "--quiet", "--with", requirement, sys.argv[0], *sys.argv[1:]])


def _token() -> str:
    if not TOKEN_FILE.is_file():
        raise SandboxFailed(f"no Claude token at {TOKEN_FILE}. Run `claude setup-token` in a "
                            "terminal and save CLAUDE_CODE_OAUTH_TOKEN=<token> there, mode 600.")
    for line in TOKEN_FILE.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "CLAUDE_CODE_OAUTH_TOKEN" and value.strip():
            return value.strip().strip('"').strip("'")
    raise SandboxFailed(f"{TOKEN_FILE} has no CLAUDE_CODE_OAUTH_TOKEN line")


def _key_check(env) -> None:
    """Refuse when `env` holds an API key under a name Claude Code reads."""
    for name in KEY_NAMES:
        if env.get(name):
            raise SandboxFailed(f"{name} would reach the VM, and there it would win over the "
                                "subscription token. Unset it before a sandbox run.")


def credentials(keys: tuple[str, ...], reaches=None) -> None:
    """`reaches` is the env a VM command would get; None judges the whole host env."""
    missing = [k for k in keys if not os.environ.get(k)]
    if missing:
        raise SandboxFailed(f"{', '.join(missing)} not set. Put them in the repo's .env; "
                            "see docs/designs/remote-sandboxes.md, decision 19.")
    _key_check(os.environ if reaches is None else reaches)


def _host_git(*args: str, cwd: Path) -> str:
    from .utils import GIT_NO_PROGRAMS
    done = subprocess.run(["git", *GIT_NO_PROGRAMS, *args], cwd=cwd, capture_output=True, text=True)
    if done.returncode != 0:
        raise SandboxFailed(f"git {' '.join(args)} failed on the host: {done.stderr.strip()}")
    return done.stdout.strip()


def _host_git_with(args: list[str], cwd: Path, env: dict, input: bytes | None = None) -> str:
    """`_host_git` with extra environment and bytes on stdin. Same no-programs flags."""
    from .utils import GIT_NO_PROGRAMS
    done = subprocess.run(["git", *GIT_NO_PROGRAMS, *args], cwd=cwd, capture_output=True,
                          input=input, env={**os.environ, **env})
    if done.returncode != 0:
        raise SandboxFailed(f"git {' '.join(args)} failed on the host: "
                            f"{done.stderr.decode(errors='replace').strip()}")
    return done.stdout.decode(errors="replace").strip()


def _bundle_head(data: bytes, synced: str) -> str:
    """The head sha in a bundle's header, after checking its shape: version 2 or 3,
    no `@filter`, one prerequisite and it is `synced`, one `HEAD` ref. Anything
    else raises SandboxFailed. The VM's own answer for HEAD is never used."""
    from . import permissions
    head = data.split(b"\n\n", 1)[0].decode("utf-8", "replace").split("\n")
    if not head or head[0] not in ("# v2 git bundle", "# v3 git bundle"):
        raise SandboxFailed("the VM's bundle has no v2 or v3 bundle header")
    needs = [line[1:].split(" ", 1)[0] for line in head[1:] if line.startswith("-")]
    refs = [line for line in head[1:] if line and line[0] not in "-@"]
    if any(line.startswith("@filter") for line in head[1:]):
        raise SandboxFailed("the VM's bundle holds a filter, which the host won't unpack")
    if needs != [synced] or len(refs) != 1 or not refs[0].endswith(" HEAD"):
        raise SandboxFailed("the VM's bundle is not one HEAD on top of the last sync")
    tip = refs[0].split(" ", 1)[0]
    if not permissions._SHA.match(tip):
        raise SandboxFailed("the VM's bundle head is not a commit id")
    return tip


def _host_python(tree: Path, problems: list[str] | None = None) -> str:
    """The Python, as major.minor, that the host's uv picks in the tree.

    Always called with the main checkout, never a run's worktree: an agent's commit can set
    every file uv reads to pick a Python (`.python-version`, `.python-versions`, `.venv/`,
    `uv.toml`, `pyproject.toml`), and `.python-version` can name a program. From the
    main checkout those files and their parents are the engineer's; so are
    `~/.config/uv/`, `/etc/uv/` and the environment (UV_PYTHON, UV_PYTHON_PREFERENCE,
    UV_CONFIG_FILE, VIRTUAL_ENV, PATH ...), except UV_PROJECT_ENVIRONMENT, which a host
    `just rebase` sets per run and which is dropped for this call.

    The VM's uv would pick its own: a `.python-version` is often untracked,
    so the clone doesn't have it, and the VM's image has another Python. A
    repo's tests can depend on the version, as argparse's help text does.
    The interpreter runs with `-I -S`, so nothing at its startup can print.
    "" when the host has no uv or no Python. "" too, with one line added to
    `problems`, when the answer isn't a major.minor."""
    problem = ""
    try:
        extra = {}
        if any(name in os.environ for name in UV_DROPPED):   # else the call is as it always was
            extra["env"] = {k: v for k, v in os.environ.items() if k not in UV_DROPPED}
        found = subprocess.run(["uv", "python", "find"], cwd=tree, capture_output=True,
                               text=True, timeout=60, **extra)
        if found.returncode != 0:
            return ""
        interpreter = found.stdout.strip()
        version = subprocess.run([interpreter, "-I", "-S", "-c",
                                  "import sys; print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True, errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    except ValueError as error:     # uv's stdout didn't decode, or held a NUL
        problem = f"`uv python find` printed no usable path ({str(error)[:ANSWER_MAX]!r})"
    else:
        if version.returncode != 0:
            return ""
        answer = version.stdout.strip()
        if MAJOR_MINOR.fullmatch(answer):
            return answer
        problem = (f"the interpreter uv picks, {interpreter[:ANSWER_MAX]!r}, printed "
                   f"{answer[:ANSWER_MAX]!r}, not a Python major.minor")
    if problems is not None:
        problems.append(problem)
    return ""


def _tar(folder: Path) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(folder, arcname=".")
    return buffer.getvalue()


def _git_verb(argv: list[str]) -> str:
    """The subcommand of a git argv, past any `-c key=value` options."""
    if not argv or argv[0] != "git":
        return ""
    i = 1
    while i < len(argv) and argv[i] == "-c":
        i += 2
    return argv[i] if i < len(argv) else ""


class RemoteProcess:
    """An agent in the VM. Its pid is a negative stand-in, unique in the run:
    the trace records it, and `just kill` knows not to signal it on the host.

    Its output is read by a thread of its own, so the runner's read loop can
    stop waiting on a stream that has gone dead. When it does, the silence
    watchdog the runner watches it with (`watched`) is told, and the runner
    raises SilentStop for it."""

    def __init__(self, box: "Remote", process, sid_file: str, pid: int, relay: "_Relay | None" = None):
        self._box, self._process, self._sid_file = box, process, sid_file
        self._relay = relay
        self.pid = pid
        self._lines: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._abandoned = False       # the read ended before the stream did
        self._silent = False          # the VM didn't answer the probe
        self._dog = None
        self._done = False
        self.started = time.time()
        threading.Thread(target=self._pump, args=(process.stdout,), daemon=True,
                         name=f"sssf-{box.name}-stream").start()
        self.stdout = self._read()
        self.stderr = _Stderr(self, process.stderr) if process.stderr is not None else None

    def watched(self, dog) -> None:
        self._dog = dog

    def _pump(self, source) -> None:
        try:
            for line in source:
                self._lines.put(line)
        except BaseException as error:      # the runner sees the stream's own error
            self._lines.put(error)
        finally:
            self._lines.put(None)

    def _read(self):
        heard = time.monotonic()
        ending = None                       # (since, why): the agent is gone or stopped
        while True:
            try:
                item = self._lines.get(timeout=POLL_SECONDS)
            except queue.Empty:
                now = time.monotonic()
                if ending is None and self._stopped.is_set():
                    ending = (now, "")
                if ending is None and now - heard >= QUIET_SECONDS:
                    alive = self._alive()
                    if alive:
                        heard = time.monotonic()
                        continue
                    if alive is None:
                        self._silent = True
                        self._lose("didn't answer when SSSF checked on the agent")
                        return
                    ending = (time.monotonic(), "said the agent had exited, but its stream never ended")
                if ending and time.monotonic() - ending[0] >= DRAIN_SECONDS:
                    self._abandoned = True
                    if ending[1]:
                        self._lose(ending[1])
                    return
                continue
            if item is None:
                return
            if isinstance(item, BaseException):
                raise item
            heard = time.monotonic()
            yield item

    def _alive(self) -> bool | None:
        """Whether the agent's session still has a live process (a zombie
        doesn't count), read from /proc so it needs no `ps`. None when the VM
        doesn't answer. A probe that can't tell says alive: keep reading, and
        the silence watchdog bounds it."""
        try:
            done = self._box._call(self._box.sandbox.run_process, "sh", ["-c", _PROBE, self._sid_file],
                                   capture_output=True)
        except Exception:
            return None
        return (done.stdout or "").strip() != "gone"

    def _lose(self, why: str) -> None:
        self._abandoned = True
        if self._dog is not None:
            self._dog.lose(why)
        if not self._stopped.is_set():
            try:
                self.stop_tree()            # once; the VM's end stops it otherwise
            except Exception:
                pass

    def wait(self) -> int:
        # Once stopped, or with its stream dead, the agent is gone or the VM may
        # be: a short wait, and no error of its own to hide why the call ended.
        # Otherwise, past the session's end the provider has stopped the VM.
        quick = self._abandoned or self._stopped.is_set()
        reach = not self._silent
        try:
            code = self._box._call(self._process.wait,
                                   deadline=CALL_SECONDS if quick else self._box.limit + CALL_SECONDS)
        except Exception:
            reach = False                   # the VM may be gone: ask it nothing more for obs
            if not quick:
                self._close_relay(reach)
                raise
            code = -1
        self._close_relay(reach)            # the agent has exited, so its extension has flushed
        if not self._done:
            self._done = True
            try:
                self._box._call_end(self.started)
            except Exception:
                pass
            try:
                self._box.handoff_out()
            except Exception:
                if not (self._dog is not None and self._dog.hung):
                    raise           # a call that keeps its result needs its handoff
        return code

    def _close_relay(self, reach: bool) -> None:
        if self._relay is not None:
            relay, self._relay = self._relay, None
            self._box._close_relay(relay, reach=reach)

    def kill(self) -> None:
        self.stop_tree()

    def stop_tree(self) -> None:
        # Started under setsid, so the agent and everything it ran share its session.
        self._stopped.set()
        self._box._call(self._box.sandbox.run_process,
                        "sh", ["-c", f'pkill -KILL -s "$(cat {self._sid_file})"'], capture_output=True)


# Run once in the verify VM's fresh clone with the patch on stdin ($1 the last sync, $2 the
# host's tree id). The patch goes into the index, the tree id is compared before any file
# is written, then every file is checked out from that index under its own .gitattributes,
# and the index goes back to HEAD (the last sync) so the patch reads as uncommitted work.
_VERIFY_CHECKOUT = r"""set -e
git rm -r -q -f --ignore-unmatch .
git read-tree "$1"
git apply --cached --binary --whitespace=nowarn -
got=$(git write-tree)
if [ "$got" != "$2" ]; then echo "the verify VM built tree $got, not $2" >&2; exit 3; fi
git checkout-index -f -a
git reset -q
"""


# Prints "alive" or "gone" for the session whose id is in the file $0 names.
# /proc/<pid>/stat is "pid (comm) state ppid pgrp session ...", and comm can
# hold spaces or parentheses, so the fields are read after its last ")".
_PROBE = r"""sid=$(cat "$0") || exit 3
for f in /proc/[0-9]*/stat; do
  read -r line < "$f" 2>/dev/null || continue
  set -- ${line##*) }
  if [ "$4" = "$sid" ] && [ "$1" != Z ]; then echo alive; exit 0; fi
done
echo gone"""


class _Relay:
    """An obs relay in the VM, for one agent call. A thread reads its stdout:
    the first line says its port, and each line after it is an event, which
    goes to the call's Forwarder as it comes."""

    def __init__(self, process, sid_file: str, path: str, forwarder: obs_relay.Forwarder):
        # the relay's port line, then tail's lines from the relay's file
        self.sid_file, self.path, self.forwarder = sid_file, path, forwarder
        self.url = ""
        self.live = True                    # the live stream ended as it should
        self.ended = threading.Event()
        self._port: queue.Queue = queue.Queue()
        threading.Thread(target=self._pump, args=(process.stdout,), daemon=True,
                         name="sssf-obs-relay-stream").start()

    def _pump(self, source) -> None:
        first = True
        try:
            for line in source:
                if first:
                    first = False
                    self._port.put(line)
                else:
                    self.forwarder.feed(line)
        except BaseException:
            self.live = False
        finally:
            self._port.put(None)
            self.ended.set()

    def wait_port(self, seconds: float) -> int | None:
        try:
            line = self._port.get(timeout=seconds)
            port = json.loads(line)["sssf_obs_relay"] if line else None
        except (queue.Empty, ValueError, TypeError, KeyError):
            return None
        return port if isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536 else None


class _Stderr:
    """The agent's stderr. Read in full once its stdout has ended, as before;
    after a stop or a dead stream, read with a deadline, and empty if it has none."""

    def __init__(self, process: RemoteProcess, source):
        self._process, self._source = process, source

    def read(self) -> str:
        if not (self._process._abandoned or self._process._stopped.is_set()):
            return self._source.read()
        try:
            return self._process._box._call(self._source.read, deadline=DRAIN_SECONDS) or ""
        except Exception:
            return ""


class Remote:
    """What every provider shares. A provider sets the class attributes and
    writes `_create` and `_delete`."""

    name = ""
    keys: tuple[str, ...] = ()
    session_minutes = 45
    owner = ""                  # the VM's own user, who gets the host paths
    errors: tuple[type[BaseException], ...] = ()

    def __init__(self, run, branch: str, project: str):
        self.run_ = run
        self.tree = Path.cwd()
        self.handoff = Path(run.context_handoff_dir)
        self.branch = branch
        self.project = project
        self.limit = float(os.environ.get("SSSF_SANDBOX_SESSION_MINUTES") or self.session_minutes) * 60
        self.sandbox = None
        self.vm = ""
        self.refreshes = 0
        self.processes = 0
        self.installed: set[str] = set()    # paths install() has copied in
        self._obs_target: obs_relay.Target | None = None   # for the next start only
        self._obs_dir = ""
        self._relays: set[_Relay] = set()
        self.synced = ""
        # None: not bound; "": bound, no green round yet; else the last green round's tree.
        self._verified: str | None = None
        self.python = ""
        self.python_problem = ""
        self.tools: dict[str, str] = {}
        self._token = ""
        self._created = 0.0
        self._session = 0.0
        self.cpu_seconds: float | None = 0.0
        self._cpu_base: float | None = None

    # What a provider adds to a pi agent's env (`start` only).
    pi_env: dict[str, str] = {}
    # What a provider adds to a Claude Code agent's env: fixed values only.
    cc_env: dict[str, str] = {}

    # --- what a provider writes ---------------------------------------------

    def _create(self):
        """Make the VM, tagged with the project and adw_id. Returns the handle."""
        raise NotImplementedError

    def _delete(self) -> None:
        raise NotImplementedError

    def _call_end(self, started: float) -> None:
        """What a provider does when an agent call ends: `started` is the call's
        start, from time.time(). Must not raise."""
        return

    def _end(self, seconds: float) -> dict:
        """What the provider adds to the run's `sandbox_end` event."""
        return {}

    # --- lifecycle -------------------------------------------------------

    def open(self) -> None:
        """Make the VM and put the run's branch in it. Raises SandboxFailed."""
        credentials(self.keys, {**self._env(None), **self.pi_env, **self.cc_env})
        if harnesses(getattr(self.run_, "cfg", None)) != {"pi"}:
            self._token = _token()
        problems: list[str] = []
        from . import worktree
        try:
            main = worktree.main_checkout()
        except worktree.WorktreeError as error:
            if worktree.checkout_here():
                raise SandboxFailed(f"cannot find the main checkout for the Python lookup: {error}") from None
            main = self.tree                    # not a repo at all: nothing of a run's to read
        self.python = _host_python(main, problems)
        self.python_problem = problems[0] if problems else ""
        try:
            self.sandbox = self._create()
            self.vm = self.sandbox.name
            self._created = self._session = time.monotonic()
            self._cpu_base = self._cpu_now()
            self._log("sandbox", {"provider": self.name, "vm": self.vm,
                                  "session_minutes": self.limit / 60, "python": self.python})
            self._clone()
            self.handoff_in()
            self._check_tools()
        except self.errors as error:
            raise SandboxFailed(f"{self.name} would not make or set up the VM: {error}") from None

    def _repo_names(self, known: list[str]) -> tuple[dict[str, str], list[dict[str, str]]]:
        """The repo's command names to look up (name -> source) and the rows for what
        can't be judged. Reads quality.py as text and repo.yaml with yaml.safe_load."""
        setup: list[str] = []
        skipped: list[dict[str, str]] = []
        try:
            from . import repo_settings, worktree
            setup = list(repo_settings.load(worktree.main_checkout()).worktree_setup or [])
        except Exception as error:
            skipped.append(_row("repo.yaml", type(error).__name__, "repo.yaml couldn't be read"))
        try:
            names, more = repo_commands(QUALITY_FILE, setup, set(known))
        except Exception as error:
            return {}, skipped + [_row("quality.py", type(error).__name__, "the commands couldn't be read")]
        return names, skipped + more

    def _check_tools(self) -> None:
        """Read the versions of the tools this run needs, in one call, before any
        agent starts, then look up the command names the repo's quality.py checks
        and worktree_setup run. Records them in the trace; raises SandboxFailed if
        one is missing or too old, so the run fails here, not partway through a call."""
        names = tools_needed(getattr(self.run_, "cfg", None))
        line = "; ".join(f"printf '%s=' {name}; {{ {TOOLS[name]['command']} 2>&1 || echo {MISSING}; }} | head -1"
                         for name in names)
        out = self._must("sh", ["-c", line])
        found = {name: MISSING for name in names}
        for row in out.splitlines():
            name, _, text = row.partition("=")
            if name in found:
                text = text.strip()
                found[name] = MISSING if not text or "not found" in text else text
        problems, notes = judge_tools(found)
        wanted, skipped = self._repo_names(names)
        tools, missing = dict(found), []
        if wanted:
            listed = list(wanted)
            out = self._must("sh", ["-c", LOOKUP, "sh", *listed])
            answers = out.split()
            if len(answers) == len(listed) and all(a in ("y", "n") for a in answers):
                for name, answer in zip(listed, answers):
                    tools[name] = FOUND if answer == "y" else MISSING
                    if answer == "n":
                        source, _, block = wanted[name].partition(":")
                        runs = f"quality.py's `{block}` check runs it" if source == "quality" \
                            else "worktree_setup runs it"
                        missing.append(f"{name} is missing; {runs}")
            else:
                skipped += [_row("vm", name, "the VM's answer couldn't be read") for name in listed]
        if len(skipped) > SKIPPED_MAX:
            rest = len(skipped) - (SKIPPED_MAX - 1)
            skipped = skipped[:SKIPPED_MAX - 1] + [_row("", "", f"{rest} more skips not listed")]
        self.tools = tools
        self._log("sandbox_tools", {"vm": self.vm, "tools": tools, "notes": notes,
                                    "problems": problems + missing, "skipped": skipped})
        for note in notes:
            self.run_.console.note(note)
        if missing:
            raise SandboxFailed(f"{self.name} VM {self.vm} can't run this repo's checks or setup: "
                                f"{'; '.join(problems + missing)}. Nothing ran in it. Add the command to "
                                "the VM's image or change the command; see docs/designs/remote-sandboxes.md, "
                                "\"As built: the tool check\".")
        if problems:
            raise SandboxFailed(f"{self.name} VM {self.vm} can't run this roster: {'; '.join(problems)}. "
                                "Nothing ran in it. Pick the other provider, or see "
                                "docs/designs/remote-sandboxes.md, \"As built: the tool check\".")

    def _clone(self) -> None:
        bundle = self.run_.session_dir / "sandbox-in.bundle"
        _host_git("bundle", "create", "-q", str(bundle), self.branch, cwd=self.tree)
        name = _host_git("config", "user.name", cwd=self.tree)
        email = _host_git("config", "user.email", cwd=self.tree)
        # The run's session folder too, not only the handoff in it: a pi agent
        # writes its session files beside the handoff (<session>/<agent>/pi_sessions).
        self._must("sh", ["-c", f'mkdir -p "$1" "$2" "$3" && chown -R {self.owner} "$1" "$2" "$3"',
                          "sh", str(self.tree), str(self.handoff), str(self.run_.session_dir)],
                   sudo=True)
        self._must("sh", ["-c", "cat > /tmp/sssf-in.bundle"], input=bundle.read_bytes())
        self._must("git", ["clone", "-q", "--branch", self.branch, "/tmp/sssf-in.bundle", str(self.tree)])
        for args in (["remote", "remove", "origin"], ["config", "user.name", name],
                     ["config", "user.email", email]):
            self._must("git", args, cwd=str(self.tree))
        self.synced = self._must("git", ["rev-parse", "HEAD"], cwd=str(self.tree)).strip()
        bundle.unlink()

    def close(self, stop: bool = False) -> None:
        """Bring back what the run made, then delete the VM.

        Raises only PermissionBreach, and only with `stop=True` (`Run.finish`): the
        VM's commits were refused. The VM is deleted first either way. The `atexit`
        call tells the engineer instead of raising."""
        from . import permissions
        if self.sandbox is None:
            return
        patch = ""
        breach = None
        for relay in list(self._relays):    # a call the runner left before its wait()
            self._close_relay(relay)
        try:
            self.commits_out()
        except permissions.PermissionBreach as error:
            breach = error
        except Exception as error:
            self._log("sandbox_error", {"vm": self.vm, "error": str(error)[-500:]})
        try:
            done = self._call(
                self.sandbox.run_process, "sh", ["-c", "git add -A && git diff --cached --binary HEAD"],
                cwd=str(self.tree), capture_output=True)
            if done.returncode == 0 and (done.stdout or "").strip():
                path = self.run_.session_dir / "sandbox-uncommitted.patch"
                session_files.write(path, done.stdout, self.run_.data_root)
                patch = str(path)
        except Exception as error:   # the VM may already be gone; deleting it still matters
            self._log("sandbox_error", {"vm": self.vm, "error": str(error)[-500:]})
        self._cpu_add()
        seconds = round(time.monotonic() - self._created, 1)
        try:
            self._call(self._delete)
            deleted = True
        except Exception as error:
            deleted = False
            self._log("sandbox_error", {"vm": self.vm, "error": f"delete failed: {error}"[-500:]})
        self._log("sandbox_end", {"provider": self.name, "vm": self.vm, "vm_seconds": seconds,
                                  "refreshes": self.refreshes, "deleted": deleted,
                                  "uncommitted_patch": patch,
                                  "cpu_seconds": None if self.cpu_seconds is None else round(self.cpu_seconds, 1),
                                  **self._end(seconds)})
        if patch:
            self.run_.console.note(f"uncommitted work from the VM is saved, not applied: {patch}")
            self._patch_note(patch)
        self.sandbox = None
        if breach is not None:
            if stop:
                raise breach
            tell = getattr(self.run_.console, "advise", self.run_.console.note)
            tell(f"The VM's commits were refused and did not land: {breach}")

    def _patch_note(self, patch: str) -> None:
        """Name the paths in the saved patch that no agent may write, or that are
        inside the git directory. Applies nothing."""
        from . import permissions
        cfg = getattr(self.run_, "cfg", None)
        if cfg is None:
            return
        try:
            done = subprocess.run([*permissions._REF_READ, "apply", "--numstat", "-z", patch],
                                  cwd=self.tree, capture_output=True)
            if done.returncode != 0:
                raise ValueError(done.stderr)
            words = done.stdout.decode("utf-8", "surrogateescape").split("\0")
            paths, i = [], 0
            while i < len(words):
                head = words[i].split("\t", 2)
                if len(head) == 3 and head[2] == "":       # a rename: both paths follow
                    paths += words[i + 1:i + 3]
                    i += 3
                else:
                    paths += head[2:3]
                    i += 1
        except Exception:
            self.run_.console.note("couldn't list the paths in the VM's patch: read it before "
                                   "you apply it")
            return
        bad = [p for p in dict.fromkeys(paths) if p and (
            permissions._in_git_dir(p)
            or not any(permissions.permitted(p, a, cfg) for a in cfg.agents))]
        if bad:
            shown = ", ".join(permissions._said_line(p) for p in bad[:20])
            more = f" and {len(bad) - 20} more" if len(bad) > 20 else ""
            self.run_.console.note(
                f"the patch holds paths no agent may write, read it before you apply it: "
                f"{shown}{more}")

    # --- the Sandbox protocol -----------------------------------------------

    def run(self, command, *, cwd=None, env=None, timeout=None,
            shell: bool = False, text: bool = True) -> subprocess.CompletedProcess:
        return self._run(command, cwd=cwd, env=env, timeout=timeout, shell=shell, text=text)

    def _run(self, command, *, cwd=None, env=None, timeout=None, shell: bool = False,
             text: bool = True, sync=None) -> subprocess.CompletedProcess:
        argv = ["sh", "-c", command] if shell else ([command] if isinstance(command, str) else list(command))
        self._refresh(timeout or COMMAND_SECONDS)
        started = time.monotonic()
        done = self._call(self.sandbox.run_process, argv[0], argv[1:], cwd=self._cwd(cwd),
                          env=self._env(env), capture_output=True, kill_after=timeout,
                          deadline=(timeout or COMMAND_SECONDS) + CALL_SECONDS)
        out, err = done.stdout or "", done.stderr or ""
        if timeout and done.returncode != 0 and time.monotonic() - started >= timeout - 1:
            raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
        if done.returncode == 0 and _git_verb(argv) == "commit":
            (sync or self.commits_out)()
        if not text:
            out, err = out.encode(), err.encode()
        return subprocess.CompletedProcess(command, done.returncode, out, err)

    def start(self, command: list[str], *, cwd=None, env=None) -> RemoteProcess:
        target, self._obs_target = self._obs_target, None    # this start's, used or not
        argv = list(command)
        if Path(argv[0]).is_absolute():
            argv[0] = Path(argv[0]).name        # the host's install path means nothing in the VM
        agent_env = self._env(env)
        self._refresh(AGENT_SECONDS)
        self.handoff_in()
        self.processes += 1
        relay = self._open_relay(target) if target is not None else None
        sid_file = f"/tmp/sssf-{uuid.uuid4().hex}.sid"
        if "OPENAI_API_KEY" in agent_env:    # only _env's rename of agent_pi's token sets this
            agent_env.update(self.pi_env)
            agent_env.update(PI_NO_CACHE)
            agent_env.update(PI_NO_COMPILE_CACHE)
        else:
            agent_env.update(self.cc_env)
            if self._token:
                agent_env["CLAUDE_CODE_OAUTH_TOKEN"] = self._token
        if relay is not None:
            agent_env["OBS_SERVER_URL"] = relay.url
            agent_env.pop("OBS_DISABLE", None)
        try:
            # -w: where the exec is a process-group leader, as on OpenShell,
            # setsid forks, and without it the exec ends at once with code 0.
            process = self._call(self.sandbox.create_process, "setsid",
                                 ["-w", "sh", "-c", 'echo $$ > "$0"; exec "$@"', sid_file, *argv],
                                 cwd=self._cwd(cwd), env=agent_env)
        except BaseException:
            if relay is not None:
                self._close_relay(relay)
            raise
        return RemoteProcess(self, process, sid_file, -self.processes, relay)

    # --- the obs relay (decisions 37 to 50) ------------------------------------

    def relay_obs(self, target: obs_relay.Target) -> None:
        """Give the next agent this starts an obs relay posting to `target`.
        Only the next start: it takes the request whether it uses it or not."""
        self._obs_target = target

    def _open_relay(self, target: obs_relay.Target) -> _Relay | None:
        """Start a relay in a session of its own, so the stream probe never
        takes it for the agent. None, with one warning, if it won't start;
        the agent then runs with obs off. The script goes in for each call,
        so a VM that lost /tmp in a refresh still gets one."""
        try:
            if not self._obs_dir:
                self._obs_dir = f"{OBS_ROOT.rstrip('/')}/sssf-obs-{uuid.uuid4().hex}"
            self._must("sh", ["-c", 'mkdir -p "$0" && cat > "$0/relay.cjs"', self._obs_dir],
                       input=obs_relay.RELAY_SCRIPT.read_bytes())
            name = uuid.uuid4().hex
            sid_file, path = f"{self._obs_dir}/{name}.sid", f"{self._obs_dir}/{name}.jsonl"
            process = self._call(self.sandbox.create_process, "setsid",     # -w: see start
                                 ["-w", "sh", "-c", RELAY_SHELL, sid_file, f"{self._obs_dir}/relay.cjs", path])
        except Exception as error:
            self._relay_warning("start", f"the obs relay didn't start: {str(error)[-300:]}")
            return None
        target = dataclasses.replace(target, session_prefix=f"sssf-{self.run_.adw_id}-")
        relay = _Relay(process, sid_file, path, obs_relay.Forwarder(target))
        port = relay.wait_port(RELAY_START_SECONDS)
        if port is None:
            self._relay_warning("start", f"the obs relay gave no port in {RELAY_START_SECONDS}s; "
                                         "this call ran with obs off")
            self._stop_relay(relay)
            return None
        relay.url = f"http://127.0.0.1:{port}"
        relay.forwarder.live()
        self._relays.add(relay)
        return relay

    def _stop_relay(self, relay: _Relay) -> str:
        """Stop the relay and its follower. Returns "relay=up|down
        follower=up|down" as they were, or "" if the VM didn't say."""
        try:
            done = self._call(self.sandbox.run_process, "sh", ["-c", RELAY_STOP, relay.sid_file],
                              capture_output=True, deadline=RELAY_SECONDS)
            return (done.stdout or "").strip()
        except Exception:
            return ""

    def _close_relay(self, relay: _Relay, reach: bool = True) -> None:
        """Stop the relay, post what its file holds that the live posts
        didn't, and record the call's counts and warnings, from this thread.
        With a VM that has stopped answering (`reach` False), it posts what
        came live and asks the VM nothing. Never raises: obs never fails a
        call (decision 39)."""
        self._relays.discard(relay)
        try:
            swept, state = None, ""
            if reach:
                state = self._stop_relay(relay)
                if "relay=down" in state:
                    self._relay_warning("died", "the obs relay stopped during the call; "
                                                "the agent's later events never reached it")
                relay.ended.wait(DRAIN_SECONDS)
                try:
                    done = self._call(self.sandbox.run_process, "head",
                                      ["-c", str(2 * obs_relay.MAX_BYTES), relay.path],
                                      capture_output=True, deadline=RELAY_SECONDS * 2)
                    if done.returncode == 0:
                        swept = done.stdout or ""
                except Exception as error:
                    self._relay_warning("sweep", f"the relay's file couldn't be read: {str(error)[-300:]}")
            live = relay.live and relay.ended.is_set() and "follower=down" not in state
            relay.forwarder.finish(swept)
            for kind, why in relay.forwarder.warnings():
                self._relay_warning(kind, why)
            self._log("obs_relay", {"vm": self.vm, **relay.forwarder.counts(), "live": live,
                                    "swept": swept is not None})
        except Exception as error:
            try:
                self._relay_warning("close", str(error)[-300:])
            except Exception:
                pass

    def _relay_warning(self, kind: str, why: str) -> None:
        self._log("obs_relay_warning", {"vm": self.vm, "kind": kind, "why": why})

    def exists(self, path) -> bool:
        return self._test("-e", path)

    def is_file(self, path) -> bool:
        return self._test("-f", path)

    def size(self, path) -> int:
        done = self._call(self.sandbox.run_process, "stat", ["-c", "%s", self._path(path)],
                          capture_output=True)
        if done.returncode != 0:
            raise FileNotFoundError(path)
        return int(done.stdout.strip())

    def read_text(self, path) -> str:
        done = self._call(self.sandbox.run_process, "cat", [self._path(path)], capture_output=True)
        if done.returncode != 0:
            raise FileNotFoundError(path)
        return done.stdout or ""

    def write_text(self, path, text: str) -> None:
        self._must("sh", ["-c", 'cat > "$0"', self._path(path)], input=text.encode())

    def install(self, path) -> None:
        """Copy one of SSSF's own host files into the VM at the same path,
        executable, once per VM. The VM's clone holds the repo's committed
        files, which can lag the skill the host is running. Its folder is
        outside the clone, where the VM's user can't make folders, so root
        makes it and hands it over, as _clone does; the user writes the file."""
        where = self._path(path)
        if where in self.installed:
            return
        self._must("sh", ["-c", f'mkdir -p "$1" && chown {self.owner} "$1"', "sh", str(Path(where).parent)],
                   sudo=True)
        self._must("sh", ["-c", 'cat > "$0" && chmod 755 "$0"', where],
                   input=Path(path).read_bytes())
        self.installed.add(where)

    def remove(self, path) -> None:
        self._call(self.sandbox.run_process, "rm", ["-f", self._path(path)], capture_output=True)

    # --- moving work between the VM and the host -----------------------------

    def handoff_in(self) -> None:
        self._must("tar", ["-xzf", "-", "-C", str(self.handoff)], input=_tar(self.handoff))

    def handoff_out(self) -> None:
        packed = self._must("sh", ["-c", 'tar -czf - -C "$0" . | base64 -w0', str(self.handoff)])
        staging = Path(tempfile.mkdtemp())
        try:
            with tarfile.open(fileobj=io.BytesIO(base64.b64decode(packed)), mode="r:gz") as tar:
                tar.extractall(staging, filter="data")
            for found in sorted(staging.rglob("*")):
                if found.is_symlink() or not found.is_file():
                    continue
                session_files.write(self.handoff / found.relative_to(staging), found.read_bytes(),
                                    self.run_.data_root)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def commits_out(self, only=None, wrote=None, *, must_move: bool = False) -> None:
        """Fast-forward the host branch to the VM's, through a bundle of the new commits.

        With `must_move` (only `commit` sets it), a VM whose HEAD is still at the
        last sync stops the run: a `git commit` that exited 0 didn't land.

        The host judges every commit first (permissions.judge_commits), reading
        only objects its own `git bundle unbundle` hashed. A refused bundle lands
        nothing: it is kept as sandbox-refused.bundle and PermissionBreach is raised.

        While bound (from the chain's first verify round on), every commit of
        `synced..tip` must hold the tree of the last green round, except at a `wrote`
        path (path -> blob id) that holds exactly that blob as a regular file, or the
        whole bundle is refused. Before the first green round nothing
        lands. A red or failed round keeps the last green tree. A `must_move` call
        (`commit`) that lands moves the binding to its tree. Only `end_verified` ends
        it; a sync leaves it."""
        from . import permissions
        wrote = permissions.wrote_map(wrote)
        head = self._must("git", ["rev-parse", "HEAD"], cwd=str(self.tree)).strip()
        if head == self.synced:
            if not must_move:
                return
            self._log("sandbox_commit_not_landed",
                      {"vm": self.vm, "synced": self.synced[:12],
                       "only": list(only) if only else None}, type="error")
            raise permissions.PermissionBreach(
                f"`git commit` exited 0 in the VM but its HEAD is still at the last sync "
                f"({self.synced[:12]}), so the commit didn't land and the run stops; the host "
                f"branch is still at {self.synced[:12]}.")
        packed = self._must("sh", ["-c", f"git bundle create -q - {self.synced}..HEAD | base64 -w0"],
                            cwd=str(self.tree))
        data = base64.b64decode(packed)
        bundle = self.run_.session_dir / "sandbox-out.bundle"
        session_files.write(bundle, data, self.run_.data_root)
        tip, refused, text = "", [], ""
        try:
            tip = _bundle_head(data, self.synced)
            _host_git("bundle", "unbundle", str(bundle), cwd=self.tree)
            cfg = getattr(self.run_, "cfg", None)
            if cfg is None:
                raise permissions.GitFailed("the run has no config to judge the VM's commits by")
            try:
                inside = Path(self.run_.session_dir).resolve().relative_to(self.tree.resolve()).as_posix()
            except ValueError:
                inside = ""
            refused = permissions.judge_commits(
                self.tree, self.synced, tip, cfg, getattr(self.run_, "ran", []), only=only,
                session=inside, wrote=wrote,
                taken=list(getattr(self.run_, "taken", ())))
            if not refused and self._verified is not None:
                # Every commit of synced..tip must hold the tree verify passed, except
                # at `wrote`. judge_commits refused any merge, root or tip that does not
                # descend from the sync, so this is one line of commits.
                line = _host_git("rev-list", "--reverse", f"{self.synced}..{tip}", cwd=self.tree).split()
                if self._verified == "":
                    refused = [(commit, ["no verify round has passed"]) for commit in line]
                    text = ("No verify round has gone green since this chain's first round "
                            "opened, so no commit made in the VM may land.")
                    line = []
                for commit in line:
                    got = _host_git("rev-parse", f"{commit}^{{tree}}", cwd=self.tree)
                    if got == self._verified:
                        continue
                    left = [p for p, status, mode, new in
                            permissions._diff_paths(self.tree, self._verified, got)
                            if not permissions.wrote_blob(wrote, p, status, mode, new)]
                    if left:
                        refused.append((commit, ["not the tree verify passed"]))
                        shown_paths = ", ".join(permissions._said_line(n) for n in left[:20])
                        text = (f"The tree verify passed is {self._verified[:12]}, and commit "
                                f"{commit[:12]} differs from it at: {shown_paths}. Every commit made "
                                f"after the last green verify must hold exactly what it checked.")
        except (permissions.PermissionBreach, SandboxFailed) as error:
            refused, text = [(tip or head, ["unreadable"])], str(error)
        if refused:
            kept = self.run_.session_dir / "sandbox-refused.bundle"
            session_files.write(kept, data, self.run_.data_root)
            bundle.unlink(missing_ok=True)
            shown = text or permissions.refusal_text(refused)
            self._log("sandbox_commits_refused",
                      {"vm": self.vm, "synced": self.synced[:12], "head": (tip or head)[:12],
                       "refused": [[c, p] for c, p in refused], "bundle": str(kept),
                       **({"verified": self._verified[:12]} if self._verified is not None else {})},
                      type="error")
            raise permissions.PermissionBreach(
                f"The VM's commits were refused and did not land (the host branch is still at "
                f"{self.synced[:12]}); the bundle is kept at {kept}:\n{shown}")
        _host_git("merge", "-q", "--ff-only", tip, cwd=self.tree)
        bundle.unlink()
        count = _host_git("rev-list", "--count", f"{self.synced}..{tip}", cwd=self.tree)
        self._log("sandbox_commits", {"vm": self.vm, "count": int(count), "head": tip[:12]})
        self.synced = tip
        if must_move and self._verified is not None:
            # The chain's own commit landed: what comes back next must hold its tree.
            self._verified = _host_git("rev-parse", f"{tip}^{{tree}}", cwd=self.tree)
        permissions.commit_passed(self.run_, only)

    # --- a fresh VM for verify ------------------------------------------------

    def _patch_out(self) -> bytes:
        """What a commit made now would hold, against the last sync, as one patch: this
        VM's real index plus `git add -A`, so a force-added ignored file and a
        skip-worktree entry are in it. Made with a copy of the index, so this VM's
        index, HEAD and tree are not changed. No git command writes into a pipe, so a
        failing one stops the round (SandboxFailed). Empty when there is none."""
        script = ('set -e; idx=$(mktemp -u); out=$(mktemp); trap \'rm -f "$idx" "$out"\' EXIT; '
                  'real=$(git rev-parse --git-path index); '
                  'if [ -e "$real" ]; then cp "$real" "$idx"; fi; '
                  'export GIT_INDEX_FILE="$idx"; git add -A; '
                  'git diff --cached --binary "$1" > "$out"; base64 -w0 "$out"')
        packed = self._must("sh", ["-c", script, "sh", self.synced], cwd=str(self.tree))
        try:
            return base64.b64decode(packed.strip(), validate=True) if packed.strip() else b""
        except ValueError as error:
            raise SandboxFailed(f"{self.name} could not read the build VM's patch: {error}") from None

    def _judge_patch(self, patch: bytes) -> str:
        """Judge `patch` on the host before any of it goes into a VM: apply it to the
        last sync in a temporary index, make a commit of the result, and ask
        permissions.judge_commits. Raises PermissionBreach if it is refused or can't
        be read, keeping the patch as sandbox-refused-verify.patch. Moves no ref and
        touches no worktree or index of the host. Returns the id of the tree the patch
        makes (the last sync's tree for an empty patch, which is judged by nothing)."""
        from . import permissions
        if not patch:
            return _host_git("rev-parse", f"{self.synced}^{{tree}}", cwd=self.tree)
        folder = Path(tempfile.mkdtemp())
        refused, text, commit, tree = [], "", "", ""
        try:
            env = {"GIT_INDEX_FILE": str(folder / "index"),
                   "GIT_AUTHOR_NAME": "sssf", "GIT_AUTHOR_EMAIL": "sssf@localhost",
                   "GIT_COMMITTER_NAME": "sssf", "GIT_COMMITTER_EMAIL": "sssf@localhost"}
            try:
                _host_git_with(["read-tree", self.synced], self.tree, env)
                _host_git_with(["apply", "--cached", "--binary", "--whitespace=nowarn"], self.tree, env, input=patch)
                tree = _host_git_with(["write-tree"], self.tree, env)
                commit = _host_git_with(["commit-tree", tree, "-p", self.synced, "-m", "sssf verify"],
                                        self.tree, env)
                cfg = getattr(self.run_, "cfg", None)
                if cfg is None:
                    raise permissions.GitFailed("the run has no config to judge the VM's patch by")
                try:
                    inside = Path(self.run_.session_dir).resolve().relative_to(self.tree.resolve()).as_posix()
                except ValueError:
                    inside = ""
                refused = permissions.judge_commits(
                    self.tree, self.synced, commit, cfg, getattr(self.run_, "ran", []),
                    session=inside, taken=list(getattr(self.run_, "taken", ())))
            except (permissions.PermissionBreach, SandboxFailed) as error:
                refused, text = [(commit or self.synced, ["unreadable"])], str(error)
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        if refused:
            kept = self.run_.session_dir / "sandbox-refused-verify.patch"
            session_files.write(kept, patch, self.run_.data_root)
            self._log("sandbox_verify_refused",
                      {"vm": self.vm, "synced": self.synced[:12], "head": (commit or self.synced)[:12],
                       "refused": [[c, p] for c, p in refused], "patch": str(kept)}, type="error")
            raise permissions.PermissionBreach(
                f"The VM's work was refused before verify and nothing was run (the host branch is "
                f"still at {self.synced[:12]}); the patch is kept at {kept}:\n"
                f"{text or permissions.refusal_text(refused)}")
        return tree

    def open_verify(self) -> "VerifyBox":
        """A new VM for one verify round: the branch at the last sync plus this VM's
        patch, applied to the index there; its tree id must be the host's `tree` (else
        the round fails before any file is written), and every file is then checked out
        from that index, so the checks see a clean checkout of the judged tree under its
        own `.gitattributes`. HEAD and the index stay at the last sync.
        `worktree_setup` is run in it. Raises SandboxFailed or PermissionBreach.
        The build VM and the host branch are not changed. The caller closes the box.
        The first round of a chain binds it: from then on every commit that comes back
        must hold the tree of the last green round (see `commits_out`), and before the
        first green nothing lands. A later round, red or failed, keeps the last green
        tree; the box's `mark_green` replaces it with this round's tree (the one the
        host built from the judged patch). Only `end_verified` ends the binding."""
        if self._verified is None:
            self._verified = ""
        try:
            patch = self._patch_out()
        except OSError as error:
            raise SandboxFailed(f"{self.name} could not take the build VM's patch: {error}") from None
        tree = self._judge_patch(patch)
        inner = type(self)(self.run_, self.branch, self.project)
        inner.python, inner.python_problem = self.python, self.python_problem
        box = VerifyBox(inner, self, tree)
        try:
            began = time.monotonic()
            inner.sandbox = inner._create()
            inner.vm = inner.sandbox.name
            inner._created = inner._session = time.monotonic()
            inner._cpu_base = inner._cpu_now()
            made = time.monotonic()
            inner._clone()
            if inner.synced != self.synced:
                raise SandboxFailed(f"the fresh VM's clone is at {inner.synced[:12]}, not at the "
                                    f"last sync {self.synced[:12]}")
            cloned = time.monotonic()
            if patch:
                inner._must("sh", ["-c", _VERIFY_CHECKOUT, "sh", self.synced, tree],
                            cwd=str(self.tree), input=patch)
            from . import repo_settings, worktree
            settings = repo_settings.load(worktree.main_checkout())
            if settings.worktree_setup:
                why = worktree.run_setup(settings.worktree_setup, self.tree,
                                         settings.worktree_setup_timeout,
                                         self.handoff / "setup_verify", self.run_.data_root, box)
                if why:
                    raise SandboxFailed(f"worktree_setup failed in the fresh VM for verify, "
                                        f"so no check ran. {why}")
            done = time.monotonic()
            self._log("sandbox_verify", {"provider": self.name, "vm": inner.vm, "build_vm": self.vm,
                                         "create_seconds": round(made - began, 1),
                                         "clone_seconds": round(cloned - made, 1),
                                         "setup_seconds": round(done - cloned, 1),
                                         "patch_bytes": len(patch), "tree": tree[:12]})
        except BaseException as error:
            if inner.sandbox is not None:
                box.close()
            if isinstance(error, (*self.errors, OSError)):
                raise SandboxFailed(f"{self.name} would not make or set up the verify VM: {error}") from None
            raise
        return box

    def commit(self, argv, *, only=None, wrote=None) -> subprocess.CompletedProcess:
        """Run a `git commit` in the VM and judge what comes back with `only` and `wrote`
        (path -> blob id: a `wrote` path passes only with that blob).
        A commit that exits 0 and doesn't move the VM's HEAD is refused."""
        return self._run(argv, sync=lambda: self.commits_out(only, wrote, must_move=True))

    def verified_text(self, path) -> str | None:
        """`path`'s text in the tree of the last green verify round, read from the host's
        object store. None when no round has gone green or the binding has ended. The
        build VM's disk is not read. Raises PermissionBreach (GitFailed) if the tree has
        no such file."""
        from . import permissions
        if not self._verified:
            return None
        return permissions._host_read(self.tree, ["cat-file", "blob", f"{self._verified}:{path}"]).decode("utf-8")

    def end_verified(self) -> None:
        """The chain's verify is over (sandbox.verify_done). Bring back what the VM holds
        while the binding is still in force, then end it. Raises PermissionBreach, and
        leaves the binding in force, if a commit there doesn't hold the verified tree."""
        from . import permissions
        if self._verified is None:
            return
        self.commits_out()
        self._verified = None
        permissions.commit_passed(self.run_, None)
        self._log("sandbox_verified_end", {"vm": self.vm, "synced": self.synced[:12]})

    # --- helpers ------------------------------------------------------------

    def _refresh(self, need: float) -> None:
        """Stop the VM when `need` seconds won't fit in this session, so it
        resumes in a new session, with its files.

        No command may ask for more than half a session, so a fresh one always
        has room for it. Otherwise a short session would stop the VM before
        every command, and Vercel limits how fast VMs resume (40 vCPUs a
        minute on Hobby)."""
        need = min(need, self.limit / 2)
        if time.monotonic() - self._session + need <= self.limit - MARGIN_SECONDS:
            return
        self._cpu_add()
        self._call(self.sandbox.stop)
        self._session = time.monotonic()
        self._cpu_base = self._cpu_now()
        self.refreshes += 1
        self._log("sandbox_refresh", {"vm": self.vm, "refreshes": self.refreshes})

    def _cpu_now(self) -> float | None:
        """The VM's busy CPU seconds since it booted, from /proc/stat: user,
        nice, system, irq, softirq and steal, summed over its vCPUs. Waiting
        is left out, as Vercel leaves it out of active CPU. None if unread."""
        try:
            done = self._call(self.sandbox.run_process, "sh", ["-c", CPU_READ], capture_output=True)
            return float(done.stdout.strip()) if done.returncode == 0 else None
        except Exception:
            return None

    def _cpu_add(self) -> None:
        """Add this session's CPU to the run's. A reading that fails makes the
        run's total unknown, never wrong. A count lower than its base means
        the VM booted again, and all of it is this session's."""
        now = self._cpu_now() if self.cpu_seconds is not None else None
        if now is None or self._cpu_base is None:
            self.cpu_seconds = None
        else:
            self.cpu_seconds += now - self._cpu_base if now >= self._cpu_base else now
        self._cpu_base = None

    def _env(self, env) -> dict[str, str]:
        source = os.environ if env is None else env
        passed = {k: v for k, v in source.items() if k.startswith(PASSED) and k not in HELD}
        # SSSF's refusing shell (sssf_refuse.py), which agent_cc installs in the
        # VM. An operator's own prefix names a host path, so it stays behind.
        if "SSSF_REFUSE_RULES" in passed and source.get("CLAUDE_CODE_SHELL_PREFIX"):
            passed["CLAUDE_CODE_SHELL_PREFIX"] = source["CLAUDE_CODE_SHELL_PREFIX"]
        # A pi agent's ChatGPT token (agent_pi.vm_token), under the name pi reads.
        # Only this name crosses: an OPENAI_API_KEY on the host stays there.
        if "SSSF_PI_TOKEN" in passed:
            passed["OPENAI_API_KEY"] = passed.pop("SSSF_PI_TOKEN")
        out = {"UV_PYTHON": self.python, **passed} if self.python else passed
        _key_check(out)     # the one place every run, commit and start passes through
        return out

    def _cwd(self, cwd) -> str:
        return str(Path(cwd).resolve()) if cwd else os.getcwd()

    def _path(self, path) -> str:
        return str(Path(os.getcwd(), path))

    def _test(self, flag: str, path) -> bool:
        return self._call(self.sandbox.run_process, "test", [flag, self._path(path)],
                          capture_output=True).returncode == 0

    def _must(self, command: str, args: list[str], **kwargs) -> str:
        done = self._call(self.sandbox.run_process, command, args, capture_output=True, **kwargs)
        if done.returncode != 0:
            raise SandboxFailed(f"{command} {' '.join(args)[:200]} failed in VM {self.vm}: "
                                f"{(done.stderr or done.stdout or '').strip()[-500:]}")
        return done.stdout or ""

    def _call(self, method, *args, deadline: float | None = None, **kwargs):
        """An SDK call, with its errors as OSError, as a subprocess's would be.
        One with no answer by `deadline` seconds raises OSError too. Its thread
        is left blocked on the dead connection; it is a daemon, so it never
        holds the ADW open."""
        deadline = COMMAND_SECONDS + CALL_SECONDS if deadline is None else deadline
        out: dict = {}

        def call() -> None:
            try:
                out["value"] = method(*args, **kwargs)
            except BaseException as error:
                out["error"] = error

        worker = threading.Thread(target=call, name=f"sssf-{self.name}-call", daemon=True)
        worker.start()
        worker.join(deadline)
        if worker.is_alive():
            what = getattr(method, "__name__", "call")
            raise OSError(f"{self.name} sandbox {self.vm}: {what} had no answer in {deadline:.0f}s")
        if "error" in out:
            if isinstance(out["error"], self.errors):
                raise OSError(f"{self.name} sandbox {self.vm}: {out['error']}") from out["error"]
            raise out["error"]
        return out.get("value")

    def _log(self, name: str, payload: dict, type: str = "log") -> None:
        from .data_types import EventRecord
        self.run_.tracer.event(EventRecord(adw_id=self.run_.adw_id, phase_id="", type=type,
                                           name=name, payload={**payload, "host": socket.gethostname()}))


class VerifyBox:
    """The VM of one verify round, made by `Remote.open_verify`. It can run a
    command, read a file and be closed, and nothing else: no `start`, `commit`,
    `install` or `open_verify`, so no agent starts in it, no commit lands from it
    and rounds don't nest."""

    name = "verify"

    def __init__(self, inner: Remote, outer: Remote, tree: str):
        self.inner = inner
        self.outer = outer
        self.tree = tree

    def mark_green(self) -> None:
        """This round passed: the build VM's commits from now on must hold this tree,
        replacing the earlier green's. A red or failed round never lifts it; only
        `end_verified` ends the binding. May be called after `close()`."""
        self.outer._verified = self.tree
        self.outer._log("sandbox_verified", {"vm": self.outer.vm, "tree": self.tree[:12]})

    def exists(self, path) -> bool:
        return self.inner.exists(path)

    def is_file(self, path) -> bool:
        return self.inner.is_file(path)

    def size(self, path) -> int:
        return self.inner.size(path)

    def read_text(self, path) -> str:
        return self.inner.read_text(path)

    @property
    def vm(self) -> str:
        return self.inner.vm

    def run(self, command, *, cwd=None, env=None, timeout=None,
            shell: bool = False, text: bool = True) -> subprocess.CompletedProcess:
        # A `git commit` a check runs here syncs nothing.
        return self.inner._run(command, cwd=cwd, env=env, timeout=timeout, shell=shell,
                               text=text, sync=lambda: None)

    def close(self) -> None:
        """Delete the VM. Never raises, never brings anything back."""
        inner = self.inner
        if inner.sandbox is None:
            return
        try:
            inner._cpu_add()
        except Exception:
            pass
        seconds = round(time.monotonic() - inner._created, 1)
        try:
            inner._call(inner._delete)
            deleted = True
        except Exception as error:
            deleted = False
            inner._log("sandbox_error", {"vm": inner.vm, "error": f"delete failed: {error}"[-500:]})
        try:
            inner._log("sandbox_end", {"provider": inner.name, "vm": inner.vm, "vm_seconds": seconds,
                                       "refreshes": inner.refreshes, "deleted": deleted,
                                       "uncommitted_patch": "", "verify": True,
                                       "cpu_seconds": None if inner.cpu_seconds is None
                                       else round(inner.cpu_seconds, 1),
                                       **inner._end(seconds)})
        except Exception:
            pass
        inner.sandbox = None

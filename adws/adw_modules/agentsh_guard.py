"""Run local agents under agentsh when a repo's `local_guard` is `agentsh`.

The text guard (rules.py, secret_files.py) stays on. This adds a service that
checks real execs, opens and connects, under a default-deny policy SSSF
generates from repo.yaml. See docs/designs/agentsh-local-sandbox.md.

Nothing here re-reads repo.yaml or asks git while a run goes on. `read` runs
once, in session.ensure, before the process moves into a worktree and before
any agent has run; the result travels on every AgentRequest as `local_guard`.
An agent can rewrite the worktree's `.git` file and the main checkout's
repo.yaml, so a later read could return what the agent chose.
"""

from __future__ import annotations

import atexit
import errno
import hashlib
import json
import math
import os
import pwd
import re
import signal
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

import yaml
from pydantic import ValidationError

from . import repo_settings, rules, sandbox
from .data_types import LocalGuard, Policy
from .utils import COPY_MODES

BINARY = "/usr/bin/agentsh"
CONFIG = "/etc/agentsh/sssf.yaml"
POLICY_DIR = "/etc/agentsh/policies"
SERVER = "unix:///run/agentsh/agentsh.sock"
API = "http://127.0.0.1:18080"
PINNED_SHA256 = "603dbf1495da87c28be301f607efd00c66fb250633334204a0b04c2f10df1747"   # build 05b3777f on sssf/links-listing
ATTACH_SECONDS = 30
PAGE = 5000
PRUNE_STATUS = "/var/lib/sssf-agentsh-prune/status.json"
PRUNE_STALE_SECONDS = 3 * 3600
STORE_WARN_BYTES = 15 * 10**9
POLL_SECONDS = 5
SLOW_POLL_SECONDS = 1.0      # a poll request slower than this gets a note
JOURNAL_SECONDS = 10         # the journal read's timeout
DROP_TEXT = "event channel full"
TIMEOUT = 10
RESTART = "sudo systemctl restart agentsh"
GLOB_CHARS = "*?[]{}\\$"

INSTALL = "uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <this checkout>"
# <hub> is the SSSF source checkout on this machine, at this project's skill version.
FIX = (f"install from the SSSF hub: run `{INSTALL}` and the sudo lines it prints, "
       "or set `local_guard: text` in adws/adw_sssf_config/repo.yaml")
DIFFERS = ("An agent may have changed adws/. Run `git status adws/` and `git diff adws/` (and "
           "`git log --stat -- adws/`, since a run may have committed it), discard any change you "
           "didn't make, then " + FIX)


class AgentshRefused(sandbox.SandboxFailed):
    """The agentsh wrap can't be trusted or reached, so no agent starts or the run stops.
    Not a RuleBreach: nothing here says a forbidden command may have run."""

    partial = None


# ── the policy ───────────────────────────────────────────────────────────────

READ = ["open", "read", "stat", "list", "readlink", "access"]
ALL = ["*"]
SECRET_ALLOW = ["**/.env.example", "**/.env.sample"]
SECRETS = ["**/.env", "**/.env.*"]
CREDENTIALS = [f"${{HOME}}/{p}" for p in (
    ".ssh/**", ".aws/**", ".azure/**", ".gnupg/**", ".kube/**", ".docker/**",
    ".config/gh/**", ".config/gcloud/**", ".config/sssf-sandbox/**",
    ".git-credentials", ".netrc", ".npmrc", ".pypirc", ".cargo/credentials", ".cargo/credentials.toml",
)]
PROC_SECRETS = ["/proc/*/environ", "/proc/*/mem", "/proc/*/task/*/environ", "/proc/*/task/*/mem"]
CLAUDE_SIGN_IN = ["${HOME}/.claude/.credentials.json"]
PI_SIGN_IN = ["${HOME}/.pi/agent/auth.json"]
CLAUDE_CONFIG = [f"${{HOME}}/.claude/{p}" for p in (
    "settings.json", "settings.local.json", "CLAUDE.md", "AGENTS.md",
    "skills/**", "rules/**", "hooks/**", "agents/**", "commands/**",
)]
WRITE_OPS = ["write", "create", "delete", "rename", "link", "symlink", "symlink_target", "mkdir", "rmdir", "chmod", "chown"]
SYSTEM_READ = ["/usr", "/usr/**", "/lib", "/lib/**", "/lib64", "/lib64/**", "/bin", "/bin/**",
               "/sbin", "/sbin/**", "/opt", "/opt/**", "/etc", "/etc/**", "/sys", "/sys/**",
               "/proc", "/proc/**", "/", "/home", "/var", "/run", "/mnt"]
# The config files tools look up in every folder above the workspace, with their sources.
# A denied file answers EACCES even when it doesn't exist, and uv stops on that as it
# searches upward for config, where ENOENT would let it go on.
#   uv.toml, pyproject.toml: uv. docs/concepts/configuration-files.md (current directory or
#     nearest parent); crates/uv-settings/src/lib.rs FilesystemOptions::find walks ancestors();
#     crates/uv-workspace/src/workspace.rs walks ancestors for a workspace root.
#   .python-version, .python-versions: uv. docs/concepts/python-versions.md;
#     crates/uv-python/src/version_files.rs (PYTHON_VERSION_FILENAME, PYTHON_VERSIONS_FILENAME, discover).
#   package.json: Corepack (README, sources/specUtils.ts loadSpec walks up); bun
#     (docs/install/workspaces.md); npm (docs/content/configuring-npm/folders.md); Node
#     (doc/api/packages.md, nearest parent package.json).
#   pnpm-workspace.yaml: pnpm, @pnpm/find-workspace-dir (find-up); pnpm-workspace_yaml.md.
#   .bunfig.toml, in ${HOME} only: bun's global config (docs/runtime/bunfig.md).
CONFIG_NAMES = ("uv.toml", "pyproject.toml", ".python-version", ".python-versions",
                "package.json", "pnpm-workspace.yaml")
HOME_CONFIG = ["${HOME}/.bunfig.toml"]
# What a lookup needs of a folder above the workspace, on its exact path. Not `list` and not
# `read`: uv walks ancestors with is_dir/metadata/canonicalize (stat, access, readlink) and
# opens the file by path, never reading a folder's entries. bun opens each folder, and on
# EACCES from listing it treats the folder as empty and goes on. Corepack makes no folder call.
# From build 05b3777f the engine judges reading a folder's entries (getdents64/getdents) as
# `list`, so these folders can be opened and checked but not listed; see config.md.
FOLDER_OPS = ["open", "stat", "readlink", "access"]
# An absolute link into a system folder, for `uv venv`'s bin/python -> /usr/bin/python3.12,
# or at a uv-managed interpreter. The rule allows a link's *target* only: the engine (build
# 05b3777f) judges a link's own path as `symlink` and its target as `symlink_target`, so the
# rule lets no agent make a link inside these trees. /usr/bin is root-owned anyway. Agents
# can't write ~/.local/share/uv at all (no rule allows it), and a link at a path there is
# judged as `symlink`, which no rule allows. The glob covers the interpreter a venv on a
# uv-managed Python links to; the engine resolves the target through links first, so uv's
# minor-version folder links reach the patch folder's bin/python3.x. /usr/lib, /usr/local,
# /opt, /mnt and /etc are left out on purpose: users own or can write folders there.
SYSTEM_LINK = ["/usr/bin/**", "/bin/**", "${HOME}/.local/share/uv/python/*/bin/python3.*"]
# A denied mkdir of an existing parent answers EACCES where the kernel says EEXIST.
MKDIR_PARENTS = ["/", "/home", "/tmp", "/run", "/run/user", "/run/user/*", "${HOME}", "${HOME}/.cache",
                 "${HOME}/.local", "${HOME}/.local/state", "${HOME}/.local/share", "${HOME}/.config"]
TEMP = ["/tmp", "/tmp/**", "/var/tmp", "/var/tmp/**", "/dev/shm", "/dev/shm/**"]
# What's left at this level after the patched tracer resolves: pipes, sockets and
# deleted files. A descriptor on a real file resolves to that file's own rule.
FD_LEAVES = ["/proc/*/fd/*", "/proc/*/task/*/fd/*"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/tty", "/dev/ptmx", "/dev/pts/**", "/dev/urandom",
           "/dev/random", "/dev/fd/**", "/dev/stdin", "/dev/stdout", "/dev/stderr"]
# The home folder itself can be opened and checked but not listed, because its entries are the
# engineer's files and repos. Agents read the files and folders under it that HOME_READ names,
# and those stay listable.
HOME_FOLDER = ["${HOME}"]
HOME_READ = [f"${{HOME}}{p}" for p in (
    "/.bashrc", "/.profile", "/.bash_profile", "/.bash_login", "/.editrc", "/.inputrc",
    "/.gitconfig", "/.config", "/.config/git/**", "/.config/uv/**", "/.cargo", "/.cargo/env",
    "/.cargo/bin/**", "/.local", "/.local/bin/**", "/.nvm/**", "/.rustup/**",
    "/.local/share/uv/python", "/.local/share/uv/python/**",
    "/.local/share/uv/tools", "/.local/share/uv/tools/**",
)]
# The Windows path is where Claude Code looks for managed settings on WSL. Denied, it warns
# that those policies are NOT in effect, even when the folder doesn't exist.
# ~/.local/share/claude holds Claude Code's program (versions/), which the engineer's own `claude`
# runs, so agents read it and never change it; DISABLE_AUTOUPDATER=1 keeps their Claude Code from
# trying to update it. ~/.claude and ~/.claude.json are in no list: the agents' config folder is
# their own, and default-deny closes the engineer's. ~/CLAUDE.md, ~/AGENTS.md, ~/github/skills and
# ~/github/plugins went unread in the 2026-11-10 probe under --safe-mode; ~/.config/anthropic was read.
CLAUDE_READ = [f"${{HOME}}/{p}" for p in (
    "CLAUDE.local.md", ".config/anthropic", ".config/anthropic/**", ".local/share/claude/**",
)] + ["/mnt/c/Program Files/ClaudeCode", "/mnt/c/Program Files/ClaudeCode/**"]
# ~/.local/state/claude holds lock files (the 2026-11-10 probe's only writes outside the config
# folder were to locks/) and ~/.cache/claude-cli-nodejs holds Claude Code's logs. Claude Code loads
# no code from either, so both stay writable.
CLAUDE_WRITE = [f"${{HOME}}/{p}" for p in (
    ".cache/claude-cli-nodejs/**", ".local/state/claude/**",
)] + ["/run/user/*/cc-socks", "/run/user/*/cc-socks/**"]
PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]

# harness (the interface's HARNESS) -> the name its policy carries
SHORT = {"claude_code": "claude", "pi": "pi"}

COMMAND_HEAD = """command_rules:
  - name: sssf-no-live-tests
    commands: [just]
    args_patterns: ['(^|\\s)test\\s+live(\\s|$)']
    decision: deny
  - name: sssf-no-gh
    commands: [gh]
    decision: deny
  - name: sssf-no-push
    commands: [git]
    # The regex runs over all of argv joined by spaces, argv[0] first: the verb itself, or
    # an option before it (-c a=b, -C /x, --git-dir /x). `push` must be a word of its own after
    # a leading option, or appear anywhere in an alias's value or after it.
    # A hook name such as hook.pre-push.enabled, or a path such as /x/push-tools, doesn't match.
    # The alias pattern ignores case, because git reads config keys in any case. It catches
    # `ALIAS.p=push` and `alias.p=PUSH`.
    # (?s) and (?is) are there so a newline in an option's value can't hide the verb.
    args_patterns: ['^\\S*\\s+push(\\s|$)', '(?s)^\\S*\\s+-.*\\spush(\\s|$)', '(?is)^\\S*\\s+-.*\\salias\\.[^=\\s]*=.*push']
    decision: deny
"""
COMMAND_TAIL = """  - name: sssf-allow-rest
    decision: allow
"""
NETWORK_HEAD = """network_rules:
  # The server's HTTP API, upstream finding #29.
  - name: sssf-no-agentsh-api
    cidrs: ['127.0.0.1/32', '::1/128']
    ports: [18080]
    decision: deny
  # The session proxy needs no rule: the patched build allows it exactly.
  # The model hosts: Claude Code and pi on a ChatGPT subscription.
  - name: sssf-model-hosts
    domains: ['api.anthropic.com', '*.anthropic.com', 'api.openai.com', 'chatgpt.com', '*.chatgpt.com']
    ports: [443]
    decision: allow
  # The login refresh hosts: pi's ChatGPT token and Claude Code's OAuth token.
  - name: sssf-auth-hosts
    domains: ['auth.openai.com', 'platform.claude.com']
    ports: [443]
    decision: allow
  # The package registries.
  - name: sssf-registries
    domains: ['registry.npmjs.org', 'pypi.org', 'files.pythonhosted.org', 'crates.io', 'index.crates.io', 'static.crates.io']
    ports: [443]
    decision: allow
"""
NETWORK_TAIL = "  # Everything else is denied: no match is a deny.\n"
# agentsh's @all is signals 1 to 31, and a signal no rule names is refused, so the real-time
# signals are named too: glibc uses 32 and 33 between a program's own threads. agentsh
# can't name 64.
EVERY_SIGNAL = ["@all"] + [str(n) for n in range(32, 64)]
SIGNAL_REFUSAL = "SSSF: an agent may signal only its own processes"


def _q(value: str) -> str:
    return value.replace("'", "''")      # a value can't break out of its YAML quote


def rule(name: str, paths: list[str], ops: list[str], decision: str) -> str:
    lines = [f"  - name: {name}", "    paths:"]
    lines += [f"      - '{_q(p)}'" for p in paths]
    lines += [f"    operations: {ops}".replace('"', "'"), f"    decision: {decision}"]
    return "\n".join(lines)


def secret_glob(pattern: str) -> str:
    """repo.yaml's secret_files pattern as secret_files._matches reads it, in
    the policy's glob: no `/` matches a name anywhere; with `/` it's from the
    root, and fnmatch's `*` crosses `/`, so a lone `*` becomes `**`."""
    pattern = pattern.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")   # fnmatch reads braces literally
    if "/" not in pattern:
        return f"**/{pattern}"
    pattern = pattern.removeprefix("./")
    return "${PROJECT_ROOT}/" + re.sub(r"(?<!\*)\*(?!\*)", "**", pattern)


def render_config(template: str, allowed: list[str]) -> str:
    """The server config SSSF installs, for a given policies.allowed. One place for the
    install script and the check. `default` is derived from the list. The last line is a
    comment naming the pin (BINARY, CONFIG, POLICY_DIR, SERVER, API and PINNED_SHA256, read at
    call time), so check_machine's byte comparison refuses an edit to any of them as "differs". agentsh loads the same YAML."""
    config = yaml.safe_load(template)
    config["policies"]["allowed"] = list(allowed)
    config["policies"]["default"] = allowed[0] if allowed else ""
    pin = (f"# SSSF pins agentsh: {BINARY} sha256 {PINNED_SHA256} "
           f"config {CONFIG} policies {POLICY_DIR} server {SERVER} api {API}\n")
    return yaml.safe_dump(config, sort_keys=False) + pin


def folders_above(main: str, data_root: str) -> list[str]:
    """The folders above a plain run's or a worktree run's workspace, shallowest first."""
    worktrees = Path(data_root) / "worktrees"
    found = {str(p) for p in Path(main).parents} | {str(worktrees)} | {str(p) for p in worktrees.parents}
    return sorted(found, key=lambda d: (len(Path(d).parts), d))


def glob_escape(path: str) -> str:
    """A literal path as a policy glob: a checkout named `repo[old]` must not match `repoo`."""
    return re.sub(r"([\\*?\[\]{}])", r"\\\1", path)


def config_search(main: str, data_root: str, quote: Callable[[str], str] = str) -> list[str]:
    """Every config name in every folder above the workspace, then the home-level ones."""
    return [f"{quote(d).rstrip('/')}/{n}" for d in folders_above(main, data_root) for n in CONFIG_NAMES] + HOME_CONFIG


def cache_env(folder: str) -> dict[str, str]:
    """The variables that move every tool's cache and install folders, and npm's user config
    and init module, into `folder`, the agents' own. Set on the wrapped agent only, so the engineer's tools never look there.
    Also COPY_MODES: in a worktree run `folder` sits outside the worktree, so an agent's install that
    hardlinked from it would make the next launch refuse."""
    return {
        "UV_CACHE_DIR": f"{folder}/uv",
        "UV_PYTHON_INSTALL_DIR": f"{folder}/uv-python",
        "UV_PYTHON_BIN_DIR": f"{folder}/bin",
        "UV_TOOL_DIR": f"{folder}/uv-tools",
        "UV_TOOL_BIN_DIR": f"{folder}/bin",
        "npm_config_cache": f"{folder}/npm",
        "npm_config_userconfig": f"{folder}/npmrc",
        "npm_config_init_module": f"{folder}/npm-init.js",
        "BUN_INSTALL_CACHE_DIR": f"{folder}/bun",
        "BUN_RUNTIME_TRANSPILER_CACHE_PATH": f"{folder}/bun-transpiler",
        "COREPACK_HOME": f"{folder}/corepack",
        "npm_config_store_dir": f"{folder}/pnpm-store",
        "npm_config_cache_dir": f"{folder}/pnpm-cache",
        "CARGO_HOME": f"{folder}/cargo",
        **COPY_MODES,
    }


TOKEN_FILE = Path.home() / ".config" / "sssf-sandbox" / "token.env"   # the file --sandbox reads
CLAUDE_HOME = "claude-config"   # the run's Claude Code config folder, in its session folder
NO_TOKEN = ("Claude Code agents under local_guard: agentsh sign in with a token, and none was found. "
            "Run `claude setup-token` in a terminal and save CLAUDE_CODE_OAUTH_TOKEN=<token> in "
            "~/.config/sssf-sandbox/token.env, mode 600 (or export CLAUDE_CODE_OAUTH_TOKEN where the ADW "
            "is launched). The token can only run inference, for about a year; agents never get your "
            "login's refresh token.")


def claude_token(environ=None) -> str:
    """CLAUDE_CODE_OAUTH_TOKEN from the launching shell, else from TOKEN_FILE; "" when neither has one."""
    environ = os.environ if environ is None else environ
    token = (environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if token:
        return token
    try:
        lines = TOKEN_FILE.read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    for line in lines:
        key, _, value = line.strip().partition("=")
        if key == "CLAUDE_CODE_OAUTH_TOKEN" and value.strip():
            return value.strip().strip('"').strip("'")
    return ""


def claude_env(guard, token: str, config_dir: str) -> dict[str, str]:
    """What a Claude Code agent under agentsh needs: its own config folder, the token, no auto-update.
    {} unless guard is an agentsh guard."""
    if guard is None or guard.mode != "agentsh":
        return {}
    if not token:
        raise AgentshRefused(NO_TOKEN)
    return {"CLAUDE_CONFIG_DIR": config_dir, "CLAUDE_CODE_OAUTH_TOKEN": token, "DISABLE_AUTOUPDATER": "1"}


def file_rules(git_dir: str, agent: str, secret_files: list[str] = (), *,
               sessions: str, refuse_script: str) -> str:
    other_sign_in, config_deny, reads, writes = {
        "claude": (PI_SIGN_IN, CLAUDE_CONFIG, CLAUDE_READ, CLAUDE_WRITE),
        "pi": (CLAUDE_SIGN_IN, [], [], PI_WRITE),
    }[agent]
    main = str(Path(git_dir).parent)
    data_root = str(Path(sessions).parent)
    # Every literal path below is escaped, so glob characters in the checkout's path match themselves.
    cache = glob_escape(f"{data_root}/agent-cache")
    search = config_search(main, data_root, glob_escape)
    folders = [glob_escape(d) for d in folders_above(main, data_root)]
    git_dir, sessions, refuse_script, main = (glob_escape(x) for x in (git_dir, sessions, refuse_script, main))
    rules_ = []
    if secret_files:
        # First: the text guard honours a repo pattern naming .env.sample, so behind
        # the allow below it would leak.
        rules_.append(rule("sssf-repo-secret-files", [secret_glob(p) for p in secret_files], ALL, "deny"))
    rules_ += [
        rule("sssf-env-examples", SECRET_ALLOW, ALL, "allow"),
        rule("sssf-secret-files", SECRETS, ALL, "deny"),
        rule("sssf-credentials", CREDENTIALS, ALL, "deny"),
        rule("sssf-proc-secrets", PROC_SECRETS, ALL, "deny"),
        rule("sssf-other-sign-in", other_sign_in, ALL, "deny"),
    ]
    if config_deny:
        rules_.append(rule("sssf-agent-config", config_deny, WRITE_OPS, "deny"))
    rules_ += [
        # Hooks and git config run on the host, outside the wrap, at the next git command.
        rule("sssf-git-config", [p for d in (git_dir, "${PROJECT_ROOT}/.git") for p in (
            f"{d}/hooks", f"{d}/hooks/**", f"{d}/config", f"{d}/config.lock")], WRITE_OPS, "deny"),
        rule("sssf-workspace", ["${PROJECT_ROOT}", "${PROJECT_ROOT}/**"], ALL, "allow"),
        rule("sssf-git-data", [git_dir, f"{git_dir}/**"], ALL, "allow"),
        # A `--worktree` run's handoff folder sits in the main checkout. Every run's, decided 2026-11-09.
        rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow"),
        # The agents' own tool caches. Every deny above stays ahead of it.
        rule("sssf-agent-cache", [cache, f"{cache}/**"], ALL, "allow"),
    ]
    if agent == "claude":
        rules_.append(rule("sssf-refuse-script", [refuse_script], READ, "allow"))
    rules_ += [
        # A worktree's agent also reads the main checkout's project settings.
        rule("sssf-main-config", [main, f"{main}/.claude", f"{main}/.claude/**"], READ, "allow"),
        rule("sssf-temp", TEMP, ALL, "allow"),
        rule("sssf-devices", DEVICES, ALL, "allow"),
        rule("sssf-fd-leaves", FD_LEAVES, ALL, "allow"),
        rule("sssf-mkdir-parents", MKDIR_PARENTS, ["mkdir"], "allow"),
        rule("sssf-agent-write", writes, ALL, "allow"),
    ]
    if reads:
        rules_.append(rule("sssf-agent-read", reads, READ, "allow"))
    rules_ += [
        rule("sssf-config-search", search, READ, "allow"),
        rule("sssf-config-folders", folders, FOLDER_OPS, "allow"),
        rule("sssf-system-read", SYSTEM_READ, READ, "allow"),
        rule("sssf-system-link", SYSTEM_LINK, ["symlink_target"], "allow"),
        rule("sssf-home-folder", HOME_FOLDER, FOLDER_OPS, "allow"),
        rule("sssf-home-read", HOME_READ, READ, "allow"),
        rule("sssf-default-deny", ["/", "/**"], ALL, "deny"),
    ]
    return "\n".join(rules_)


def signal_rules() -> str:
    """Signals to the agent's own processes, and nothing else. The first rule that matches
    decides, so the allows come first: a process may signal its parent when the parent is
    in the session too. The SSSF runner, the agent's real parent, is outside it: `external`."""
    signals = str(EVERY_SIGNAL)          # ['@all', '32', ...]: quoted strings, as agentsh reads them
    lines = ["signal_rules:"]
    for target, decision in (("self", "allow"), ("session", "allow"),
                             ("parent", "deny"), ("external", "deny"), ("system", "deny")):
        lines += [f"  - name: sssf-signal-{target}", f"    signals: {signals}",
                  "    target:", f"      type: {target}", f"    decision: {decision}"]
        if decision == "deny":
            lines.append(f"    message: '{_q(SIGNAL_REFUSAL)}'")
    return "\n".join(lines) + "\n"


def policy_name(main: Path, harness: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", Path(main).name.lower()).strip("-") or "repo"
    return f"sssf-{slug}-{SHORT[harness]}"


def policy_text(settings: repo_settings.RepoSettings, git_common_dir: str, harness: str,
                name: str, *, sessions: str, refuse_script: str) -> str:
    agent = SHORT[harness]
    commands = COMMAND_HEAD
    for deny in settings.deny_exec:        # before sssf-allow-rest, or they never fire
        commands += f"  - name: '{deny.name}'\n    # {deny.reason}\n    commands: ['{_q(deny.command)}']\n"
        if deny.args is not None:
            commands += f"    args_patterns: ['{_q(deny.args)}']\n"
        commands += "    decision: deny\n"
    commands += COMMAND_TAIL
    network = NETWORK_HEAD
    if settings.network_allow:
        hosts = ", ".join(f"'{_q(h)}'" for h in settings.network_allow)
        network += (f"  - name: sssf-repo-hosts\n    domains: [{hosts}]\n    ports: [443]\n"
                    "    decision: allow\n")
    network += NETWORK_TAIL
    return (f"version: 1\nname: {name}\n"
            f"description: SSSF default-deny policy for {agent}\n"
            f"{commands}file_rules:\n{file_rules(git_common_dir, agent, settings.secret_files, sessions=sessions, refuse_script=refuse_script)}\n{network}{signal_rules()}")


def guard_for(settings, cfg, root: Path, common: str, refuse_script: str, template: str) -> LocalGuard:
    """The agentsh guard for a checkout, from data alone. `read` and the hub's install script
    both call it, so the run and the install render the same text."""
    data_dir = Path(cfg.defaults.data_dir)
    sessions = str((data_dir if data_dir.is_absolute() else root / data_dir).resolve() / "sessions")
    harnesses = sorted({"claude_code" if a.coding_agent == "claude_code" else "pi" for a in cfg.agents})
    policies = {h: Policy(name=(n := policy_name(root, h)), text=policy_text(
        settings, common, h, n, sessions=sessions, refuse_script=refuse_script)) for h in harnesses}
    return LocalGuard(mode="agentsh", policies=policies, config_template=template,
                      agent_cache=str(Path(sessions).parent / "agent-cache"), git_common_dir=common)


def read(cfg) -> LocalGuard:
    """What `local_guard` asks, read once. Only session.ensure calls this, before
    worktree.enter moves the process. A repo is asked for first, because
    main_checkout() raises outside one."""
    from . import worktree
    in_repo = worktree.git("rev-parse", "--git-dir", check=False).returncode == 0
    root = worktree.main_checkout() if in_repo else Path.cwd().resolve()
    try:
        settings = repo_settings.load(root)
    except (ValidationError, yaml.YAMLError, ValueError, OSError) as error:
        raise SystemExit(f"{repo_settings.PATH} is invalid: {error}") from None
    if settings.local_guard == "text":
        return LocalGuard(mode="text")
    if not in_repo:
        raise SystemExit("local_guard: agentsh needs a git checkout; this directory is not one. "
                         f"Run `git init` here or set `local_guard: text` in {repo_settings.PATH}.")
    common = worktree.git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    refuse_script = str(Path(__file__).resolve().with_name("sssf_refuse.py"))
    try:
        template = (Path(__file__).parent / "agentsh_sssf.yaml").read_text()
    except OSError as error:
        raise SystemExit(f"can't read agentsh_sssf.yaml ({error})") from None
    return guard_for(settings, cfg, root, common, refuse_script, template)


# ── checks ───────────────────────────────────────────────────────────────────

def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AgentshRefused(f"`{' '.join(command[:3])}` failed ({type(error).__name__}); {FIX}") from None


def _service_started() -> int:
    """When the agentsh service started, in whole seconds since the epoch."""
    result = _run(["systemctl", "show", "agentsh", "-p", "ExecMainStartTimestamp", "--timestamp=unix"])
    match = re.fullmatch(r"ExecMainStartTimestamp=@(\d+)", result.stdout.strip()) if result.returncode == 0 else None
    if not match:
        raise AgentshRefused(f"can't read when the agentsh service started; run `{RESTART}`, "
                             "then start the run again")
    return int(match.group(1))


def _plain(value) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def store_notes(now: Optional[float] = None) -> list[str]:
    """Notes about agentsh's event store and audit log (counted together), from the prune's status file. They are printed at the agent's
    start and kept in the end-of-call event. Never raises, never refuses."""
    try:
        now = time.time() if now is None else now
        try:
            data = json.loads(Path(PRUNE_STATUS).read_text())
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            return [f"agentsh's event store has no prune status at {PRUNE_STATUS}, so its size is unknown; "
                    "install the prune timer with `just agentsh-install` and the sudo lines it prints"]
        notes = []
        last = None
        try:
            last = datetime.fromisoformat(str(data.get("last_prune")).replace("Z", "+00:00")).timestamp()
        except (ValueError, TypeError, OverflowError, OSError):
            pass
        if last is None or now - last > PRUNE_STALE_SECONDS:
            text = f"agentsh's event store was last pruned {_plain(data.get('last_prune')) if last is not None else 'never'}"
            if data.get("skipped"):
                text += f"; last skipped: {_plain(data['skipped'])}"
            if data.get("error"):
                text += f"; last error: {_plain(data['error'])}"
            notes.append(text + ". The prune skips while the service lists a session; with no run going, run "
                         "`sudo systemctl start sssf-agentsh-prune.service`. If it still skips on an active "
                         "session, a session may have been left open: with no run going, run "
                         "`sudo systemctl restart agentsh`")
        counted = [data.get(key) for key in ("store_bytes", "audit_bytes")]
        counted = [v for v in counted if isinstance(v, (int, float)) and not isinstance(v, bool)]
        total = sum(counted)
        if counted and total >= STORE_WARN_BYTES:
            notes.append(f"agentsh's event store and audit log are {total / 1e9:.1f} GB together, at least "
                         f"{STORE_WARN_BYTES / 1e9:.0f} GB; with no "
                         "run going, stop the service, move /var/lib/agentsh/events.db and its -wal and -shm files "
                         "aside, start it, and delete the moved files once a run works (see \"Running local agents "
                         "under agentsh\" in .claude/skills/sssf/references/config.md)")
        return notes
    except Exception:
        return []


def check_machine(guard: LocalGuard) -> list[str]:
    """Refuse unless the service is active, the binary is the pinned build, the config and
    policies are what this checkout renders (the pin line names BINARY, CONFIG, POLICY_DIR, SERVER, API
    and PINNED_SHA256), BINARY and CONFIG are not links, and the service started after they changed.
    Returns notes (never a refusal) about the event store's prune status; `start` prints them at the
    agent's start and keeps them in the end-of-call event."""
    if _run(["systemctl", "is-active", "agentsh"]).stdout.strip() != "active":
        raise AgentshRefused(f"the agentsh service is not active; {FIX}")
    for path in (BINARY, CONFIG):
        try:
            mode = os.lstat(path).st_mode
        except OSError as error:
            raise AgentshRefused(f"can't read {path} ({error}); {FIX}") from None
        if stat.S_ISLNK(mode):
            raise AgentshRefused(f"{path} is a link; the install writes a root-owned file there, never a link. "
                                 f"Look at it with `ls -l {path}`, then {FIX}")
    try:
        digest = hashlib.sha256(Path(BINARY).read_bytes()).hexdigest()
    except OSError as error:
        raise AgentshRefused(f"can't read {BINARY} ({error}); {FIX}") from None
    if digest != PINNED_SHA256:
        raise AgentshRefused(f"{BINARY} is not the pinned build (sha256 {digest[:12]}); {FIX}")
    try:
        installed = Path(CONFIG).read_bytes()
        allowed = (yaml.safe_load(installed) or {})["policies"]["allowed"]
        if not isinstance(allowed, list) or not all(isinstance(a, str) for a in allowed):
            raise TypeError("policies.allowed is not a list of strings")
    except (OSError, ValueError, yaml.YAMLError, KeyError, AttributeError, TypeError) as error:
        raise AgentshRefused(f"can't use {CONFIG} ({type(error).__name__}); {FIX}") from None
    try:
        rendered = render_config(guard.config_template, allowed).encode()
    except Exception as error:    # yaml can hand back any shape, or raise several ways, and nothing else runs here
        raise AgentshRefused(f"agentsh_sssf.yaml can't be rendered ({type(error).__name__}); restore the file from "
                             "the SSSF skill, or set `local_guard: text` in adws/adw_sssf_config/repo.yaml") from None
    if installed != rendered:
        raise AgentshRefused(f"{CONFIG} differs from what this checkout renders; {DIFFERS}")
    for policy in guard.policies.values():
        if policy.name not in allowed:
            raise AgentshRefused(f"policy {policy.name} is not in {CONFIG} policies.allowed; {DIFFERS}")
        try:
            on_disk = (Path(POLICY_DIR) / f"{policy.name}.yaml").read_bytes()
        except OSError:
            raise AgentshRefused(f"policy {policy.name} is not installed in {POLICY_DIR}; {FIX}") from None
        if on_disk != policy.text.encode():
            raise AgentshRefused(f"installed policy {policy.name} differs from what this checkout renders; {DIFFERS}")
    started = _service_started()
    for path in (BINARY, CONFIG):
        try:
            changed = int(os.lstat(path).st_ctime)
        except OSError as error:
            raise AgentshRefused(f"can't read {path} ({error}); {FIX}") from None
        # Whole seconds with >=: fails closed. A restart in the same second as the install
        # refuses once by mistake, and a second restart clears it.
        if changed >= started:
            raise AgentshRefused(f"{path} changed after the agentsh service started, so the service may still "
                                 f"run an older build or config; run `{RESTART}` (again, if you just did, since "
                                 "the check counts whole seconds), then start the run again")
    return store_notes()


LINK_SAMPLES = 5    # files a refusal names


def _too_long(path: str) -> AgentshRefused:
    return AgentshRefused(
        f"a path in the workspace is too long for the link check ({len(os.fsencode(path))} bytes, at or over PATH_MAX, 4,096 bytes on Linux), "
        "so its files can't be checked for links outside the workspace and no agent is started. "
        f"It starts: {path[:200]}. Remove it (`rm -rf` on its top folder), then start the run again. "
        "See \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md")


def outside_links(trees: list[str]) -> list[tuple[str, str]]:
    """(tree, path) for each regular file under `trees` with a hard link outside them, sorted by path.
    Follows no symlink and skips nothing. Each folder is read once, by (st_dev, st_ino), so a
    git dir inside the workspace isn't counted twice. A folder that is no longer a folder when the walk
    reaches it refuses. A path of PATH_MAX or more refuses with its own message.
    Reads only: lstat and scandir."""
    seen_dirs: set[tuple[int, int]] = set()
    found: dict[tuple[int, int], list] = {}     # (dev, ino) -> [links seen, st_nlink, tree, path]
    for tree in trees:
        try:
            top = os.lstat(tree)
        except OSError as error:
            raise AgentshRefused(f"can't read {tree} ({error}), so no agent is started; {FIX}") from None
        if not stat.S_ISDIR(top.st_mode):
            raise AgentshRefused(f"{tree} is a link or a file, not a folder, so no agent is started")
        stack = [tree]
        while stack:
            folder = stack.pop()
            try:
                info = os.lstat(folder)
                if not stat.S_ISDIR(info.st_mode):
                    raise AgentshRefused(
                        f"{folder} was a folder when its parent was read and isn't one now, so it can't be "
                        "checked for links outside the workspace and no agent is started. Something changed "
                        "the workspace during the check: look at it with `ls -la`, then start the run again")
                if (info.st_dev, info.st_ino) in seen_dirs:
                    continue
                seen_dirs.add((info.st_dev, info.st_ino))
                entries = list(os.scandir(folder))
            except FileNotFoundError:
                continue
            except OSError as error:
                if error.errno == errno.ENAMETOOLONG:
                    raise _too_long(folder) from None
                raise AgentshRefused(
                    f"can't read {folder} ({error}), so its files can't be checked for links outside the "
                    "workspace; no agent is started. Make it readable or remove it, or set local_guard: text "
                    "in adws/adw_sssf_config/repo.yaml") from None
            for entry in entries:
                try:
                    st = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                except OSError as error:
                    if error.errno == errno.ENAMETOOLONG:
                        raise _too_long(entry.path) from None
                    raise AgentshRefused(f"can't read {entry.path} ({error}), so no agent is started; "
                                         "make it readable or remove it") from None
                if stat.S_ISDIR(st.st_mode):
                    stack.append(entry.path)
                elif stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                    key = (st.st_dev, st.st_ino)
                    slot = found.setdefault(key, [0, st.st_nlink, tree, entry.path])
                    slot[0] += 1
                    slot[1] = st.st_nlink
    return sorted(((t, p) for n, nlink, t, p in found.values() if n < nlink), key=lambda f: f[1])


def _link_hint(tree_path: str, git_common_dir: str) -> tuple[str, str]:
    parts = Path(tree_path).parts
    if tree_path == git_common_dir or tree_path.startswith(git_common_dir.rstrip("/") + "/"):
        return "git", "a git clone made with hardlinks: re-clone the other clone with `git clone --no-local`"
    if "node_modules" in parts:
        return "bun", "`node_modules` installed with links: `rm -rf node_modules && bun install --backend=copyfile`"
    if ".venv" in parts or "site-packages" in parts:
        return "uv", "a uv venv: `UV_LINK_MODE=copy uv sync --reinstall`"
    return "other", ("find its other links with `find / -xdev -samefile <path>` and replace this one with a copy")


def check_links(guard: LocalGuard) -> None:
    """Refuse while a regular file under guard.root or guard.git_common_dir has a hard link outside
    them. agentsh judges by path, so an agent's write to the workspace link would change the other
    copy, which it can't see. Runs at every launch. Cost: one lstat per entry; a pure-Python walk of
    101,000 entries took 1.2 s cold and about 0.5 s warm. The cost is linear in entries, old session
    folders included: the hub's 725,510 entries on 2026-11-11 cost several seconds per launch."""
    if not guard.git_common_dir:
        raise AgentshRefused("no git common dir reached the launch; this is a bug in the caller")
    root = guard.root
    common = guard.git_common_dir
    found = outside_links([root, common])
    if not found:
        return
    # A file under the git dir inside the workspace is still a git file, so classify by the common dir.
    hints: dict[str, str] = {}
    named = []
    for tree, path in found[:LINK_SAMPLES]:
        kind, hint = _link_hint(path, common)
        named.append(f"{path} (in {tree})")
        hints.setdefault(kind, hint)
    for tree, path in found[LINK_SAMPLES:]:
        kind, hint = _link_hint(path, common)
        hints.setdefault(kind, hint)
    more = f" and {len(found) - LINK_SAMPLES} more" if len(found) > LINK_SAMPLES else ""
    raise AgentshRefused(
        f"{len(found)} file(s) in the workspace or the git common dir have a hard link outside them, so no "
        "agent is started: an agent's write to its link changes the other copy, outside the workspace, which "
        f"agentsh judges by path and can't see. Files: {'; '.join(named)}{more}. Rebuild: "
        + "; ".join(hints.values())
        + ". See \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md, "
        "or set local_guard: text in adws/adw_sssf_config/repo.yaml")


def check_paths(root: str, env=None) -> None:
    """Refuse a root or home folder holding glob syntax: agentsh puts ${PROJECT_ROOT} and
    ${HOME} into its rules as raw text, so the rules would also match other folders."""
    home = (os.environ if env is None else env).get("HOME")
    candidates = [("checkout", root)]
    if home:
        candidates.append(("home folder", home))
    candidates.append(("home folder", pwd.getpwuid(os.getuid()).pw_dir))
    for kind, path in candidates:
        found = next((c for c in GLOB_CHARS if c in path), None)
        if found:
            raise AgentshRefused(
                f"the {kind} path {path} holds `{found}`. agentsh puts this path into its rules as raw text, "
                "where * ? [ ] { } \\ $ are glob syntax, so the rules would also match other folders. Move the "
                "checkout, or use a home folder, without any of * ? [ ] { } \\ $, or set `local_guard: text` "
                "in adws/adw_sssf_config/repo.yaml")


def check_root(root: str) -> None:
    """Refuse a root or home folder with glob syntax, then a root that is not the top of a git checkout."""
    check_paths(root)
    result = _run(["git", "-C", root, "rev-parse", "--show-toplevel"])
    if result.returncode != 0 or Path(result.stdout.strip()).resolve() != Path(root).resolve():
        raise AgentshRefused(f"{root} is not the top of a git checkout, so agentsh can't be rooted there; "
                             f"{FIX}")


# ── the wrapped process ──────────────────────────────────────────────────────

_SESSION = re.compile(r"agentsh: session (\S+) created \(policy: ([^)]+)\)")
_STARTED = re.compile(r"agentsh: agent .*? started with (\w+) interception")


class _Stderr:
    def __init__(self, process: "AgentshProcess"):
        self._process = process

    def read(self) -> str:
        self._process._drain.join(5)
        return "".join(self._process._other)


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _kill_descendants(pid: int) -> None:
    """SIGKILL everything under `pid`, found by reading /proc. No process is started,
    so it works when `ps` can't run. The wrap has no parent-death signal, so killing
    it alone would leave the agent running. `stat` is read as bytes because a process's
    name needn't be valid UTF-8, and a skipped process would take its subtree out of the walk."""
    children: dict[int, list[int]] = {}
    try:
        entries = [e for e in Path("/proc").iterdir() if e.name.isdigit()]
    except OSError:
        return
    for entry in entries:
        try:
            parent = int((entry / "stat").read_bytes().rsplit(b")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(parent, []).append(int(entry.name))
    seen, queue = set(), [pid]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    for each in seen:
        try:
            os.kill(each, signal.SIGKILL)
        except OSError:
            pass


COMMAND_CHARS = 160


def command_text(event: dict) -> str:
    """The command an event names: its `command`, else argv joined (argv[0] by basename),
    with control characters escaped and a long one shortened to its first word and its tail."""
    fields = event.get("fields") or {}
    text = event.get("command") or fields.get("command")
    if not (isinstance(text, str) and text):
        argv = fields.get("argv") or event.get("argv")
        if not (isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv)):
            return ""
        text = " ".join([os.path.basename(argv[0]), *argv[1:]])
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    if len(text) > COMMAND_CHARS:
        head = text.split(" ", 1)[0]
        tail = text[-140:]
        if " " in tail:
            tail = tail.split(" ", 1)[1]
        return f"{head} … {tail}"
    return text


class AgentshProcess:
    """The wrap, in the sandbox.Process shape. `pid` is the wrap's."""

    def __init__(self, popen: subprocess.Popen, policy: str, tally: rules.RefusalTally):
        self._popen = popen
        self.pid = popen.pid
        self.policy = policy
        self.tally = tally
        self.stdout = self._lines()
        self.stderr = _Stderr(self)
        self._other: list[str] = []
        self._lock = threading.Lock()
        self._status = threading.Condition()
        self._stopped = False
        self._ended = False
        self._done = threading.Event()
        self.session: Optional[str] = None
        self.attached = False
        self.problem = ""             # why the attach failed
        self.failure = ""             # why the service failed after attach
        self.tripped = False
        self.counted = 0              # exec denials this process counted
        self.last = ("", "")          # the last counted denial's rule and its shortened command
        self.other_denials: list[dict] = []
        self._offset = 0
        self._seen: set[str] = set()
        self._failed_polls = 0
        self.trace_notes: list[str] = []
        self.started_at: Optional[float] = None
        self.polls = 0
        self.pages = 0
        self.slowest = 0.0
        self._drain = threading.Thread(target=self._read_stderr, daemon=True)
        self._drain.start()
        self._watcher: Optional[threading.Thread] = None

    # output
    def _lines(self) -> Iterator[str]:
        for line in self._popen.stdout:
            if not line.startswith("agentsh:"):
                yield line

    def _read_stderr(self) -> None:
        try:
            for line in self._popen.stderr:
                if line.startswith("agentsh:"):
                    self._status_line(line.strip())
                else:
                    self._other.append(line)
        finally:
            with self._status:
                self._status.notify_all()

    def _status_line(self, line: str) -> None:
        with self._status:
            if "running without interception" in line:
                self.problem = self.problem or "agentsh reported the agent is running without interception"
            elif (m := _SESSION.search(line)) and self.session is None:
                if m.group(2) != self.policy:
                    self.problem = self.problem or f"agentsh loaded policy {m.group(2)}, not {self.policy}"
                self.session = m.group(1)
            elif "agent " in line and "started (pid:" in line and not self.attached:
                self.problem = self.problem or "agentsh started the agent with no interception mechanism"
            elif (m := _STARTED.search(line)) and not self.attached:
                if m.group(1) != "ptrace":
                    self.problem = self.problem or f"agentsh intercepts with {m.group(1)}, not ptrace"
                else:
                    self.attached = True
            self._status.notify_all()

    def wait_attached(self) -> None:
        def ready() -> bool:
            return bool(self.problem) or (self.attached and self.session is not None) \
                or self._popen.poll() is not None and not self._drain.is_alive()
        with self._status:
            self._status.wait_for(ready, timeout=ATTACH_SECONDS)
            ok = self.attached and self.session is not None and not self.problem
            problem = self.problem
        if ok:
            self._watcher = threading.Thread(target=self._watch, daemon=True)
            self._watcher.start()
            return
        self.stop_tree()
        self._end_session()
        atexit.unregister(self._at_exit)
        why = problem or ("the wrap exited before it attached" if self._popen.poll() is not None
                          else f"the wrap did not attach in {ATTACH_SECONDS} s")
        raise AgentshRefused(f"{why}; {FIX}")

    # Process shape
    def wait(self) -> int:
        return self._popen.wait()

    def kill(self) -> None:
        # The wrap's death alone can leave the agent running, reparented; take the tree.
        self.stop_tree()
        self._popen.kill()

    def stop_tree(self) -> None:
        """Kill the tree, once. Ending the session is the main thread's: see `_end_session`."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        _kill_descendants(self.pid)     # from /proc, with no fork: `ps` can fail on a full process table
        rules.stop_tree(self.pid)

    def _at_exit(self) -> None:
        self.stop_tree()
        self._end_session()

    # events
    def _get(self, query: dict) -> list[dict]:
        qs = "&".join(f"{k}={v}" for k, v in query.items())
        with _opener().open(f"{API}/api/v1/events/search?{qs}", timeout=5) as reply:
            data = json.loads(reply.read().decode())
        rows = data.get("events") if isinstance(data, dict) else data
        if rows is None:
            return []
        if not isinstance(rows, list):
            raise ValueError("unexpected events body")
        return [r for r in rows if isinstance(r, dict)]

    def _timed_get(self, query: dict) -> list[dict]:
        """`_get`, with the request timed. A failed request counts toward the slowest, not the pages."""
        began = time.monotonic()
        answered = False
        try:
            rows = self._get(query)
            answered = True
            return rows
        finally:
            elapsed = time.monotonic() - began
            with self._lock:
                self.slowest = max(self.slowest, elapsed)
                if answered:
                    self.pages += 1

    def _pages(self, start: int, extra: dict) -> list[dict]:
        out, offset = [], start
        while True:
            rows = self._timed_get({"session_id": self.session, **extra, "order": "asc",
                              "limit": PAGE, "offset": offset})
            out += rows
            offset += len(rows)
            if len(rows) < PAGE:
                return out

    def _take(self, rows: list[dict]) -> None:
        for event in rows:
            key = str(event.get("id") or json.dumps(event, sort_keys=True))
            if key in self._seen:
                continue
            self._seen.add(key)
            policy, fields = event.get("policy") or {}, event.get("fields") or {}
            decision = policy.get("effective_decision") or fields.get("decision")
            rule_name = policy.get("rule") or fields.get("rule")
            if decision != "deny":
                continue
            command = command_text(event)
            if event.get("type") == "ptrace_execve" and rule_name:
                self.counted += 1
                self.last = (str(rule_name), str(command))
                if self.tally.add() >= rules.MAX_REFUSALS:
                    self.tripped = True
            else:       # file, network and unnamed exec denials: traced, never counted
                self.other_denials.append({
                    "type": str(event.get("type") or ""),
                    "path": str(event.get("path") or fields.get("path") or event.get("domain")
                                or fields.get("domain") or command),
                    "rule": str(rule_name or "")})

    def _poll(self) -> None:
        while True:     # each page is taken as it arrives, so a flood of events can't hold back the stop
            rows = self._timed_get({"session_id": self.session, "order": "asc", "limit": PAGE,
                              "offset": self._offset})
            with self._lock:
                if self._done.is_set():
                    return
                self._offset += len(rows)
                self._take(rows)
                if self.tripped:
                    return
            if len(rows) < PAGE:
                return

    def _watch(self) -> None:
        while not self._done.wait(POLL_SECONDS):
            with self._lock:
                self.polls += 1
            try:
                self._poll()
                self._failed_polls = 0
            except Exception:
                self._failed_polls += 1
                if self._failed_polls >= 3:
                    with self._lock:
                        if not self._done.is_set():
                            self.failure = "the agentsh service stopped answering event polls"
                    self.stop_tree()
                    return
                continue
            if self.tripped:
                self.stop_tree()
                return

    def _end_session(self) -> None:
        """The final read, then the session's DELETE. Once, and only on the main thread."""
        with self._lock:
            first = not self._ended and self.session is not None
            if first:
                self._ended = True
                self.polls += 1
                self._done.set()
        if not first:
            return
        try:
            try:
                rows = self._pages(0, {})
                with self._lock:
                    self._take(rows)
            except Exception as error:
                self.trace_notes.append(f"final event read failed: {type(error).__name__}")
                self.failure = f"the final read of agentsh events failed ({type(error).__name__})"
        finally:
            try:
                request = urllib.request.Request(f"{API}/api/v1/sessions/{self.session}", method="DELETE")
                _opener().open(request, timeout=5).close()
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    self.trace_notes.append(f"session delete failed: HTTP {error.code}")
            except Exception as error:
                self.trace_notes.append(f"session delete failed: {type(error).__name__}")


GUARD_EVENT_PREFIX = "agentsh_"


def guard_event(event) -> bool:
    """True for an event whose type only this guard may send (agentsh_note, agentsh_denials, and any
    later agentsh_ type). The harness read loops drop such an event from the agent's stream before
    on_event, because a command can write lines to the harness's stdout; start() and close() call
    on_event directly. Never raises."""
    return isinstance(event, dict) and isinstance(event.get("type"), str) \
        and event["type"].startswith(GUARD_EVENT_PREFIX)


def start(tree, harness: str, cmd: list[str], *, cwd, env, guard: Optional[LocalGuard],
          tally: Optional[rules.RefusalTally] = None, on_event: Optional[Callable[[dict], None]] = None):
    if tree is not sandbox.LOCAL:
        return tree.start(cmd, cwd=cwd, env=env)
    if guard is None:
        raise AgentshRefused("no local_guard reached the launch, so the agent is not started; "
                             "this is a bug in the caller")
    if guard.mode == "text":
        return tree.start(cmd, cwd=cwd, env=env)
    notes = check_machine(guard)
    if on_event:
        for note in notes:
            try:
                on_event({"type": "agentsh_note", "note": note})
            except Exception:
                pass
    if not guard.root or Path(cwd).resolve() != Path(guard.root):
        raise AgentshRefused(f"the agent would run in {cwd}, not the guarded root {guard.root or '(none)'}")
    policy = guard.policies.get(harness)
    if policy is None:
        raise AgentshRefused(f"no agentsh policy was generated for {harness}; {FIX}")
    check_paths(guard.root, env)
    if not guard.agent_cache:
        raise AgentshRefused("no agent cache folder reached the launch; this is a bug in the caller")
    cache = os.path.abspath(guard.agent_cache)
    try:
        os.makedirs(os.path.dirname(cache), exist_ok=True)   # the data root, which already holds sessions/
        try:
            os.mkdir(cache, 0o700)      # outside the wrap; mkdir never follows a link at its last component
        except FileExistsError:
            pass
        is_folder = stat.S_ISDIR(os.lstat(cache).st_mode)
    except OSError as error:
        raise AgentshRefused(f"can't make the agent cache {cache} ({error}); {FIX}") from None
    if not is_folder:
        raise AgentshRefused(f"{cache} is a link or a file, not a folder, so no agent is started; an agent "
                             "may have put it there. Look at it with `ls -la`, remove it, and start the run again")
    check_links(guard)      # after check_machine, before any process starts
    child = dict(os.environ if env is None else env)
    ours = cache_env(guard.agent_cache)
    wanted = {k.lower() for k in ours}
    for key in [k for k in child if k.lower() in wanted]:
        del child[key]
    child.update(ours)
    child.update({"AGENTSH_CONFIG": CONFIG, "AGENTSH_SERVER": SERVER, "AGENTSH_NO_AUTO": "1",
                  "NODE_USE_ENV_PROXY": "1", "SSSF_AGENTSH": "1", "JUST_NO_DOTENV": "true"})
    started_at = time.time()
    try:
        popen = subprocess.Popen(
            [BINARY, "wrap", "--policy", policy.name, "--root", guard.root, "--report=false", "--", *cmd],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", bufsize=1, cwd=cwd, env=child)
    except OSError as error:
        raise AgentshRefused(f"can't run {BINARY} ({error}); {FIX}") from None
    process = AgentshProcess(popen, policy.name, tally or rules.RefusalTally())
    process.started_at = started_at
    process.trace_notes.extend(notes)
    # Before the attach wait: on_spawn hasn't run, so `just kill` can't see the wrap yet.
    atexit.register(process._at_exit)
    process.wait_attached()
    return process


# The lookbehind keeps `subtype=` and `event_subtype=` from being read as the type.
_DROP_TYPE = re.compile(r'(?<![A-Za-z0-9_])"?(?:event_)?type"?\s*[=:]\s*"?([^"\s,}]+)')
_TYPE_NAME = re.compile(r"[a-z0-9_]{1,64}")


def journal_drops(since: float, until: float,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[dict[str, int], Optional[str]]:
    """agentsh's "event channel full" lines between `since` and `until`, counted by event type,
    as the runner's own user. Returns (counts, problem); `problem` is set, and the counts empty,
    when the journal can't be read. journald's rate limit can suppress lines, so the counts are
    a floor. The window is in whole seconds and ends when the read runs, so a line journald delivers
    late isn't counted, and a line under the unit can carry agent-chosen text, so the counts are
    advice. Never raises."""
    try:
        command = ["journalctl", "-u", "agentsh", "--since", f"@{int(since)}",
                   "--until", f"@{math.ceil(until)}", "-o", "cat", "--no-pager"]
        try:
            done = run(command, capture_output=True, text=True, errors="replace", timeout=JOURNAL_SECONDS,
                       env={**os.environ, "LC_ALL": "C"})
        except subprocess.TimeoutExpired:
            return {}, f"timed out after {JOURNAL_SECONDS} s"
        except OSError as error:
            return {}, f"could not run ({type(error).__name__})"
        if done.returncode != 0:
            return {}, f"exited {done.returncode}"
        err = (done.stderr or "").lower()
        if any(text in err for text in ("insufficient permissions", "not seeing messages", "permission denied")):
            return {}, "no permission to read the agentsh unit's lines"
        counts: dict[str, int] = {}
        for line in (done.stdout or "").splitlines():
            if DROP_TEXT not in line:
                continue
            m = _DROP_TYPE.search(line)
            kind = m.group(1) if m and _TYPE_NAME.fullmatch(m.group(1)) else "unknown"
            counts[kind] = counts.get(kind, 0) + 1
        return counts, None
    except Exception as error:
        return {}, f"failed ({type(error).__name__})"


def close(process, on_event: Optional[Callable[[dict], None]] = None) -> None:
    """After the agent exits: end the session, count the events agentsh dropped (from its journal, as a
    floor), and trace the denials that didn't count, with the poll figures. Never raises, and does
    nothing for another process type."""
    if not isinstance(process, AgentshProcess):
        return
    try:
        process._end_session()
        atexit.unregister(process._at_exit)
        try:
            if process.started_at is not None:
                counts, problem = journal_drops(process.started_at, time.time())
                if problem:
                    process.trace_notes.append(
                        f"agentsh's journal couldn't be read for this call ({problem}), so events agentsh dropped "
                        "are not counted; check with `journalctl -u agentsh --since <start> | grep -c 'event channel full'`")
                elif counts:
                    total = sum(counts.values())
                    detail = ", ".join(f"{t} {n}" for t, n in sorted(counts.items()))
                    process.trace_notes.append(
                        f"agentsh dropped at least {total} event(s) during this call, when its store's channel was "
                        f"full: {detail}. journald's rate limit can hide more. The trace's denials may be short by "
                        "these. Drops are counted for the agentsh service as a whole, so a concurrent run's drops "
                        "show here too")
                    if counts.get("ptrace_execve"):
                        process.trace_notes.append(
                            f"at least {counts['ptrace_execve']} dropped event(s) were ptrace_execve: a denied exec "
                            "may have gone uncounted toward the three-refusal stop. agentsh still enforced it")
            if process.slowest > SLOW_POLL_SECONDS:
                process.trace_notes.append(
                    f"the slowest agentsh event poll took {process.slowest:.1f} s; a poll request gives up at 5 s "
                    "and three failed polls in a row stop the agent. A slow poll usually means a big event store: "
                    "see \"The event store\" in .claude/skills/sssf/references/config.md")
        except Exception:
            pass
        if on_event:
            counts: dict[tuple, int] = {}
            for d in process.other_denials:
                key = (d["type"], d["path"], d["rule"])
                counts[key] = counts.get(key, 0) + 1
            denials = [{"type": t, "path": p, "rule": r, "count": n} for (t, p, r), n in sorted(counts.items())]
            try:
                on_event({"type": "agentsh_denials", "denials": denials, "notes": process.trace_notes,
                          "polls": process.polls, "pages": process.pages,
                          "slowest_poll_seconds": round(process.slowest, 2)})
            except Exception:
                pass
    except Exception:
        pass


def breach(process, agent: str = "") -> Optional[rules.RuleBreach]:
    """The three-refusal stop if this process reached it, else None."""
    if not isinstance(process, AgentshProcess):
        return None
    total = process.tally.total
    if process.counted and (process.tripped or total >= rules.MAX_REFUSALS):
        name, command = process.last
        return rules.RuleBreach(agent, rules.AgentRule(rule=f"agentsh rule {name}"), name, command,
                                refusals=total)
    return None


def check_stopped(process, agent: str = "") -> None:
    """Raise the stop agentsh's watch asked for. Call after `close`, so a denial seen
    only in the final read counts too."""
    if not isinstance(process, AgentshProcess):
        return
    stop = breach(process, agent)
    if stop:
        raise stop
    if process.failure:
        raise AgentshRefused(f"{process.failure}; the agent was stopped. {FIX}")

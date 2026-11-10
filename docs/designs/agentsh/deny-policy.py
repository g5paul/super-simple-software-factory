#!/usr/bin/env python3
"""Writes the default-deny test policies, sssf-dd-claude.yaml and sssf-dd-pi.yaml.

Usage: deny-policy.py GIT_COMMON_DIR OUTDIR
The command and network rules come from sssf-net.yaml beside this file. Only the
file rules differ: first match wins, and the last rule denies everything.
GIT_COMMON_DIR is literal because a run's worktree keeps its git data in the main
checkout, and ${GIT_ROOT} is only the worktree.
"""
import re
import sys
from pathlib import Path
from typing import Callable

READ = ["open", "read", "stat", "list", "readlink", "access"]
ALL = ["*"]

# Only an entry here can be read by name, whichever directory it's in.
SECRET_ALLOW = ["**/.env.example", "**/.env.sample"]
SECRETS = ["**/.env", "**/.env.*"]
# What neither agent needs. Some sit under a tree allowed below, the rest are named so
# that the trace shows why the open failed.
CREDENTIALS = [f"${{HOME}}/{p}" for p in (
    ".ssh/**", ".aws/**", ".azure/**", ".gnupg/**", ".kube/**", ".docker/**",
    ".config/gh/**", ".config/gcloud/**", ".config/sssf-sandbox/**",
    ".git-credentials", ".netrc", ".npmrc", ".pypirc", ".cargo/credentials", ".cargo/credentials.toml",
)]
PROC_SECRETS = ["/proc/*/environ", "/proc/*/mem", "/proc/*/task/*/environ", "/proc/*/task/*/mem"]
CLAUDE_SIGN_IN = ["${HOME}/.claude/.credentials.json"]
PI_SIGN_IN = ["${HOME}/.pi/agent/auth.json"]
# Writing these would change what the engineer's own next session runs.
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
CLAUDE_READ = [f"${{HOME}}/{p}" for p in (
    "AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".config/anthropic", ".config/anthropic/**",
    "github/skills/**", "github/plugins/**",
)] + ["/mnt/c/Program Files/ClaudeCode", "/mnt/c/Program Files/ClaudeCode/**"]
CLAUDE_WRITE = [f"${{HOME}}/{p}" for p in (
    ".claude", ".claude/**", ".claude.json", ".claude.json.*", ".cache/claude-cli-nodejs/**",
    ".local/state/claude/**", ".local/share/claude/**",
)] + ["/run/user/*/cc-socks", "/run/user/*/cc-socks/**"]
PI_WRITE = ["${HOME}/.pi", "${HOME}/.pi/**"]


def rule(name, paths, ops, decision):
    lines = [f"  - name: {name}", "    paths:"]
    lines += [f"      - '{p}'" for p in paths]
    lines += [f"    operations: {ops}".replace('"', "'"), f"    decision: {decision}"]
    return "\n".join(lines)


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


def file_rules(git_dir, agent, *, sessions, refuse_script):
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
    rules = [
        rule("sssf-env-examples", SECRET_ALLOW, ALL, "allow"),
        rule("sssf-secret-files", SECRETS, ALL, "deny"),
        rule("sssf-credentials", CREDENTIALS, ALL, "deny"),
        rule("sssf-proc-secrets", PROC_SECRETS, ALL, "deny"),
        rule("sssf-other-sign-in", other_sign_in, ALL, "deny"),
    ]
    if config_deny:
        rules.append(rule("sssf-agent-config", config_deny, WRITE_OPS, "deny"))
    rules += [
        # Hooks and git config run on the host, outside the wrap, at the next git command.
        rule("sssf-git-config", [p for d in (git_dir, "${PROJECT_ROOT}/.git") for p in (
            f"{d}/hooks", f"{d}/hooks/**", f"{d}/config", f"{d}/config.lock")], WRITE_OPS, "deny"),
        rule("sssf-workspace", ["${PROJECT_ROOT}", "${PROJECT_ROOT}/**"], ALL, "allow"),
        rule("sssf-git-data", [git_dir, f"{git_dir}/**"], ALL, "allow"),
        # A `--worktree` run's handoff folder sits in the main checkout. Every run's, decided 2026-11-09.
        rule("sssf-sessions", [sessions, f"{sessions}/**"], ALL, "allow"),
        rule("sssf-agent-cache", [cache, f"{cache}/**"], ALL, "allow"),
    ]
    if agent == "claude":
        rules.append(rule("sssf-refuse-script", [refuse_script], READ, "allow"))
    rules += [
        # A worktree's agent also reads the main checkout's project settings.
        rule("sssf-main-config", [main, f"{main}/.claude", f"{main}/.claude/**"], READ, "allow"),
        rule("sssf-temp", TEMP, ALL, "allow"),
        rule("sssf-devices", DEVICES, ALL, "allow"),
        rule("sssf-fd-leaves", FD_LEAVES, ALL, "allow"),
        rule("sssf-mkdir-parents", MKDIR_PARENTS, ["mkdir"], "allow"),
        rule("sssf-agent-write", writes, ALL, "allow"),
    ]
    if reads:
        rules.append(rule("sssf-agent-read", reads, READ, "allow"))
    rules += [
        rule("sssf-config-search", search, READ, "allow"),
        rule("sssf-config-folders", folders, FOLDER_OPS, "allow"),
        rule("sssf-system-read", SYSTEM_READ, READ, "allow"),
        rule("sssf-system-link", SYSTEM_LINK, ["symlink_target"], "allow"),
        rule("sssf-home-folder", HOME_FOLDER, FOLDER_OPS, "allow"),
        rule("sssf-home-read", HOME_READ, READ, "allow"),
        rule("sssf-default-deny", ["/", "/**"], ALL, "deny"),
    ]
    return "\n".join(rules)


def main():
    git_dir, out = sys.argv[1], Path(sys.argv[2])
    main = str(Path(git_dir).parent)
    sessions = f"{main}/adws/adw_data/sessions"
    refuse = f"{main}/adws/adw_modules/sssf_refuse.py"
    base = (Path(__file__).parent / "sssf-net.yaml").read_text()
    commands = base[base.index("command_rules:"):base.index("file_rules:")]
    network = base[base.index("network_rules:"):]
    for agent in ("claude", "pi"):
        name = f"sssf-dd-{agent}"
        text = (f"version: 1\nname: {name}\n"
                f"description: SSSF default-deny test policy for {agent}\n"
                f"{commands}file_rules:\n{file_rules(git_dir, agent, sessions=sessions, refuse_script=refuse)}\n{network}")
        (out / f"{name}.yaml").write_text(text)
        print(out / f"{name}.yaml")


main()

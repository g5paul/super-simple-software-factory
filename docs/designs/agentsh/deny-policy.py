#!/usr/bin/env python3
"""Writes the default-deny test policies, sssf-dd-claude.yaml and sssf-dd-pi.yaml.

Usage: deny-policy.py GIT_COMMON_DIR OUTDIR
The command and network rules come from sssf-net.yaml beside this file. Only the
file rules differ: first match wins, and the last rule denies everything.
GIT_COMMON_DIR is literal because a run's worktree keeps its git data in the main
checkout, and ${GIT_ROOT} is only the worktree.
"""
import sys
from pathlib import Path

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
WRITE_OPS = ["write", "create", "delete", "rename", "link", "symlink", "mkdir", "rmdir", "chmod", "chown"]

SYSTEM_READ = ["/usr/**", "/lib/**", "/lib64/**", "/bin/**", "/sbin/**", "/opt/**", "/etc/**",
               "/sys/**", "/proc", "/proc/**", "/", "/home", "/var", "/run", "/mnt", "/tmp"]
TEMP = ["/tmp/**", "/var/tmp/**", "/dev/shm/**"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/tty", "/dev/ptmx", "/dev/pts/**", "/dev/urandom",
           "/dev/random", "/dev/fd/**", "/dev/stdin", "/dev/stdout", "/dev/stderr"]
HOME_READ = [f"${{HOME}}{p}" for p in (
    "", "/.bashrc", "/.profile", "/.bash_profile", "/.bash_login", "/.editrc", "/.inputrc",
    "/.gitconfig", "/.config", "/.config/git/**", "/.config/uv/**", "/.cargo", "/.cargo/env",
    "/.cargo/bin/**", "/.local", "/.local/bin/**", "/.nvm/**", "/.rustup/**",
)]
TOOL_WRITE = [f"${{HOME}}/{p}" for p in (
    ".cache/uv", ".cache/uv/**", ".local/share/uv", ".local/share/uv/**", ".npm/**",
    ".cargo/registry/**", ".cargo/git/**",
)]
CLAUDE_READ = [f"${{HOME}}/{p}" for p in (
    "AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", ".config/anthropic", ".config/anthropic/**",
    "github/skills/**", "github/plugins/**",
)]
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


def file_rules(git_dir, agent):
    other_sign_in, config_deny, reads, writes = {
        "claude": (PI_SIGN_IN, CLAUDE_CONFIG, CLAUDE_READ, CLAUDE_WRITE),
        "pi": (CLAUDE_SIGN_IN, [], [], PI_WRITE),
    }[agent]
    main = str(Path(git_dir).parent)
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
        rule("sssf-workspace", ["${PROJECT_ROOT}", "${PROJECT_ROOT}/**"], ALL, "allow"),
        rule("sssf-git-data", [git_dir, f"{git_dir}/**"], ALL, "allow"),
        # A worktree's agent also reads the main checkout's project settings.
        rule("sssf-main-config", [main, f"{main}/.claude", f"{main}/.claude/**"], READ, "allow"),
        rule("sssf-temp", TEMP, ALL, "allow"),
        rule("sssf-devices", DEVICES, ALL, "allow"),
        rule("sssf-tool-write", TOOL_WRITE, ALL, "allow"),
        rule("sssf-agent-write", writes, ALL, "allow"),
    ]
    if reads:
        rules.append(rule("sssf-agent-read", reads, READ, "allow"))
    rules += [
        rule("sssf-system-read", SYSTEM_READ, READ, "allow"),
        rule("sssf-home-read", HOME_READ, READ, "allow"),
        rule("sssf-default-deny", ["/", "/**"], ALL, "deny"),
    ]
    return "\n".join(rules)


def main():
    git_dir, out = sys.argv[1], Path(sys.argv[2])
    base = (Path(__file__).parent / "sssf-net.yaml").read_text()
    commands = base[base.index("command_rules:"):base.index("file_rules:")]
    network = base[base.index("network_rules:"):]
    for agent in ("claude", "pi"):
        name = f"sssf-dd-{agent}"
        text = (f"version: 1\nname: {name}\n"
                f"description: SSSF default-deny test policy for {agent}\n"
                f"{commands}file_rules:\n{file_rules(git_dir, agent)}\n{network}")
        (out / f"{name}.yaml").write_text(text)
        print(out / f"{name}.yaml")


main()

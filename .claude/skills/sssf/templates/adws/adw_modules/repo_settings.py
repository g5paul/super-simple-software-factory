"""This repo's SSSF rules, from adws/adw_sssf_config/repo.yaml.

The roster (sssf.config.yaml) says who runs a chain, and SSSF_CONFIG can swap
it for one run. A rule like "this repo keeps a linear history" must not change
with the roster, and must not live only in an untracked .env, where a fresh
clone never sees it. So it gets its own committed file.

A repo installed before the file existed has none until its next update, and
then every rule takes its default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .rules import AgentRule, text

PATH = "adws/adw_sssf_config/repo.yaml"


class SandboxAllow(BaseModel):
    """One network rule `--sandbox openshell` opens beyond its defaults."""
    model_config = ConfigDict(extra="forbid")
    binary: str          # a path in the sandbox starting with /, or "*" for any binary
    host: str
    port: int = Field(443, ge=1, le=65535)
    access: Literal["read-only", "full"]

    @field_validator("binary")
    @classmethod
    def _binary(cls, value: str) -> str:
        if value != "*" and not value.startswith("/"):
            raise ValueError('binary must be a path in the sandbox starting with "/", or "*"')
        return value

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        if not value or "/" in value or "://" in value:
            raise ValueError("host must be a bare host name: no scheme and no /")
        return value


# The base rule names agentsh_guard writes, which a deny_exec rule may not reuse.
BASE_RULE_NAMES = {"sssf-no-live-tests", "sssf-no-gh", "sssf-no-push", "sssf-allow-rest"}
_NO_RE2 = ("(?<", "(?=", "(?!")


class DenyExec(BaseModel):
    """One command `local_guard: agentsh` refuses to exec, beside the built-in ones."""
    model_config = ConfigDict(extra="forbid")
    name: str
    command: str         # a bare name: agentsh rules match the basename
    args: Optional[str] = None   # a Go (RE2) regex over all of argv joined by spaces, argv[0] included
    reason: str

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9-]+", value):
            raise ValueError("name must be a slug: lower case letters, digits and -")
        if value in BASE_RULE_NAMES:
            raise ValueError(f"name {value!r} is a built-in rule name")
        return value

    @field_validator("command")
    @classmethod
    def _command(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9._+-]+", value):
            raise ValueError("command must be a bare name: letters, digits and . _ + - only")
        return value

    @field_validator("args")
    @classmethod
    def _args(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            re.compile(value)
        except re.error as error:
            raise ValueError(f"args is not a valid regex: {error}")
        if any(token in value for token in (*_NO_RE2, "(?P=", "\\k<")) or re.search(r"\\[1-9]", value):
            raise ValueError("args is read as Go RE2: no lookaround and no backreferences")
        return value

    @field_validator("reason")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("reason must be one non-empty line")
        return value


def _bare_host(value: str) -> str:
    last = value.rsplit(".", 1)[-1]
    # The kernel reads 127.1, 0x7f.1 and 2130706433 as addresses: the last label decides.
    if (not value or "://" in value or "/" in value or ":" in value or last.lower() == "localhost"
            or value.startswith(".") or re.fullmatch(r"(0x[0-9a-f]+|[0-9]+)", last, re.I)):
        raise ValueError(f"{value!r} must be a bare host name: no scheme, /, port or IP literal")
    if "*" in value and not (value.startswith("*.") and "*" not in value[2:] and "." in value[2:]):
        raise ValueError(f"{value!r}: the only wildcard allowed is *.a.b")
    if not re.fullmatch(r"(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?", value):
        raise ValueError(f"{value!r} is not a host name")
    return value


class RepoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a misspelled rule fails, never silently defaults
    merge: Literal["any", "ff_only"] = "any"
    max_worktree_runs: int = Field(2, ge=1)
    # What every live run in the repo may spend together, in dollars. None = off.
    max_live_cost: Optional[float] = Field(None, gt=0)
    # What the repo's runs may spend in the last 24 hours, rolling, in dollars. None = off.
    max_daily_cost: Optional[float] = Field(None, gt=0)
    # Where a chain that takes --sandbox does a new run's work when the flag
    # is left out: "none" on this machine, or a provider's VM (openshell is local).
    sandbox: Literal["none", "vercel", "e2b", "openshell"] = "none"
    # The hosts `--sandbox openshell` opens beyond its defaults, each one network rule.
    sandbox_allow: list[SandboxAllow] = Field(default_factory=list)
    # Stop an agent that writes nothing for this many minutes: a hang spends
    # nothing, so the cost caps never see it (silence.py). None = off.
    max_silence_minutes: Optional[float] = Field(15, gt=0)
    worktree_setup: list[str] = Field(default_factory=list)
    # The repo's own checks, run on a `just export` branch, which holds no SSSF
    # files and so no quality.py. Each is a shell command from the repo root.
    # Empty: the export reports "unchecked".
    export_checks: list[str] = Field(default_factory=list)
    worktree_setup_timeout: int = Field(600, ge=1)
    # Rules every agent gets at the end of its system prompt, whatever its role.
    # A string only asks; an AgentRule with `forbid` also stops the run (rules.py).
    agent_rules: list[str | AgentRule] = Field(default_factory=list)
    # Paths or globs, from the repo root, of files agents must not read, on top of
    # .env and .env.* (but .env.sample, .env.example). A pattern without / matches a
    # file name anywhere (secret_files.py).
    secret_files: list[str] = Field(default_factory=list)
    # Keys from .env that agents still get, besides SSSF_*, OBS_*, CLAUDE_CODE_OAUTH_TOKEN
    # and, for pi, its provider keys (utils.agent_env).
    agent_env: list[str] = Field(default_factory=list)
    # How a local agent is held to the rules: "text" is SSSF's own command checks alone;
    # "agentsh" also runs it under the agentsh service (agentsh_guard.py). Not with a VM sandbox.
    local_guard: Literal["text", "agentsh"] = "text"
    # Commands `local_guard: agentsh` refuses to exec, besides the built-in ones.
    deny_exec: list[DenyExec] = Field(default_factory=list)
    # Hosts (port 443) `local_guard: agentsh` lets agents reach, besides the model hosts and registries.
    network_allow: list[str] = Field(default_factory=list)

    @field_validator("network_allow")
    @classmethod
    def _hosts(cls, values: list[str]) -> list[str]:
        return [_bare_host(v) for v in values]

    @model_validator(mode="after")
    def _guard(self):
        if self.local_guard == "agentsh" and self.sandbox != "none":
            raise ValueError("local_guard: agentsh wraps agents on this machine; it needs sandbox: none")
        names = [d.name for d in self.deny_exec]
        if len(names) != len(set(names)):
            raise ValueError("deny_exec names must be unique")
        return self


def load(root: Path) -> RepoSettings:
    path = root / PATH
    data = yaml.safe_load(path.read_text()) if path.is_file() else None
    return RepoSettings.model_validate(data or {})


def merge_ff_only(root: Path) -> bool:
    """SSSF_MERGE_FF_ONLY in the environment (from .env) wins, either way;
    otherwise repo.yaml's `merge`."""
    override = os.environ.get("SSSF_MERGE_FF_ONLY", "").strip()
    if override:
        return override != "0"
    return load(root).merge == "ff_only"


def with_agent_rules(system_text: str, root: Path) -> str:
    """An agent's system prompt with this repo's agent_rules after it. The
    rules sit in repo.yaml, not in each prompt file, so a repo can state one
    without customizing every prompt and reviewing each on every update."""
    rules = load(root).agent_rules
    if not rules:
        return system_text
    return (system_text.rstrip() + "\n\n## This repo's rules\n\n"
            + "\n".join(f"- {text(rule)}" for rule in rules) + "\n")


def max_worktree_runs(root: Path) -> int:
    """How many --worktree runs may be alive in this repo at once."""
    return load(root).max_worktree_runs

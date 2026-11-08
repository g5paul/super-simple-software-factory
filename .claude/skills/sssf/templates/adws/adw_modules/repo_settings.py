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
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

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

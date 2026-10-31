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
from pydantic import BaseModel, ConfigDict, Field

PATH = "adws/adw_sssf_config/repo.yaml"


class RepoSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a misspelled rule fails, never silently defaults
    merge: Literal["any", "ff_only"] = "any"
    max_worktree_runs: int = Field(2, ge=1)
    # What every live run in the repo may spend together, in dollars. None = off.
    max_live_cost: Optional[float] = Field(None, gt=0)
    worktree_setup: list[str] = Field(default_factory=list)
    worktree_setup_timeout: int = Field(600, ge=1)


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


def max_worktree_runs(root: Path) -> int:
    """How many --worktree runs may be alive in this repo at once."""
    return load(root).max_worktree_runs

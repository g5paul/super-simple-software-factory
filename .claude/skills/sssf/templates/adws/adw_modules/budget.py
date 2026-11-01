"""Cost caps: stop spending before an unattended run gets expensive.

Three caps. Two are set in the roster:

- `max_call_cost` on an agent goes to `claude -p --max-budget-usd`, so a
  runaway call ends itself. Claude Code counts it per call, not per session,
  and checks it after each API request, so a call can overshoot by one request.
  agent_cc turns that ending into CallBudgetStop, which is never retried.
- `defaults.max_run_cost` is checked by `check_run` before EVERY send, retries
  and corrections included. Checking only when an agent starts would let one
  phase's retries spend the call cap again and again.

Two more are the repo's own, in repo.yaml. `max_live_cost` caps what every
live run spends together, so two runs at once can't each spend a full
max_run_cost. `max_daily_cost` caps what the repo's runs spent in the last 24
hours, finished ones too. `check_live` and `check_daily` run beside
`check_run`, and `check_launch` checks both before a run starts.

One more is the machine's, in ~/.config/sssf/limits.yaml beside the project
registry: its `max_daily_cost` caps what every registered repo's runs spent in
the last 24 hours together. `check_machine` runs after `check_daily`.

A stop records why in the trace. If the tree holds uncommitted work, the
message says how to look at it, keep it, or drop it; nothing is discarded.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from . import git_helper, repo_settings, worktree
from .data_types import AgentResult, EventRecord


class BudgetStop(RuntimeError):
    pass


class RunBudgetStop(BudgetStop):
    pass


class LiveBudgetStop(BudgetStop):
    """The live runs together have spent repo.yaml's max_live_cost."""


class DailyBudgetStop(BudgetStop):
    """The repo's runs have spent repo.yaml's max_daily_cost in the last 24 hours."""


class MachineBudgetStop(BudgetStop):
    """Every repo's runs on this machine have spent limits.yaml's max_daily_cost in the last 24 hours."""


DAY = timedelta(hours=24)


class CallBudgetStop(BudgetStop):
    """The call hit its max_call_cost. `result` carries what it spent anyway."""

    def __init__(self, message: str, result: Optional[AgentResult] = None):
        super().__init__(message)
        self.result = result or AgentResult()


def uncommitted_help(run) -> str:
    """How to handle uncommitted work, or "" for a clean tree. The run's own
    data dir is left out: it is the trace, not work."""
    try:
        if not git_helper.work_in_progress(run.cfg.defaults.data_dir):
            return ""
    except (OSError, RuntimeError):         # not a repo, or no git: nothing to advise
        return ""
    # A worktree run's work is in its own folder, not the engineer's checkout.
    root = git_helper.repo_root()
    cd = f"cd {root} && " if Path(root).resolve() != worktree.main_checkout() else ""
    return ("\nThe tree has uncommitted work from this run. Nothing was discarded.\n"
            f"  look at it:  {cd}git status && git diff\n"
            f"  keep it:     {cd}git stash\n"
            f"  drop it:     {cd}git checkout . && git clean -fd")


def check_run(run, agent_name: str, phase_id: str) -> None:
    """Raise RunBudgetStop when the run has spent its max_run_cost."""
    cap = run.cfg.defaults.max_run_cost
    if cap is None or run.cost < cap:
        return
    message = (f"run {run.adw_id} spent ${run.cost:.2f} of max_run_cost ${cap:.2f}; "
               f"stopped before {agent_name}'s next call. Raise defaults.max_run_cost "
               f"in sssf.config.yaml to allow more." + uncommitted_help(run))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase_id, type="error",
                                 name="budget_stop",
                                 payload={"agent": agent_name, "spent": run.cost, "cap": cap,
                                          "error": message}))
    raise RunBudgetStop(message)


def _root() -> Path:
    try:
        return worktree.main_checkout()
    except Exception:                       # not a repo: repo.yaml is beside the cwd
        return Path.cwd()


def max_live_cost() -> Optional[float]:
    return repo_settings.load(_root()).max_live_cost


def max_daily_cost() -> Optional[float]:
    return repo_settings.load(_root()).max_daily_cost


def _db(cfg, root: Path) -> str:
    db = cfg.observability.db
    return db if Path(db).is_absolute() else str(root / db)


def _live_over(cfg, run=None) -> Optional[tuple[str, float, float]]:
    """Why the live runs are at max_live_cost, their total, and the cap; None under it or off."""
    root = _root()
    cap = repo_settings.load(root).max_live_cost
    if cap is None:
        return None
    spent = worktree.live_spend(_db(cfg, root))
    if run is not None:
        # The trace learns a call's cost when the call ends, corrections and
        # all; this run's own count already has every send of this attempt.
        spent[run.adw_id] = max(spent.get(run.adw_id, 0.0), run.cost)
    total = sum(spent.values())
    if total < cap:
        return None
    each = ", ".join(f"{adw_id} ${cost:.2f}" for adw_id, cost in
                     sorted(spent.items(), key=lambda item: -item[1]))
    return (f"the live runs have spent ${total:.2f} together, at or over max_live_cost "
            f"${cap:.2f} ({each})"), total, cap


def check_live(run, agent_name: str, phase_id: str) -> None:
    """Raise LiveBudgetStop when every live run in the repo together has spent
    max_live_cost. Each counts its current attempt; a call in flight is not
    counted until it ends."""
    over = _live_over(run.cfg, run)
    if not over:
        return
    why, total, cap = over
    message = (f"{why}; stopped before {agent_name}'s next call. Raise max_live_cost in "
               f"adws/adw_sssf_config/repo.yaml, or let a run finish." + uncommitted_help(run))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase_id, type="error",
                                 name="live_budget_stop",
                                 payload={"agent": agent_name, "spent": total, "cap": cap,
                                          "error": message}))
    raise LiveBudgetStop(message)


def _daily_over(cfg, run=None) -> Optional[tuple[str, float, float]]:
    """Why the last 24 hours are at max_daily_cost, their total, and the cap; None under it or off."""
    root = _root()
    cap = repo_settings.load(root).max_daily_cost
    if cap is None:
        return None
    db = _db(cfg, root)
    now = datetime.now(timezone.utc)
    calls = [(datetime.fromisoformat(at), cost) for at, cost in
             worktree.spend_since(db, (now - DAY).isoformat(timespec="milliseconds"))]
    if run is not None:
        # What this attempt has spent that the trace doesn't hold yet, such as
        # a reply whose correction is still to go out, counts as spent now.
        traced = worktree.live_spend(db).get(run.adw_id, 0.0)
        if run.cost > traced:
            calls.append((now, run.cost - traced))
    total = sum(cost for _, cost in calls)
    if total < cap:
        return None
    left = total
    for at, cost in calls:                  # oldest first: when enough has aged out
        left -= cost
        if left < cap:
            clears = at + DAY
            break
    return (f"this repo's runs have spent ${total:.2f} in the last 24 hours, at or over "
            f"max_daily_cost ${cap:.2f}. It drops under at {clears:%Y-%m-%d %H:%M} UTC "
            f"if nothing else runs"), total, cap


def check_daily(run, agent_name: str, phase_id: str) -> None:
    """Raise DailyBudgetStop when the repo's runs, finished ones too, have spent
    max_daily_cost in the last 24 hours."""
    over = _daily_over(run.cfg, run)
    if not over:
        return
    why, total, cap = over
    message = (f"{why}. Stopped before {agent_name}'s next call. Raise max_daily_cost in "
               f"adws/adw_sssf_config/repo.yaml, or resume once it drops under." + uncommitted_help(run))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase_id, type="error",
                                 name="daily_budget_stop",
                                 payload={"agent": agent_name, "spent": total, "cap": cap,
                                          "error": message}))
    raise DailyBudgetStop(message)


def _config_dir() -> Path:
    """The registry's folder, as install.py and account.py find it, so $SSSF_REGISTRY moves it."""
    if os.environ.get("SSSF_REGISTRY"):
        return Path(os.environ["SSSF_REGISTRY"]).parent
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sssf"


def limits_path() -> Path:
    return _config_dir() / "limits.yaml"


def machine_daily_cost() -> Optional[float]:
    """limits.yaml's max_daily_cost; None when the file is missing or doesn't set it.
    A file that can't be read stops runs rather than turning the cap off unseen."""
    path = limits_path()
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
        cap = data.get("max_daily_cost") if isinstance(data, dict) else "not a mapping"
        if cap is not None and (isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap <= 0):
            raise ValueError(f"max_daily_cost must be a positive number, not {cap!r}")
    except (OSError, yaml.YAMLError, ValueError) as error:
        raise MachineBudgetStop(f"{path} can't be read ({error}). Fix it, or remove it "
                                f"to turn the machine's daily cap off.") from None
    return None if cap is None else float(cap)


def _machine_repos(cfg, root: Path) -> list[tuple[str, str]]:
    """(name, trace) for every registered repo, and this one even if unregistered."""
    try:
        projects = json.loads((_config_dir() / "projects.json").read_text()).get("projects", {})
    except (OSError, ValueError, AttributeError):
        projects = {}
    here = root.resolve()
    own = here.name
    repos = []
    for name, entry in projects.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("root"), str):
            continue
        repo = Path(entry["root"])
        if repo.resolve() == here:
            own = name
            continue
        db = Path(entry.get("db") or "adws/adw_data/sssf.db")
        repos.append((name, str(db if db.is_absolute() else repo / db)))
    return [(own, _db(cfg, root)), *repos]


def _spend_read_only(db: str, since: str) -> list[tuple[str, float]]:
    """worktree.spend_since without creating or migrating another repo's trace.
    A trace that is missing or won't open has spent nothing."""
    if not Path(db).is_file():
        return []
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            return [(at, float(cost or 0)) for at, cost in conn.execute(
                "select started_at, json_extract(payload_json, '$.cost') from events "
                "where type = 'agent_end' and started_at >= ? order by started_at", (since,))]
    except sqlite3.Error:
        return []


def _machine_over(cfg, run=None) -> Optional[tuple[str, float, float]]:
    """Why every repo's last 24 hours are at the machine's cap, their total, and the cap; None under it or off."""
    cap = machine_daily_cost()
    if cap is None:
        return None
    root = _root()
    now = datetime.now(timezone.utc)
    since = (now - DAY).isoformat(timespec="milliseconds")
    calls: list[tuple[datetime, float]] = []
    shares: dict[str, float] = {}
    for i, (name, db) in enumerate(_machine_repos(cfg, root)):
        repo_calls = [(datetime.fromisoformat(at), cost) for at, cost in _spend_read_only(db, since)]
        if i == 0 and run is not None:
            # This repo's own run: what the trace doesn't hold yet counts as spent now.
            traced = worktree.live_spend(db).get(run.adw_id, 0.0)
            if run.cost > traced:
                repo_calls.append((now, run.cost - traced))
        calls += repo_calls
        shares[name] = shares.get(name, 0.0) + sum(cost for _, cost in repo_calls)
    total = sum(shares.values())
    if total < cap:
        return None
    left = total
    for at, cost in sorted(calls, key=lambda call: call[0]):    # oldest first
        left -= cost
        if left < cap:
            clears = at + DAY
            break
    each = ", ".join(f"{name} ${cost:.2f}" for name, cost in
                     sorted(shares.items(), key=lambda item: -item[1]))
    return (f"the runs on this machine have spent ${total:.2f} in the last 24 hours ({each}), "
            f"at or over max_daily_cost ${cap:.2f} in {limits_path()}. It drops under at "
            f"{clears:%Y-%m-%d %H:%M} UTC if nothing else runs"), total, cap


def check_machine(run, agent_name: str, phase_id: str) -> None:
    """Raise MachineBudgetStop when every registered repo's runs together have
    spent limits.yaml's max_daily_cost in the last 24 hours."""
    over = _machine_over(run.cfg, run)
    if not over:
        return
    why, total, cap = over
    message = (f"{why}. Stopped before {agent_name}'s next call. Raise max_daily_cost in "
               f"{limits_path()}, or resume once it drops under." + uncommitted_help(run))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase_id, type="error",
                                 name="machine_budget_stop",
                                 payload={"agent": agent_name, "spent": total, "cap": cap,
                                          "error": message}))
    raise MachineBudgetStop(message)


def check_launch(cfg) -> None:
    """Refuse a new run while the live runs are at max_live_cost, or the last
    24 hours at the repo's or the machine's max_daily_cost."""
    over = _live_over(cfg)
    if over:
        raise LiveBudgetStop(f"{over[0]}, so this run did not start. Let a run finish, or raise "
                             f"max_live_cost in adws/adw_sssf_config/repo.yaml.")
    over = _daily_over(cfg)
    if over:
        raise DailyBudgetStop(f"{over[0]}. This run did not start. Launch it then, or raise "
                              f"max_daily_cost in adws/adw_sssf_config/repo.yaml.")
    over = _machine_over(cfg)
    if over:
        raise MachineBudgetStop(f"{over[0]}. This run did not start. Launch it then, or raise "
                                f"max_daily_cost in {limits_path()}.")


def record_call_stop(run, agent_name: str, phase_id: str, cap: Optional[float],
                     stop: CallBudgetStop) -> CallBudgetStop:
    """Count what the stopped call spent, and record why it stopped."""
    run.add_usage(stop.result.tokens, stop.result.cost)
    message = f"{stop} Stopped with no retry: split the work, or raise {agent_name}'s max_call_cost."
    message += uncommitted_help(run)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=phase_id, type="error",
                                 name="call_budget_stop",
                                 payload={"agent": agent_name, "spent": stop.result.cost,
                                          "cap": cap, "error": message}))
    return CallBudgetStop(message, result=stop.result)

"""Cost caps: stop spending before an unattended run gets expensive.

Three caps. Two are set in the roster:

- `max_call_cost` on an agent goes to `claude -p --max-budget-usd`, so a
  runaway call ends itself. Claude Code counts it per call, not per session,
  and checks it after each API request, so a call can overshoot by one request.
  agent_cc turns that ending into CallBudgetStop, which is never retried.
- `defaults.max_run_cost` is checked by `check_run` before EVERY send, retries
  and corrections included. Checking only when an agent starts would let one
  phase's retries spend the call cap again and again.

The third is the repo's own, `max_live_cost` in repo.yaml: what every live run
spends together, so two runs at once can't each spend a full max_run_cost.
`check_live` runs beside `check_run`, and `check_launch` before a run starts.

A stop records why in the trace. If the tree holds uncommitted work, the
message says how to look at it, keep it, or drop it; nothing is discarded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import git_helper, repo_settings, worktree
from .data_types import AgentResult, EventRecord


class BudgetStop(RuntimeError):
    pass


class RunBudgetStop(BudgetStop):
    pass


class LiveBudgetStop(BudgetStop):
    """The live runs together have spent repo.yaml's max_live_cost."""


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


def _live_over(cfg, run=None) -> Optional[tuple[str, float, float]]:
    """Why the live runs are at max_live_cost, their total, and the cap; None under it or off."""
    root = _root()
    cap = repo_settings.load(root).max_live_cost
    if cap is None:
        return None
    db = cfg.observability.db
    spent = worktree.live_spend(db if Path(db).is_absolute() else str(root / db))
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


def check_launch(cfg) -> None:
    """Refuse a new run while the live runs are already at max_live_cost."""
    over = _live_over(cfg)
    if over:
        raise LiveBudgetStop(f"{over[0]}, so this run did not start. Let a run finish, or raise "
                             f"max_live_cost in adws/adw_sssf_config/repo.yaml.")


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

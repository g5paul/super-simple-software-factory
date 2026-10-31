"""Session lifecycle: pin-or-create an adw_id, build the Run object.

`ensure(cfg, adw_id)` joins the session if it exists or creates it under
exactly that id (pinned ids for repeatable runs); omitted, a fresh id is
minted and printed so the next ADW can pick it up.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from . import account, agent_cc, agents, budget, changes, permissions, worktree
from .data_types import EventRecord, SSSFConfig
from .runner import Run
from .tracer import Tracer
from .utils import engineer_name, just, new_id


def _finalize_when_killed(run: Run) -> None:
    """A killed run still closes its own trace.

    Python's default SIGTERM handling exits without unwinding, so `just kill`
    (or any `kill <pid>`) would leave the session reading `running` forever and
    its process rows open — the trace would claim work is in flight that is
    already dead. Turning the signal into SystemExit both finalizes here and
    lets the phase context manager record the phase as failed on the way out.
    """
    def handler(signum, _frame):
        run.tracer.session_finish(run.adw_id, ok=False)   # also closes process rows
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)


# Failures SSSF raises on purpose. Each one's message says what happened and
# what to do, and the trace already holds the failed phase, so a Python
# traceback on top adds nothing but noise. Anything else is a crash, and
# keeps its traceback.
STOPS = (agents.GateFailure, permissions.PermissionBreach,
         budget.BudgetStop, account.RateLimitStop, changes.ChangeCaptureError,
         agent_cc.NotLoggedIn, worktree.SetupFailed)


def _report_stops_plainly(run: Run) -> None:
    """A stop ends the run with its full message and exit code 1, not a traceback.

    The phase line clips its error to one line, and a breach message lists
    paths over several, so the message is printed again here in full.
    """
    default = sys.excepthook

    def hook(kind, error, trace):
        if isinstance(error, STOPS):
            run.console.advise(f"{kind.__name__}: {error}")
        else:
            default(kind, error, trace)

    sys.excepthook = hook


def _start(cfg: SSSFConfig, adw_id: str, place: dict | None) -> tuple[Tracer, Run]:
    tracer = Tracer(cfg.observability.db,
                    f"{cfg.defaults.data_root}/sessions/{adw_id}/events.jsonl")
    run = Run(cfg=cfg, adw_id=adw_id, tracer=tracer, engineer=engineer_name())
    tracer.session_start(adw_id, run.engineer, adw_name=Path(sys.argv[0]).stem)
    # This process is the run. Record it before any phase opens, so a run that
    # hangs in its first agent call is still killable by adw_id.
    tracer.process_start(adw_id, "adw", "", os.getpid(),
                         " ".join([Path(sys.argv[0]).name, *sys.argv[1:]]))
    if place and not place["resumed"]:
        tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="worktree",
                                 payload={k: v for k, v in place.items() if k != "resumed"}))
    return tracer, run


def ensure(cfg: SSSFConfig, adw_id: str | None = None, use_worktree: bool = False) -> Run:
    """Join or create the session. With `use_worktree`, or when this adw_id
    already has a worktree, the process moves into the run's worktree first
    (see worktree.py), so everything after this works there."""
    adw_id = adw_id or new_id(8)
    try:
        # Held until this run's process and worktree rows are written, so two
        # launches at once can't both count the other runs before either counts.
        with worktree.launching():
            budget.check_launch(cfg)
            place = worktree.enter(cfg, adw_id, use_worktree)
            tracer, run = _start(cfg, adw_id, place)
    except (worktree.WorktreeError, budget.LiveBudgetStop, budget.DailyBudgetStop) as error:
        raise SystemExit(f"{type(error).__name__}: {error}") from None
    # The caps this run is held to, so the dashboard can draw spend against
    # them. A joined run records them again; the latest row wins.
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="run_caps",
                             payload={"max_run_cost": cfg.defaults.max_run_cost,
                                      "rate_limit_stop": cfg.defaults.rate_limit_stop,
                                      "max_live_cost": budget.max_live_cost(),
                                      "max_daily_cost": budget.max_daily_cost()}))
    _finalize_when_killed(run)
    _report_stops_plainly(run)
    run.console.session_started(adw_id, run.engineer)
    if place:
        verb = "resuming in" if place["resumed"] else "working in"
        run.console.note(f"{verb} {place['worktree']} on {place['branch']}, from "
                         f"{place['launch_branch']} at {place['base']}; "
                         f"`{just(f'merge {adw_id}')}` lands it")
        if place.get("launch_dirty") and not place["resumed"]:
            run.console.note("the launch checkout has uncommitted changes; they are not part of this run")
        if not place["resumed"]:
            worktree.set_up(run)
    return run

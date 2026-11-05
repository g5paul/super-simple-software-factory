"""Session lifecycle: pin-or-create an adw_id, build the Run object.

`ensure(cfg, adw_id)` joins the session if it exists or creates it under
exactly that id (pinned ids for repeatable runs); omitted, a fresh id is
minted and printed so the next ADW can pick it up.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import sys
from pathlib import Path

from . import (account, agent_cc, agents, budget, changes, permissions, repo_settings,
               rules, sandbox, silence, worktree)
from .data_types import EventRecord, PhaseParams, SSSFConfig
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
STOPS = (agents.GateFailure, agents.AgentFailed, permissions.PermissionBreach,
         budget.BudgetStop, account.RateLimitStop, changes.ChangeCaptureError,
         agent_cc.NotLoggedIn, worktree.SetupFailed, rules.RuleBreach, sandbox.SandboxFailed,
         silence.SilentStop)


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


def choose_sandbox(asked: str | None, db: str, adw_id: str | None) -> str | None:
    """The provider a run of a chain that takes --sandbox works in, or None
    for this machine. The flag wins, and `none` means this machine. Without
    it, a new run takes repo.yaml's `sandbox`. A run that already exists keeps
    where it ran: a VM run resumes in a new VM (design decision 12), and a
    host run stays on the host, so a setting added since can't move it."""
    if asked:
        return None if asked == "none" else asked
    root = budget._root()
    path = Path(db) if Path(db).is_absolute() else root / db
    if adw_id and worktree._query(str(path), "select 1 from sessions where adw_id=?", adw_id):
        return None
    name = repo_settings.load(root).sandbox
    return None if name == "none" else name


def ensure(cfg: SSSFConfig, adw_id: str | None = None, use_worktree: bool = False,
           sandbox_name: str | None = None, repo_sandbox: bool = False) -> Run:
    """Join or create the session. With `use_worktree`, or when this adw_id
    already has a worktree, the process moves into the run's worktree first
    (see worktree.py), so everything after this works there. With
    `sandbox_name`, the run's work goes to a VM (see sandbox.py); that implies
    a worktree. `repo_sandbox` is for the chains that take --sandbox: it reads
    `sandbox_name` as the flag, `none` included, and fills a missing one from
    repo.yaml (see choose_sandbox)."""
    if repo_sandbox:
        sandbox_name = choose_sandbox(sandbox_name, cfg.observability.db, adw_id)
    adw_id = adw_id or new_id(8)
    if sandbox_name:
        use_worktree = True
        sandbox.prepare(sandbox_name, cfg)
    elif os.environ.get("SSSF_PI_TOKEN_SOCKET"):
        sandbox.extensions_in_repo(cfg, worktree.main_checkout())   # the operator container (decision 14)
    try:
        # Held until this run's process and worktree rows are written, so two
        # launches at once can't both count the other runs before either counts.
        with worktree.launching():
            budget.check_launch(cfg)
            place = worktree.enter(cfg, adw_id, use_worktree)
            if place and sandbox_name and not place["resumed"]:
                place["sandbox"] = sandbox_name
            tracer, run = _start(cfg, adw_id, place)
    except (worktree.WorktreeError, budget.LiveBudgetStop, budget.DailyBudgetStop,
            budget.MachineBudgetStop) as error:
        raise SystemExit(f"{type(error).__name__}: {error}") from None
    # The caps this run is held to, so the dashboard can draw spend against
    # them. A joined run records them again; the latest row wins.
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="run_caps",
                             payload={"max_run_cost": cfg.defaults.max_run_cost,
                                      "rate_limit_stop": cfg.defaults.rate_limit_stop,
                                      "max_live_cost": budget.max_live_cost(),
                                      "max_daily_cost": budget.max_daily_cost(),
                                      "machine_daily_cost": budget.machine_daily_cost()}))
    # An attempt of a trial says which trial and roster it is, once, at its
    # start. `just trial` passes the tag in; see trial.py.
    tag = os.environ.get("SSSF_TRIAL")
    if tag and not (place and place["resumed"]):
        tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="trial",
                                 payload=json.loads(tag)))
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
        # A run that asked for a sandbox keeps it on a resume, with a new VM
        # made from the host branch and set up again (design decision 12).
        provider = place.get("sandbox")
        if provider:
            _open_sandbox(run, place, provider, sandbox_name)
        if not place["resumed"] or provider:
            worktree.set_up(run)
    return run


def _open_sandbox(run: Run, place: dict, provider: str, asked: str | None) -> None:
    if asked and asked != provider:
        raise SystemExit(f"run {run.adw_id} ran in a {provider} sandbox, so it resumes in one, "
                         f"not in {asked}")
    if not asked:
        sandbox.prepare(provider, run.cfg)   # a resume without the flag
    with run.phase(PhaseParams(name="sandbox", kind="code", owner="sandbox",
                               description=f"Make the run's {provider} VM and clone its branch in")) as ph:
        # Extensions come from the VM's clone, committed at the run's start
        # (design decisions 21, 22). Checked inside the phase, before the VM
        # exists, so a stop here ends the run failed, as a VM that won't start does.
        sandbox.extensions_in_clone(run.cfg, worktree.main_checkout(), place.get("base", ""), Path.cwd())
        box, swept = sandbox.open_remote(provider, run, place["branch"],
                                         worktree.main_checkout().name,
                                         set(worktree.live_runs(run.cfg.observability.db)))
        # Registered before the VM exists, so a failed setup still deletes it.
        atexit.register(box.close)
        box.open()
        sandbox.use(box)
        ph.log(provider=provider, vm=box.vm, swept=swept, tools=box.tools)
    run.console.note(f"working in {provider} VM {box.vm}; commits come back to {place['branch']}")

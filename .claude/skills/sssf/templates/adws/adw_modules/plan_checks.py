"""The plan's own checks, run as code beside quality.py's blocks.

quality.py holds the repo's fixed commands: test, lint, format, typecheck,
build. A plan can also require a check of its own, such as a validation script
it adds. In pi-agent-observability's run 0e822c23 no phase ran that script;
the reviewer judged it by reading, and the documenter wrote that the diff did
not show whether it passed. PlanOutput.checks names those commands, and verify
runs them with the blocks, so their failures reach the builder the same way.

This module is SSSF's, not the repo's: quality.py is the file each repo edits,
and this has to work whatever a repo did to it.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time

from . import quality, sandbox, session_files
from .data_types import EventRecord, PlanOutput, QualityCheckResult, QualityResult
from .utils import copy_installs, now_iso, operator_env

TIMEOUT_SECONDS = 600


def _run(run, index: int, command: str) -> QualityCheckResult:
    phase = run.phases[-1]
    name = f"plan_check_{index}"
    path = run.context_handoff_dir / "quality" / f"{phase.seq:02d}_{name}"
    path.mkdir(parents=True, exist_ok=True)
    log = path / "command.log"

    run.console.note(f"plan check {index}: {command}")
    started_at = now_iso()
    clock = time.monotonic()
    try:
        # A plan writes its checks as shell lines (`bun scripts/x.ts`,
        # `cd web && npm test`), so they run through a shell.
        done = sandbox.current().run(["bash", "-c", command], cwd=run.repo_root,
                                     env=copy_installs(operator_env(), getattr(getattr(run, "local_guard", None), "mode", None)),
                                     timeout=TIMEOUT_SECONDS)
        returncode, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as error:
        returncode = 124
        out = f"{error.stdout or ''}{error.stderr or ''}\nTimed out after {TIMEOUT_SECONDS}s."
    duration = time.monotonic() - clock
    session_files.write(log, f"$ {command}\nexit: {returncode}\nduration_seconds: {duration:.3f}\n\n{out}\n",
                        run.data_root)
    passed = returncode == 0
    run.tracer.event(EventRecord(
        adw_id=run.adw_id, phase_id=phase.phase_id, type="tool_call", name=f"quality:{name}",
        payload={"area": "plan", "operation": "check", "command": command,
                 "returncode": returncode, "passed": passed, "output_artifact": str(log)},
        started_at=started_at, ended_at=now_iso()))
    run.console.note(f"plan check {index}: {'passed' if passed else 'failed'} "
                     f"(exit {returncode}, {duration:.1f}s)")
    return QualityCheckResult(name=name, area="plan", operation="check", command=command,
                              returncode=returncode, passed=passed, duration_seconds=duration,
                              output_artifact=str(log), output_tail=out[-quality.TAIL_CHARS:])


def verify_brief(result: QualityResult | None, phase: str, revised: bool = False) -> str:
    """The reviewer's `host_verify` text: which checks SSSF's own verify ran and
    passed. "" unless the whole result passed, so a red or missing verify gives
    the reviewer nothing. Each command is one JSON string on one line: a plan's
    check is the planner's words, and must not be able to add a line here."""
    if result is None or not result.passed:
        return ""
    lines = [f"`{phase}` passed. SSSF ran each check below itself, as code, outside any "
             f"agent's sandbox, and each one exited 0:"]
    for check in result.checks:
        lines.append(f"- {check.name}: {json.dumps(check.command)}" if check.configured
                     else f"- {check.name}: not configured, so nothing ran")
    if revised:
        lines.append("The builder has revised the code since. SSSF runs these checks again "
                     "after your review, and commits nothing unless they pass.")
    return "\n".join(lines)


def _plan_commands(plan: PlanOutput | None) -> list[tuple[int, str]]:
    """The (index, command) pairs `verify` runs for a plan."""
    return [(i, command) for i, command in enumerate(plan.checks if plan else [], start=1)
            if command.strip()]


def planned(run, plan: PlanOutput | None, base=None) -> list[tuple[str, str]]:
    """(name, command) for each command `verify(run, plan, base)` will run,
    without running one. quality._run is swapped for a recorder while the
    blocks are called, and restored in a `finally`.

    This must work whatever a repo did to its quality.py. A block that does not
    go through `_run` runs for real here, which is the same command verify runs
    later. If quality has no `_run`, or the call raises, the answer is []."""
    original = getattr(quality, "_run", None)
    if original is None:
        return []

    def recorder(spec, run_) -> QualityCheckResult:
        return QualityCheckResult(name=spec.name, area=spec.area, operation=spec.operation,
                                  command=shlex.join(spec.argv), returncode=0, passed=True,
                                  duration_seconds=0.0, output_artifact="")

    quality._run = recorder
    try:
        result = (base or quality.run_quality)(run)
        found = [(check.name, check.command) for check in result.checks if check.configured]
    except Exception:
        return []
    finally:
        quality._run = original
    return found + [(f"plan_check_{i}", command) for i, command in _plan_commands(plan)]


def builder_brief(run, plan: PlanOutput | None, base=None) -> str:
    """The `verify_next` slot of a builder call that a host verify follows: the
    commands verify will run, and what to do about a test the sandbox denied.
    Written by code only. "" when nothing is listed, so the prompt keeps its
    baseline bytes."""
    commands = planned(run, plan, base)
    if not commands:
        return ""
    listing = "\n".join(f"- {name}: {json.dumps(command)}" for name, command in commands)
    return (
        "## What SSSF runs after you\n\n"
        "SSSF's code wrote this section; no agent did. After you report, SSSF runs each command "
        "below itself, outside your sandbox. Nothing is committed and the run does not pass "
        "unless every one exits 0:\n\n"
        f"{listing}\n\n"
        "This matters for one case only. A test that one of these commands runs may fail in your "
        "sandbox because the sandbox denied (`EACCES`, `EPERM`, \"Permission denied\", "
        "\"Operation not permitted\") a file that the test itself created during that run, in a "
        "temp folder the test itself made (`mkdtemp` and the like). That test is not yours to "
        "fix. A refused or denied probe is an answer, never a reason to try the real file another "
        "way: do not skip, weaken, mock out or delete the test, and do not reach the path another "
        "way. When everything else passes, report `success`, and name each such test, the denied "
        "path and the error in `notes_for_next_agent`.\n\n"
        "Nothing else is covered. A permission error on a file in this repo or its worktree, on a "
        "secret file the test did not create, on a port, or on any other path is yours to fix, or "
        "`fail`. So is a failing test that none of the commands above runs.\n\n")


def verify(run, plan: PlanOutput | None, base=None) -> QualityResult:
    """quality.py's blocks (or `base`, e.g. quality.run_tests), then every check
    the plan named, as one result. The default is looked up at call time, so a
    wrapped or patched run_quality is the one that runs."""
    result = (base or quality.run_quality)(run)
    extra = [_run(run, i, command) for i, command in _plan_commands(plan)]
    failures = result.failures + [
        f"{check.name}: `{check.command}` exited {check.returncode}\n{check.output_tail}".rstrip()
        for check in extra if not check.passed]
    return QualityResult(passed=not failures, checks=result.checks + extra, failures=failures,
                         artifacts=result.artifacts + [check.output_artifact for check in extra])

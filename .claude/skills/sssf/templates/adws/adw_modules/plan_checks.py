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

import subprocess
import time

from . import quality
from .data_types import EventRecord, PlanOutput, QualityCheckResult, QualityResult
from .utils import now_iso, operator_env

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
        done = subprocess.run(["bash", "-c", command], cwd=run.repo_root, env=operator_env(),
                              capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        returncode, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as error:
        returncode = 124
        out = f"{error.stdout or ''}{error.stderr or ''}\nTimed out after {TIMEOUT_SECONDS}s."
    duration = time.monotonic() - clock
    log.write_text(f"$ {command}\nexit: {returncode}\nduration_seconds: {duration:.3f}\n\n{out}\n")
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


def verify(run, plan: PlanOutput | None, base=None) -> QualityResult:
    """quality.py's blocks (or `base`, e.g. quality.run_tests), then every check
    the plan named, as one result. The default is looked up at call time, so a
    wrapped or patched run_quality is the one that runs."""
    result = (base or quality.run_quality)(run)
    extra = [_run(run, i, command)
             for i, command in enumerate(plan.checks if plan else [], start=1)
             if command.strip()]
    failures = result.failures + [
        f"{check.name}: `{check.command}` exited {check.returncode}\n{check.output_tail}".rstrip()
        for check in extra if not check.passed]
    return QualityResult(passed=not failures, checks=result.checks + extra, failures=failures,
                         artifacts=result.artifacts + [check.output_artifact for check in extra])

"""The checks a plan names run as code, beside quality.py's blocks.

In pi-agent-observability's run 0e822c23 the plan required
scripts/validate-sidebar-ui.ts, and no phase ran it: the reviewer judged it by
reading, and the documenter said the diff did not show it passed.
PlanOutput.checks now names such commands, and verify runs them.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import plan_checks
from adw_modules.data_types import PlanOutput, QualityResult


def fake_run(tmp_path):
    events = []
    return SimpleNamespace(
        adw_id="abc", repo_root=tmp_path, context_handoff_dir=tmp_path / "handoff",
        phases=[SimpleNamespace(seq=5, phase_id="p5")],
        tracer=SimpleNamespace(event=events.append), console=SimpleNamespace(note=lambda m: None),
        events=events)


def green(run):
    return QualityResult(passed=True, checks=[], failures=[], artifacts=[])


def plan(*checks: str) -> PlanOutput:
    return PlanOutput(status="success", summary="s", checks=list(checks))


def test_a_plan_without_checks_is_the_blocks_alone(tmp_path):
    result = plan_checks.verify(fake_run(tmp_path), plan(), base=green)
    assert result.passed and result.checks == []


def test_every_check_runs_and_a_failure_reaches_the_builder(tmp_path):
    run = fake_run(tmp_path)
    result = plan_checks.verify(run, plan("true", "echo broken; exit 3"), base=green)
    assert not result.passed
    assert [c.passed for c in result.checks] == [True, False]
    assert result.failures == ["plan_check_2: `echo broken; exit 3` exited 3\nbroken"]
    assert [e.name for e in run.events] == ["quality:plan_check_1", "quality:plan_check_2"]
    assert result.tally() == "1/2"


def test_checks_run_from_the_repo_root(tmp_path):
    (tmp_path / "marker").write_text("here")
    result = plan_checks.verify(fake_run(tmp_path), plan("test -f marker"), base=green)
    assert result.passed


def test_every_plan_chain_runs_the_plans_checks():
    for name in ("adw_simple_sdlc.py", "adw_plan_build_test.py", "adw_plan_build_test_quality.py"):
        assert "plan_checks.verify(run, plan" in (SKILL / "templates/adws" / name).read_text(), name


def test_the_planner_is_asked_for_them():
    assert '"checks"' in (SKILL / "templates/prompt_engineering/planner/user.md").read_text()

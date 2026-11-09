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
        adw_id="abc", repo_root=tmp_path, context_handoff_dir=tmp_path / "handoff", data_root=tmp_path,
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


# A project's run c6cba6d3 spent two fix rounds on a check ending in
# `git diff --exit-code config.toml.example`: verify runs before the build is
# committed, so it could never pass once the build changed that file.
import pytest
from pydantic import ValidationError

from adw_modules.data_types import PlanOutput


@pytest.mark.parametrize("check", [
    "uv run python tests/test_config.py --regenerate && git diff --exit-code config.toml.example",
    "git diff --quiet",
    "git -C web diff --exit-code -- src",
    "test -z \"$(git status --porcelain)\"",
    "git status --short | wc -l",
    "git diff-index --quiet HEAD --",
])
def test_a_check_that_compares_the_tree_with_git_is_rejected(check):
    with pytest.raises(ValidationError, match="before the build is committed"):
        PlanOutput(status="success", summary="s", checks=["bun x.ts", check])


@pytest.mark.parametrize("check", [
    "bun scripts/validate.ts",
    "python gen.py > /tmp/x && diff /tmp/x config.toml.example",
    "git diff --cached --exit-code",
    "git log --oneline -1",
    "git status",
    "git diff > /tmp/d; test -s /tmp/d",
])
def test_other_checks_pass(check):
    assert PlanOutput(status="success", summary="s", checks=[check]).checks == [check]


def test_the_planner_is_told_why_and_its_rewritten_check_is_kept():
    """The rejection rides the envelope correction: the planner's own session
    gets the reason, and its next answer is the plan that goes on."""
    import json
    from adw_modules import agents
    bad = {"status": "success", "summary": "s", "checks": ["gen && git diff --exit-code a.toml"]}
    good = dict(bad, checks=["gen > /tmp/a && diff /tmp/a a.toml"])
    sent = []

    def send(message):
        sent.append(message)
        return SimpleNamespace(text=json.dumps(good))

    run = SimpleNamespace(tracer=SimpleNamespace(envelope_row=lambda *a: None),
                          console=SimpleNamespace(retry=lambda *a: None))
    phase = SimpleNamespace(params=SimpleNamespace(owner="planner"))
    call = SimpleNamespace(output_type=PlanOutput)
    plan, attempt = agents._parse_with_retries(run, phase, call,
                                               SimpleNamespace(text=json.dumps(bad)), send)
    assert attempt == 2 and plan.checks == good["checks"]
    assert len(sent) == 1 and "before the build is committed" in sent[0]

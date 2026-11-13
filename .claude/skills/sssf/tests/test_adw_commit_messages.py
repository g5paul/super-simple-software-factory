"""A code commit names the whole change, not the last repair.

A fix or a revision reports only its own delta. When an ADW committed with
the LATEST builder envelope, a run that needed one repair landed its whole
feature under a message like "Fix the failing test". These tests run each
ADW's real main() in a throwaway git repo, with fake agents and a fake suite
that force exactly one repair, and read the commit subjects back from git.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, quality
from adw_modules.data_types import (BuildOutput, DocumentOutput, PlanOutput,
                                    QualityResult, ReviewOutput, SSSFConfig)

FEATURE = "Add slugify to textkit"
REPAIR = "Fix the failing slugify test"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    Path(".gitignore").write_text("adws/\n")
    git("add", "-A")
    git("commit", "-qm", "init")
    monkeypatch.setattr(agents, "load_config", lambda path=None: SSSFConfig())
    monkeypatch.setattr(agents, "validate", lambda cfg, required: None)
    monkeypatch.setattr(agents, "execute", fake_execute)
    return tmp_path


def fake_execute(run, phase, call):
    """Each agent writes a real file, so every commit has something in it."""
    name, owner = phase.params.name, phase.params.owner
    Path(f"{name}.txt").write_text(name)
    if owner == "planner":
        return PlanOutput(status="success", summary="plan", commit_message="Add the slugify spec")
    if owner == "builder":
        message = FEATURE if name == "build" else REPAIR
        return BuildOutput(status="success", summary=name, commit_message=message)
    if owner == "reviewer":
        approved = name != "review_1"
        return ReviewOutput(status="success", summary=name, approved=approved,
                            blocking=[] if approved else ["two tests are missing"])
    return DocumentOutput(status="success", summary="docs", commit_message="Document slugify")


def suite(*outcomes: bool):
    """A fake suite that returns these verdicts in order."""
    verdicts = iter(outcomes)

    def run(_run) -> QualityResult:
        passed = next(verdicts)
        return QualityResult(passed=passed, failures=[] if passed else ["1 test failed"])
    return run


def subjects() -> list[str]:
    return git("log", "--format=%s").splitlines()


def run_adw(module: str) -> int:
    adw = importlib.import_module(module)
    return adw.main("add slugify")


def no_tests_only(_run):
    raise AssertionError("adw_simple_sdlc must run every quality block, not run_tests() alone")


def test_simple_sdlc_after_a_fix(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_quality", suite(False, True))
    monkeypatch.setattr(quality, "run_tests", no_tests_only)
    monkeypatch.setattr(agents, "execute", _always_approve(fake_execute))
    assert run_adw("adw_simple_sdlc") == 0
    assert FEATURE in subjects()
    assert REPAIR not in subjects()


def test_simple_sdlc_after_a_revision(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_quality", suite(True, True))
    monkeypatch.setattr(quality, "run_tests", no_tests_only)
    assert run_adw("adw_simple_sdlc") == 0
    assert FEATURE in subjects()
    assert REPAIR not in subjects()


def test_plan_build_test_after_a_fix(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_tests", suite(False, True))
    assert run_adw("adw_plan_build_test") == 0
    assert subjects()[0] == FEATURE


def test_plan_build_test_quality_after_a_fix(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_quality", suite(False, True))
    assert run_adw("adw_plan_build_test_quality") == 0
    assert subjects()[0] == FEATURE


def _always_approve(execute):
    """The reviewer approves at once, so the only repair is the test fix."""
    def wrapped(run, phase, call):
        if phase.params.owner == "reviewer":
            Path(f"{phase.params.name}.txt").write_text("ok")
            return ReviewOutput(status="success", summary="ok", approved=True)
        return execute(run, phase, call)
    return wrapped


def test_simple_sdlc_sends_a_lint_failure_to_the_builder(repo, monkeypatch):
    # The chain must check what CI checks. A failing lint or format block is a
    # failure like a red test: the builder gets the output and repairs it.
    calls = []

    def lint_fails_once(_run):
        calls.append(1)
        passed = len(calls) > 1
        return QualityResult(passed=passed,
                             failures=[] if passed else ["lint: `ruff check` exited 1\nE501"])
    seen = []

    def execute(run, phase, call):
        seen.append(phase.params.name)
        return fake_execute(run, phase, call)
    monkeypatch.setattr(quality, "run_quality", lint_fails_once)
    monkeypatch.setattr(agents, "execute", _always_approve(execute))
    assert run_adw("adw_simple_sdlc") == 0
    assert "fix_1" in seen


def test_build_test_commits_its_work_in_a_worktree(repo, monkeypatch):
    # Without a commit, a --worktree run's work stayed uncommitted, and
    # `just merge` landed an unchanged branch and removed the worktree.
    monkeypatch.setattr(quality, "run_tests", suite(False, True))
    assert importlib.import_module("adw_build_test").main("add slugify", worktree=True) == 0
    assert Path.cwd().name != repo.name                # the run moved into its worktree
    assert subjects()[0] == FEATURE
    assert git("status", "--porcelain").strip() == ""


def test_build_test_leaves_a_plain_run_uncommitted_as_before(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_tests", suite(True))
    assert run_adw("adw_build_test") == 0
    assert subjects() == ["init"]
    assert "build.txt" in git("status", "--porcelain")


def test_build_test_commits_nothing_when_the_suite_stays_red(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_tests", suite(False, False, False, False))
    assert importlib.import_module("adw_build_test").main("add slugify", worktree=True) == 1
    assert subjects() == ["init"]


@pytest.mark.parametrize("module, block", [
    ("adw_build_test", "run_tests"),
    ("adw_plan_build_test", "run_tests"),
    ("adw_plan_build_test_quality", "run_quality"),
    ("adw_simple_sdlc", "run_quality"),
])
def test_a_red_suite_gets_no_fix_it_never_checks(repo, monkeypatch, module, block):
    # Three checks leave room for two fixes. A fix after the last check is an
    # agent call whose work nothing tests and nothing commits.
    checks = []
    seen = []

    def red(_run):
        checks.append(1)
        return QualityResult(passed=False, failures=["1 test failed"])

    def execute(run, phase, call):
        seen.append(phase.params.name)
        return fake_execute(run, phase, call)
    monkeypatch.setattr(quality, block, red)
    monkeypatch.setattr(agents, "execute", _always_approve(execute))
    assert run_adw(module) == 1
    assert len(checks) == 3
    assert [n for n in seen if n.startswith("fix_")] == ["fix_1", "fix_2"]


def test_simple_sdlc_ends_before_review_when_the_checks_stay_red(repo, monkeypatch):
    # A build that failed its last check is never committed, so a review of
    # it is a reviewer call spent on code the run throws away.
    seen = []

    def execute(run, phase, call):
        seen.append(phase.params.name)
        return fake_execute(run, phase, call)
    monkeypatch.setattr(quality, "run_quality", lambda _run: QualityResult(passed=False, failures=["1 test failed"]))
    monkeypatch.setattr(agents, "execute", execute)
    assert run_adw("adw_simple_sdlc") == 1
    assert not [n for n in seen if n.startswith(("review_", "revise_", "document"))]
    assert subjects()[0] == "Add the slugify spec"     # the plan commit stands


def test_a_resumed_runs_leftover_code_waits_for_the_code_commit(repo, monkeypatch):
    # agentsh run 3c724f0e: attempt 1's builder failed and left its code in the
    # tree. On resume, commit_plan's `git add -A` swept that code into the spec
    # commit, before verify and review, and the code commit got a gofmt touch-up.
    # The plan commit takes only what the planner may write.
    from adw_modules.data_types import AgentConfig, PromptEngineering
    planner = AgentConfig(name="planner", writes=["specs/"],
                          prompt_engineering=PromptEngineering(system="s.md", user="u.md"))
    monkeypatch.setattr(agents, "load_config", lambda path=None: SSSFConfig(agents=[planner]))
    monkeypatch.setattr(quality, "run_quality", suite(True))
    monkeypatch.setattr(quality, "run_tests", no_tests_only)

    def planner_writes_a_spec(run, phase, call):
        if phase.params.owner == "planner":
            Path("specs").mkdir(exist_ok=True)
            Path("specs/abc123_slugify.md").write_text("spec")
            return PlanOutput(status="success", summary="plan", commit_message="Add the slugify spec")
        return fake_execute(run, phase, call)
    monkeypatch.setattr(agents, "execute", _always_approve(planner_writes_a_spec))
    Path("leftover.py").write_text("code from the failed attempt")

    assert run_adw("adw_simple_sdlc") == 0
    by_subject = {}
    for sha in git("rev-list", "HEAD").split():
        files = git("show", "--format=", "--name-only", sha).split()
        by_subject[git("log", "-1", "--format=%s", sha).strip()] = files
    assert by_subject["Add the slugify spec"] == ["specs/abc123_slugify.md"]
    assert "leftover.py" in by_subject[FEATURE]

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


def test_simple_sdlc_after_a_fix(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_tests", suite(False, True))
    monkeypatch.setattr(agents, "execute", _always_approve(fake_execute))
    assert run_adw("adw_simple_sdlc") == 0
    assert FEATURE in subjects()
    assert REPAIR not in subjects()


def test_simple_sdlc_after_a_revision(repo, monkeypatch):
    monkeypatch.setattr(quality, "run_tests", suite(True, True))
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

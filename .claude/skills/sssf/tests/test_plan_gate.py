"""gates.plan_well_formed: a plan the builder can act on, checked before the build.

A malformed plan used to reach the builder, which then spent a whole build on
it. The gate checks what the planner's own prompt asks for and code can see:
both copies written and the same, a spec that overwrites no earlier one, a
section on how to verify, and at least one real file or folder named. A
failure goes back to the planner's session as a correction, so a fix costs one
short turn instead of a build. Every real plan in four project repos passed
all four checks on 2026-11-03.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import gates
from adw_modules.data_types import PlanOutput

GOOD = """# Plan: add a health endpoint

## Steps

1. Add the route in `src/app.py`.
2. Add `tests/test_health.py`.

## Verification

Run `uv run pytest tests/test_health.py`.
"""


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("app = 1\n")
    (root / "specs").mkdir()
    (root / "specs" / "old_spec.md").write_text("# Old\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    monkeypatch.chdir(root)
    handoff = tmp_path / "session" / "context_handoff"
    handoff.mkdir(parents=True)
    return SimpleNamespace(repo_root=root, context_handoff_dir=handoff)


def plan(run, text: str = GOOD, spec: str = "specs/ab12cd34_health-endpoint.md",
         spec_text: str | None = None) -> PlanOutput:
    handoff = run.context_handoff_dir / "plan.md"
    handoff.write_text(text)
    if spec:
        (run.repo_root / spec).write_text(text if spec_text is None else spec_text)
    return PlanOutput(status="success", artifacts=[str(handoff)] + ([spec] if spec else []))


def failed(report) -> list[str]:
    return report.violations


def test_a_good_plan_passes(repo):
    report = gates.plan_well_formed(plan(repo), repo)
    assert report.passed, report.violations
    assert len(report.checks) == 4


def test_a_plan_without_its_spec_copy_fails(repo):
    assert "no copy under specs/" in " ".join(failed(gates.plan_well_formed(plan(repo, spec=""), repo)))


def test_copies_that_differ_fail(repo):
    report = gates.plan_well_formed(plan(repo, spec_text=GOOD + "\nExtra line.\n"), repo)
    assert "differs from" in " ".join(failed(report))


def test_a_spec_that_overwrites_an_earlier_one_fails(repo):
    report = gates.plan_well_formed(plan(repo, spec="specs/old_spec.md"), repo)
    assert "_v2" in " ".join(failed(report))


def test_a_plan_with_no_way_to_verify_fails(repo):
    text = GOOD.replace("## Verification\n\nRun `uv run pytest tests/test_health.py`.\n", "")
    assert "how to verify" in " ".join(failed(gates.plan_well_formed(plan(repo, text), repo)))


def test_a_plan_that_names_no_real_file_fails(repo):
    text = "# Plan\n\n## Steps\n\nMake it better.\n\n## Testing\n\nRun the tests.\n"
    assert "names no file" in " ".join(failed(gates.plan_well_formed(plan(repo, text), repo)))


@pytest.mark.parametrize("named", ["`src/new_module.py`", "`src/app.py:12`", "`README.md` and `src/`"])
def test_a_new_file_in_a_real_folder_counts(repo, named):
    (repo.repo_root / "README.md").write_text("hi\n")
    text = f"# Plan\n\n## Steps\n\nEdit {named}.\n\n## How to verify\n\nRun it.\n"
    assert gates.plan_well_formed(plan(repo, text), repo).passed


def test_a_new_file_at_the_root_alone_does_not_count(repo):
    text = "# Plan\n\n## Steps\n\nAdd `v1.2` support in `newthing.py`.\n\n## Tests\n\nRun it.\n"
    assert not gates.plan_well_formed(plan(repo, text), repo).passed


def test_every_plan_call_uses_the_plan_gates():
    adws = SKILL / "templates" / "adws"
    for chain in sorted(adws.glob("adw_*.py")):
        text = chain.read_text()
        for at in [i for i in range(len(text)) if text.startswith("output_type=PlanOutput", i)]:
            call = text[at:at + 400]
            assert "gates=gates.PLAN" in call, chain.name

"""A quality block still on the placeholder echo is not counted as a pass.

quality.py ships every block as `echo PLACEHOLDER ...`, which exits 0. In
yt-summarizer the build block was never wired up, and every quality run still
reported 5/5, a check that never ran. The tally now names it.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import quality
from adw_modules.data_types import QualityCheckResult, QualityResult


def check(name: str, argv: list[str], passed: bool = True) -> QualityCheckResult:
    return QualityCheckResult(name=name, area="backend", operation="lint", command=shlex.join(argv),
                              returncode=0 if passed else 1, passed=passed,
                              duration_seconds=0.0, output_artifact="x.log")


def test_the_shipped_placeholder_is_not_configured():
    assert not check("build", quality._placeholder("build")).configured
    assert check("test", ["bun", "test"]).configured
    assert check("say", ["echo", "hello"]).configured


def test_a_placeholder_is_named_and_left_out_of_the_count():
    result = QualityResult(passed=True, checks=[
        check("test", ["bun", "test"]),
        check("lint", ["ruff", "check", "."]),
        check("build", quality._placeholder("build")),
    ])
    assert result.tally() == "2/2 passed, 1 not configured (build)"


def test_a_fully_wired_run_reads_as_before():
    result = QualityResult(passed=False, checks=[check("test", ["bun", "test"]),
                                                 check("lint", ["ruff"], passed=False)])
    assert result.tally() == "1/2"


def test_every_adw_reports_the_tally():
    for path in (SKILL / "templates" / "adws").glob("adw_*.py"):
        text = path.read_text()
        if "result.checks" in text or "run_quality" in text:
            assert "result.tally()" in text, path.name


def test_the_console_line_says_not_configured(tmp_path):
    from types import SimpleNamespace
    from adw_modules.data_types import QualityCheckSpec
    notes = []
    run = SimpleNamespace(adw_id="a", repo_root=tmp_path, context_handoff_dir=tmp_path,
                          phases=[SimpleNamespace(seq=1, phase_id="p")],
                          tracer=SimpleNamespace(event=lambda e: None),
                          console=SimpleNamespace(note=notes.append))
    quality._run(QualityCheckSpec(name="build", area="backend", operation="build",
                                  argv=quality._placeholder("build")), run)
    assert notes[-1].startswith("quality build: not configured")

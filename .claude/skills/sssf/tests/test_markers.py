"""The `pushes` and `host_only` markers only label tests. A run with no `-m`, as verify
makes, must collect every test, and the fast run an agent makes under agentsh must add none."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FAST = ["--strict-markers", "-m", "not pushes and not host_only"]
PUSHING = ("tests/test_export.py", "tests/test_install_hook.py")


def _collect(*extra: str):
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
         "-p", "no:cacheprovider", *extra],
        cwd=SKILL, capture_output=True, text=True)
    out = proc.stdout + proc.stderr
    ids = [line.strip() for line in proc.stdout.splitlines() if "::" in line]
    return proc.returncode, out, ids


@pytest.fixture(scope="module")
def plain():
    return _collect()


@pytest.fixture(scope="module")
def no_conftest():
    return _collect("--noconftest")


@pytest.fixture(scope="module")
def fast():
    return _collect(*FAST)


def test_the_conftest_changes_no_count(plain, no_conftest):
    assert plain[0] == 0 and no_conftest[0] == 0
    assert plain[2] and plain[2] == no_conftest[2]
    assert "deselected" not in plain[1]


def test_both_markers_are_registered_and_in_use(plain, fast):
    assert fast[0] == 0, fast[1][-2000:]
    left_out = set(plain[2]) - set(fast[2])
    assert any(not i.startswith(PUSHING) for i in left_out), "host_only marks no test"


def test_pushes_covers_the_two_files_whole(plain, fast):
    for name in PUSHING:
        assert any(i.startswith(name + "::") for i in plain[2])
        assert not any(i.startswith(name + "::") for i in fast[2])


def test_the_fast_run_adds_no_test(plain, fast):
    assert set(fast[2]) <= set(plain[2])

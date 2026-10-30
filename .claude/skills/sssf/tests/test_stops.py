"""A run SSSF stops on purpose ends with its message, not a Python traceback.

In pi-agent-observability's run e19b9a8c a permission breach ended the ADW
with a traceback. The trace had already recorded the failed phase, and the
breach message said what happened, so the traceback was only noise around
it. A stop now prints its full message and exits 1. A crash, anything SSSF
did not raise on purpose, keeps its traceback.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ADWS = SKILL / "templates" / "adws"

SCRIPT = """
import sys
from types import SimpleNamespace
sys.path.insert(0, {adws!r})
from adw_modules import permissions, session

run = SimpleNamespace(console=SimpleNamespace(advise=lambda m: print("ADVISE " + m)))
session._report_stops_plainly(run)
raise {error}
"""


def end_with(error: str) -> subprocess.CompletedProcess:
    script = SCRIPT.format(adws=str(ADWS), error=error)
    return subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)


def test_a_stop_prints_its_whole_message_and_exits_1():
    result = end_with('permissions.PermissionBreach("builder modified 1 path(s):\\n  - x.py")')
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "ADVISE PermissionBreach: builder modified 1 path(s):\n  - x.py" in result.stdout


@pytest.mark.parametrize("error", ["ValueError('a bug')", "RuntimeError('a bug')"])
def test_a_crash_keeps_its_traceback(error):
    result = end_with(error)
    assert result.returncode == 1
    assert "Traceback" in result.stderr
    assert "ADVISE" not in result.stdout

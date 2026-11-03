"""An agent that reports fail ends the run plainly, not with a traceback.

A builder in sssf-sandbox run eceb5b8a declined its task and reported
status fail. agents.execute raised a plain RuntimeError, which isn't in
session.STOPS, so the run ended in a Python traceback, the sign the docs give
for an SSSF crash. It now raises AgentFailed, a stop.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, session


def test_a_reported_failure_is_a_stop():
    assert agents.AgentFailed in session.STOPS
    assert issubclass(agents.AgentFailed, RuntimeError)     # callers that catch RuntimeError still do
    text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    assert "raise AgentFailed(f\"{agent.name} reported status=" in text

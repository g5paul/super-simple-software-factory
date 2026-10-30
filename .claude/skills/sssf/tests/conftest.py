"""Shared test setup.

install.py records every repo it installs into in a registry file under the
user's config dir. Point it at a temp file for every test, so the suite never
writes to the real ~/.config/sssf/projects.json. monkeypatch changes
os.environ, so install.py subprocesses inherit the setting.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def private_registry(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("registry") / "projects.json"
    monkeypatch.setenv("SSSF_REGISTRY", str(path))
    return path


@pytest.fixture(autouse=True)
def claude_logged_in(monkeypatch):
    """agents.execute checks `claude auth status` before the first Claude Code
    agent. No test runs a real claude, so each starts as if the check passed;
    the login tests in test_agent_cc.py turn it back on."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))
    from adw_modules import agent_cc
    monkeypatch.setattr(agent_cc, "_logged_in", True)

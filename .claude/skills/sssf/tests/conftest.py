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


@pytest.fixture(autouse=True)
def no_dotenv_keys(monkeypatch):
    """A developer's own .env must never change the suite: no key counts as
    one .env set unless the test says so."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))
    from adw_modules import utils
    for key in utils.DOTENV_KEYS:          # utils' load_dotenv put these in os.environ
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(utils, "DOTENV_KEYS", frozenset())


SKILL_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
_CACHES = {".pytest_cache", ".ruff_cache", "__pycache__"}


@pytest.fixture(autouse=True, scope="session")
def no_stray_files():
    """Fail the run if a test leaves a new file in the skill's folder or in
    the folder pytest ran from. A test that passed a bare relative path once
    wrote an empty `r` there, and two guard builds shipped it in the skill."""
    import os
    roots = {SKILL_ROOT, __import__("pathlib").Path(os.getcwd()).resolve()}
    before = {root: set(os.listdir(root)) for root in roots}
    yield
    stray = sorted(f"{root}/{name}" for root in roots
                   for name in set(os.listdir(root)) - before[root] - _CACHES)
    assert not stray, f"tests left files behind: {stray}"


def _left_running(folder: str) -> list[tuple[int, str]]:
    """Processes, other than this one, whose command line names `folder`."""
    import os
    from pathlib import Path
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            line = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if folder in line:
            found.append((int(proc.name), line.strip()))
    return found


@pytest.fixture(autouse=True, scope="session")
def no_processes_left(tmp_path_factory):
    """Fail the run if a process a test started is still running at the end,
    and kill it. A relay's follower runs in its own session, so it outlives a
    test that never stops it: 360 had built up on one machine, holding 9 GB,
    before anyone looked. The folder in each command line names the test."""
    import os
    import signal
    import sys
    import time
    yield
    if not sys.platform.startswith("linux"):
        return
    folder = str(tmp_path_factory.getbasetemp())
    deadline = time.monotonic() + 3
    while (left := _left_running(folder)) and time.monotonic() < deadline:
        time.sleep(0.2)
    for pid, _ in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    assert not left, "tests left processes running (now killed):\n" + "\n".join(
        f"  {pid}: {line[:300]}" for pid, line in left)

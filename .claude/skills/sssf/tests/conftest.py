"""Shared test setup.

install.py records every repo it installs into in a registry file under the
user's config dir. Point it at a temp file for every test, so the suite never
writes to the real ~/.config/sssf/projects.json. monkeypatch changes
os.environ, so install.py subprocesses inherit the setting.

Every test also gets a git identity in its own global config file, through
GIT_CONFIG_GLOBAL, so temp repos commit where the machine has no ~/.gitconfig.
A test that needs its own HOME's .gitconfig as the global config unsets it.

A test that must pass with pi signed out or missing takes `stand_in_pi`, which points
agent_pi.PI_PATH at a script that answers `--list-models` and `auth print-bearer-token`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def keep_the_working_folder():
    """Put the working folder back after each test. Many tests `os.chdir` into their
    `tmp_path` and never leave. With `tmp_path_retention_policy = failed` pytest removes
    that folder when the test passes, which would leave the next test in a deleted folder.
    This fixture is set up first, so it is torn down after `tmp_path` is removed."""
    import os
    before = os.getcwd()
    yield
    os.chdir(before)


@pytest.fixture(autouse=True)
def a_tmp_path_first(keep_the_working_folder, tmp_path):
    """Set `tmp_path` up before the other autouse fixtures, which take `monkeypatch`. pytest
    sets autouse fixtures up in name order, so this name sorts first. Then
    `monkeypatch` is undone before pytest removes a passed test's `tmp_path`. A test that
    patches `os.scandir` would otherwise break that removal."""


@pytest.fixture(autouse=True)
def private_registry(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("registry") / "projects.json"
    monkeypatch.setenv("SSSF_REGISTRY", str(path))
    return path


TEST_GIT_NAME = "SSSF Test"
TEST_GIT_EMAIL = "sssf-test@example.invalid"


@pytest.fixture(autouse=True)
def git_identity(tmp_path_factory, monkeypatch):
    """Every test gets a git identity that doesn't come from the machine, so a temp
    repo can commit where there is no ~/.gitconfig, as in an OpenShell VM. It is git's
    global config, so a repo's own user.name or user.email still wins. The file is new
    for each test: a test's `git config --global` never reaches the next one. It hides
    the engineer's ~/.gitconfig. A test whose own HOME holds the .gitconfig it means git
    to read asks for this fixture and unsets GIT_CONFIG_GLOBAL."""
    path = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    path.write_text(f"[user]\n\tname = {TEST_GIT_NAME}\n\temail = {TEST_GIT_EMAIL}\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
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


# A `pi` for tests that must not depend on the machine's pi or its sign-in. It answers the two
# calls those tests reach and exits 2 on anything else, so a new call shows up as a failure.
STAND_IN_PI = """#!/bin/sh
case "$1 $2" in
  "--list-models "*)
    echo "provider  model             context  max-out  thinking  images"
    echo "openai    gpt-6.1-sol       400K     128K     yes       yes"
    echo "openai    gpt-6.1-terra     400K     128K     yes       yes"
    echo "google    gemini-3.6-flash  1M       64K      yes       yes" ;;
  "auth print-bearer-token") echo "eyJ-stand-in-not-a-real-token" ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def stand_in_pi(tmp_path_factory, monkeypatch):
    """Point agent_pi.PI_PATH at a script that lists the ChatGPT roster's models and
    prints a made-up bearer token, so a test passes with pi signed out or missing, as in
    an OpenShell VM. The calls through agents.validate and agent_pi.vm_token still run.
    _pi_catalog caches its rows for the process, so the cache is cleared before and after."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))
    from adw_modules import agent_pi
    script = tmp_path_factory.mktemp("pi") / "pi"
    script.write_text(STAND_IN_PI)
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.delenv(agent_pi.CONTAINER_SOCKET_VAR, raising=False)
    agent_pi._pi_catalog.cache_clear()
    yield script
    agent_pi._pi_catalog.cache_clear()


def pytest_configure(config):
    """Two markers for tests that can't pass under agentsh. A marker only labels a test.
    Nothing here skips or deselects one, so a run with no `-m`, verify's included, runs them
    all. An agent under agentsh runs `-m 'not pushes and not host_only'`."""
    config.addinivalue_line("markers", "pushes: the test runs `git push`, which agentsh refuses")
    config.addinivalue_line("markers", "host_only: the test passes outside agentsh and fails under "
                            "it, whether its error names the sandbox or only follows from a denial")


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

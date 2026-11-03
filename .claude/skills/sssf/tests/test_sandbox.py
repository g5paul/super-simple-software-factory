"""adw_modules/sandbox.py: every process and file read in a run's tree goes
through one sandbox, so a remote one can take the run without the code above
it changing (docs/designs/remote-sandboxes.md, decisions 3 and 17).

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
MODULES = SKILL / "templates" / "adws" / "adw_modules"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import gates, git_helper, permissions, sandbox
from adw_modules.data_types import EnvelopeBase


class Recording(sandbox.Local):
    """The local sandbox, writing down each call it is given."""

    name = "recording"

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def run(self, command, **kwargs):
        self.calls.append(("run", command))
        return super().run(command, **kwargs)

    def start(self, command, **kwargs):
        self.calls.append(("start", command))
        return super().start(command, **kwargs)

    def exists(self, path):
        self.calls.append(("exists", str(path)))
        return super().exists(path)

    def read_text(self, path):
        self.calls.append(("read_text", str(path)))
        return super().read_text(path)

    def remove(self, path):
        self.calls.append(("remove", str(path)))
        return super().remove(path)


@pytest.fixture
def recording(monkeypatch):
    box = Recording()
    monkeypatch.setattr(sandbox, "_current", box)
    return box


@pytest.fixture
def repo(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "a"],
                   cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_the_default_sandbox_is_this_machine():
    assert sandbox.current() is sandbox.LOCAL
    assert sandbox.current().name == "local"


def test_git_goes_through_the_sandbox(recording, repo):
    assert len(git_helper.rev()) == 40
    assert git_helper.tracked("a.txt")
    assert ("run", ["git", "-c", "core.fsmonitor=", "rev-parse", "HEAD"]) in recording.calls


def test_the_tree_check_and_its_rollback_go_through_the_sandbox(recording, repo):
    run = type("Run", (), {"repo_root": repo})()
    (repo / "new.txt").write_text("x\n")
    assert permissions.snapshot(run) == {"new.txt": "untracked"}
    assert permissions._roll_back(run, "new.txt", {}, {"new.txt": "untracked"}) == "deleted"
    assert not (repo / "new.txt").exists()
    assert ("remove", str(repo / "new.txt")) in recording.calls


def test_gates_read_files_and_run_commands_through_the_sandbox(recording, repo):
    (repo / "plan.json").write_text('{"ok": true}')
    envelope = EnvelopeBase(status="success", artifacts=["plan.json", "missing.md"])
    assert [c.ok for c in gates.artifacts_exist(envelope, None).checks] == [True, False]
    assert gates.json_parses(envelope, None).checks[0].ok
    assert gates.tests_pass("true")(envelope, None).checks[0].ok
    assert ("read_text", "plan.json") in recording.calls
    assert ("run", "true") in recording.calls


def test_a_started_process_streams_lines_and_stops_with_its_children(tmp_path):
    process = sandbox.LOCAL.start(["sh", "-c", "echo one; echo two; sleep 30 & sleep 30"], cwd=tmp_path)
    lines = [next(iter(process.stdout)), next(iter(process.stdout))]
    assert lines == ["one\n", "two\n"]
    time.sleep(0.2)
    children = subprocess.run(["ps", "-o", "pid=", "--ppid", str(process.pid)],
                              capture_output=True, text=True).stdout.split()
    assert children
    process.stop_tree()
    process.wait()
    time.sleep(0.2)
    for pid in children:
        assert not os.path.exists(f"/proc/{pid}") or "Z" in Path(f"/proc/{pid}/stat").read_text().split()[2]


# What may still start a process with subprocess directly: work on the host,
# never in the run's tree. A new in-tree call goes through sandbox.current().
HOST_ONLY = {
    "agent_cc.py": ['[CLAUDE_PATH, "auth", "status"]'],   # the sign-in check
    "agent_pi.py": ["subprocess.run(", '[PI_PATH, "auth", "print-bearer-token"'],  # sign-in, VM token
}
IN_TREE = ["git_helper.py", "permissions.py", "gates.py", "quality.py", "plan_checks.py",
           "agent_cc.py", "agent_pi.py"]


@pytest.mark.parametrize("name", IN_TREE)
def test_in_tree_modules_start_nothing_around_the_sandbox(name):
    text = (MODULES / name).read_text()
    assert "subprocess.Popen(" not in text
    calls = [m.start() for m in re.finditer(r"subprocess\.run\(", text)]
    allowed = [text.find(marker) for marker in HOST_ONLY.get(name, [])]
    for at in calls:
        assert any(0 <= a and abs(a - at) < 120 for a in allowed), \
            f"{name} calls subprocess.run at offset {at}; in-tree work goes through sandbox.current()"


def test_a_write_goes_through_the_sandbox(repo):
    sandbox.LOCAL.write_text("w.txt", "w\n")
    assert (repo / "w.txt").read_text() == "w\n"


def test_a_stop_in_a_vm_says_the_work_is_saved_as_a_patch(repo, monkeypatch):
    from adw_modules import budget
    (repo / "a.txt").write_text("changed\n")
    run = type("Run", (), {"cfg": type("C", (), {"defaults": type("D", (), {"data_dir": "adws/adw_data"})()})(),
                           "session_dir": repo / "adws/adw_data/sessions/r1"})()
    assert "git stash" in budget.uncommitted_help(run)
    monkeypatch.setattr(sandbox, "_current", Recording())
    help_text = budget.uncommitted_help(run)
    assert "sandbox-uncommitted.patch" in help_text and "git stash" not in help_text

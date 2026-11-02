"""adw_modules/sandbox_vercel.py, against a fake VM.

The fake runs each command on this machine, with every path under the host
folder moved to a separate "VM" folder, so the VM and the host don't share
files any more than a real one would. Proof against Vercel itself is in
docs/designs/remote-sandboxes.md, decision 16.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))


class SandboxError(Exception):
    pass


class FakeVM:
    """Runs commands locally, with `host` paths rewritten under `vm`."""

    def __init__(self, host: Path, vm: Path):
        self.host, self.vm = str(host), str(vm)
        self.name = "fake-vm"
        self.calls: list[dict] = []
        self.stops = 0
        self.destroyed = False

    def _map(self, value):
        return value.replace(self.host, self.vm) if isinstance(value, str) else value

    def run_process(self, command, args=(), *, cwd=None, env=None, sudo=False,
                    capture_output=False, input=None, kill_after=None):
        argv = [self._map(a) for a in [command, *args]]
        self.calls.append({"argv": argv, "env": dict(env or {}), "cwd": cwd})
        if sudo:
            argv = [a.replace("&& chown -R ubuntu", "&& true") for a in argv]
        data = input.encode() if isinstance(input, str) else input
        done = subprocess.run(argv, cwd=self._map(cwd), input=data, capture_output=True,
                              env={"PATH": os.environ["PATH"], "HOME": self.vm, **(env or {})})
        return types.SimpleNamespace(returncode=done.returncode,
                                     stdout=done.stdout.decode(errors="replace"),
                                     stderr=done.stderr.decode(errors="replace"))

    def create_process(self, command, args=(), *, cwd=None, env=None):
        argv = [self._map(a) for a in [command, *args]]
        self.calls.append({"argv": argv, "env": dict(env or {}), "cwd": cwd, "start": True})
        popen = subprocess.Popen(argv, cwd=self._map(cwd), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True,
                                 env={"PATH": os.environ["PATH"], "HOME": self.vm, **(env or {})})
        return types.SimpleNamespace(stdout=popen.stdout, stderr=popen.stderr, wait=popen.wait,
                                     kill=popen.kill)

    def stop(self):
        self.stops += 1

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def sdk(monkeypatch):
    made: list[FakeVM] = []
    module = types.ModuleType("vercel.sandbox.sync")
    module.SandboxError = SandboxError
    module.create_sandbox = lambda **kwargs: made[-1]
    module.query_sandboxes = lambda: iter([])
    for name in ("vercel", "vercel.sandbox"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "vercel.sandbox.sync", module)
    return made


@pytest.fixture
def box(tmp_path, monkeypatch, sdk):
    from adw_modules import sandbox_vercel
    host, vm = tmp_path / "host", tmp_path / "vm"
    tree = host / "repo"
    tree.mkdir(parents=True)
    vm.mkdir()
    git = lambda *a: subprocess.run(["git", *a], cwd=tree, check=True, capture_output=True)
    git("init", "-q", "-b", "sssf/abc")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t")
    (tree / "a.txt").write_text("a\n")
    git("add", ".")
    git("commit", "-qm", "a")
    session = host / "data" / "sessions" / "abc"
    handoff = session / "context_handoff"
    handoff.mkdir(parents=True)
    (handoff / "plan.md").write_text("the plan\n")
    token = tmp_path / "token.env"
    token.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test\n")
    monkeypatch.setattr(sandbox_vercel, "TOKEN_FILE", token)
    for key in sandbox_vercel.KEYS:
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SSSF_ADW_ID", "abc")
    monkeypatch.chdir(tree)
    events: list[tuple[str, dict]] = []
    run = types.SimpleNamespace(
        adw_id="abc", context_handoff_dir=handoff, session_dir=session,
        tracer=types.SimpleNamespace(event=lambda e: events.append((e.name, e.payload))),
        console=types.SimpleNamespace(note=lambda text: None))
    sdk.append(FakeVM(host, vm))
    b = sandbox_vercel.Vercel(run, "sssf/abc", "repo")
    b.open()
    b.events, b.fake, b.host_tree, b.vm_tree = events, sdk[-1], tree, vm / "repo"
    return b


def test_open_clones_the_branch_and_the_handoff_folder_at_the_host_paths(box):
    assert (box.vm_tree / "a.txt").read_text() == "a\n"
    assert subprocess.run(["git", "branch", "--show-current"], cwd=box.vm_tree,
                          capture_output=True, text=True).stdout.strip() == "sssf/abc"
    vm_handoff = Path(box.fake._map(str(box.handoff)))
    assert (vm_handoff / "plan.md").read_text() == "the plan\n"
    assert box.events[0][0] == "sandbox" and box.events[0][1]["vm"] == "fake-vm"


def test_checks_get_no_token_and_agents_get_only_the_token_and_sssf_variables(box, monkeypatch):
    monkeypatch.setenv("SECRET_THING", "no")
    done = box.run(["sh", "-c", "env"])
    assert "SSSF_ADW_ID=abc" in done.stdout
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in done.stdout and "SECRET_THING" not in done.stdout
    process = box.start(["sh", "-c", "env"], env={**os.environ, "ANTHROPIC_API_KEY": "k"})
    names = {line.split("=", 1)[0] for line in process.stdout}
    process.wait()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in names
    assert not names & {"ANTHROPIC_API_KEY", "SECRET_THING", "VERCEL_TOKEN"}
    assert process.pid < 0


def test_an_agent_writes_the_handoff_folder_and_the_host_gets_it_back(box):
    process = box.start(["sh", "-c", f"echo done > {box.handoff}/build.md; echo ok"])
    assert list(process.stdout) == ["ok\n"]
    process.wait()
    assert (box.handoff / "build.md").read_text() == "done\n"


def test_a_commit_in_the_vm_lands_on_the_host_branch_with_the_same_sha(box):
    (box.vm_tree / "b.txt").write_text("b\n")
    box.run(["git", "add", "-A"])
    assert box.run(["git", "commit", "-qm", "b"]).returncode == 0
    vm_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=box.vm_tree,
                             capture_output=True, text=True).stdout
    host_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=box.host_tree,
                               capture_output=True, text=True).stdout
    assert vm_head == host_head
    assert (box.host_tree / "b.txt").read_text() == "b\n"
    commits = dict(box.events)["sandbox_commits"]
    assert commits["count"] == 1 and commits["head"] == vm_head[:12]


def test_close_saves_uncommitted_work_as_a_patch_and_deletes_the_vm(box):
    (box.vm_tree / "a.txt").write_text("changed\n")
    box.close()
    patch = box.run_.session_dir / "sandbox-uncommitted.patch"
    assert "+changed" in patch.read_text()
    assert (box.host_tree / "a.txt").read_text() == "a\n"     # saved, not applied
    assert box.fake.destroyed
    end = dict(box.events)["sandbox_end"]
    assert end["deleted"] and end["uncommitted_patch"] == str(patch)


def test_a_command_that_would_not_fit_in_the_session_gets_a_fresh_one(box):
    box.limit = 120
    box.run(["true"], timeout=10)
    assert box.fake.stops == 0
    box.run(["true"], timeout=100)
    assert box.fake.stops == 1 and box.refreshes == 1


def test_files_are_read_in_the_vm_not_on_the_host(box):
    (box.vm_tree / "only-in-vm.json").write_text('{"a": 1}')
    assert box.exists("only-in-vm.json") and box.is_file("only-in-vm.json")
    assert box.read_text("only-in-vm.json") == '{"a": 1}'
    assert box.size("only-in-vm.json") == 8
    assert not (box.host_tree / "only-in-vm.json").exists()
    box.remove("only-in-vm.json")
    assert not box.exists("only-in-vm.json")


def test_an_api_key_where_the_run_starts_stops_it(monkeypatch, sdk):
    from adw_modules import sandbox_vercel
    for key in sandbox_vercel.KEYS:
        monkeypatch.setenv(key, "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(sandbox_vercel.SandboxFailed, match="ANTHROPIC_API_KEY"):
        sandbox_vercel._credentials()


def test_kill_leaves_vm_stand_in_pids_alone(tmp_path, monkeypatch):
    import sqlite3
    from adw_modules import kill
    from adw_modules.tracer import Tracer
    db = tmp_path / "t.db"
    tracer = Tracer(str(db), os.devnull)
    tracer.session_start("abc", "t", adw_name="x")
    tracer.process_start("abc", "agent", "builder", -1, "claude_code builder m")
    signalled = []
    monkeypatch.setattr(kill.os, "kill", lambda pid, sig: signalled.append(pid))
    report = kill.kill(str(db), "abc")
    assert signalled == [] and "sandbox VM" in report[0]

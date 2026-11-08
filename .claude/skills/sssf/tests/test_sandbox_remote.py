"""adw_modules/sandbox_remote.py, through each provider, against a fake VM.

The fakes run each command on this machine, with every path under the host
folder moved to a separate "VM" folder, so the VM and the host don't share
files any more than a real one would. One fake stands in for Vercel's SDK
and one for E2B's, and every test of the shared work runs on both. Proof
against the real providers is in docs/designs/remote-sandboxes.md.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))


def _local_env(home: str, env) -> dict:
    return {"PATH": os.environ["PATH"], "HOME": home, **(env or {})}


def _as_root(call: dict) -> bool:
    return bool(call.get("sudo")) or call.get("user") == "root"


def _words(call: dict) -> str:
    return " ".join(call.get("argv") or [call.get("line", "")])


def _vm_path(value, host: str, vm: str):
    """A host path as the fake VM sees it. The VM's /tmp is its own, so the
    bundle path goes under `vm` too: on the host's real /tmp, two test runs at
    once overwrite each other's bundle."""
    if not isinstance(value, str):
        return value
    return value.replace(host, vm).replace("/tmp/sssf-in.bundle", f"{vm}/sssf-in.bundle")


def _kill_left_under(folder: Path) -> None:
    """Kill every process whose command line names `folder`. A relay's
    follower runs in its own session and outlives a test that never stops it."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if str(folder).encode() in (proc / "cmdline").read_bytes():
                os.kill(int(proc.name), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def _no_chown(text: str) -> str:
    return re.sub(r"&& chown (-R )?\w+", "&& true", text)


# --- Vercel's SDK -------------------------------------------------------------

class VercelError(Exception):
    pass


class FakeVercel:
    """Runs commands locally, with `host` paths rewritten under `vm`."""

    def __init__(self, host: Path, vm: Path):
        self.host, self.vm = str(host), str(vm)
        self.name = "fake-vm"
        self.vcpus, self.memory = 2, 4096
        self.calls: list[dict] = []
        self.stops = 0
        self.destroyed = False

    def _map(self, value):
        return _vm_path(value, self.host, self.vm)

    def run_process(self, command, args=(), *, cwd=None, env=None, sudo=False,
                    capture_output=False, input=None, kill_after=None):
        argv = [self._map(a) for a in [command, *args]]
        self.calls.append({"argv": argv, "env": dict(env or {}), "cwd": cwd, "sudo": sudo})
        if sudo:
            argv = [_no_chown(a) for a in argv]
        data = input.encode() if isinstance(input, str) else input
        try:
            done = subprocess.run(argv, cwd=self._map(cwd), input=data, capture_output=True,
                                  env=_local_env(self.vm, env), timeout=kill_after)
        except subprocess.TimeoutExpired:
            return types.SimpleNamespace(returncode=137, stdout="", stderr="killed")
        return types.SimpleNamespace(returncode=done.returncode,
                                     stdout=done.stdout.decode(errors="replace"),
                                     stderr=done.stderr.decode(errors="replace"))

    def create_process(self, command, args=(), *, cwd=None, env=None):
        argv = [self._map(a) for a in [command, *args]]
        self.calls.append({"argv": argv, "env": dict(env or {}), "cwd": cwd, "start": True})
        popen = subprocess.Popen(argv, cwd=self._map(cwd), stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=_local_env(self.vm, env))
        return types.SimpleNamespace(stdout=popen.stdout, stderr=popen.stderr, wait=popen.wait,
                                     kill=popen.kill)

    def stop(self):
        self.stops += 1

    def destroy(self, delete_orphan_snapshots=False):
        self.destroyed = delete_orphan_snapshots


def vercel_sdk(monkeypatch, host: Path, vm: Path):
    fake = FakeVercel(host, vm)
    module = types.ModuleType("vercel.sandbox.sync")
    module.SandboxError = VercelError
    module.create_sandbox = lambda **kwargs: fake
    module.query_sandboxes = lambda: iter([])
    for name in ("vercel", "vercel.sandbox"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "vercel.sandbox.sync", module)
    return fake, fake


# --- E2B's SDK ----------------------------------------------------------------

class E2BError(Exception):
    pass


class E2BExit(E2BError):
    def __init__(self, exit_code, stdout, stderr):
        super().__init__(f"exit {exit_code}")
        self.exit_code, self.stdout, self.stderr = exit_code, stdout, stderr


class E2BTimeout(E2BError):
    pass


class FakeE2BHandle:
    def __init__(self, popen, chunk: int):
        self.popen, self.chunk = popen, chunk

    def wait(self, on_stdout=None, on_stderr=None, on_pty=None):
        # Odd-sized chunks, so lines arrive split, as E2B's do, each sent as
        # soon as it's there (os.read), not held until a chunk fills.
        fd = self.popen.stdout.fileno()
        while piece := os.read(fd, self.chunk):
            on_stdout(piece.decode())
        err = self.popen.stderr.read()
        if err:
            on_stderr(err)
        code = self.popen.wait()
        if code:
            raise E2BExit(code, "", err)
        return types.SimpleNamespace(exit_code=code)


class FakeE2B:
    """E2B's Sandbox: shell strings run locally, with `host` paths rewritten under `vm`."""

    def __init__(self, host: Path, vm: Path, chunk: int = 3):
        self.host, self.vm = str(host), str(vm)
        self.sandbox_id = "fake-e2b"
        self.chunk = chunk
        self.calls: list[dict] = []
        self.pauses = 0
        self.connects = 0
        self.killed = False
        self.created: dict = {}
        self.commands = types.SimpleNamespace(run=self._run)
        self.files = types.SimpleNamespace(write=self._write)

    def _map(self, value):
        return _vm_path(value, self.host, self.vm)

    def _write(self, path, data):
        Path(self._map(path)).write_bytes(data.encode() if isinstance(data, str) else data)

    def _run(self, line, background=None, envs=None, user=None, cwd=None, timeout=60):
        line = self._map(line)
        self.calls.append({"line": line, "env": dict(envs or {}), "cwd": cwd, "user": user,
                           "timeout": timeout, "start": bool(background)})
        if user == "root":
            line = _no_chown(line)
        if background:
            popen = subprocess.Popen(["bash", "-c", line], cwd=self._map(cwd), text=True,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     env=_local_env(self.vm, envs))
            return FakeE2BHandle(popen, self.chunk)
        try:
            done = subprocess.run(["bash", "-c", line], cwd=self._map(cwd), capture_output=True,
                                  text=True, env=_local_env(self.vm, envs), timeout=timeout or None)
        except subprocess.TimeoutExpired:
            raise E2BTimeout("command timed out") from None
        if done.returncode:
            raise E2BExit(done.returncode, done.stdout, done.stderr)
        return types.SimpleNamespace(exit_code=0, stdout=done.stdout, stderr=done.stderr)

    def get_info(self):
        return types.SimpleNamespace(cpu_count=2, memory_mb=4096)

    def pause(self):
        self.pauses += 1
        return True

    def connect(self, timeout=None):
        self.connects += 1
        return self

    def kill(self):
        self.killed = True


def e2b_sdk(monkeypatch, host: Path, vm: Path):
    fake = FakeE2B(host, vm)
    module = types.ModuleType("e2b")
    module.SandboxException = E2BError
    module.CommandExitException = E2BExit
    module.TimeoutException = E2BTimeout

    class Sandbox:
        listed: list = []
        killed: list = []

        @staticmethod
        def create(**kwargs):
            fake.created = kwargs
            return fake

        @classmethod
        def kill(cls, sandbox_id):
            cls.killed.append(sandbox_id)

    class Pages:
        def __init__(self, items):
            self.items = items
            self.has_next = True

        def next_items(self):
            self.has_next = False
            return self.items

    Sandbox.list = classmethod(lambda cls, query=None: Pages(list(cls.listed)))
    module.Sandbox = Sandbox
    module.SandboxQuery = lambda **kwargs: kwargs
    module.SandboxState = types.SimpleNamespace(RUNNING="running", PAUSED="paused")
    monkeypatch.setitem(sys.modules, "e2b", module)
    return fake, fake


class E2BView:
    """What the shared tests ask of a fake, for E2B's."""

    def __init__(self, fake: FakeE2B):
        self.fake = fake

    @property
    def stops(self):
        return self.fake.pauses

    @property
    def destroyed(self):
        return self.fake.killed

    def _map(self, value):
        return self.fake._map(value)


# --- one box per provider -------------------------------------------------------

def _make_box(provider, tmp_path, monkeypatch, cfg=None, token=True):
    """The box for `provider`, unopened, with events, fake, raw, host_tree and vm_tree set."""
    from adw_modules import sandbox, sandbox_remote
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
    token_file = tmp_path / "token.env"
    if token:
        token_file.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test\n")
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", token_file)
    if provider == "vercel":
        fake, view = vercel_sdk(monkeypatch, host, vm)
    else:
        fake, _ = e2b_sdk(monkeypatch, host, vm)
        view = E2BView(fake)
    module = sandbox._provider(provider)
    for key in module.KEYS:
        monkeypatch.setenv(key, "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SSSF_REFUSE_RULES", raising=False)
    monkeypatch.setenv("SSSF_ADW_ID", "abc")
    monkeypatch.chdir(tree)
    events: list[tuple[str, dict]] = []
    run = types.SimpleNamespace(
        adw_id="abc", context_handoff_dir=handoff, session_dir=session,
        tracer=types.SimpleNamespace(event=lambda e: events.append((e.name, e.payload))),
        console=types.SimpleNamespace(note=lambda text: None))
    if cfg is not None:
        run.cfg = cfg
    b = module.Box(run, "sssf/abc", "repo")
    b.events, b.fake, b.raw, b.host_tree, b.vm_tree = events, view, fake, tree, vm / "repo"
    return b


@pytest.fixture(params=["vercel", "e2b"])
def box(request, tmp_path, monkeypatch):
    b = _make_box(request.param, tmp_path, monkeypatch)
    b.open()
    return b


def test_open_clones_the_branch_and_the_handoff_folder_at_the_host_paths(box):
    assert (box.vm_tree / "a.txt").read_text() == "a\n"
    assert subprocess.run(["git", "branch", "--show-current"], cwd=box.vm_tree,
                          capture_output=True, text=True).stdout.strip() == "sssf/abc"
    vm_handoff = Path(box.fake._map(str(box.handoff)))
    assert (vm_handoff / "plan.md").read_text() == "the plan\n"
    assert box.events[0][0] == "sandbox" and box.events[0][1]["vm"].startswith("fake-")


def test_checks_get_no_token_and_agents_get_only_the_token_and_sssf_variables(box, monkeypatch):
    monkeypatch.setenv("SECRET_THING", "no")
    done = box.run(["sh", "-c", "env"])
    assert "SSSF_ADW_ID=abc" in done.stdout
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in done.stdout and "SECRET_THING" not in done.stdout
    process = box.start(["sh", "-c", "env"], env={**os.environ, "ANTHROPIC_API_KEY": "k"})
    names = {line.split("=", 1)[0] for line in process.stdout}
    process.wait()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in names
    assert not names & {"ANTHROPIC_API_KEY", "SECRET_THING", "VERCEL_TOKEN", "E2B_API_KEY"}
    assert process.pid < 0


def test_vm_commands_use_the_python_the_hosts_uv_picks(box, tmp_path):
    # An untracked .python-version reaches the host worktree from the main
    # checkout above it, but not the clone, so the VM's uv would pick its own.
    from adw_modules import sandbox_remote
    project = tmp_path / "project"
    project.mkdir()
    (project / ".python-version").write_text("3.12\n")
    if subprocess.run(["uv", "python", "find", "3.12"], capture_output=True).returncode:
        pytest.skip("this machine's uv has no Python 3.12")
    assert sandbox_remote._host_python(project) == "3.12"
    box.python = "3.12"
    assert "UV_PYTHON=3.12" in box.run(["sh", "-c", "env"]).stdout
    process = box.start(["sh", "-c", "env"])
    assert "UV_PYTHON=3.12\n" in list(process.stdout)
    process.wait()


def test_a_bad_answer_means_no_uv_python(box, tmp_path, monkeypatch):
    from adw_modules import sandbox_remote
    bin_ = tmp_path / "badbin"
    bin_.mkdir()
    interp = bin_ / "interp"
    interp.write_text("#!/bin/sh\necho 'hello from sitecustomize'\n")
    interp.chmod(0o755)
    uv = bin_ / "uv"
    uv.write_text(f"#!/bin/sh\necho {interp}\n")
    uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    box.python = sandbox_remote._host_python(box.host_tree)
    assert box.python == ""
    assert "UV_PYTHON" not in box.run(["sh", "-c", "env"]).stdout


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
    box.limit = 240
    box.run(["true"], timeout=10)
    assert box.fake.stops == 0
    box._session -= 100                 # 100s into the session, 110s of it left
    box.run(["true"], timeout=200)      # asks for half the session, 120s
    assert box.fake.stops == 1 and box.refreshes == 1


def test_a_short_session_does_not_refresh_before_every_command(box):
    box.limit = 240
    box._session -= 100     # git fits in what is left; an agent, at half a session, doesn't
    for _ in range(5):
        box.run(["git", "status"])
    process = box.start(["true"])
    list(process.stdout)
    process.wait()
    assert box.refreshes == 1


def test_files_are_read_in_the_vm_not_on_the_host(box):
    (box.vm_tree / "only-in-vm.json").write_text('{"a": 1}')
    assert box.exists("only-in-vm.json") and box.is_file("only-in-vm.json")
    assert box.read_text("only-in-vm.json") == '{"a": 1}'
    assert box.size("only-in-vm.json") == 8
    assert not (box.host_tree / "only-in-vm.json").exists()
    box.remove("only-in-vm.json")
    assert not box.exists("only-in-vm.json")


def test_a_write_lands_in_the_vm_not_on_the_host(box):
    box.write_text("a.txt", "written in the vm\n")
    assert (box.vm_tree / "a.txt").read_text() == "written in the vm\n"
    assert (box.host_tree / "a.txt").read_text() == "a\n"


def test_close_records_the_vms_busy_cpu_seconds(box):
    box.run(["sh", "-c", "i=0; while [ $i -lt 20000 ]; do i=$((i+1)); done"])
    box.close()
    cpu = dict(box.events)["sandbox_end"]["cpu_seconds"]
    assert cpu is not None and cpu >= 0


def test_cpu_adds_each_session_and_a_reboot_starts_the_count_again(box, monkeypatch):
    # Open read 10. A refresh reads 15, then the VM boots again and reads 2;
    # close reads 5. The run used 5 + 3.
    readings = iter([15.0, 2.0, 5.0])
    box._cpu_base = 10.0
    monkeypatch.setattr(box, "_cpu_now", lambda: next(readings))
    box._session -= box.limit
    box.run(["true"])
    box.close()
    assert dict(box.events)["sandbox_end"]["cpu_seconds"] == 8.0


def test_a_cpu_reading_that_fails_makes_the_total_unknown_not_wrong(box, monkeypatch):
    monkeypatch.setattr(box, "_cpu_now", lambda: None)
    box.close()
    assert dict(box.events)["sandbox_end"]["cpu_seconds"] is None


def test_a_command_past_its_timeout_raises_timeout_expired(box):
    with pytest.raises(subprocess.TimeoutExpired):
        box.run(["sleep", "5"], timeout=1)


def test_an_api_key_where_the_run_starts_stops_it(monkeypatch):
    from adw_modules import sandbox_remote
    monkeypatch.setenv("E2B_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    with pytest.raises(sandbox_remote.SandboxFailed, match="ANTHROPIC_API_KEY"):
        sandbox_remote.credentials(("E2B_API_KEY",))


def test_kill_leaves_vm_stand_in_pids_alone(tmp_path, monkeypatch):
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


def test_vercel_close_records_the_memory_gb_hours_hobby_counts(box):
    if box.name != "vercel":
        pytest.skip("Vercel only")
    box._created -= 1800
    box.close()
    end = dict(box.events)["sandbox_end"]
    assert end["vcpus"] == 2 and end["memory_gib"] == 4.0
    assert end["memory_gb_hours"] == pytest.approx(2.0, abs=0.01)


# --- E2B only -----------------------------------------------------------------

@pytest.fixture
def e2b_box(box):
    if box.name != "e2b":
        pytest.skip("E2B only")
    return box


def test_an_agent_started_as_a_process_group_leader_keeps_its_exit_code(box, monkeypatch):
    # OpenShell starts each exec as a process-group leader, so a bare setsid
    # forks and its parent exits at once. OpenShell then held the agent's
    # output about 30 seconds and ended the exec with 74, "exec output could
    # not be delivered intact". setsid -w waits for the agent instead.
    import functools
    monkeypatch.setattr(subprocess, "Popen", functools.partial(subprocess.Popen, start_new_session=True))
    process = box.start(["sh", "-c", "sleep 0.3; echo late; exit 3"])
    assert list(process.stdout) == ["late\n"]
    assert process.wait() == 3


def test_e2b_chunks_come_back_as_whole_lines_and_stderr_is_kept(e2b_box):
    process = e2b_box.start(["sh", "-c", "printf 'first line\\nsecond'; echo oops >&2; exit 3"])
    assert list(process.stdout) == ["first line\n", "second"]
    assert process.wait() == 3
    assert "oops" in process.stderr.read()


def test_e2b_makes_the_vm_from_the_sssf_template_with_the_session_as_its_timeout(e2b_box):
    created = e2b_box.raw.created
    assert created["template"] == "sssf" and created["timeout"] == 3600
    assert created["metadata"] == {"sssf": "repo", "adw_id": "abc"}


def test_e2b_refresh_pauses_and_resumes_the_vm(e2b_box):
    e2b_box._call(e2b_box.sandbox.stop)
    assert e2b_box.raw.pauses == 1 and e2b_box.raw.connects == 1


def test_e2b_root_commands_run_as_root_not_through_sudo(e2b_box):
    roots = [c for c in e2b_box.raw.calls if c["user"] == "root"]
    assert len(roots) == 1 and "mkdir -p" in roots[0]["line"] and "sudo" not in roots[0]["line"]


def test_e2b_close_records_the_vm_cost_estimate(e2b_box):
    e2b_box._created -= 1000
    e2b_box.close()
    end = dict(e2b_box.events)["sandbox_end"]
    assert end["vcpus"] == 2 and end["memory_gib"] == 4.0
    assert end["vm_cost_usd"] == pytest.approx(1000 * (2 * 0.000014 + 4 * 0.0000045), abs=0.001)


def test_e2b_sweep_kills_only_vms_of_runs_that_are_not_live(monkeypatch, tmp_path):
    from adw_modules import sandbox_e2b
    e2b_sdk(monkeypatch, tmp_path, tmp_path)
    Sandbox = sys.modules["e2b"].Sandbox
    Sandbox.listed = [types.SimpleNamespace(sandbox_id="old", metadata={"sssf": "repo", "adw_id": "dead"}),
                      types.SimpleNamespace(sandbox_id="mine", metadata={"sssf": "repo", "adw_id": "live1"})]
    Sandbox.killed = []
    assert sandbox_e2b.sweep("repo", {"live1"}) == ["old (dead)"]
    assert Sandbox.killed == ["old"]


def test_install_copies_a_host_file_into_the_vm_executable_once(box, tmp_path):
    script = tmp_path / "host" / "skill" / "sssf_refuse.py"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho refused\n")
    box.install(script)
    before = len(box.raw.calls)
    box.install(script)
    assert len(box.raw.calls) == before
    copied = Path(box.fake._map(str(script)))
    assert copied.read_text() == script.read_text() and os.access(copied, os.X_OK)
    assert box.installed == {str(script)}


def test_install_makes_the_folder_as_root_and_writes_the_file_as_the_vm_user(box, tmp_path):
    script = tmp_path / "host" / "skill" / "adw_modules" / "sssf_refuse.py"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\necho refused\n")
    before = len(box.raw.calls)
    box.install(script)
    new = box.raw.calls[before:]
    assert len(new) == 2
    assert _as_root(new[0])
    first = _words(new[0])
    assert "mkdir -p" in first and f"chown {box.owner}" in first and "chown -R" not in first
    assert box.fake._map(str(script.parent)) in first.split() and "cat >" not in first
    assert not _as_root(new[1])
    second = _words(new[1])
    assert "cat >" in second and "chmod 755" in second and "mkdir" not in second
    assert box.fake._map(str(script)) in second
    copied = Path(box.fake._map(str(script)))
    assert copied.read_text() == script.read_text() and os.access(copied, os.X_OK)


def test_a_folder_root_cannot_make_stops_the_install_and_records_nothing(box, tmp_path):
    from adw_modules.sandbox import SandboxFailed
    script = tmp_path / "host" / "blocked" / "sub" / "f.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n")
    blocker = Path(box.fake._map(str(tmp_path / "host" / "blocked")))
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("x")
    before = len(box.raw.calls)
    with pytest.raises(SandboxFailed):
        box.install(script)
    assert box.installed == set()
    assert not any("cat >" in _words(c) for c in box.raw.calls[before:])


def test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules(box, monkeypatch):
    env = {**os.environ, "CLAUDE_CODE_SHELL_PREFIX": "/opt/audit.sh"}
    seen = box.start(["sh", "-c", "env"], env=env)
    assert "CLAUDE_CODE_SHELL_PREFIX" not in "".join(seen.stdout)
    seen.wait()
    env.update(SSSF_REFUSE_RULES="[]", CLAUDE_CODE_SHELL_PREFIX="/skill/sssf_refuse.py")
    seen = box.start(["sh", "-c", "env"], env=env)
    assert "CLAUDE_CODE_SHELL_PREFIX=/skill/sssf_refuse.py" in "".join(seen.stdout)
    seen.wait()


def test_a_pi_token_reaches_the_vm_as_openai_api_key_and_a_host_key_does_not(box):
    env = {**os.environ, "OPENAI_API_KEY": "sk-host-key"}
    seen = box.start(["sh", "-c", "env"], env=env)
    out = "".join(seen.stdout)
    seen.wait()
    assert "OPENAI_API_KEY" not in out
    seen = box.start(["sh", "-c", "env"], env={**env, "SSSF_PI_TOKEN": "chatgpt-access"})
    out = "".join(seen.stdout)
    seen.wait()
    assert "OPENAI_API_KEY=chatgpt-access" in out and "SSSF_PI_TOKEN" not in out


def test_the_vm_user_owns_the_runs_session_folder_for_pi_sessions(box):
    setup = [c for c in box.raw.calls if "chown" in " ".join(c.get("argv") or [c.get("line", "")])]
    words = " ".join(setup[0].get("argv") or [setup[0]["line"]])
    assert box.fake._map(str(box.run_.session_dir)) in words.split()


# --- the tool check (remote-sandboxes.md, "As built: the tool check") ---------

def test_open_records_the_vms_tools_in_the_trace(box):
    tools = dict(box.events)["sandbox_tools"]
    assert set(tools["tools"]) == {"git", "uv"}          # no roster: what every run needs
    assert tools["tools"]["git"].startswith("git version") and tools["problems"] == []
    assert box.tools == tools["tools"]


def test_a_missing_or_old_tool_fails_the_vm_before_any_agent(box, tmp_path, monkeypatch):
    from adw_modules import sandbox, sandbox_remote
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "uv").symlink_to(shutil.which("uv"))
    (bin_ / "pi").write_text("#!/bin/sh\necho 0.9.0\n")
    (bin_ / "pi").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    box.run_.cfg = types.SimpleNamespace(agents=[types.SimpleNamespace(coding_agent="pi"),
                                                 types.SimpleNamespace(coding_agent="claude_code")])
    with pytest.raises(sandbox.SandboxFailed) as error:
        box._check_tools()
    said = str(error.value)
    assert "claude is missing" in said and "node is missing" in said
    assert "pi is 0.9.0, and SSSF needs 1.4.0 or later" in said and "Nothing ran in it" in said
    assert dict(box.events)["sandbox_tools"]["tools"]["git"].startswith("git version")


def test_the_tool_check_reads_only_what_the_roster_runs():
    from adw_modules.sandbox_remote import tools_needed
    agents = lambda *kinds: types.SimpleNamespace(agents=[types.SimpleNamespace(coding_agent=k) for k in kinds])
    assert tools_needed(agents("claude_code")) == ["git", "uv", "claude"]
    assert tools_needed(agents("pi")) == ["git", "uv", "node", "pi"]
    assert tools_needed(agents("pi", "claude_code")) == ["git", "uv", "claude", "node", "pi"]


def test_a_newer_major_is_noted_and_an_older_minimum_refused():
    from adw_modules.sandbox_remote import judge_tools
    assert judge_tools({"pi": "1.5.0", "node": "v24.11.1", "claude": "2.1.317 (Claude Code)"}) == ([], [])
    problems, notes = judge_tools({"pi": "2.0.1", "node": "v22.18.0", "claude": "weird"})
    assert problems == ["node is 22.18.0, and SSSF needs 22.19.0 or later",
                        "claude is unreadable ('weird')"]
    assert notes == ["pi 2.0.1 is a newer major than SSSF has run on (1.x); the run goes on"]


def test_a_commit_with_git_options_still_lands_on_the_host(box):
    """Twelfth review: git_helper's commit carries `-c core.fsmonitor=` before
    `commit`, and the exact-argv match missed it, so commits came back only
    at close."""
    (box.vm_tree / "b.txt").write_text("b\n")
    box.run(["git", "-c", "core.fsmonitor=", "add", "-A"])
    assert box.run(["git", "-c", "core.fsmonitor=", "commit", "-qm", "b"]).returncode == 0
    assert (box.host_tree / "b.txt").read_text() == "b\n"
    assert "sandbox_commits" in dict(box.events)


def test_a_pi_agents_obs_token_stays_on_the_host_and_obs_is_disabled(box, monkeypatch):
    from adw_modules import agent_pi
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "secret", "OBS_NAME": "builder", "SSSF_ADW_ID": "x"}, box, [])
    passed = box._env(env)
    assert "OBS_AUTH_TOKEN" not in passed
    assert passed["OBS_DISABLE"] == "true"
    assert box._obs_target is None
    assert passed["OBS_NAME"] == "builder"
    assert passed["OPENAI_API_KEY"] == "eyJ-access"


def test_obs_token_stays_out_of_checks_and_agents_but_other_obs_variables_cross(box, monkeypatch):
    monkeypatch.setenv("OBS_AUTH_TOKEN", "secret-obs")
    monkeypatch.setenv("OBS_NAME", "n")
    done = box.run(["sh", "-c", "env"])
    assert "OBS_AUTH_TOKEN" not in done.stdout and "secret-obs" not in done.stdout
    assert "OBS_NAME=n" in done.stdout and "SSSF_ADW_ID=abc" in done.stdout
    seen = box.start(["sh", "-c", "env"])
    out = "".join(seen.stdout)
    seen.wait()
    assert "OBS_AUTH_TOKEN" not in out and "OBS_NAME=n" in out


def test_obs_token_in_a_callers_env_dict_is_dropped_too(box):
    env = {"OBS_AUTH_TOKEN": "secret-obs", "OBS_POOL": "p", "SSSF_ADW_ID": "abc"}
    done = box.run(["sh", "-c", "env"], env=env)
    assert "OBS_AUTH_TOKEN" not in done.stdout and "OBS_POOL=p" in done.stdout
    seen = box.start(["sh", "-c", "env"], env=env)
    out = "".join(seen.stdout)
    seen.wait()
    assert "OBS_AUTH_TOKEN" not in out and "OBS_POOL=p" in out
    sent = [c["env"] for c in box.raw.calls]
    assert sent and all("OBS_AUTH_TOKEN" not in e for e in sent)


def test_a_host_command_still_gets_the_obs_token(tmp_path, monkeypatch):
    from adw_modules import sandbox
    monkeypatch.setenv("OBS_AUTH_TOKEN", "secret-obs")
    done = sandbox.LOCAL.run(["sh", "-c", "env"], cwd=tmp_path)
    assert "OBS_AUTH_TOKEN=secret-obs" in done.stdout


# --- a call the provider never answers (run 80481cd6) --------------------------

@pytest.fixture
def hang(box, monkeypatch):
    """Short deadlines, and a call that blocks until the test ends, as one
    on a dropped connection does."""
    from adw_modules import sandbox_remote
    monkeypatch.setattr(sandbox_remote, "CALL_SECONDS", 0.3, raising=False)
    monkeypatch.setattr(sandbox_remote, "COMMAND_SECONDS", 0.2)
    gone = threading.Event()

    def stuck(*a, **k):
        gone.wait(30)
    yield stuck
    gone.set()


def test_a_command_the_vm_never_answers_fails_by_its_deadline(box, hang, monkeypatch):
    monkeypatch.setattr(box.sandbox, "run_process", hang)
    started = time.monotonic()
    with pytest.raises(OSError, match="no answer in"):
        box.run(["true"], timeout=0.2)
    assert time.monotonic() - started < 5


def test_close_with_a_vm_that_never_answers_still_ends_and_says_so(box, hang, monkeypatch):
    monkeypatch.setattr(box.sandbox, "run_process", hang)
    monkeypatch.setattr(box.sandbox, "destroy", hang)
    started = time.monotonic()
    box.close()
    assert time.monotonic() - started < 10
    end = [p for n, p in box.events if n == "sandbox_end"]
    assert end and end[-1]["deleted"] is False
    assert any("no answer in" in p["error"] for n, p in box.events if n == "sandbox_error")


def test_an_agent_wait_is_bounded_by_the_session(box, hang):
    process = box.start(["sleep", "30"])
    box.limit = 0.2
    try:
        with pytest.raises(OSError, match="no answer in"):
            process.wait()
    finally:
        process.kill()


def test_sdk_errors_become_oserror_and_others_pass_through(box, monkeypatch):
    def sdk(*a, **k):
        raise box.errors[0]("boom")

    def other(*a, **k):
        raise ValueError("ours")
    monkeypatch.setattr(box.sandbox, "run_process", sdk)
    with pytest.raises(OSError, match="boom"):
        box.run(["true"])
    monkeypatch.setattr(box.sandbox, "run_process", other)
    with pytest.raises(ValueError, match="ours"):
        box.run(["true"])


# --- an agent's output stream that goes dead (decisions 28 to 35) ---------------
#
# A stream stays open while anything holds the agent's stdout. A grandchild
# started in a session of its own does, after the agent's session has gone:
# to the host that is a stream that never ends, as one on a dropped
# connection is.

HOLD = "(setsid sleep 6 >&1 2>&2 &)"


@pytest.fixture
def quick(monkeypatch):
    from adw_modules import sandbox_remote
    monkeypatch.setattr(sandbox_remote, "QUIET_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(sandbox_remote, "DRAIN_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(sandbox_remote, "POLL_SECONDS", 0.05, raising=False)
    monkeypatch.setattr(sandbox_remote, "CALL_SECONDS", 0.5, raising=False)
    monkeypatch.setattr(sandbox_remote, "COMMAND_SECONDS", 0.5)


def _read(process, dog=None):
    """The runner's read loop, as agent_cc and agent_pi run it."""
    from adw_modules import silence
    lines = []
    with silence.watch(process, dog) as watchdog:
        for line in process.stdout:
            watchdog.touch()
            lines.append(line)
        stderr = process.stderr.read() if process.stderr else ""
        code = process.wait()
    return lines, watchdog, stderr, code


def test_a_quiet_agent_that_is_still_running_keeps_its_stream(box, quick):
    process = box.start(["sh", "-c", "echo one; sleep 1.5; echo two"])
    lines, dog, _, code = _read(process)
    assert [l.strip() for l in lines] == ["one", "two"]
    assert code == 0 and not dog.fired and not dog.lost


def test_a_stream_the_vm_never_answers_about_ends_and_says_so(box, quick, hang, monkeypatch):
    from adw_modules import silence
    process = box.start(["sh", "-c", "echo one; sleep 6"])
    started = time.monotonic()
    first = next(iter(process.stdout))
    monkeypatch.setattr(box.sandbox, "run_process", hang)   # the VM stops answering
    rest, dog, stderr, code = _read(process)
    assert first.strip() == "one" and rest == []
    assert time.monotonic() - started < 5
    assert dog.lost and dog.hung and code == -1 and stderr == ""
    stop = silence.SilentStop("builder", 15, "abc")
    assert stop.lost and "didn't answer" in str(stop) and "--adw-id abc" in str(stop)


def test_a_stream_that_outlives_its_agent_ends_and_says_so(box, quick):
    from adw_modules import silence
    process = box.start(["sh", "-c", f"echo one; {HOLD}"])
    started = time.monotonic()
    lines, dog, _, _ = _read(process)
    assert [l.strip() for l in lines] == ["one"]
    assert time.monotonic() - started < 5
    assert dog.lost and dog.hung
    assert "had exited" in str(silence.SilentStop("builder", 15, "abc"))


def test_a_silence_stop_ends_the_read_in_a_vm(box, quick):
    from adw_modules import silence, sandbox_remote
    sandbox_remote.QUIET_SECONDS = 60          # the probe stays out of it
    process = box.start(["sh", "-c", f"echo one; {HOLD}; sleep 6"])
    started = time.monotonic()
    lines, dog, _, _ = _read(process, dog=0.5)
    assert [l.strip() for l in lines] == ["one"]
    assert time.monotonic() - started < 5
    assert dog.fired and dog.hung and not dog.lost
    assert not silence.SilentStop("builder", 15, "abc").lost


def test_a_lost_stream_is_stopped_once(box, quick):
    process = box.start(["sh", "-c", f"echo one; {HOLD}"])
    stops = []
    real = process.stop_tree
    process.stop_tree = lambda: (stops.append(1), real())
    _read(process)
    assert stops == [1]


def test_a_stream_that_ends_normally_is_unchanged(box, quick):
    from adw_modules import silence
    process = box.start(["sh", "-c", "echo one; echo two >&2; exit 3"])
    lines, dog, stderr, code = _read(process)
    assert [l.strip() for l in lines] == ["one"] and "two" in stderr
    assert code == 3 and not dog.fired
    assert not silence.SilentStop("builder", 15, "abc").lost


def test_a_probe_that_cant_tell_keeps_reading(box, quick, monkeypatch):
    from adw_modules import sandbox_remote
    monkeypatch.setattr(sandbox_remote, "_PROBE", "exit 127")    # no shell tools, say
    process = box.start(["sh", "-c", "echo one; sleep 1.5; echo two"])
    lines, dog, _, code = _read(process)
    assert [l.strip() for l in lines] == ["one", "two"] and code == 0 and not dog.lost


def test_the_probe_sees_a_live_agent_and_a_gone_one(box):
    process = box.start(["sh", "-c", "echo one; sleep 3"])
    next(iter(process.stdout))
    assert process._alive() is True
    process.stop_tree()
    time.sleep(0.3)
    assert process._alive() is False


def test_after_a_stop_stderr_and_wait_are_bounded(box, quick):
    process = box.start(["sh", "-c", f"echo one; {HOLD}; sleep 6"])
    next(iter(process.stdout))
    process.stop_tree()                        # as the runner does, leaving by an error
    started = time.monotonic()
    process.stderr.read()
    process.wait()
    assert time.monotonic() - started < 3


def test_a_result_kept_after_a_lost_stream_still_needs_its_handoff(box, quick, monkeypatch):
    from adw_modules import silence
    process = box.start(["sh", "-c", f"echo one; {HOLD}"])

    def broken():
        raise OSError("handoff failed")
    monkeypatch.setattr(box, "handoff_out", broken)
    with silence.watch(process, None) as dog:
        for _ in process.stdout:
            dog.finished()                     # the agent had answered
        with pytest.raises(OSError, match="handoff failed"):
            process.wait()
    assert dog.lost and not dog.hung


def test_a_lost_stream_ends_the_watchdog(box, quick):
    process = box.start(["sh", "-c", f"echo one; {HOLD}"])
    stops = []
    real = process.stop_tree
    process.stop_tree = lambda: (stops.append(1), real())
    lines, dog, _, _ = _read(process, dog=1.5)
    time.sleep(2)
    assert dog.lost and stops == [1]


# --- the obs relay (decisions 37 to 50) ----------------------------------------

from test_obs_relay import FakeObs, _event  # noqa: E402

POSTER = ("const u = process.env.OBS_SERVER_URL; "
          "const ev = JSON.parse(process.argv[process.argv.length - 1]); "
          "(async () => { for (const e of ev) await fetch(u + '/events', {method: 'POST', "
          "headers: {'content-type': 'application/json'}, body: JSON.stringify([e])}); "
          "console.log('posted ' + ev.length); })();")


@pytest.fixture
def relaying(box, tmp_path, monkeypatch):
    from adw_modules import obs_relay, sandbox_remote
    if shutil.which("node") is None:
        pytest.skip("needs node")
    monkeypatch.setattr(sandbox_remote, "OBS_ROOT", str(tmp_path / "vmtmp"))
    fake = FakeObs()
    target = obs_relay.Target(server=fake.url, token="obs-secret", name="builder",
                              pool="sssf-abc", tags=["sssf", "adw-abc"])
    yield box, fake, target
    fake.close()
    _kill_left_under(tmp_path / "vmtmp")


def _poster(events) -> list[str]:
    return ["node", "-e", POSTER, json.dumps(events)]


def _relays(box) -> list[dict]:
    return [p for name, p in box.events if name == "obs_relay"]


def test_a_relayed_call_reaches_obs_with_the_calls_names(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    process = box.start(_poster([_event(n) for n in range(3)]), env={"OBS_DISABLE": "true"})
    lines, _, _, code = _read(process)
    assert code == 0 and lines[-1].strip() == "posted 3"
    assert sorted(e["event_id"] for e in obs.events) == ["e0", "e1", "e2"]
    assert all(e["pool"] == "sssf-abc" and e["agent_name"] == "builder" for e in obs.events)
    assert all(e["session_id"].startswith("sssf-abc-") for e in obs.events)   # the run's sessions only
    assert all(h["Authorization"] == "Bearer obs-secret" for h in obs.headers)
    [summary] = _relays(box)
    assert summary["posted"] == 3 and summary["dropped"] == 0 and summary["live"] is True


def test_the_agent_gets_the_relay_on_loopback_and_never_the_obs_token(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    process = box.start(["sh", "-c", "env | grep ^OBS_ || true"],
                        env={"OBS_DISABLE": "true", "OBS_AUTH_TOKEN": "obs-secret", "OBS_SERVER_URL": "http://host:1"})
    lines, _, _, _ = _read(process)
    out = "".join(lines)
    assert re.search(r"^OBS_SERVER_URL=http://127\.0\.0\.1:\d+$", out, re.M)
    assert "OBS_DISABLE" not in out and "obs-secret" not in out
    start = [c for c in box.raw.calls if c.get("start") and "OBS_SERVER_URL" in json.dumps(c.get("env"))]
    assert start and all("obs-secret" not in json.dumps(c) for c in box.raw.calls)


def test_a_pi_call_with_an_extension_and_the_obs_token_is_relayed_without_the_token(relaying, monkeypatch):
    from adw_modules import agent_pi
    box, obs, _ = relaying
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "obs-secret", "OBS_SERVER_URL": obs.url, "OBS_NAME": "builder",
                           "OBS_POOL": "sssf-abc", "OBS_TAG": "sssf,adw-abc", "SSSF_ADW_ID": "abc"},
                          box, [], ["obs.ts"])
    assert box._obs_target.token == "obs-secret" and box._obs_target.server == obs.url
    lines, _, _, _ = _read(box.start(["sh", "-c", "env | grep ^OBS_ || true"], env=env))
    out = "".join(lines)
    assert re.search(r"^OBS_SERVER_URL=http://127\.0\.0\.1:\d+$", out, re.M)
    assert "OBS_DISABLE" not in out
    assert all("obs-secret" not in json.dumps(c) for c in box.raw.calls)
    assert "obs-secret" not in json.dumps(box.events)


def test_a_pi_call_that_obs_couldnt_post_from_gets_obs_disable_true_and_no_relay(box, monkeypatch):
    from adw_modules import agent_pi
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    env = agent_pi._in_vm({"OBS_AUTH_TOKEN": "obs-secret", "OBS_NAME": "builder", "SSSF_ADW_ID": "abc"},
                          box, [], [])
    lines, _, _, _ = _read(box.start(["sh", "-c", "env | grep ^OBS_ || true"], env=env))
    out = "".join(lines)
    assert "OBS_DISABLE=true" in out and "obs-secret" not in out
    assert all("obs-secret" not in json.dumps(c) for c in box.raw.calls)
    assert not _relays(box)


def test_a_call_with_no_relay_asked_is_unchanged(relaying):
    box, obs, _ = relaying
    before = len(box.raw.calls)
    process = box.start(["sh", "-c", "echo ${OBS_DISABLE:-on}"], env={"OBS_DISABLE": "true"})
    lines, _, _, _ = _read(process)
    assert lines == ["true\n"] and not _relays(box) and not obs.batches
    assert not any("sssf-obs" in _words(c) for c in box.raw.calls[before:])


def test_a_relay_request_is_used_by_the_next_start_only(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    _read(box.start(["true"], env={"OBS_DISABLE": "true"}))
    process = box.start(["sh", "-c", "echo ${OBS_DISABLE:-on}"], env={"OBS_DISABLE": "true"})
    lines, _, _, _ = _read(process)
    assert lines == ["true\n"] and len(_relays(box)) == 1


def test_the_relay_is_not_part_of_the_agents_session(relaying):
    box, _, target = relaying
    box.relay_obs(target)
    process = box.start(["true"], env={"OBS_DISABLE": "true"})
    list(process.stdout)
    time.sleep(0.3)
    assert process._alive() is False          # the relay is still up, in its own session
    process.wait()


def test_a_relay_that_wont_start_leaves_obs_off_and_warns(relaying, monkeypatch, tmp_path):
    from adw_modules import obs_relay, sandbox_remote
    box, obs, target = relaying
    dud = tmp_path / "dud.cjs"
    dud.write_text("setTimeout(() => {}, 5000);\n")
    monkeypatch.setattr(obs_relay, "RELAY_SCRIPT", dud)
    monkeypatch.setattr(sandbox_remote, "RELAY_START_SECONDS", 0.5, raising=False)
    box.relay_obs(target)
    process = box.start(["sh", "-c", "echo ${OBS_DISABLE:-on}"], env={"OBS_DISABLE": "true"})
    lines, _, _, code = _read(process)
    assert lines == ["true\n"] and code == 0
    assert [p["kind"] for n, p in box.events if n == "obs_relay_warning"] == ["start"]
    assert not _relays(box)


def test_the_sweep_posts_what_the_live_stream_missed(relaying, monkeypatch, tmp_path):
    from adw_modules import obs_relay
    box, obs, target = relaying
    from adw_modules import sandbox_remote
    follow = 'exec node "$1" --follow "$2"'
    assert follow in sandbox_remote.RELAY_SHELL
    monkeypatch.setattr(sandbox_remote, "RELAY_SHELL", sandbox_remote.RELAY_SHELL.replace(follow, "exec sleep 600"))
    box.relay_obs(target)
    _read(box.start(_poster([_event(n) for n in range(4)]), env={"OBS_DISABLE": "true"}))
    assert sorted(e["event_id"] for e in obs.events) == ["e0", "e1", "e2", "e3"]
    assert _relays(box)[0]["posted"] == 4


def test_obs_down_never_fails_the_call(relaying):
    box, obs, target = relaying
    obs.status = 503
    box.relay_obs(target)
    lines, _, _, code = _read(box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"}))
    assert code == 0 and lines[-1].strip() == "posted 1"
    assert _relays(box)[0]["unposted"] == 1
    assert [p["kind"] for n, p in box.events if n == "obs_relay_warning"] == ["post"]


def test_the_events_file_stays_out_of_the_clone(relaying):
    box, _, target = relaying
    box.relay_obs(target)
    _read(box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"}))
    status = subprocess.run(["git", "status", "--porcelain", "--ignored"], cwd=box.vm_tree,
                            capture_output=True, text=True).stdout
    assert status == ""


def test_a_relay_left_open_is_closed_with_the_vm(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    process = box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"})
    list(process.stdout)                       # the runner left before wait()
    box.close()
    assert [e["event_id"] for e in obs.events] == ["e1"] and len(_relays(box)) == 1


def test_a_stopped_agent_still_gets_its_events_posted(relaying, quick, tmp_path):
    box, obs, target = relaying
    script = tmp_path / "poster.js"
    script.write_text(POSTER)
    box.relay_obs(target)
    process = box.start(["sh", "-c", 'node "$0" "$1"; sleep 5', str(script), json.dumps([_event(1)])],
                        env={"OBS_DISABLE": "true"})
    for line in process.stdout:
        if "posted" in line:
            process.stop_tree()
    process.wait()
    assert [e["event_id"] for e in obs.events] == ["e1"], box.events


def test_relay_warnings_are_logged_from_the_runners_thread(relaying):
    box, obs, target = relaying
    obs.status = 503
    threads = []
    tracer = box.run_.tracer
    box.run_.tracer = types.SimpleNamespace(
        event=lambda e: (threads.append(threading.current_thread()), tracer.event(e)))
    box.relay_obs(target)
    _read(box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"}))
    assert [p["kind"] for n, p in box.events if n == "obs_relay_warning"] == ["post"]
    assert set(threads) == {threading.main_thread()}


def test_a_vm_gone_silent_gets_no_relay_stop_or_sweep(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    process = box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"})
    list(process.stdout)
    time.sleep(1)                                      # the live posts land
    process._silent = True                             # as the probe leaves it
    before = len(box.raw.calls)
    started = time.monotonic()
    process.wait()
    assert time.monotonic() - started < 5
    assert not any("pkill" in _words(c) or _words(c).startswith("head") for c in box.raw.calls[before:])
    [summary] = _relays(box)
    assert summary["swept"] is False and summary["posted"] == 1


def test_a_vm_that_lost_tmp_still_gets_a_relay(relaying):
    box, obs, target = relaying
    box.relay_obs(target)
    _read(box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"}))
    shutil.rmtree(box._obs_dir)                        # as a resume without /tmp would
    box.relay_obs(target)
    _read(box.start(_poster([_event(2)]), env={"OBS_DISABLE": "true"}))
    assert sorted(e["event_id"] for e in obs.events) == ["e1", "e2"]
    assert not [p for n, p in box.events if n == "obs_relay_warning"]


def test_big_batches_arrive_live_and_the_follower_stays_up(relaying, tmp_path):
    box, obs, target = relaying
    box.relay_obs(target)
    batch = [_event(n, payload={"pad": "x" * 2500}) for n in range(50)]
    events = batch + [_event(100 + n) for n in range(5)]
    data = tmp_path / "events.json"                   # too long for one argument
    data.write_text(json.dumps(events))
    poster = POSTER.replace("JSON.parse(process.argv[process.argv.length - 1])",
                            "JSON.parse(require('fs').readFileSync(process.argv[process.argv.length - 1], 'utf8'))")
    process = box.start(["sh", "-c", 'node -e "$0" "$1"; sleep 3', poster, str(data)],
                        env={"OBS_DISABLE": "true"})
    for line in process.stdout:
        if "posted" in line:
            break
    deadline = time.monotonic() + 5
    while len(obs.events) < 55 and time.monotonic() < deadline:
        time.sleep(0.1)
    assert len(obs.events) == 55                       # live, before the call ends
    list(process.stdout)
    process.wait()
    [summary] = _relays(box)
    assert summary["live"] is True and summary["posted"] == 55


def test_a_line_with_no_end_in_the_file_is_skipped(relaying, tmp_path):
    box, obs, target = relaying
    box._obs_dir = str(tmp_path / "obsdir")           # so the agent can find the relay's file
    box.relay_obs(target)
    flood = ('f=$(ls "$2"/*.jsonl | head -1); head -c 8000000 /dev/zero | tr "\\0" a >> "$f"; '
             'echo >> "$f"; node -e "$0" "$1"')
    process = box.start(["sh", "-c", flood, POSTER, json.dumps([_event(1)]), box._obs_dir],
                        env={"OBS_DISABLE": "true"})
    list(process.stdout)
    process.wait()
    [summary] = _relays(box)
    assert [e["event_id"] for e in obs.events] == ["e1"] and summary["dropped"] == 1


def test_a_relay_that_died_in_the_call_is_named(relaying, tmp_path):
    box, obs, target = relaying
    box._obs_dir = str(tmp_path / "obsdir")
    box.relay_obs(target)
    process = box.start(["sh", "-c", 'kill "$(cat "$0"/*.sid.relay)"', box._obs_dir],
                        env={"OBS_DISABLE": "true"})
    list(process.stdout)
    process.wait()
    assert "died" in [p["kind"] for n, p in box.events if n == "obs_relay_warning"]


def test_a_failed_wait_asks_the_vm_nothing_for_obs(relaying, quick):
    box, obs, target = relaying
    box.relay_obs(target)
    process = box.start(_poster([_event(1)]), env={"OBS_DISABLE": "true"})
    list(process.stdout)
    time.sleep(1)

    def gone():
        raise OSError("the VM is gone")
    process._process.wait = gone
    process.stop_tree()                                # quick path, as after a stop
    before = len(box.raw.calls)
    process.wait()
    assert not any("pkill -KILL -s" in _words(c) and "relay" in _words(c) or _words(c).startswith("head")
                   for c in box.raw.calls[before:])
    assert _relays(box)[0]["swept"] is False


def test_a_start_that_fails_early_uses_up_its_relay_request(relaying):
    box, obs, target = relaying
    box.relay_obs(target)

    def broken(need):
        raise OSError("refresh failed")
    real = box._refresh
    box._refresh = broken
    with pytest.raises(OSError):
        box.start(["true"], env={"OBS_DISABLE": "true"})
    box._refresh = real
    process = box.start(["sh", "-c", "echo ${OBS_DISABLE:-on}"], env={"OBS_DISABLE": "true"})
    assert _read(process)[0] == ["true\n"]


# --- pi agents and the Claude token ------------------------------------------------

def roster(*kinds):
    """The shipped config, its agents cycled through `kinds`; each pi agent on an openai/ model."""
    from adw_modules import agents
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    for agent, kind in zip(cfg.agents, itertools.cycle(kinds)):
        agent.coding_agent = kind
        if kind == "pi":
            agent.model = "openai/gpt-5.6-terra"
    return cfg


def _env_of(process) -> dict:
    out = "".join(process.stdout)
    process.wait()
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def test_a_pi_start_gets_no_claude_token(box):
    seen = _env_of(box.start(["sh", "-c", "env"], env={**os.environ, "SSSF_PI_TOKEN": "chatgpt-access"}))
    assert seen["OPENAI_API_KEY"] == "chatgpt-access" and "CLAUDE_CODE_OAUTH_TOKEN" not in seen
    assert "PI_OFFLINE" not in seen


def test_a_pi_start_leaves_the_claude_token_out_of_every_provider_call(box):
    before = len(box.raw.calls)
    _env_of(box.start(["sh", "-c", "env"], env={**os.environ, "SSSF_PI_TOKEN": "chatgpt-access"}))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in repr(box.raw.calls[before:])


def test_a_claude_start_keeps_the_token_and_gets_no_pi_env(box):
    seen = _env_of(box.start(["sh", "-c", "env"], env=dict(os.environ)))
    assert seen["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-test"
    assert "PI_OFFLINE" not in seen and "OPENAI_API_KEY" not in seen


def test_a_hosts_pi_offline_does_not_cross(box):
    seen = _env_of(box.start(["sh", "-c", "env"], env={**os.environ, "PI_OFFLINE": "1",
                                                        "SSSF_PI_TOKEN": "chatgpt-access"}))
    assert "PI_OFFLINE" not in seen
    assert "PI_OFFLINE" not in box._env({"PI_OFFLINE": "1"})


@pytest.fixture(params=["vercel", "e2b"])
def tokenless(request, tmp_path, monkeypatch):
    from adw_modules import sandbox_remote
    monkeypatch.setattr(sandbox_remote.Remote, "_check_tools", lambda self: None)
    return lambda cfg: _make_box(request.param, tmp_path, monkeypatch, cfg=cfg, token=False)


def test_an_all_pi_roster_opens_without_a_token_file(tokenless):
    b = tokenless(roster("pi"))
    b.open()
    assert b.sandbox is not None and b._token == ""
    seen = _env_of(b.start(["sh", "-c", "env"], env={**os.environ}))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen
    seen = _env_of(b.start(["sh", "-c", "env"], env={**os.environ, "SSSF_PI_TOKEN": "chatgpt-access"}))
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in seen and seen["OPENAI_API_KEY"] == "chatgpt-access"


@pytest.mark.parametrize("kinds", [("claude_code",), ("pi", "claude_code")])
def test_a_roster_with_a_claude_agent_needs_the_token_file(tokenless, kinds):
    from adw_modules.sandbox import SandboxFailed
    b = tokenless(roster(*kinds))
    with pytest.raises(SandboxFailed, match="token"):
        b.open()
    assert b.sandbox is None and b.raw.calls == []

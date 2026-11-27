"""Verify runs in a fresh VM, away from what the builder left in the build VM.

The stand-in is FakeVercel from test_sandbox_remote, except that each create gives a
NEW one with its own HOME and TMPDIR (standing for /home and /tmp, which stand-ins
on one host can't otherwise keep apart). The config is the one SSSF ships.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sandbox_remote import FakeVercel, _kill_left_under, _make_box  # noqa: E402


class VMFake(FakeVercel):
    """FakeVercel with a private TMPDIR, so one stand-in's /tmp is its own."""

    def __init__(self, host: Path, vm: Path, name: str):
        super().__init__(host, vm)
        self.name = name
        self.tmp = Path(vm) / "tmp"
        self.tmp.mkdir(parents=True, exist_ok=True)

    def run_process(self, command, args=(), *, env=None, **kwargs):
        return super().run_process(command, args, env={**(env or {}), "TMPDIR": str(self.tmp)}, **kwargs)


def _git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=tree, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """An open build box, a list of every stand-in made, and a `run` for verify."""
    from adw_modules import agents, plan_checks, quality  # noqa: F401
    box = _make_box("vercel", tmp_path, monkeypatch)
    tree = box.host_tree
    (tree / ".gitignore").write_text(".venv\nflag\n")
    (tree / "b.txt").write_text("b\n")
    (tree / "c.bin").write_bytes(b"\x00\x01\x02")
    _git(tree, "add", ".")
    _git(tree, "commit", "-qm", "more")
    made: list[VMFake] = []
    host = tmp_path / "host"

    def create(**kwargs):
        vm = tmp_path / ("vm" if not made else f"vm{len(made)}")
        vm.mkdir(exist_ok=True)
        fake = VMFake(host, vm, f"fake-vm-{len(made)}")
        made.append(fake)
        return fake

    sync = sys.modules["vercel.sandbox.sync"]
    sync.create_sandbox = create
    box.cfg_path = SKILL / "templates" / "sssf.config.yaml"
    run = box.run_
    run.cfg = agents.load_config(str(box.cfg_path))
    run.ran.append(next(a for a in run.cfg.agents if a.name == "builder"))
    run.repo_root = tree
    run.phases = [types.SimpleNamespace(seq=1, phase_id="p1")]
    run.context_handoff_dir.mkdir(parents=True, exist_ok=True)
    box.open()
    box.made = made
    from adw_modules import sandbox
    sandbox.use(box)
    yield box
    sandbox.use(sandbox.LOCAL)
    _kill_left_under(tmp_path)


def _plan(*checks: str):
    return types.SimpleNamespace(checks=list(checks))


def _block(command: str, seen: list | None = None):
    """A `base=` for verify: one quality block that runs `command` in the current sandbox."""
    from adw_modules import data_types, sandbox

    def base(run):
        done = sandbox.current().run(["bash", "-c", command], cwd=run.repo_root, timeout=60)
        if seen is not None:
            seen.append((sandbox.current(), done.stdout))
        ok = done.returncode == 0
        check = data_types.QualityCheckResult(
            name="block", area="backend", operation="check", command=command, returncode=done.returncode,
            passed=ok, duration_seconds=0.0, output_artifact="", output_tail=done.stdout)
        return data_types.QualityResult(passed=ok, checks=[check], failures=[] if ok else ["block"])
    return base


def _verify(rig, *checks: str, base=None):
    from adw_modules import plan_checks
    return plan_checks.verify(rig.run_, _plan(*checks), base=base or _block("true"))


def _in_build(rig, command: str) -> int:
    return rig.run(["bash", "-c", command], cwd=rig.host_tree, timeout=60).returncode


def _plant_fails_verify(rig, command: str) -> None:
    assert _in_build(rig, command) == 0        # the plant is live in the build VM
    result = _verify(rig, command)
    assert result.passed is False              # and gone in the fresh one
    assert len(rig.made) == 2 and rig.made[1].destroyed


# --- the red test -------------------------------------------------------------

def test_every_command_of_the_round_runs_in_a_fresh_vm_with_none_of_the_builders_files(rig):
    from adw_modules import sandbox
    (rig.made[0].tmp.parent / "marker").write_text("x")      # the build VM's HOME
    seen: list = []
    result = _verify(rig, 'test ! -e "$HOME/marker"', base=_block('test ! -e "$HOME/marker"', seen))
    assert result.passed
    assert len(rig.made) == 2
    build, fresh = rig.made
    assert seen[0][0] is not rig and fresh.destroyed
    ran = [c for c in fresh.calls if c["argv"][0] == "bash"]
    assert len(ran) == 2                                   # the block and the plan check
    assert not [c for c in build.calls if c["argv"][0] == "bash" and "marker" in " ".join(c["argv"])]
    assert sandbox.current() is rig


def test_the_current_box_is_the_build_box_again_when_a_check_raises(rig):
    from adw_modules import sandbox

    def boom(run):
        assert sandbox.current() is not rig
        raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        _verify(rig, base=boom)
    assert sandbox.current() is rig and rig.made[1].destroyed


# --- plants -------------------------------------------------------------------

def test_plant_a_conftest_in_the_build_vms_temp_folder(rig):
    (rig.made[0].tmp / "conftest.py").write_text("import os\nos._exit(0)\n")
    check = ('d=$(mktemp -d) && echo "def test_x(): assert False" > "$d/test_x.py" && '
             f'{sys.executable} -m pytest -q --confcutdir=/ -p no:cacheprovider "$d"')
    _plant_fails_verify(rig, check)


def test_plant_b_conftest_that_sets_the_exit_status(rig):
    (rig.made[0].tmp / "conftest.py").write_text(
        "def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 0\n")
    check = ('d=$(mktemp -d) && echo "def test_x(): assert False" > "$d/test_x.py" && '
             f'{sys.executable} -m pytest -q --confcutdir=/ -p no:cacheprovider "$d"')
    _plant_fails_verify(rig, check)


@pytest.mark.skipif(not Path("/usr/bin/python3").exists(), reason="no base interpreter")
def test_plant_c_pth_in_the_user_site(rig):
    home = rig.made[0].tmp.parent
    site = subprocess.run(["/usr/bin/python3", "-c", "import site;print(site.getusersitepackages())"],
                          env={"HOME": str(home), "PATH": os.environ["PATH"]},
                          capture_output=True, text=True).stdout.strip()
    Path(site).mkdir(parents=True)
    (Path(site) / "x.pth").write_text("import os; os._exit(0)\n")
    _plant_fails_verify(rig, "/usr/bin/python3 -c 'raise SystemExit(1)'")


def test_plant_d_uv_config_and_cache(rig):
    home = rig.made[0].tmp.parent
    (home / ".config" / "uv").mkdir(parents=True)
    (home / ".config" / "uv" / "uv.toml").write_text("offline = true\n")
    (home / ".cache" / "uv").mkdir(parents=True)
    (home / ".cache" / "uv" / "x").write_text("x")
    _plant_fails_verify(rig, 'test -e "$HOME/.config/uv/uv.toml" && test -e "$HOME/.cache/uv/x"')


def test_plant_e_ignored_venv_pytest(rig):
    bin_ = rig.vm_tree / ".venv" / "bin"
    bin_.mkdir(parents=True)
    (bin_ / "pytest").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "pytest").chmod(0o755)
    _plant_fails_verify(rig, ".venv/bin/pytest")


def test_plant_f_a_left_process_that_keeps_writing_a_file(rig):
    flag = rig.vm_tree / "flag"
    rig.run(["bash", "-c", f"setsid sh -c 'while :; do echo 0 > {flag}; sleep 0.1; done' "
                           ">/dev/null 2>&1 </dev/null &"], cwd=rig.host_tree, timeout=30)
    for _ in range(50):
        if flag.exists():
            break
        subprocess.run(["sleep", "0.1"])
    _plant_fails_verify(rig, 'exit "$(cat flag 2>/dev/null || echo 1)"')


# --- the patch ----------------------------------------------------------------

def test_the_patch_carries_changes_deletes_new_files_binaries_and_a_commit(rig):
    tree = rig.vm_tree
    (tree / "a.txt").write_text("changed\n")
    _git(tree, "commit", "-qam", "own commit")             # a commit the builder made, unsynced
    (tree / "b.txt").unlink()
    (tree / "new.txt").write_text("new\n")
    (tree / "c.bin").write_bytes(b"\x00\xff\xfe\x00bin")
    (tree / "a.txt").write_text("changed again\n")
    head, status = _git(tree, "rev-parse", "HEAD"), _git(tree, "status", "--porcelain=v1")
    host_head, synced = _git(rig.host_tree, "rev-parse", "HEAD"), rig.synced
    seen: list = []
    result = _verify(rig, base=_block(
        "cat a.txt new.txt; test ! -e b.txt && head -c 20 c.bin | od -An -tx1; git rev-parse HEAD", seen))
    assert result.passed
    out = seen[0][1]
    assert "changed again" in out and "new" in out and "00 ff fe 00 62 69 6e" in out
    assert out.strip().splitlines()[-1] == synced           # the fresh VM's HEAD stays at the sync
    assert _git(tree, "rev-parse", "HEAD") == head and _git(tree, "status", "--porcelain=v1") == status
    assert _git(rig.host_tree, "rev-parse", "HEAD") == host_head and rig.synced == synced


def test_a_patch_that_writes_a_path_the_builder_may_not_is_refused_before_a_vm_is_made(rig):
    (rig.vm_tree / "GLOSSARY.md").write_text("mine\n")
    from adw_modules import permissions
    host_head, status = _git(rig.host_tree, "rev-parse", "HEAD"), _git(rig.host_tree, "status", "--porcelain=v1")
    ran: list = []
    with pytest.raises(permissions.PermissionBreach):
        _verify(rig, "true", base=lambda run: ran.append(1) or _block("true")(run))
    assert not ran and len(rig.made) == 1
    assert (rig.run_.session_dir / "sandbox-refused-verify.patch").is_file()
    assert _git(rig.host_tree, "rev-parse", "HEAD") == host_head
    assert _git(rig.host_tree, "status", "--porcelain=v1") == status
    assert not (rig.host_tree / "GLOSSARY.md").exists()


# --- fail closed --------------------------------------------------------------

def test_a_second_vm_that_cannot_be_made_stops_the_run_and_no_check_runs_in_the_build_vm(rig):
    from adw_modules import sandbox
    sync = sys.modules["vercel.sandbox.sync"]
    first = len(rig.made[0].calls)

    def refuse(**kwargs):
        raise sys.modules["vercel.sandbox.sync"].SandboxError("no quota")
    sync.create_sandbox = refuse
    ran: list = []
    with pytest.raises(sandbox.SandboxFailed):
        _verify(rig, "true", base=lambda run: ran.append(1) or _block("true")(run))
    assert not ran
    assert not [c for c in rig.made[0].calls[first:] if c["argv"][0] == "bash"]


def test_a_failing_worktree_setup_in_the_fresh_vm_stops_the_run(rig, monkeypatch):
    from adw_modules import repo_settings, sandbox
    monkeypatch.setattr(repo_settings, "load", lambda root: types.SimpleNamespace(
        worktree_setup=["exit 3"], worktree_setup_timeout=60))
    ran: list = []
    with pytest.raises(sandbox.SandboxFailed):
        _verify(rig, "true", base=lambda run: ran.append(1) or _block("true")(run))
    assert not ran and rig.made[1].destroyed


# --- the verify VM's limits ---------------------------------------------------

def test_the_verify_vm_has_no_start_commit_or_install_and_a_commit_through_it_moves_no_branch(rig):
    from adw_modules import sandbox
    host_head = _git(rig.host_tree, "rev-parse", "HEAD")
    seen: list = []
    _verify(rig, "true", base=_block("true", seen))
    fresh = rig.open_verify()
    try:
        for name in ("start", "commit", "install", "write_text", "remove", "handoff_out", "open_verify"):
            assert not hasattr(fresh, name)
        done = fresh.run(["git", "commit", "-q", "--allow-empty", "-m", "x"], cwd=rig.host_tree)
        assert done.returncode == 0
    finally:
        fresh.close()
    assert _git(rig.host_tree, "rev-parse", "HEAD") == host_head and rig.synced == host_head.strip()
    for fake in rig.made[1:]:
        for call in fake.calls:
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in call["env"] and "OPENAI_API_KEY" not in call["env"]
    assert sandbox.current() is rig


# --- local, and the chains ----------------------------------------------------

def test_under_local_verifying_yields_none_and_verify_runs_as_before(monkeypatch):
    from adw_modules import sandbox
    sandbox.use(sandbox.LOCAL)
    with sandbox.verifying(object()) as fresh:
        assert fresh is None
    from adw_modules import plan_checks
    from adw_modules.data_types import QualityResult
    base = QualityResult(passed=True, checks=[], failures=[], artifacts=["a"])
    result = plan_checks.verify(types.SimpleNamespace(), None, base=lambda run: base)
    assert (result.passed, result.checks, result.failures, result.artifacts) == (
        base.passed, base.checks, base.failures, base.artifacts)


def test_no_chain_template_calls_the_blocks_directly():
    import re
    chains = sorted((SKILL / "templates" / "adws").glob("adw_*.py"))
    assert chains
    for path in chains:
        text = path.read_text()
        for match in re.finditer(r"quality\.run_(quality|tests)\(run\)", text):
            line = text[text.rfind("\n", 0, match.start()) + 1:text.find("\n", match.end())]
            assert "def " in line or "base" in line or line.lstrip().startswith("#"), (path.name, line)


def test_a_host_git_config_that_errors_on_whitespace_does_not_refuse_the_patch(rig):
    _git(rig.host_tree, "config", "apply.whitespace", "error")
    (rig.vm_tree / "b.txt").write_text("a markdown line break  \n")
    seen: list = []
    assert _verify(rig, base=_block("cat b.txt", seen)).passed
    assert "line break  " in seen[0][1]


def test_a_transport_failure_while_making_the_fresh_vm_is_sandbox_failed(rig, monkeypatch):
    from adw_modules import sandbox
    from adw_modules.sandbox_remote import Remote

    def lost(self):
        raise OSError("connection reset")
    monkeypatch.setattr(Remote, "_clone", lost)
    with pytest.raises(sandbox.SandboxFailed):
        _verify(rig, "true")
    assert rig.made[1].destroyed

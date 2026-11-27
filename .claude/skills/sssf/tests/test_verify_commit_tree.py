"""A commit under a VM holds only the tree verify passed (run 3d9f277f).

Every plant here is made in the build VM, inside the guard calls `agents.execute`
makes around an agent (`_as_builder`), or outside them, as a process the agent left
running would. Stand-ins are those of test_verify_fresh_vm; the config is the one
SSSF ships.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_verify_fresh_vm import _block, _git, _in_build, _verify, rig  # noqa: E402,F401

APP = ("OK = 1\n"
       "try:\n"
       "    from local_settings import OK\n"
       "except ImportError:\n"
       "    pass\n")
CHECK = "python3 -c 'import app,sys; sys.exit(0 if app.OK == 1 else 1)'"


def _as_builder(rig, plant):
    """Run `plant()` inside the guard calls `agents.execute` makes around an agent."""
    from adw_modules import permissions
    run = rig.run_
    agent = next(a for a in run.cfg.agents if a.name == "builder")
    if agent not in run.ran:            # as agents.execute does; a landed commit cleared it
        run.ran.append(agent)
    phase = types.SimpleNamespace(phase_id="p1", seq=1, params=types.SimpleNamespace(retries=0))
    before = permissions.snapshot(run)
    head = permissions.head(run)
    guard = permissions.guard_git_dir(run)
    plant()
    assert not permissions.git_dir_changed(guard)
    permissions.restore_git_dir(run, guard)
    permissions.undo_commits(run, head)
    permissions.enforce(run, phase, agent, before, head)


def _chain(rig, *checks, base=None):
    """What a chain does: verify, the commit when it passed, then the close."""
    from adw_modules import git_helper, plan_checks  # noqa: F401
    out = None
    try:
        out = _verify(rig, *checks, base=base)
        if out.passed:
            git_helper.commit_all("build", run=rig.run_)
    except Exception as error:
        out = error
    try:
        rig.close(stop=True)
    except Exception as error:
        if not isinstance(out, Exception):
            out = error
    return out


def _host_head(rig) -> str:
    return _git(rig.host_tree, "rev-parse", "HEAD").strip()


def _setup(rig) -> str:
    """app.py and an ignored local_settings.py, committed and synced. Returns the sync."""
    (rig.vm_tree / ".gitignore").write_text(".venv\nflag\nlocal_settings.py\n")
    (rig.vm_tree / "app.py").write_text(APP)
    _git(rig.vm_tree, "add", "-A")
    assert rig.run(["git", "commit", "-qm", "app"], cwd=rig.host_tree).returncode == 0   # syncs
    assert rig.synced == _host_head(rig) != ""
    return rig.synced


def _pending(rig):
    """A change to commit: a new file, made as the builder would."""
    _as_builder(rig, lambda: (rig.vm_tree / "feat.txt").write_text("v1\n"))


def _unpack(tree: Path) -> None:
    """Turn the packs of `tree`'s object store into loose objects."""
    pack = tree / ".git" / "objects" / "pack"
    for one in sorted(pack.glob("*.pack")):
        data = one.read_bytes()
        one.unlink()
        one.with_suffix(".idx").unlink()
        subprocess.run(["git", "unpack-objects", "-q"], cwd=tree, input=data, check=True)


def _lose_blob(rig, name: str):
    """Delete the loose object of `name`'s blob in the build VM. Returns a put-back."""
    tree = rig.vm_tree
    _unpack(tree)
    sha = _git(tree, "rev-parse", f"HEAD:{name}").strip()
    path = tree / ".git" / "objects" / sha[:2] / sha[2:]
    data = path.read_bytes()
    path.unlink()

    def put_back():
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    return put_back


# --- the plants ---------------------------------------------------------------

def test_a_force_added_ignored_file_reaches_verify_and_nothing_lands(rig):
    sync = _setup(rig)

    def plant():
        assert _in_build(rig, 'printf "OK = 0\\n" > local_settings.py && git add -f local_settings.py') == 0
    _as_builder(rig, plant)
    out = _chain(rig, CHECK)
    assert not isinstance(out, Exception) and out.passed is False
    assert _host_head(rig) == sync


def test_a_missing_blob_stops_the_round_and_nothing_lands(rig):
    from adw_modules import sandbox
    sync = _setup(rig)
    _lose_blob(rig, "b.txt")

    def plant():
        (rig.vm_tree / "b.txt").unlink()
        (rig.vm_tree / "app.py").write_text("OK = 0\n")
    _as_builder(rig, plant)
    ran: list = []
    out = _chain(rig, CHECK, base=lambda run: ran.append(1) or _block("true")(run))
    assert isinstance(out, sandbox.SandboxFailed)
    assert len(rig.made) == 1 and not ran
    assert _host_head(rig) == sync


def test_a_missing_blob_put_back_after_the_patch_still_lands_nothing(rig, monkeypatch):
    from adw_modules import sandbox
    sync = _setup(rig)
    put_back = _lose_blob(rig, "b.txt")
    first = rig._patch_out

    def wrapped():
        try:
            return first()
        finally:
            put_back()
    monkeypatch.setattr(rig, "_patch_out", wrapped)

    def plant():
        (rig.vm_tree / "b.txt").unlink()
        (rig.vm_tree / "app.py").write_text("OK = 0\n")
    _as_builder(rig, plant)
    out = _chain(rig, CHECK)
    assert isinstance(out, sandbox.SandboxFailed)
    assert _host_head(rig) == sync


def test_skip_worktree_bytes_reach_verify_and_nothing_lands(rig):
    sync = _setup(rig)

    def plant():
        assert _in_build(rig, 'printf "OK = 0\\n" > app.py && git add app.py && '
                              'git update-index --skip-worktree app.py') == 0
        (rig.vm_tree / "app.py").write_text(APP)
    _as_builder(rig, plant)
    out = _chain(rig, CHECK)
    assert not isinstance(out, Exception) and out.passed is False
    assert _host_head(rig) == sync


def test_a_file_changed_after_a_green_verify_is_refused_at_the_commit(rig):
    from adw_modules import git_helper, permissions
    sync = _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed
    (rig.vm_tree / "feat.txt").write_text("changed after\n")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_)
    assert (rig.run_.session_dir / "sandbox-refused.bundle").is_file()
    assert _host_head(rig) == sync


@pytest.mark.parametrize("then_commit", [False, True])
def test_a_bad_commit_then_one_that_restores_the_verified_tree_lands_nothing(rig, then_commit):
    from adw_modules import git_helper, permissions
    sync = _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed
    tree = rig.vm_tree
    (tree / "app.py").write_text("OK = 0\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "X")
    (tree / "app.py").write_text(APP)
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "Y")
    if then_commit:
        with pytest.raises(RuntimeError):
            git_helper.commit_all("build", run=rig.run_)
    with pytest.raises(permissions.PermissionBreach):
        rig.close(stop=True)
    assert _host_head(rig) == sync


def test_a_synced_commit_of_the_verified_tree_does_not_end_the_check(rig):
    from adw_modules import git_helper, permissions
    _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed
    _git(rig.vm_tree, "add", "-A")
    assert rig.run(["git", "commit", "-qm", "x"], cwd=rig.host_tree).returncode == 0
    landed = _host_head(rig)
    assert landed == rig.synced
    assert _git(rig.host_tree, "rev-parse", "HEAD^{tree}").strip() == rig._verified != ""
    (rig.vm_tree / "feat.txt").write_text("changed after\n")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_)
    assert _host_head(rig) == landed


def test_a_bad_commit_made_before_the_round_is_refused(rig):
    from adw_modules import git_helper, permissions
    sync = _setup(rig)
    tree = rig.vm_tree
    (tree / "app.py").write_text("OK = 0\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "X")
    (tree / "app.py").write_text(APP)
    assert _verify(rig, CHECK).passed
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_)
    assert _host_head(rig) == sync


# --- the rule's edges ---------------------------------------------------------

def test_a_builder_that_commits_during_its_own_call_lands_one_commit_of_the_verified_tree(rig):
    from adw_modules import git_helper
    sync = _setup(rig)

    def plant():
        (rig.vm_tree / "feat.txt").write_text("v1\n")
        _git(rig.vm_tree, "add", "-A")
        _git(rig.vm_tree, "commit", "-qm", "own")
    _as_builder(rig, plant)
    assert _git(rig.vm_tree, "rev-parse", "HEAD").strip() == sync   # the guards undid it
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("build", run=rig.run_)
    assert _git(rig.host_tree, "rev-list", "--count", f"{sync}..HEAD").strip() == "1"
    assert _git(rig.host_tree, "show", "HEAD:feat.txt") == "v1\n"


@pytest.mark.parametrize("second", [False, True])
def test_a_path_in_wrote_may_differ_from_the_verified_tree_but_a_second_one_may_not(rig, second):
    from adw_modules import git_helper, permissions
    _setup(rig)
    _as_builder(rig, lambda: ((rig.vm_tree / "feat.txt").write_text("v1\n"),
                              (rig.vm_tree / "other.txt").write_text("o1\n")))
    assert _verify(rig, CHECK).passed
    before = _host_head(rig)
    (rig.vm_tree / "feat.txt").write_text("v2\n")
    if not second:
        git_helper.commit_all("build", run=rig.run_, wrote=["feat.txt"])
        assert _git(rig.host_tree, "show", "HEAD:feat.txt") == "v2\n"
        return
    (rig.vm_tree / "other.txt").write_text("o2\n")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_, wrote=["feat.txt"])
    assert _host_head(rig) == before


def test_an_only_commit_before_any_verify_lands(rig):
    from adw_modules import git_helper
    _setup(rig)
    _pending(rig)
    before = _host_head(rig)
    git_helper.commit_all("plan", only=["feat.txt"], run=rig.run_)
    assert _host_head(rig) != before and rig._verified == ""


def test_after_the_chains_own_commit_the_check_ends_and_a_later_commit_lands(rig):
    from adw_modules import git_helper
    _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed and rig._verified
    git_helper.commit_all("build", run=rig.run_)
    assert rig._verified == ""
    rig.run_.ran.append(next(a for a in rig.run_.cfg.agents if a.name == "documenter"))
    (rig.vm_tree / "docs").mkdir(exist_ok=True)
    (rig.vm_tree / "docs" / "x.md").write_text("doc\n")
    git_helper.commit_all("docs", run=rig.run_)
    assert _git(rig.host_tree, "show", "HEAD:docs/x.md") == "doc\n"


def test_opening_a_new_round_forgets_the_earlier_green(rig):
    _setup(rig)
    assert _verify(rig, "true").passed and rig._verified
    assert _verify(rig, "false").passed is False
    assert rig._verified == ""


def test_a_verify_round_moves_nothing_and_reads_no_head_from_the_build_vm(rig):
    _setup(rig)
    _pending(rig)
    host, synced, vm = _host_head(rig), rig.synced, _git(rig.vm_tree, "rev-parse", "HEAD")
    seen = len(rig.made[0].calls)
    assert _verify(rig, CHECK).passed
    assert (_host_head(rig), rig.synced, _git(rig.vm_tree, "rev-parse", "HEAD")) == (host, synced, vm)
    for call in rig.made[0].calls[seen:]:
        argv = [str(a) for a in call["argv"]]
        assert not any(argv[i:i + 2] == ["rev-parse", "HEAD"] for i in range(len(argv))), argv


def _edit(rig):
    (rig.vm_tree / "b.txt").write_text("edited\n")


def _rename(rig):
    _git(rig.vm_tree, "mv", "b.txt", "d.txt")


def _delete(rig):
    (rig.vm_tree / "b.txt").unlink()


def _binary(rig):
    (rig.vm_tree / "c.bin").write_bytes(b"\x00\xff\xfe\x00bin")


@pytest.mark.parametrize("change", [_edit, _rename, _delete, _binary])
def test_a_real_run_still_verifies_and_lands_with_the_verified_tree(rig, change):
    from adw_modules import git_helper
    _setup(rig)
    _as_builder(rig, lambda: change(rig))
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("build", run=rig.run_)
    shown = [p for n, p in rig.events if n == "sandbox_verify"][-1]["tree"]
    assert _git(rig.host_tree, "rev-parse", "HEAD^{tree}").startswith(shown)


# --- the harms ----------------------------------------------------------------

def test_the_verify_box_answers_the_read_calls(rig):
    from adw_modules import sandbox
    _setup(rig)
    _pending(rig)
    got: list = []

    def base(run):
        box = sandbox.current()
        path = run.repo_root / "feat.txt"
        got.append((box.read_text(path), box.exists(path), box.is_file(path), box.size(path)))
        return _block("true")(run)
    assert _verify(rig, base=base).passed
    assert got == [("v1\n", True, True, 3)]


def test_a_base_that_says_passed_with_a_failure_listed_fails_verify():
    from adw_modules import plan_checks, sandbox
    from adw_modules.data_types import QualityResult
    sandbox.use(sandbox.LOCAL)
    result = plan_checks.verify(
        types.SimpleNamespace(), None,
        base=lambda run: QualityResult(passed=True, checks=[], failures=["x"]))
    assert result.passed is False

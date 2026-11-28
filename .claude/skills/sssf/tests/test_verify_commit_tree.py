"""A commit under a VM holds only the tree verify passed (run 3d9f277f).

Every plant here is made in the build VM, inside the guard calls `agents.execute`
makes around an agent (`_as_builder`), or outside them, as a process the agent left
running would. Stand-ins are those of test_verify_fresh_vm; the config is the one
SSSF ships.
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
    wrote = {"feat.txt": git_helper.blob_id("v2\n")}
    if not second:
        git_helper.commit_all("build", run=rig.run_, wrote=wrote)
        assert _git(rig.host_tree, "show", "HEAD:feat.txt") == "v2\n"
        return
    (rig.vm_tree / "other.txt").write_text("o2\n")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_, wrote=wrote)
    assert _host_head(rig) == before


@pytest.mark.parametrize("what", ["v3", "deleted"])
def test_a_wrote_path_with_another_blob_or_none_is_refused(rig, what):
    from adw_modules import git_helper, permissions
    _setup(rig)
    _as_builder(rig, lambda: (rig.vm_tree / "b.txt").write_text("v1\n"))
    assert _verify(rig, CHECK).passed
    before = _host_head(rig)
    if what == "v3":
        (rig.vm_tree / "b.txt").write_text("v3\n")
    else:
        (rig.vm_tree / "b.txt").unlink()
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_, wrote={"b.txt": git_helper.blob_id("v2\n")})
    assert _host_head(rig) == before


def test_an_only_commit_before_any_verify_lands(rig):
    from adw_modules import git_helper
    _setup(rig)
    _pending(rig)
    before = _host_head(rig)
    git_helper.commit_all("plan", only=["feat.txt"], run=rig.run_)
    assert _host_head(rig) != before and rig._verified is None


@pytest.mark.parametrize("ended", [False, True])
def test_after_the_chains_own_commit_the_binding_is_its_tree_until_the_chain_ends_it(rig, ended):
    from adw_modules import git_helper, permissions, sandbox
    _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed and rig._verified
    git_helper.commit_all("build", run=rig.run_)
    assert rig._verified == _git(rig.host_tree, "rev-parse", "HEAD^{tree}").strip()
    if ended:
        sandbox.verify_done()
        assert rig._verified is None and rig.run_.ran == []
    rig.run_.ran.append(next(a for a in rig.run_.cfg.agents if a.name == "documenter"))
    (rig.vm_tree / "docs").mkdir(exist_ok=True)
    (rig.vm_tree / "docs" / "x.md").write_text("doc\n")
    if not ended:
        before = _host_head(rig)
        with pytest.raises(permissions.PermissionBreach):
            git_helper.commit_all("docs", run=rig.run_)
        assert _host_head(rig) == before
        return
    git_helper.commit_all("docs", run=rig.run_)
    assert _git(rig.host_tree, "show", "HEAD:docs/x.md") == "doc\n"


def test_a_red_round_keeps_the_earlier_green(rig):
    _setup(rig)
    assert _verify(rig, "true").passed and rig._verified
    green = rig._verified
    assert _verify(rig, "false").passed is False
    assert rig._verified == green


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


def _attrs(rig):
    (rig.vm_tree / ".gitattributes").write_text("*.txt text\n")      # changes no file's bytes


@pytest.mark.parametrize("change", [_edit, _rename, _delete, _binary, _attrs])
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


# --- the binding lasts until the chain ends it (run 1befc519) ------------------

BAD = "OK = 0\n"


def _left_commit(rig, text=BAD, name="app.py"):
    """A process the builder left running commits in the build VM, outside any agent call.
    The builder's call is on record, so the path rules alone would let the file through."""
    builder = next(a for a in rig.run_.cfg.agents if a.name == "builder")
    if builder not in rig.run_.ran:
        rig.run_.ran.append(builder)
    (rig.vm_tree / name).write_text(text)
    _git(rig.vm_tree, "add", "-A")
    _git(rig.vm_tree, "commit", "-qm", "left")


def _refused_bundle(rig) -> Path:
    return Path(rig.run_.session_dir) / "sandbox-refused.bundle"


def _documenter(rig):
    rig.run_.ran.append(next(a for a in rig.run_.cfg.agents if a.name == "documenter"))


def _fail_patch(rig, monkeypatch):
    from adw_modules import sandbox

    def broken():
        raise sandbox.SandboxFailed("git failed in the build VM")
    monkeypatch.setattr(rig, "_patch_out", broken)


def _failed_round(rig):
    from adw_modules import sandbox
    with pytest.raises(sandbox.SandboxFailed):
        _verify(rig, "true")


def test_a_tickets_run_whose_review_fix_changes_nothing_lands_its_docs_commit(rig):
    from adw_modules import git_helper, sandbox
    _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("t01", run=rig.run_)
    assert _verify(rig, CHECK).passed            # the review round: nothing changed
    sandbox.verify_done()
    _documenter(rig)
    (rig.vm_tree / "app_docs").mkdir(exist_ok=True)
    (rig.vm_tree / "app_docs" / "x.md").write_text("doc\n")
    git_helper.commit_all("docs", run=rig.run_)
    assert _git(rig.host_tree, "show", "HEAD:app_docs/x.md") == "doc\n"


@pytest.mark.parametrize("where", ["sync", "close"])
@pytest.mark.parametrize("second", ["red", "failed"])
def test_a_bad_commit_after_a_green_round_and_a_red_or_failed_one_lands_nothing(
        rig, monkeypatch, second, where):
    from adw_modules import permissions
    sync = _setup(rig)
    assert _verify(rig, "true").passed
    if second == "red":
        assert _verify(rig, "false").passed is False
    else:
        _fail_patch(rig, monkeypatch)
        _failed_round(rig)
    _left_commit(rig)
    with pytest.raises(permissions.PermissionBreach):
        rig.commits_out() if where == "sync" else rig.close(stop=True)
    assert _host_head(rig) == sync
    assert _refused_bundle(rig).exists()


@pytest.mark.parametrize("second", ["red", "failed"])
def test_nothing_lands_before_the_first_green_round(rig, monkeypatch, second):
    from adw_modules import permissions
    sync = _setup(rig)
    if second == "red":
        assert _verify(rig, "false").passed is False
    else:
        _fail_patch(rig, monkeypatch)
        _failed_round(rig)
    assert rig._verified == ""
    (rig.vm_tree / "docs").mkdir(exist_ok=True)
    _left_commit(rig, "doc\n", "docs/x.md")      # the sync's tree plus a docs file
    with pytest.raises(permissions.PermissionBreach):
        rig.close(stop=True)
    assert _host_head(rig) == sync


# Round 2's plants: the build VM's word about its own patch decides nothing.

@pytest.mark.parametrize("forge", ["config", "patch"])
def test_a_forged_empty_patch_still_binds_the_last_sync(rig, monkeypatch, forge):
    from adw_modules import git_helper, permissions
    sync = _setup(rig)
    (rig.vm_tree / "app.py").write_text(BAD)
    if forge == "config":
        _git(rig.vm_tree, "config", "diff.external", "true")
    else:
        # stand-in for any way a left process empties the patch
        monkeypatch.setattr(rig, "_patch_out", lambda: b"")
    result = _verify(rig, "true")
    assert result.passed and rig._verified is not None
    if forge == "config" and rig._verified != _git(rig.host_tree, "rev-parse", "HEAD^{tree}").strip():
        pytest.skip("diff.external does not forge the patch in this stand-in")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_)
    assert _host_head(rig) == sync


def test_good_bytes_during_the_patch_and_bad_ones_after_land_nothing(rig, monkeypatch):
    from adw_modules import git_helper, permissions
    sync = _setup(rig)
    real = rig._patch_out
    app = rig.vm_tree / "app.py"
    app.write_text(BAD)

    def wrapped():
        app.write_text(APP)
        try:
            return real()
        finally:
            app.write_text(BAD)
    monkeypatch.setattr(rig, "_patch_out", wrapped)
    assert _verify(rig, "true").passed
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("build", run=rig.run_)
    assert _host_head(rig) == sync


@pytest.mark.parametrize("where", ["verify_done", "close"])
def test_a_bad_commit_then_the_good_bytes_back_lands_nothing(rig, where):
    from adw_modules import permissions, sandbox
    sync = _setup(rig)
    _left_commit(rig)
    (rig.vm_tree / "app.py").write_text(APP)
    assert _verify(rig, "true").passed
    with pytest.raises(permissions.PermissionBreach):
        sandbox.verify_done() if where == "verify_done" else rig.close(stop=True)
    assert _host_head(rig) == sync


def _forged_empty(rig, monkeypatch):
    monkeypatch.setattr(rig, "_patch_out", lambda: b"")


def _forged_config(rig, monkeypatch):
    _git(rig.vm_tree, "config", "diff.external", "true")


def _failed(rig, monkeypatch):
    _fail_patch(rig, monkeypatch)


def _head_moved(rig, monkeypatch):
    _left_commit(rig, "OK = 2\n")


@pytest.mark.parametrize("report", [None, _forged_empty, _failed, _forged_config, _head_moved])
def test_what_the_build_vm_reports_never_turns_the_check_off(rig, monkeypatch, report):
    from adw_modules import permissions, sandbox
    sync = _setup(rig)
    _pending(rig)
    if report is not None:
        report(rig, monkeypatch)
    try:
        _verify(rig, CHECK)
    except sandbox.SandboxFailed:
        assert report is _failed
    assert rig._verified is not None
    _left_commit(rig)
    with pytest.raises(permissions.PermissionBreach):
        rig.commits_out()
    assert _host_head(rig) == sync


@pytest.mark.parametrize("event", ["sync", "red", "failed", "chain_commit"])
def test_only_the_chains_call_ends_the_binding(rig, monkeypatch, event):
    from adw_modules import git_helper, sandbox
    _setup(rig)
    _pending(rig)
    assert _verify(rig, CHECK).passed
    if event == "sync":
        _git(rig.vm_tree, "add", "-A")
        assert rig.run(["git", "commit", "-qm", "x"], cwd=rig.host_tree).returncode == 0
    elif event == "red":
        assert _verify(rig, "false").passed is False
    elif event == "failed":
        _fail_patch(rig, monkeypatch)
        _failed_round(rig)
    else:
        git_helper.commit_all("build", run=rig.run_)
    assert rig._verified is not None
    sandbox.verify_done()
    assert rig._verified is None


def test_verify_done_with_a_bad_commit_in_the_vm_refuses_and_stays_bound(rig):
    from adw_modules import permissions, sandbox
    sync = _setup(rig)
    assert _verify(rig, "true").passed
    _left_commit(rig)
    with pytest.raises(permissions.PermissionBreach):
        sandbox.verify_done()
    assert rig._verified is not None and _host_head(rig) == sync
    with pytest.raises(permissions.PermissionBreach):
        rig.close(stop=True)
    assert _host_head(rig) == sync


# Ordinary runs, per chain: each chain's host calls in its order.

def _commit_count(rig, since) -> int:
    return int(_git(rig.host_tree, "rev-list", "--count", f"{since}..HEAD").strip())


def _chain_simple_sdlc(rig, change):
    from adw_modules import git_helper, sandbox
    agents = {a.name: a for a in rig.run_.cfg.agents}
    rig.run_.ran.append(agents["planner"])
    (rig.vm_tree / "specs").mkdir(exist_ok=True)
    (rig.vm_tree / "specs" / "s.md").write_text("spec\n")
    git_helper.commit_all("plan", ["specs/s.md"], run=rig.run_)
    _as_builder(rig, lambda: change(rig))
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("build", run=rig.run_)
    sandbox.verify_done()
    _documenter(rig)
    (rig.vm_tree / "app_docs").mkdir(exist_ok=True)
    (rig.vm_tree / "app_docs" / "x.md").write_text("doc\n")
    git_helper.commit_all("docs", run=rig.run_)
    return 3


def _chain_tickets(rig, change, fix):
    from adw_modules import git_helper, sandbox
    count = 0
    for n, work in enumerate([lambda: change(rig), lambda: (rig.vm_tree / "t2.txt").write_text("t2\n")]):
        _as_builder(rig, work)
        assert _verify(rig, CHECK).passed
        mark = f"issues/{n}.md"
        (rig.vm_tree / "issues").mkdir(exist_ok=True)
        (rig.vm_tree / mark).write_text("resolved\n")
        git_helper.commit_all(f"t{n}", run=rig.run_, wrote={mark: git_helper.blob_id("resolved\n")})
        count += 1
    if fix:
        _as_builder(rig, lambda: (rig.vm_tree / "rf.txt").write_text("rf\n"))
    assert _verify(rig, CHECK).passed
    if fix:
        git_helper.commit_all("review fix", run=rig.run_)
        count += 1
    sandbox.verify_done()
    _documenter(rig)
    (rig.vm_tree / "app_docs").mkdir(exist_ok=True)
    (rig.vm_tree / "app_docs" / "x.md").write_text("doc\n")
    git_helper.commit_all("docs", run=rig.run_)
    return count + 1


def _chain_build_test(rig, change):
    from adw_modules import git_helper
    _as_builder(rig, lambda: change(rig))
    assert _verify(rig, "false").passed is False
    _as_builder(rig, lambda: (rig.vm_tree / "fix.txt").write_text("fix\n"))
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("build", run=rig.run_)
    return 1


def _chain_guard(rig, change):
    from adw_modules import git_helper
    rig.run_.ran.append(next(a for a in rig.run_.cfg.agents if a.name == "planner"))
    (rig.vm_tree / "specs").mkdir(exist_ok=True)
    (rig.vm_tree / "specs" / "s.md").write_text("spec\n")
    _git(rig.vm_tree, "add", "specs/s.md")
    assert rig.run(["git", "commit", "-qm", "spec"], cwd=rig.host_tree).returncode == 0
    _as_builder(rig, lambda: change(rig))
    assert _verify(rig, CHECK).passed
    assert rig.run(["git", "commit-tree", "-m", "snap", "HEAD^{tree}"], cwd=rig.host_tree).returncode == 0
    assert _verify(rig, CHECK).passed
    git_helper.commit_all("build", run=rig.run_)
    return 2


def _chain_quality(rig, change):
    assert _verify(rig, CHECK).passed
    return 0


CHAINS = {
    "simple_sdlc": _chain_simple_sdlc,
    "tickets_fix": lambda rig, change: _chain_tickets(rig, change, True),
    "tickets_nofix": lambda rig, change: _chain_tickets(rig, change, False),
    "build_test": _chain_build_test,
    "guard": _chain_guard,
    "quality": _chain_quality,
}


def _attrs(rig):
    (rig.vm_tree / ".gitattributes").write_text("*.txt text\n")      # changes no file's bytes


@pytest.mark.parametrize("change", [_edit, _rename, _delete, _binary, _attrs])
@pytest.mark.parametrize("chain", list(CHAINS))
def test_each_chains_ordinary_run_lands_every_commit(rig, chain, change):
    sync = _setup(rig)
    expected = CHAINS[chain](rig, change)
    rig.close(stop=True)
    assert _commit_count(rig, sync) == expected
    assert not _refused_bundle(rig).exists()


def test_the_chains_end_the_binding_before_the_documenter():
    import re
    chains = SKILL / "templates" / "adws"
    for name in ("adw_simple_sdlc.py", "adw_tickets.py"):
        text = (chains / name).read_text()
        calls = [m.start() for m in re.finditer(r"^\s+sandbox\.verify_done\(\)", text, re.M)]
        assert len(calls) == 1, name
        call = calls[0]
        assert call > text.rindex("plan_checks.verify("), name
        assert call < text.index('owner="documenter"'), name
        assert call > max(text.rfind('name="commit_build"'), text.rfind('name="commit_review_fix"')), name
    for path in sorted(chains.glob("*.py")):
        if path.name not in ("adw_simple_sdlc.py", "adw_tickets.py"):
            assert not re.search(r"^\s+sandbox\.verify_done\(\)", path.read_text(), re.M), path.name


# --- the verify VM holds a checkout of the verified tree ------------------------

def _clean_checkout(rig, tmp_path, tree: str) -> Path:
    out = tmp_path / "clean"
    out.mkdir()
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "clean-index")}
    subprocess.run(["git", f"--git-dir={rig.host_tree}/.git", f"--work-tree={out}", "read-tree",
                    "-u", "--reset", tree], check=True, env=env, capture_output=True)
    return out


def _plant_ident(rig):
    (rig.vm_tree / ".gitattributes").write_text("app.py ident\n")
    with (rig.vm_tree / "app.py").open("a") as f:
        f.write("# $Id$\n")


def _plant_crlf(rig):
    (rig.vm_tree / "run.sh").write_text("echo hi\n")
    (rig.vm_tree / ".gitattributes").write_text("*.sh eol=crlf\n")


def _plant_crlf_untouched(rig):
    (rig.vm_tree / ".gitattributes").write_text("*.sh eol=crlf\n")


_IDENT_CHECK = "grep -qx '# \\$Id\\$' app.py"
_CRLF_CHECK = 'test "$(wc -c < run.sh)" = 8'


@pytest.mark.parametrize("plant, check", [(_plant_ident, _IDENT_CHECK), (_plant_crlf, _CRLF_CHECK),
                                          (_plant_crlf_untouched, _CRLF_CHECK)])
def test_verify_checks_a_checkout_under_the_patched_gitattributes(rig, tmp_path, plant, check):
    if plant is _plant_crlf_untouched:
        (rig.vm_tree / "run.sh").write_text("echo hi\n")      # committed with the sync
    _setup(rig)
    _as_builder(rig, lambda: plant(rig))
    tree = rig._judge_patch(rig._patch_out())
    clean = _clean_checkout(rig, tmp_path, tree)
    assert subprocess.run(["bash", "-c", check], cwd=clean).returncode != 0
    assert _verify(rig, check).passed is False
    seen: list = []
    assert _verify(rig, check, base=_block(check, seen)).passed is False


def test_a_verify_vm_whose_tree_is_not_the_hosts_fails_the_round(rig, monkeypatch):
    from adw_modules import sandbox
    sync = _setup(rig)
    _pending(rig)
    monkeypatch.setattr(rig, "_judge_patch", lambda patch: _git(rig.host_tree, "rev-parse", "HEAD^{tree}").strip())
    ran: list = []
    with pytest.raises(sandbox.SandboxFailed):
        _verify(rig, CHECK, base=_block("true", ran))
    assert ran == [] and rig.made[-1].destroyed and _host_head(rig) == sync


def test_a_last_sync_holding_a_crlf_blob_under_a_text_attribute_still_verifies(rig):
    _setup(rig)
    rig.run_.ran.append(next(a for a in rig.run_.cfg.agents if a.name == "builder"))
    (rig.vm_tree / "w.py").write_bytes(b"a = 1\r\n")
    (rig.vm_tree / ".gitattributes").write_text("*.py text\n")
    blob = _git(rig.vm_tree, "hash-object", "-w", "--no-filters", "w.py").strip()
    _git(rig.vm_tree, "update-index", "--add", "--cacheinfo", f"100644,{blob},w.py")
    _git(rig.vm_tree, "add", ".gitattributes")
    assert rig.run(["git", "commit", "-qm", "crlf"], cwd=rig.host_tree).returncode == 0   # syncs
    assert _git(rig.host_tree, "rev-parse", "HEAD:w.py").strip() == blob
    _pending(rig)
    assert _verify(rig, CHECK).passed        # the fresh clone's `git rm` must not stop on w.py


def test_the_verify_vm_keeps_head_and_index_at_the_last_sync(rig):
    sync = _setup(rig)
    _pending(rig)
    seen: list = []
    command = "git rev-parse HEAD; git status --porcelain"
    assert _verify(rig, CHECK, base=_block(command, seen)).passed
    head, status = seen[-1][1].split("\n", 1)
    assert head == sync and status.strip() == "?? feat.txt"


# --- the chain's own file is bound to its blob ------------------------------------

_TICKET = "# 01: A\n\n**Status:** ready-for-agent\n\nAccept: the page loads\n"


def _ticket_round(rig):
    from adw_modules.data_types import Ticket
    _setup(rig)
    _as_builder(rig, lambda: ((rig.vm_tree / "issues").mkdir(),
                              (rig.vm_tree / "issues" / "01-a.md").write_text(_TICKET)))
    assert _verify(rig, CHECK).passed
    return Ticket(number="01", slug="01-a", path="issues/01-a.md", title="A")


def test_a_ticket_rewritten_before_the_mark_lands_as_the_green_tree_has_it(rig):
    from adw_modules import git_helper, tickets
    ticket = _ticket_round(rig)
    (rig.vm_tree / ticket.path).write_text(_TICKET.replace("page loads", "anything passes"))
    wrote = tickets.mark_done(ticket)
    git_helper.commit_all("t01", run=rig.run_, wrote=wrote)
    assert _git(rig.host_tree, "show", f"HEAD:{ticket.path}") == _TICKET.replace("ready-for-agent", "resolved")


@pytest.mark.parametrize("how", ["text", "symlink"])
def test_a_ticket_rewritten_after_the_mark_is_refused(rig, how):
    from adw_modules import git_helper, permissions, tickets
    ticket = _ticket_round(rig)
    sync = _host_head(rig)
    wrote = tickets.mark_done(ticket)
    path = rig.vm_tree / ticket.path
    if how == "text":
        path.write_text(_TICKET.replace("page loads", "anything passes"))
    else:
        path.unlink()
        path.symlink_to("../app.py")
    with pytest.raises(permissions.PermissionBreach):
        git_helper.commit_all("t01", run=rig.run_, wrote=wrote)
    assert _host_head(rig) == sync and _refused_bundle(rig).exists()


def test_wrote_as_a_list_is_a_type_error_and_stages_nothing(rig):
    from adw_modules import git_helper
    _setup(rig)
    _pending(rig)
    with pytest.raises(TypeError):
        git_helper.commit_all("x", run=rig.run_, wrote=["feat.txt"])
    assert _git(rig.vm_tree, "diff", "--cached", "--name-only").strip() == ""

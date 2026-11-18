"""SSSF's own git runs no program during the snapshot, the commit undo and the
rollback, and never recurses into a submodule.

Each marker test plants a program that touches a marker file behind one key
(filter, hook, fsmonitor, a submodule's config), runs the real
permissions.snapshot / undo_commits / enforce, and asserts the marker doesn't
exist. The config is the roster SSSF ships (templates/sssf.config.yaml). The
fake agent's own git can fire a marker (fresh files are stat-racy), so each test
plants the program as its last step and clears the marker before SSSF's git.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, permissions

CFG = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
BUILDER = next(a for a in CFG.agents if a.name == "scout")      # writes: [] (read-only)
ENV = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
       "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd, env=ENV,
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done.stdout.strip()


def write(path: Path, text: str, old: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if old:     # a file as old as the checkout, so git records it as unchanged
        os.utime(path, (1_000_000_000, 1_000_000_000))


def run_for(repo: Path):
    return SimpleNamespace(cfg=CFG, repo_root=repo, adw_id="r", console=None, tracer=None)


def touch_cmd(marker: Path) -> str:
    return f"touch {marker}; cat"


def new_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    write(repo / "a.dat", "one\n")
    write(repo / "justfile", "ok:\n")
    write(repo / ".gitattributes", "*.dat filter=h\njustfile filter=h\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    return repo


def set_filter(repo: Path, marker: Path, name="h", keys=("clean", "smudge")) -> None:
    cfg = repo / ".git" / "config"
    for key in keys:
        git(repo, "config", "--file", str(cfg), f"filter.{name}.{key}", touch_cmd(marker))


def sandboxed():
    return permissions.sandbox.current()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A HOME of the test's own, so the guard saves a test ~/.gitconfig."""
    path = tmp_path / "home"
    path.mkdir()
    (path / ".gitconfig").write_text("[user]\n\tname = t\n")
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return path


@pytest.fixture
def marker(tmp_path):
    return tmp_path / "marker"


@pytest.mark.parametrize("name", ["h", "a.b", "a=b", "with space"])
@pytest.mark.parametrize("key", ["clean", "process"])
def test_snapshot_runs_no_filter(tmp_path, marker, name, key):
    repo = new_repo(tmp_path)
    set_filter(repo, marker, name, (key,))
    git(repo, "config", f"filter.{name}.required", "true")
    (repo / "a.dat").write_text("one\n")        # touched, unchanged: git would run clean
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


def test_the_filter_setup_bites_with_plain_git(tmp_path, marker):
    repo = new_repo(tmp_path)
    set_filter(repo, marker)
    (repo / "a.dat").write_text("one\n")
    subprocess.run(["git", "diff", "HEAD", "--name-status"], cwd=repo, env=ENV, capture_output=True)
    assert marker.exists()


def test_a_rollback_runs_no_smudge_filter_and_puts_the_bytes_back(tmp_path, marker):
    repo = new_repo(tmp_path)
    set_filter(repo, marker)
    before = permissions.snapshot(run_for(repo))
    marker.unlink(missing_ok=True)
    (repo / "justfile").write_text("evil:\n")
    with pytest.raises(permissions.PermissionBreach, match="justfile"):
        permissions.enforce(run_for(repo), None, BUILDER, before)
    assert not marker.exists()
    assert (repo / "justfile").read_text() == "ok:\n"       # `cat` keeps the bytes


@pytest.mark.parametrize("hook", ["post-index-change", "post-checkout", "reference-transaction"])
def test_the_undo_and_rollback_run_no_hook(tmp_path, marker, hook):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    before = permissions.snapshot(run)
    head = permissions.head(run)
    (repo / "justfile").write_text("evil:\n")
    git(repo, "commit", "-qam", "agent commit")
    hooks = repo / ".git" / "hooks"
    (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}\n")
    (hooks / hook).chmod(0o755)
    marker.unlink(missing_ok=True)
    assert permissions.undo_commits(run, head)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, BUILDER, before, head)
    assert not marker.exists()
    assert (repo / "justfile").read_text() == "ok:\n"


def test_the_engineers_fsmonitor_runs_nothing(tmp_path, marker):
    repo = new_repo(tmp_path)
    fs = tmp_path / "fsm.sh"
    fs.write_text(f"#!/bin/sh\ntouch {marker}\n")
    fs.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(fs))
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


def test_a_pager_runs_nothing(tmp_path, marker):
    repo = new_repo(tmp_path)
    git(repo, "config", "core.pager", f"touch {marker}")
    git(repo, "config", "pager.diff", f"touch {marker}")
    (repo / "a.dat").write_text("two\n")
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


def test_the_hooks_folder_is_still_watched(tmp_path):
    repo = new_repo(tmp_path)
    folder = tmp_path / "myhooks"
    folder.mkdir()
    git(repo, "config", "core.hooksPath", str(folder))
    walked, paths, kinds = permissions._watched(run_for(repo))
    assert str(folder) in paths and "/dev/null" not in paths


def test_a_filter_with_cat_gives_the_same_bytes_as_no_filter(tmp_path):
    repo = new_repo(tmp_path)
    git(repo, "config", "filter.h.clean", "cat")
    git(repo, "config", "filter.h.smudge", "cat")
    before = permissions.snapshot(run_for(repo))
    (repo / "justfile").write_text("evil:\n")
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run_for(repo), None, BUILDER, before)
    assert (repo / "justfile").read_text() == "ok:\n"


# ── submodules ───────────────────────────────────────────────────────────────

def sub_world(tmp_path: Path) -> Path:
    src = tmp_path / "subsrc"
    src.mkdir()
    git(src, "init", "-qb", "main")
    write(src / "f.dat", "f\n")
    write(src / "g.dat", "g\n")
    write(src / ".gitattributes", "*.dat filter=h\n")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "sub")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    write(repo / "justfile", "ok:\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")
    git(repo, "submodule", "add", "-q", str(src), "sub")
    git(repo, "commit", "-qm", "add sub")
    return repo


def plant_sub_filters(repo: Path, marker: Path) -> None:
    cfg = repo / ".git" / "modules" / "sub" / "config"
    for key in ("clean", "smudge"):
        git(repo, "config", "--file", str(cfg), f"filter.h.{key}", touch_cmd(marker))


def test_a_filter_in_a_submodules_config_never_runs(tmp_path, marker):
    repo = sub_world(tmp_path)
    plant_sub_filters(repo, marker)
    (repo / "sub" / "g.dat").write_text("g\n")      # touched, unchanged
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


def test_a_nested_submodules_filter_never_runs(tmp_path, marker):
    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-qb", "main")
    write(inner / "n.dat", "n\n")
    write(inner / ".gitattributes", "*.dat filter=deep\n")
    git(inner, "add", "-A")
    git(inner, "commit", "-qm", "inner")
    repo = sub_world(tmp_path)
    git(repo / "sub", "submodule", "add", "-q", str(inner), "inner")
    git(repo / "sub", "commit", "-qm", "nest")
    git(repo, "commit", "-qam", "bump")
    nested = repo / ".git" / "modules" / "sub" / "modules" / "inner" / "config"
    git(repo, "config", "--file", str(nested), "filter.deep.clean", touch_cmd(marker))
    (repo / "sub" / "inner" / "n.dat").write_text("n\n")
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


def test_the_submodule_recurse_reproduction(tmp_path, marker):
    """Spec review 2: submodule.recurse=true, `git rm --cached sub`, a filter in
    the submodule's config. No marker; the gitlink is back; sub/f.dat stays deleted."""
    repo = sub_world(tmp_path)
    git(repo, "config", "submodule.recurse", "true")
    run = run_for(repo)
    before = permissions.snapshot(run)
    head = permissions.head(run)
    (repo / "sub" / "f.dat").unlink()
    git(repo, "rm", "-q", "--cached", "sub")
    (repo / "justfile").write_text("evil:\n")
    plant_sub_filters(repo, marker)
    marker.unlink(missing_ok=True)
    permissions.undo_commits(run, head)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, BUILDER, before, head)
    assert not marker.exists()
    assert (repo / "justfile").read_text() == "ok:\n"
    assert git(repo, "ls-files", "-s", "sub").startswith("160000")
    assert not (repo / "sub" / "f.dat").exists()
    permissions.snapshot(run)                       # a fresh list: still no marker
    assert not marker.exists()


def test_a_rolled_back_gitlink_leaves_the_submodule_checkout(tmp_path, marker):
    repo = sub_world(tmp_path)
    git(repo, "config", "submodule.recurse", "true")
    run = run_for(repo)
    old = git(repo, "ls-files", "-s", "sub").split()[1]
    before = permissions.snapshot(run)
    head = permissions.head(run)
    write(repo / "sub" / "h.txt", "h\n", old=False)
    git(repo / "sub", "add", "h.txt")
    git(repo / "sub", "commit", "-qm", "inside")
    moved = git(repo / "sub", "rev-parse", "HEAD")
    git(repo, "add", "sub")
    git(repo, "commit", "-qm", "bump")
    plant_sub_filters(repo, marker)
    marker.unlink(missing_ok=True)
    assert permissions.undo_commits(run, head)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, BUILDER, before, head)
    assert not marker.exists()
    assert git(repo, "ls-files", "-s", "sub").split()[1] == old
    assert git(repo / "sub", "rev-parse", "HEAD") == moved       # exception 5


def test_an_untracked_repo_with_a_marker_filter_is_never_entered(tmp_path, marker):
    repo = new_repo(tmp_path)
    stray = repo / "stray"
    stray.mkdir()
    git(stray, "init", "-qb", "main")
    set_filter(stray, marker)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


# ── the split ────────────────────────────────────────────────────────────────

def test_a_locked_hooks_folder_doesnt_stop_git_but_a_changed_config_does(tmp_path):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    guard = permissions.guard_git_dir(run)
    hooks = repo / ".git" / "hooks"
    (hooks / "post-commit").write_text("#!/bin/sh\n")
    hooks.chmod(0o555)
    try:
        with pytest.raises(permissions.PermissionBreach, match="hooks"):
            permissions.restore_git_dir(run, guard)
    finally:
        hooks.chmod(0o755)
    assert guard["restored"] is True

    guard = permissions.guard_git_dir(run)
    config = repo / ".git" / "config"
    config.write_text(config.read_text() + "[x]\n\ty = 1\n")
    repo_git = repo / ".git"
    repo_git.chmod(0o555)
    try:
        with pytest.raises(permissions.PermissionBreach):
            permissions.restore_git_dir(run, guard)
    finally:
        repo_git.chmod(0o755)
    assert guard["restored"] is not True


def test_a_hooks_dir_with_17mb_still_lets_the_rollback_run(tmp_path):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    guard = permissions.guard_git_dir(run)
    hooks = repo / ".git" / "hooks"
    (hooks / "big").write_bytes(os.urandom(17 * 1024 * 1024))
    hooks.chmod(0o555)
    try:
        with pytest.raises(permissions.PermissionBreach):
            permissions.restore_git_dir(run, guard)
    finally:
        hooks.chmod(0o755)
    assert guard["restored"] is True


# ── the docs ─────────────────────────────────────────────────────────────────

# ── git fails closed, and no child starts in a submodule (-5) ────────────────
# No test here puts a long argument in a command it starts, except the ones
# marked host_only, which never run under agentsh (argv_truncated).

def names_config(path: Path, length: int, count: int) -> None:
    """Append `count` filter sections whose names are `length` characters long,
    with the file API (never `git config <long value>`)."""
    with path.open("a") as out:
        for i in range(count):
            out.write(f'[filter "{str(i).ljust(length, "n")}"]\n\tclean = cat\n')


def bypass(tmp_path: Path, length: int, count: int):
    """p1_longname: a nested repo staged as a gitlink, a long-named filter in its
    config, and a protected write. Returns (repo, run, before, head)."""
    repo = new_repo(tmp_path)
    run = run_for(repo)
    before = permissions.snapshot(run)
    head = permissions.head(run)
    sub = repo / "sub"
    sub.mkdir()
    git(sub, "init", "-qb", "main")
    write(sub / "f", "f\n")
    git(sub, "add", "-A")
    git(sub, "commit", "-qm", "sub")
    git(repo, "add", "sub")
    names_config(sub / ".git" / "config", length, count)
    (repo / "justfile").write_text("evil:\n")
    return repo, run, before, head


def check_bypass_closed(repo, run, before, head):
    after = permissions.snapshot(run)
    assert "justfile" in after and "sub" in after
    with pytest.raises(permissions.PermissionBreach, match="unstaged; could not delete") as caught:
        permissions.enforce(run, None, BUILDER, before, head)
    assert "sub" in str(caught.value)
    assert (repo / "justfile").read_text() == "ok:\n"
    assert (repo / "sub" / "f").exists()
    assert "sub" not in git(repo, "ls-files", "-s").split()


@pytest.mark.parametrize("length,count", [(40_000, 1), (200, 300)])
def test_a_long_named_filter_in_a_submodules_config_cant_hide_a_write(tmp_path, length, count):
    check_bypass_closed(*bypass(tmp_path, length, count))


# host_only: passes only outside agentsh, which refuses the command by argv_truncated
# (on code that walks the submodule's config, every git is past 2 MiB; the built code starts only short commands)
@pytest.mark.host_only
def test_a_two_megabyte_filter_list_in_a_submodules_config_cant_hide_a_write(tmp_path):
    check_bypass_closed(*bypass(tmp_path, 2_000, 1_000))


# host_only: passes only outside agentsh, which refuses the command by argv_truncated
@pytest.mark.host_only
@pytest.mark.parametrize("length,count", [(2_000, 1_000), (140_000, 1)])
def test_sssfs_own_git_that_really_cant_start_stops_the_snapshot(tmp_path, length, count):
    repo = new_repo(tmp_path)
    names_config(repo / ".git" / "config", length, count)
    with pytest.raises(permissions.PermissionBreach, match="couldn't start"):
        permissions.snapshot(run_for(repo))


def failing(monkeypatch, sub: str, how: str, nth: int = 1, needle: str = ""):
    """Fail the `nth` call of git subcommand `sub` (or of the `sh` loop), by exit
    128 with stderr `boom`, or by raising OSError(E2BIG) in place of starting it."""
    import errno
    box = sandboxed()
    real, seen = box.run, [0]
    subs = {"rev-parse", "diff", "ls-files", "symbolic-ref", "merge-base", "rev-list", "reset",
            "rm", "config", "check-ignore", "update-index", "checkout"}

    def which(argv):
        if argv[0] == "sh":
            return "sh"
        return next((a for a in argv[argv.index("git"):] if a in subs), "") if "git" in argv else ""

    def run(argv, **kw):
        # The index rebuild's python3 carries the ls-files argv it runs itself.
        if argv[:1] != ["python3"] and which(argv) == sub and needle in " ".join(argv):
            seen[0] += 1
            if seen[0] == nth:
                if how == "oserror":
                    raise OSError(errno.E2BIG, "Argument list too long")
                return subprocess.CompletedProcess(argv, 128, b"", b"boom")
        return real(argv, **kw)
    monkeypatch.setattr(box, "run", run)
    return "boom" if how == "exit" else "Argument list too long"


def test_when_sssfs_own_git_cant_start_the_snapshot_raises(tmp_path, monkeypatch):
    repo = new_repo(tmp_path)
    said = failing(monkeypatch, "diff", "oserror")
    with pytest.raises(permissions.PermissionBreach, match="diff") as caught:
        permissions.snapshot(run_for(repo))
    assert said in str(caught.value) and "couldn't start" in str(caught.value)


@pytest.mark.parametrize("how", ["exit", "oserror"])
@pytest.mark.parametrize("sub,nth", [("config", 1), ("rev-parse", 1), ("diff", 1),
                                     ("ls-files", 1), ("ls-files", 2), ("sh", 1)])
def test_a_failing_git_in_the_snapshot_is_a_breach(tmp_path, monkeypatch, sub, nth, how):
    repo = new_repo(tmp_path)
    write(repo / "u.txt", "untracked\n")
    said = failing(monkeypatch, sub, how, nth)
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.snapshot(run_for(repo))
    text = str(caught.value)
    assert (sub in text or "file-hash" in text) and (said in text or "exited 128" in text)


@pytest.mark.parametrize("how", ["exit", "oserror"])
@pytest.mark.parametrize("sub", ["rev-parse", "symbolic-ref"])
def test_a_failing_head_read_is_a_breach(tmp_path, monkeypatch, sub, how):
    repo = new_repo(tmp_path)
    said = failing(monkeypatch, sub, how)
    with pytest.raises(permissions.PermissionBreach, match=sub) as caught:
        permissions.head(run_for(repo))
    assert said in str(caught.value)


def agent_commit(repo: Path) -> None:
    write(repo / "justfile", "evil:\n", old=False)
    git(repo, "commit", "-qam", "agent")


@pytest.mark.parametrize("how", ["exit", "oserror"])
@pytest.mark.parametrize("sub", ["config", "merge-base", "rev-list", "reset"])
def test_a_failing_git_in_the_undo_is_a_breach(tmp_path, monkeypatch, sub, how):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    head = permissions.head(run)
    agent_commit(repo)
    said = failing(monkeypatch, sub, how)
    with pytest.raises(permissions.PermissionBreach, match=sub) as caught:
        permissions.undo_commits(run, head)
    assert said in str(caught.value)


def rollback_case(tmp_path):
    """A modified protected file and a staged new file, so every rollback command runs."""
    repo = new_repo(tmp_path)
    run = run_for(repo)
    before = permissions.snapshot(run)
    (repo / "justfile").write_text("evil:\n")
    write(repo / "new.txt", "n\n")
    git(repo, "add", "new.txt")
    return repo, run, before


@pytest.mark.parametrize("how", ["exit", "oserror"])
@pytest.mark.parametrize("sub,outcome", [
    ("rm", "new.txt — could not unstage"),
    ("check-ignore", "new.txt — unstaged; couldn't ask git whether it is gitignored"),
    ("checkout", "justfile — could not roll back")])
def test_a_failing_git_in_the_rollback_is_named_in_the_breach(tmp_path, monkeypatch, sub, how, outcome):
    repo, run, before = rollback_case(tmp_path)
    failing(monkeypatch, sub, how, needle="new.txt" if sub != "checkout" else "justfile")
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.enforce(run, None, BUILDER, before)
    assert outcome in str(caught.value)
    if sub == "check-ignore":
        assert (repo / "new.txt").exists()         # not read as "is gitignored"


def test_an_update_index_that_cant_start_is_named_as_could_not_roll_back(tmp_path, monkeypatch):
    repo, run, before = rollback_case(tmp_path)
    failing(monkeypatch, "update-index", "oserror")
    with pytest.raises(permissions.PermissionBreach, match=r"could not roll back \(.*Argument list too long"):
        permissions.enforce(run, None, BUILDER, before)


@pytest.mark.parametrize("how", ["exit", "oserror"])
def test_a_failing_hooks_path_read_stops_the_git_directory_guard(tmp_path, monkeypatch, how):
    repo = new_repo(tmp_path)
    said = failing(monkeypatch, "config", how, needle="core.hooksPath")
    with pytest.raises(permissions.PermissionBreach, match="core.hooksPath") as caught:
        permissions.guard_git_dir(run_for(repo))
    assert said in str(caught.value)


FIRST_COMMIT = r"first commit.*git commit --allow-empty -m init"


def no_commit_repo(tmp_path: Path, setup: str) -> Path:
    """A repo with no commit, set up as run 6ef2f3f0's three review rounds did."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-qb", "main")
    if setup == "mine_staged":
        write(repo / "mine.txt", "mine\n")
        git(repo, "add", "mine.txt")
    elif setup == "mine_staged_edited":
        write(repo / "mine.txt", "mine\n")
        git(repo, "add", "mine.txt")
        write(repo / "mine.txt", "mine, edited\n", old=False)
    elif setup == "ignored":
        write(repo / ".gitignore", "local.db\ndata/\n")
        write(repo / "local.db", "mine\n")
        write(repo / "data" / "x", "mine\n")
    elif setup == "foo_staged":
        write(repo / "foo" / "x", "mine\n")
        git(repo, "add", "foo/x")
    return repo


NO_COMMIT_SETUPS = ["empty", "mine_staged", "mine_staged_edited", "ignored", "foo_staged"]


def tree_state(repo: Path) -> tuple:
    files = {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*"))
             if p.is_file() and ".git" not in p.relative_to(repo).parts}
    # A linked worktree's `.git` is a file, so ask git where the index is.
    index = repo / git(repo, "rev-parse", "--git-path", "index")
    return files, index.read_bytes() if index.exists() else None


@pytest.mark.parametrize("setup", NO_COMMIT_SETUPS)
def test_a_repo_with_no_commit_is_refused_and_left_as_it_was(tmp_path, setup):
    repo = no_commit_repo(tmp_path, setup)
    before = tree_state(repo)
    with pytest.raises(permissions.GitFailed, match=FIRST_COMMIT):
        permissions.snapshot(run_for(repo))
    assert tree_state(repo) == before


@pytest.mark.parametrize("state", ["orphan_checkout", "missing_branch", "orphan_worktree"])
def test_an_orphan_branch_in_a_repo_with_history_is_refused_with_a_true_message(tmp_path, state):
    repo = new_repo(tmp_path)
    if state == "orphan_checkout":
        git(repo, "checkout", "-q", "--orphan", "fresh")
    elif state == "missing_branch":
        git(repo, "symbolic-ref", "HEAD", "refs/heads/nope")
    else:
        git(repo, "worktree", "add", "-q", "--orphan", "-b", "lone", str(tmp_path / "wt"))
        repo = tmp_path / "wt"
    before = tree_state(repo)
    with pytest.raises(permissions.GitFailed, match=FIRST_COMMIT) as caught:
        permissions.snapshot(run_for(repo))
    assert "orphan or unborn branch" in str(caught.value)
    assert tree_state(repo) == before


def test_round_2_the_agents_forced_add_of_an_ignored_file_is_not_reached(tmp_path):
    repo = no_commit_repo(tmp_path, "ignored")
    git(repo, "add", "-f", "local.db")
    before = tree_state(repo)
    with pytest.raises(permissions.GitFailed, match=FIRST_COMMIT):
        permissions.enforce(run_for(repo), None, BUILDER, {})
    assert tree_state(repo) == before
    assert git(repo, "ls-files") == "local.db"


def test_round_3_a_folder_turned_into_a_file_is_not_reached(tmp_path, monkeypatch):
    repo = no_commit_repo(tmp_path, "foo_staged")
    shutil.rmtree(repo / "foo")
    write(repo / "foo", "now a file\n", old=False)
    ran = []
    box = sandboxed()
    real = box.run

    def run(argv, **kw):
        ran.append(argv)
        return real(argv, **kw)
    monkeypatch.setattr(box, "run", run)
    with pytest.raises(permissions.GitFailed, match=FIRST_COMMIT):
        permissions.enforce(run_for(repo), None, BUILDER, {})
    assert (repo / "foo").read_text() == "now a file\n"
    assert not [a for a in ran if "rm" in a or a[:1] == ["rm"] or "update-ref" in a]


def test_undo_commits_with_an_empty_before_sha_stops_on_merge_base(tmp_path, monkeypatch):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    head = {**permissions.head(run), "sha": ""}
    ran = []
    box = sandboxed()
    real = box.run

    def spy(argv, **kw):
        ran.append(argv)
        return real(argv, **kw)
    monkeypatch.setattr(box, "run", spy)
    with pytest.raises(permissions.GitFailed, match="merge-base") as caught:
        permissions.undo_commits(run, head)
    assert "first commit" not in str(caught.value)
    flat = [" ".join(a) for a in ran]
    assert not [c for c in flat if " update-ref " in c or " reset " in c or " rm " in c]


def test_git_answers_keep_their_meaning(tmp_path):
    repo = new_repo(tmp_path)
    git(repo, "checkout", "-q", "--detach")
    assert permissions._head(run_for(repo))["branch"] == ""          # exit 1 is "detached"
    permissions.guard_git_dir(run_for(repo))                         # core.hooksPath unset: exit 1


def test_merge_base_exit_1_still_says_it_doesnt_descend(tmp_path):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    head = permissions.head(run)
    root = git(repo, "commit-tree", "-m", "other", git(repo, "rev-parse", "HEAD^{tree}"))
    git(repo, "update-ref", "refs/heads/main", root)
    with pytest.raises(permissions.PermissionBreach, match="doesn't descend"):
        permissions.undo_commits(run, head)


def test_a_staged_new_file_that_is_not_ignored_is_still_deleted(tmp_path):
    repo, run, before = rollback_case(tmp_path)
    with pytest.raises(permissions.PermissionBreach, match="new.txt — unstaged and deleted"):
        permissions.enforce(run, None, BUILDER, before)


def test_a_corrupted_index_is_a_breach_naming_diff(tmp_path):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    before = permissions.snapshot(run)
    (repo / "justfile").write_text("evil:\n")
    (repo / ".git" / "index").write_bytes(b"garbage")
    with pytest.raises(permissions.PermissionBreach, match=r"git diff HEAD.*index") as caught:
        permissions.enforce(run, None, BUILDER, before)
    assert "may hold writes no check saw" in str(caught.value)


def test_snapshot_before_the_agent_raises_on_a_corrupted_index(tmp_path):
    repo = new_repo(tmp_path)
    (repo / ".git" / "index").write_bytes(b"garbage")
    with pytest.raises(permissions.PermissionBreach, match="diff"):
        permissions.snapshot(run_for(repo))


# ── no child in a submodule ──────────────────────────────────────────────────

def plant_sub_programs(cfg: Path, tmp_path: Path, marker: Path) -> None:
    """A filter, an fsmonitor and a hooks folder in a submodule's config, written
    last: each touches `marker`."""
    script = tmp_path / "mon.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir(exist_ok=True)
    for hook in ("post-index-change", "post-checkout", "reference-transaction"):
        (hooks / hook).write_text(f"#!/bin/sh\ntouch {marker}\ncat >/dev/null\n")
        (hooks / hook).chmod(0o755)
    with cfg.open("a") as out:
        out.write(f'[filter "h"]\n\tclean = touch {marker}; cat\n\tsmudge = touch {marker}; cat\n'
                  f'\tprocess = touch {marker}\n[core]\n\tfsmonitor = {script}\n\thooksPath = {hooks}\n')


def trace_lines(trace: Path) -> list[str]:
    return [l for l in trace.read_text().splitlines() if '"child_start"' in l] if trace.exists() else []


def starts(trace: Path) -> list[str]:
    """SSSF's own git `start` events: proof the trace file was live."""
    return [l for l in trace.read_text().splitlines() if '"event":"start"' in l] if trace.exists() else []


def moved(repo: Path) -> None:
    write(repo / "sub" / "h.txt", "h\n", old=False)
    git(repo / "sub", "add", "h.txt")
    git(repo / "sub", "commit", "-qm", "inside")


@pytest.mark.parametrize("case", ["removed", "staged_move", "unstaged_move", "dir_staged",
                                  "committed_move", "committed_removed"])
def test_no_git_child_starts_in_a_submodule(tmp_path, marker, monkeypatch, case):
    if case == "dir_staged":              # a nested repo with a `.git` directory, staged as a gitlink
        repo, run, before, head = bypass(tmp_path, 1, 1)
        cfg = repo / "sub" / ".git" / "config"
        (repo / "justfile").write_text("ok:\n")      # only the gitlink is the agent's change here
    else:
        repo = sub_world(tmp_path)
        run = run_for(repo)
        before = permissions.snapshot(run)
        head = permissions.head(run)
        cfg = repo / ".git" / "modules" / "sub" / "config"
        if case in ("removed", "committed_removed"):
            git(repo, "rm", "-q", "--cached", "sub")
        else:
            moved(repo)
            if case in ("staged_move", "committed_move"):
                git(repo, "add", "sub")
        if case.startswith("committed"):    # the agent commits the gitlink change itself
            git(repo, "commit", "-qm", "agent commit")
        (repo / "justfile").write_text("evil:\n")
    old = git(repo, "rev-parse", "HEAD:sub") if case in ("removed", "staged_move", "unstaged_move") else ""
    old_committed = git(repo, "rev-parse", "HEAD~1:sub") if case == "committed_move" else (
        git(repo, "rev-parse", "HEAD~1:sub") if case == "committed_removed" else "")
    plant_sub_programs(cfg, tmp_path, marker)
    trace = tmp_path / "trace"
    control = subprocess.run(["git", "diff", "HEAD", "--name-status"], cwd=repo, capture_output=True,
                             env={**ENV, "GIT_TRACE2_EVENT": str(trace)})
    if case in ("staged_move", "dir_staged"):
        assert trace_lines(trace), "plain git diff HEAD starts a child here"
    marker.unlink(missing_ok=True)
    trace.unlink(missing_ok=True)
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    permissions.undo_commits(run, head)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, BUILDER, before, head)
    monkeypatch.delenv("GIT_TRACE2_EVENT")
    assert not marker.exists()
    assert starts(trace), "the trace saw none of SSSF's own git starts"
    assert trace_lines(trace) == []
    if case.startswith("committed"):
        assert git(repo, "rev-parse", "HEAD") == head["sha"]            # the agent's commit is undone
        assert git(repo, "rev-parse", "HEAD:sub") == old_committed
    if case in ("removed", "staged_move"):
        assert git(repo, "rev-parse", "HEAD:sub") == old
        assert git(repo, "ls-files", "-s", "sub").split()[1] == old
    if case == "dir_staged":
        assert "sub" not in git(repo, "ls-files", "-s").split()


# ── the empty filter name ────────────────────────────────────────────────────

def test_the_empty_filter_name_in_the_repos_config_runs_nothing(tmp_path, marker):
    repo = new_repo(tmp_path)
    write(repo / ".gitattributes", "* filter=\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "attrs")
    with (repo / ".git" / "config").open("a") as out:
        out.write(f'[filter ""]\n\tclean = touch {marker}; cat\n\tsmudge = touch {marker}; cat\n')
    (repo / "a.dat").write_text("one\n")
    subprocess.run(["git", "diff", "HEAD", "--name-status"], cwd=repo, env=ENV, capture_output=True)
    assert marker.exists()                                  # the control: git runs `[filter ""]`
    marker.unlink()
    run = run_for(repo)
    assert "--config-env=filter..clean=SSSF_GIT_EMPTY" in permissions._filters_off(repo)
    before = permissions.snapshot(run)
    (repo / "justfile").write_text("evil:\n")
    with pytest.raises(permissions.PermissionBreach, match="justfile"):
        permissions.enforce(run, None, BUILDER, before)
    assert not marker.exists() and (repo / "justfile").read_text() == "ok:\n"


def test_the_empty_filter_name_in_a_submodules_config_runs_nothing(tmp_path, marker):
    repo = sub_world(tmp_path)
    with (repo / ".git" / "modules" / "sub" / "config").open("a") as out:
        out.write(f'[filter ""]\n\tclean = touch {marker}; cat\n\tsmudge = touch {marker}; cat\n')
    write(repo / "sub" / ".gitattributes", "* filter=\n", old=False)
    (repo / "sub" / "g.dat").write_text("g\n")
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


# ── decided differences, and the argument list ───────────────────────────────

def test_an_edit_inside_a_submodules_work_tree_is_seen_with_no_git_there(tmp_path):
    # --ignore-submodules=dirty starts no child in the submodule; the files are
    # read by _DIGEST instead, so the edit (gitlink unchanged) changes the snapshot.
    repo = sub_world(tmp_path)
    clean = permissions.snapshot(run_for(repo))["sub"]
    assert clean.startswith("sub:D@")
    (repo / "sub" / "f.dat").write_text("edited\n")
    assert permissions.snapshot(run_for(repo))["sub"] != clean


def test_a_repo_with_no_gitlink_has_no_sub_kind_and_starts_no_python(tmp_path, monkeypatch):
    repo = new_repo(tmp_path)
    seen: list[list[str]] = []
    real = permissions.sandbox.current()

    class Spy:
        def __getattr__(self, name):
            return getattr(real, name)

        def run(self, argv, *a, **k):
            seen.append(list(argv))
            return real.run(argv, *a, **k)
    permissions.sandbox.use(Spy())
    try:
        snap = permissions.snapshot(run_for(repo))
    finally:
        permissions.sandbox.use(real)
    assert not any(":sub" in v.partition(":")[0] or "sub" in v.partition(":")[0].split(",")
                   for v in snap.values())
    assert not any(argv[:1] == ["python3"] and permissions._DIGEST in argv for argv in seen)


def digest(tmp_path: Path, *names: str) -> dict:
    return permissions._digests(tmp_path, list(names))


def test_the_digest_sees_what_changes_and_ignores_what_doesnt(tmp_path):
    top = tmp_path / "d"
    write(top / "a.txt", "one\n")
    write(top / "sub" / "b.txt", "two\n")
    os.symlink("a.txt", top / "link")
    base = digest(tmp_path, "d")["d"]
    assert base[1] == ""

    def changed(edit) -> bool:
        edit()
        now = digest(tmp_path, "d")["d"]
        return now != base

    # no change: mtime, an empty folder, a write under a nested .git
    os.utime(top / "a.txt", (2_000_000_000, 2_000_000_000))
    (top / "empty").mkdir()
    (top / "sub" / ".git").mkdir()
    (top / "sub" / ".git" / "x").write_text("x")
    assert digest(tmp_path, "d")["d"] == base

    assert changed(lambda: write(top / "a.txt", "ONE\n"))                 # a changed byte
    base = digest(tmp_path, "d")["d"]
    assert changed(lambda: (top / "new").write_text("n"))                  # a new file
    base = digest(tmp_path, "d")["d"]
    assert changed(lambda: (top / "new").unlink())                         # a deleted file
    base = digest(tmp_path, "d")["d"]
    assert changed(lambda: (top / "a.txt").chmod(0o755))                   # execute bit
    base = digest(tmp_path, "d")["d"]
    assert changed(lambda: (top / "link").unlink() or os.symlink("b", top / "link"))


def test_the_digest_never_follows_a_link_or_opens_a_fifo_or_imports_from_the_cwd(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("one")
    top = tmp_path / "d"
    top.mkdir()
    os.symlink(outside, top / "out")
    os.mkfifo(top / "pipe")
    (tmp_path / "hashlib.py").write_text("raise SystemExit(7)\n")
    first = digest(tmp_path, "d")["d"]
    outside.write_text("two")
    assert digest(tmp_path, "d")["d"] == first
    assert first[1] == ""


def test_an_unreadable_file_gives_an_unread_content(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    top = tmp_path / "d"
    write(top / "a.txt", "one\n")
    (top / "a.txt").chmod(0)
    try:
        assert digest(tmp_path, "d")["d"][1] == "a.txt"
    finally:
        (top / "a.txt").chmod(0o644)


def fake_box(real, mode: str, canned: str = "c" * 64):
    seen: list = []

    class Fake:
        def __getattr__(self, name):
            return getattr(real, name)

        def run(self, argv, *a, **k):
            seen.append((list(argv), k.get("cwd")))
            if argv[:1] == ["python3"] and permissions._DIGEST in argv:
                if mode == "raise":
                    raise FileNotFoundError("python3")
                if mode == "127":
                    return SimpleNamespace(returncode=127, stdout=b"", stderr=b"not found")
                if mode == "canned":
                    return SimpleNamespace(returncode=0, stdout=f"{canned}\0\0".encode(), stderr=b"")
            return real.run(argv, *a, **k)
    return Fake(), seen


@pytest.mark.parametrize("mode", ["pass", "canned", "raise", "127"])
def test_the_digest_runs_through_the_current_sandbox(tmp_path, mode):
    repo = sub_world(tmp_path)
    real = permissions.sandbox.current()
    box, seen = fake_box(real, mode)
    permissions.sandbox.use(box)
    try:
        if mode in ("raise", "127"):
            with pytest.raises(permissions.GitFailed, match="submodule digest command failed"):
                permissions.snapshot(run_for(repo))
            return
        snap = permissions.snapshot(run_for(repo))
    finally:
        permissions.sandbox.use(real)
    argv, cwd = next((a, c) for a, c in seen if a[:1] == ["python3"] and permissions._DIGEST in a)
    assert argv[:4] == ["python3", "-I", "-c", permissions._DIGEST] and argv[4:] == ["sub"]
    assert str(cwd) == str(repo)
    if mode == "canned":
        assert snap["sub"] == "sub:D@" + "c" * 64


def test_a_rewritten_submodule_gitfile_runs_nothing_of_the_agents(tmp_path, marker):
    repo = sub_world(tmp_path)
    run = run_for(repo)
    real = permissions.sandbox.current()
    box, seen = fake_box(real, "pass")
    permissions.sandbox.use(box)
    try:
        before = permissions.snapshot(run)
        head = permissions.head(run)
        # What the agent does: a git directory of its own outside the repo, with
        # a program in its config and a hooks folder, the `.git` file pointed at
        # it, and an edit inside the submodule.
        copy = tmp_path / "agentgit"
        shutil.copytree(repo / ".git" / "modules" / "sub", copy)
        hooks = tmp_path / "agenthooks"
        hooks.mkdir()
        for name in ("post-index-change", "post-checkout", "reference-transaction"):
            (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
            (hooks / name).chmod(0o755)
        git(repo, "config", "--file", str(copy / "config"), "core.fsmonitor", touch_cmd(marker))
        git(repo, "config", "--file", str(copy / "config"), "core.hooksPath", str(hooks))
        (repo / "sub" / ".git").write_text(f"gitdir: {copy}\n")
        (repo / "sub" / "f.dat").write_text("evil\n")
        marker.unlink(missing_ok=True)
        seen.clear()
        with pytest.raises(permissions.PermissionBreach, match="sub"):
            permissions.enforce(run, None, BUILDER, before, head)
    finally:
        permissions.sandbox.use(real)
    assert not marker.exists()
    sub = repo / "sub"
    for argv, cwd in seen:
        if cwd is not None:
            where = Path(cwd).resolve()
            assert where != sub.resolve() and sub.resolve() not in where.parents, (argv, cwd)
        for i, arg in enumerate(argv):
            if arg == "-C" and i + 1 < len(argv):
                named = argv[i + 1]
                assert named != "sub" and not named.startswith("sub/"), argv
                assert Path(named).resolve() != sub.resolve(), argv
            assert arg != "sub/.git" and not arg.endswith("/sub/.git"), argv
            assert arg != "ls-tree", argv       # -10: no object `main` doesn't read


def test_change_three_left_no_mechanism():
    import inspect
    source = inspect.getsource(permissions)
    # GIT_INDEX_FILE is not here: the cached diff reads an index SSSF builds from
    # the entries (`_INDEX`), the accepted cache-tree fix of guard run ea9dc12f.
    # read-tree is not here either: `carried_tree` builds a fresh index with it for
    # the commit check, never for the rollback.
    for word in ("_filtered", "_contents", "check-attr",
                 '"mktemp", "-d"'):
        assert word not in source, word
    body = inspect.getsource(permissions.enforce)
    for word in ("same", "left =", "_CONTENT"):
        assert word not in body, word


@pytest.mark.parametrize("how", ["gitmodules", "diff_config"])
def test_a_moved_gitlink_shows_even_under_ignore_all(tmp_path, how):
    repo = sub_world(tmp_path)
    if how == "gitmodules":
        git(repo, "config", "-f", ".gitmodules", "submodule.sub.ignore", "all")
    else:
        git(repo, "config", "diff.ignoreSubmodules", "all")
    moved(repo)
    assert permissions.snapshot(run_for(repo))["sub"].startswith("M,sub:")


def test_a_submodules_config_is_never_an_argument(tmp_path):
    repo = sub_world(tmp_path)
    names_config(repo / ".git" / "modules" / "sub" / "config", 200, 300)
    assert permissions._filters_off(repo) == []


def test_config_md_documents_the_scenario():
    text = (SKILL / "references" / "config.md").read_text()
    start = text.index("## Write permissions")
    end = text.find("\n## ", start + 1)
    part = text[start:end if end != -1 else None]
    for word in ("git-lfs", "git-crypt", "pointer", "ciphertext", "git lfs checkout",
                 "git checkout HEAD --", "git submodule update", "left as-is", "--worktree",
                 "git status", "git diff"):
        assert word in part, word
    assert "never kept" not in part
    hub = SKILL.parent.parent.parent
    if (hub / "adws" / "adw_guard_change.py").is_file():  # the guide ships with the hub, not an install
        run_md = (hub / "docs" / "guide" / "run.md").read_text()
        assert "git-lfs" in run_md and "config.md" in run_md
    # -5: a failing git is never "no change"; no git child starts in a submodule
    flat = " ".join(part.split())
    assert "never read as \"no change\"" in flat and "may still hold the agent's writes" in flat
    assert "starts no git process inside one" in flat
    assert "isn't seen" in flat and "`ignore = all`" in flat
    assert "makes git report no change" not in flat
    assert "isn't reported as lost work" not in flat
    paragraph = next(p for p in part.split("\n\n")
                     if "REVERTED-BY-AGENT" in p and "only refreshed the index" in " ".join(p.split()))
    assert "git diff" in paragraph and "git status" in paragraph
    assert "rewrite a submodule's `.git` file" in flat
    assert "vendor/lib/x/.git/hooks/post-checkout" in flat
    assert flat.index("cat <path>/.git") < flat.index("git -C <path> status")
    # -10: a staged change inside a submodule is a documented limit, and so is PATH
    bullet = next(b for b in flat.split(" - ") if "staged change inside a submodule" in b
                  and "git -C <path> add" in b)
    assert bullet.index("cat <path>/.git") < bullet.index("git -C <path> diff --cached")
    assert any("`PATH`" in b and "`python3`" in b for b in flat.split(" - "))
    assert "rolled back the writes only when every path was put back" in flat


# ── bypass review 1 ──────────────────────────────────────────────────────────

def test_a_gitlink_dir_with_a_broken_config_doesnt_stop_the_undo_and_rollback(tmp_path):
    repo = new_repo(tmp_path)
    run = run_for(repo)
    before = permissions.snapshot(run)
    head = permissions.head(run)
    (repo / "justfile").write_text("evil:\n")
    git(repo, "commit", "-qam", "agent")
    (repo / "new.txt").write_text("agent's\n")
    evil = repo / "evil"
    evil.mkdir()
    git(evil, "init", "-q")
    (evil / ".git" / "config").write_text("[[[ not a config\n")
    git(repo, "update-index", "--add", "--cacheinfo",
        f"160000,{git(repo, 'rev-parse', 'HEAD')},evil")
    assert permissions.undo_commits(run, head)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, BUILDER, before, head)
    assert (repo / "justfile").read_text() == "ok:\n"
    assert not (repo / "new.txt").exists()
    assert git(repo, "rev-parse", "HEAD") == head["sha"]


def test_a_submodule_work_tree_redirect_doesnt_hide_a_nested_filter(tmp_path, marker):
    repo = sub_world(tmp_path)
    nsrc = tmp_path / "nsrc"
    nsrc.mkdir()
    git(nsrc, "init", "-qb", "main")
    write(nsrc / "x.dat", "x\n")
    write(nsrc / ".gitattributes", "*.dat filter=zz\n")
    git(nsrc, "add", "-A")
    git(nsrc, "commit", "-qm", "n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(elsewhere, "clone", "-q", str(nsrc), "n")
    git(repo / "sub", "update-index", "--add", "--cacheinfo",
        f"160000,{git(elsewhere / 'n', 'rev-parse', 'HEAD')},n")
    git(repo, "config", "--file", str(elsewhere / "n" / ".git" / "config"),
        "filter.zz.clean", touch_cmd(marker))
    git(repo, "config", "--file", str(repo / ".git" / "modules" / "sub" / "config"),
        "core.worktree", str(elsewhere))
    (elsewhere / "n" / "x.dat").write_text("x\n")
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


@pytest.mark.parametrize("tail", [" ", "\t", "\n"])
def test_a_redirect_to_a_name_ending_in_whitespace_doesnt_hide_a_nested_filter(tmp_path, marker, tail):
    repo = sub_world(tmp_path)
    nsrc = tmp_path / "nsrc"
    nsrc.mkdir()
    git(nsrc, "init", "-qb", "main")
    write(nsrc / "x.dat", "x\n")
    write(nsrc / ".gitattributes", "*.dat filter=zz\n")
    git(nsrc, "add", "-A")
    git(nsrc, "commit", "-qm", "n")
    elsewhere = tmp_path / f"elsewhere{tail}"
    elsewhere.mkdir()
    git(elsewhere, "clone", "-q", str(nsrc), "n")
    git(repo / "sub", "update-index", "--add", "--cacheinfo",
        f"160000,{git(elsewhere / 'n', 'rev-parse', 'HEAD')},n")
    git(repo, "config", "--file", str(elsewhere / "n" / ".git" / "config"),
        "filter.zz.clean", touch_cmd(marker))
    git(repo, "config", "--file", str(repo / ".git" / "modules" / "sub" / "config"),
        "core.worktree", str(elsewhere))
    (elsewhere / "n" / "x.dat").write_text("x\n")
    marker.unlink(missing_ok=True)
    permissions.snapshot(run_for(repo))
    assert not marker.exists()


# ── the restore's config read ────────────────────────────────────────────────

def _failing_keys(monkeypatch, how: str) -> None:
    real = permissions._sh

    def fake(script, *args, **kw):
        if script == permissions._KEYS:
            if how == "oserror":
                raise OSError(7, "Argument list too long")
            return subprocess.CompletedProcess(["sh"], 3, "", "boom")
        return real(script, *args, **kw)
    monkeypatch.setattr(permissions, "_sh", fake)


@pytest.mark.parametrize("how", ["exit", "oserror"])
def test_a_failed_config_read_is_unlisted_not_a_crash(tmp_path, home, monkeypatch, how):
    repo = new_repo(tmp_path)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    saved = (home / ".gitconfig").read_bytes()
    with (home / ".gitconfig").open("a") as out:
        out.write("[alias]\n\tx = status\n")
    _failing_keys(monkeypatch, how)
    assert permissions._entries("-", str(home / ".gitconfig")) is None
    with pytest.raises(permissions.PermissionBreach, match="could not be listed"):
        permissions.restore_git_dir(run, guard)
    assert (home / ".gitconfig").read_bytes() == saved        # still put back


def test_a_very_deep_folder_chain_is_unread_not_a_crash(tmp_path):
    top = tmp_path / "d"
    top.mkdir()
    fd = os.open(top, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(1500):
            os.mkdir("d", dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)
    found = digest(tmp_path, "d")["d"]
    assert found[1].startswith("d/d/")

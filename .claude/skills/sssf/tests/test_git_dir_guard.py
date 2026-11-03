"""An agent's write to the git directory is put back, and stops the run.

`git status` never shows the git directory, so permissions.enforce could not
see an agent write there. bypass-reviewer found the cost: a hook planted in
.git/hooks runs on the chain's own commit, outside every agent rule, and a
core.fsmonitor or filter line in .git/config runs on ordinary git commands.
A line in .git/info/exclude would hide a new file from enforce itself.
guard_git_dir copies hooks/, info/, config and config.worktree aside before
each agent, and restore_git_dir puts them back after it.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import permissions


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-qb", "main")
    (root / "app.py").write_text("x = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "base")
    return root


def state(root: Path) -> dict[str, bytes]:
    git_dir = root / ".git"
    return {str(p.relative_to(git_dir)): p.read_bytes() + oct(p.stat().st_mode).encode()
            for name in permissions.GIT_WATCHED
            for p in ([git_dir / name] if (git_dir / name).is_file() else (git_dir / name).rglob("*"))
            if p.is_file()}


def test_nothing_changed_passes_and_cleans_up(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    assert Path(guard["backup"]).is_dir()
    assert permissions.restore_git_dir(run, guard) == []
    assert not Path(guard["backup"]).exists()


@pytest.mark.parametrize("plant", ["hook", "fsmonitor", "exclude", "chmod", "delete"])
def test_a_change_is_put_back_and_stops_the_run(repo, plant):
    run = SimpleNamespace(repo_root=repo)
    git_dir = repo / ".git"
    sample = next((git_dir / "hooks").glob("*.sample"))
    before = state(repo)
    guard = permissions.guard_git_dir(run)
    if plant == "hook":
        hook = git_dir / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ntouch HOOK_RAN\n")
        hook.chmod(0o755)
    elif plant == "fsmonitor":
        git(repo, "config", "core.fsmonitor", "touch FSMONITOR_RAN")
    elif plant == "exclude":
        with open(git_dir / "info" / "exclude", "a") as f:
            f.write("secret_new_file.py\n")
    elif plant == "chmod":
        sample.chmod(0o755)
    else:
        sample.unlink()
    with pytest.raises(permissions.PermissionBreach, match="git directory") as caught:
        permissions.restore_git_dir(run, guard)
    assert ".git/" in str(caught.value)
    assert state(repo) == before
    assert not Path(guard["backup"]).exists()


def test_a_restored_hook_does_not_run_on_the_next_commit(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch HOOK_RAN\n")
    hook.chmod(0o755)
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    (repo / "app.py").write_text("x = 2\n")
    git(repo, "commit", "-qam", "next")
    assert not (repo / "HOOK_RAN").exists()


def test_a_failed_agent_is_restored_without_a_second_error(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    (repo / ".git" / "hooks" / "post-commit").write_text("#!/bin/sh\n")
    assert permissions.restore_git_dir(run, guard, breach=False) == [".git/hooks/post-commit"]
    assert not (repo / ".git" / "hooks" / "post-commit").exists()
    assert permissions.restore_git_dir(run, guard) == []      # once only


def test_a_worktree_guards_the_shared_git_directory(repo, tmp_path):
    tree = tmp_path / "tree"
    git(repo, "worktree", "add", "-q", "-b", "run", str(tree))
    run = SimpleNamespace(repo_root=tree)
    guard = permissions.guard_git_dir(run)
    assert len(guard["dirs"]) == 2
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\n")
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_outside_a_repo_there_is_nothing_to_guard(tmp_path):
    os.environ.pop("GIT_DIR", None)
    assert permissions.guard_git_dir(SimpleNamespace(repo_root=tmp_path)) is None


def test_every_agent_call_guards_the_git_directory():
    text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    assert "permissions.guard_git_dir(run)" in text
    assert "permissions.restore_git_dir(run, git_before)" in text
    assert "permissions.restore_git_dir(run, git_before, breach=False)" in text

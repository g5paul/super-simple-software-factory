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


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """A HOME of the test's own, so the guard copies and restores a test
    ~/.gitconfig, never the real one."""
    path = tmp_path / "home"
    path.mkdir()
    (path / ".gitconfig").write_text("[user]\n\tname = t\n")
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return path


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
    assert permissions.restore_git_dir(run, guard) == []


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
    assert permissions.restore_git_dir(run, guard, breach=False) == [".git/hooks"]
    assert not (repo / ".git" / "hooks" / "post-commit").exists()
    assert permissions.restore_git_dir(run, guard) == []      # once only


def test_a_worktree_guards_the_shared_git_directory(repo, tmp_path):
    tree = tmp_path / "tree"
    git(repo, "worktree", "add", "-q", "-b", "run", str(tree))
    run = SimpleNamespace(repo_root=tree)
    guard = permissions.guard_git_dir(run)
    assert sum(p.endswith("/hooks") for p in guard["paths"]) == 2
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


@pytest.mark.parametrize("plant", ["global_hooks_path", "xdg_config"])
def test_the_global_git_config_is_put_back(repo, home, plant):
    run = SimpleNamespace(repo_root=repo)
    before = (home / ".gitconfig").read_bytes()
    guard = permissions.guard_git_dir(run)
    if plant == "global_hooks_path":
        git(repo, "config", "--global", "core.hooksPath", str(home / "evil"))
    else:
        (home / ".config" / "git").mkdir(parents=True)
        (home / ".config" / "git" / "config").write_text("[core]\n\tfsmonitor = touch X\n")
    with pytest.raises(permissions.PermissionBreach, match="git config"):
        permissions.restore_git_dir(run, guard)
    assert (home / ".gitconfig").read_bytes() == before
    assert not (home / ".config" / "git" / "config").exists()


def test_a_same_size_edit_is_caught(repo):
    run = SimpleNamespace(repo_root=repo)
    config = repo / ".git" / "config"
    guard = permissions.guard_git_dir(run)
    text = config.read_text()
    config.write_text(text.replace("false", "fals3", 1) if "false" in text else text[:-1] + "#")
    assert config.stat().st_size == len(text.encode())
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert config.read_text() == text


# ── second bypass-reviewer pass ─────────────────────────────────────────────

def plant_hook(hooks: Path) -> None:
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\ntouch HOOK_RAN\n")
    hook.chmod(0o755)


def commit_runs_no_hook(repo: Path) -> bool:
    (repo / "app.py").write_text("x = 3\n")
    git(repo, "commit", "-qam", "probe")
    return not (repo / "HOOK_RAN").exists()


def test_the_saved_copy_is_not_on_disk(repo, tmp_path, monkeypatch):
    """An agent could edit a copy on disk along with the original. The copy is
    held in the SSSF process instead."""
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    assert list((tmp_path / "tmp").iterdir()) == []
    plant_hook(repo / ".git" / "hooks")
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert commit_runs_no_hook(repo)
    assert list((tmp_path / "tmp").iterdir()) == []


def test_a_commondir_redirect_is_put_back(repo, tmp_path):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    (repo / ".git" / "commondir").write_text(str(tmp_path / "fake") + "\n")
    with pytest.raises(permissions.PermissionBreach, match="commondir"):
        permissions.restore_git_dir(run, guard)
    assert not (repo / ".git" / "commondir").exists()


def test_an_included_config_file_is_watched(repo, home):
    local = home / ".gitconfig.local"
    local.write_text("")
    with open(home / ".gitconfig", "a") as f:
        f.write(f"[include]\n\tpath = {local}\n")
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    git(repo, "config", "-f", str(local), "core.hooksPath", str(home / "evil"))
    with pytest.raises(permissions.PermissionBreach, match="gitconfig.local"):
        permissions.restore_git_dir(run, guard)
    assert local.read_text() == ""


def test_an_existing_hooks_path_directory_is_watched(repo, home):
    hooks = home / ".githooks"
    hooks.mkdir()
    git(repo, "config", "--global", "core.hooksPath", str(hooks))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    plant_hook(hooks)
    with pytest.raises(permissions.PermissionBreach, match="githooks"):
        permissions.restore_git_dir(run, guard)
    assert commit_runs_no_hook(repo)


def test_a_write_through_a_symlinked_gitconfig_is_caught(repo, home):
    dotfiles = home / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "gitconfig").write_text("[user]\n\tname = t\n")
    (home / ".gitconfig").unlink()
    (home / ".gitconfig").symlink_to(dotfiles / "gitconfig")
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    git(repo, "config", "--global", "core.hooksPath", str(home / "evil"))
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)


def test_the_credential_store_is_never_copied(repo, home):
    store = home / ".config" / "git"
    store.mkdir(parents=True)
    (store / "credentials").write_text("https://u:not-a-real-token@example.com\n")
    guard = permissions.guard_git_dir(SimpleNamespace(repo_root=repo))
    assert not any("credentials" in p for p in guard["paths"])
    assert b"not-a-real-token" not in __import__("base64").b64decode(guard["plain"])
    permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can remove anything")
def test_one_failed_restore_does_not_stop_the_rest(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    stuck = repo / ".git" / "hooks" / "stuck"
    stuck.mkdir()
    (stuck / "f").write_text("")
    stuck.chmod(0)
    git(repo, "config", "core.fsmonitor", "touch FSMONITOR_RAN")
    try:
        with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
            permissions.restore_git_dir(run, guard, breach=False)
        assert "fsmonitor" not in (repo / ".git" / "config").read_text()
    finally:
        stuck.chmod(0o755)


def test_changed_is_seen_before_the_gates():
    run_text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    loop = run_text.index("for gate_attempt in range(")
    assert run_text.index("permissions.git_dir_changed(git_before)", loop) \
        < run_text.index("for gate in call.gates:", loop)


def test_git_dir_changed_runs_no_restore(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    assert not permissions.git_dir_changed(guard)
    plant_hook(repo / ".git" / "hooks")
    assert permissions.git_dir_changed(guard)
    assert (repo / ".git" / "hooks" / "pre-commit").exists()
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)

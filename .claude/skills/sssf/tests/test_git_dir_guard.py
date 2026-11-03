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


def test_a_symlink_to_a_big_tree_is_cut_off(repo, tmp_path, monkeypatch):
    monkeypatch.setattr(permissions, "PACK_CAP", 64 * 1024)
    big = tmp_path / "big"
    big.mkdir()
    (big / "blob").write_bytes(os.urandom(1024 * 1024))
    run = SimpleNamespace(repo_root=repo)
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

def test_a_retargeted_config_dir_link_is_put_back(repo, home, tmp_path):
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
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(home / ".config") == str(dot)
    assert "PWNED" not in git(repo, "config", "--list")


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
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(link) == str(real)


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
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(root / ".git") == str(store)


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
    with pytest.raises(permissions.PermissionBreach):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(link) == str(good)
    assert commit_runs_no_hook(repo)


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


def test_a_retargeted_inner_link_is_put_back(repo, tmp_path, monkeypatch):
    link, real, dot = nested_home(tmp_path, monkeypatch)
    run = SimpleNamespace(repo_root=repo)
    guard = permissions.guard_git_dir(run)
    evil = tmp_path / "evil"
    (evil / "git").mkdir(parents=True)
    (evil / "git" / "config").write_text("[core]\n\tfsmonitor = touch PWNED\n")
    (real / ".config").unlink()
    (real / ".config").symlink_to(evil)
    with pytest.raises(permissions.PermissionBreach, match="put back as they were"):
        permissions.restore_git_dir(run, guard)
    assert os.readlink(link) == str(real)
    assert os.readlink(real / ".config") == str(dot)


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

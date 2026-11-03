"""install.py stamps .githooks/pre-push, a gitleaks scan of every push.

It never turns the hook on: `git config core.hooksPath .githooks` is the
engineer's step, so the installer prints it, names any .git/hooks file the
switch would turn off, and says when gitleaks is missing. The hook itself
refuses a push from an SSSF agent, a push with a secret in it, and any push
when gitleaks isn't installed.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import secrets
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL / "templates" / "githooks" / "pre-push"
HOOK = Path(".githooks/pre-push")
ZERO = "0" * 40
GITLEAKS = shutil.which("gitleaks")


def run(*args: str, cwd: Path | None = None, env: dict | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, env=env, input=stdin,
                          capture_output=True, text=True, check=False)


def git(cwd: Path, *args: str) -> str:
    result = run("git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args, cwd=cwd)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def install(*args: str) -> str:
    result = run(sys.executable, str(SKILL / "scripts" / "install.py"), *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q")
    return tmp_path


def test_install_stamps_the_hook_executable_and_leaves_it_off(repo):
    out = install("--coding-agent", "claude_code")
    assert HOOK.read_bytes() == TEMPLATE.read_bytes()
    assert os.access(HOOK, os.X_OK)
    assert "git config core.hooksPath .githooks" in out
    assert run("git", "config", "--get", "core.hooksPath").stdout == ""


def test_install_names_a_hook_the_switch_would_turn_off(repo):
    (repo / ".git/hooks/pre-push").write_text("#!/bin/sh\nexit 0\n")
    out = install("--coding-agent", "claude_code")
    assert "switches off .git/hooks/pre-push" in out


def test_install_says_when_the_scan_is_on(repo):
    git(repo, "config", "core.hooksPath", ".githooks")
    out = install("--coding-agent", "claude_code")
    assert "pre-push secret scan: on" in out
    assert "git config core.hooksPath .githooks   #" not in out


def test_install_keeps_a_hook_the_repo_already_has(repo):
    HOOK.parent.mkdir()
    HOOK.write_text("#!/bin/sh\n# the repo's own\n")
    install("--coding-agent", "claude_code")
    assert "the repo's own" in HOOK.read_text()
    out = install("--update")
    assert "the repo's own" in HOOK.read_text()
    assert "to review: 0" in out


def test_update_adds_the_hook_to_an_older_install(repo):
    install("--coding-agent", "claude_code")
    HOOK.unlink()
    out = install("--update")
    assert f"+ {HOOK}" in out
    assert os.access(HOOK, os.X_OK)


def test_the_hubs_hook_is_the_template():
    hub = SKILL.parent.parent.parent / ".githooks" / "pre-push"
    if not hub.exists():
        pytest.skip("not the hub checkout")
    assert hub.resolve() == TEMPLATE.resolve()


# The hook, run as git runs it: remote name in $1, ref lines on stdin.

@pytest.fixture
def pushed(tmp_path):
    """A clone whose main matches its remote's, ready for one more commit."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    (origin / "README").write_text("hello\n")
    git(origin, "add", "README")
    git(origin, "commit", "-qm", "first")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    return clone


def commit(clone: Path, name: str, text: str) -> None:
    (clone / name).write_text(text)
    git(clone, "add", name)
    git(clone, "commit", "-qm", name)


def push(clone: Path, env: dict | None = None, new_branch: bool = False):
    head = git(clone, "rev-parse", "HEAD")
    remote = ZERO if new_branch else git(clone, "rev-parse", "origin/main")
    ref = "refs/heads/new" if new_branch else "refs/heads/main"
    environ = {k: v for k, v in os.environ.items() if k != "SSSF_ADW_ID"}
    return run(shutil.which("bash"), str(TEMPLATE), "origin", "url", cwd=clone, env={**environ, **(env or {})},
               stdin=f"{ref} {head} {ref} {remote}\n")


def fake_token() -> str:
    return "ghp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(36))


def test_an_agent_push_is_refused(pushed):
    commit(pushed, "a.txt", "fine\n")
    result = push(pushed, env={"SSSF_ADW_ID": "test1234", "SSSF_AGENT": "builder"})
    assert result.returncode == 1
    assert "SSSF agent is pushing" in result.stderr
    assert "test1234, agent builder" in result.stderr


def test_a_push_without_gitleaks_is_refused(pushed, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    commit(pushed, "a.txt", "fine\n")
    result = push(pushed, env={"PATH": str(bin_dir)})
    assert result.returncode == 1
    assert "gitleaks isn't installed" in result.stderr


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
@pytest.mark.parametrize("new_branch", [False, True])
def test_a_clean_push_passes(pushed, new_branch):
    commit(pushed, "a.txt", "fine\n")
    assert push(pushed, new_branch=new_branch).returncode == 0


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
@pytest.mark.parametrize("new_branch", [False, True])
def test_a_push_with_a_secret_is_refused_and_says_where(pushed, new_branch):
    token = fake_token()
    commit(pushed, "leak.env", f'token = "{token}"\n')
    result = push(pushed, new_branch=new_branch)
    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "leak.env" in output
    assert token not in output


# Private project names, listed in .git/sssf-private-names, never leave in a push.

def names(clone: Path, text: str) -> None:
    (clone / ".git" / "sssf-private-names").write_text(text)


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
@pytest.mark.parametrize("new_branch", [False, True])
def test_a_push_naming_a_private_project_in_a_file_is_refused(pushed, new_branch):
    names(pushed, "# private projects\nsecret-ledger\n\n")
    commit(pushed, "notes.md", "Installed SSSF in Secret-Ledger today.\n")
    result = push(pushed, new_branch=new_branch)
    assert result.returncode == 1
    assert "names a private project (secret-ledger" in result.stderr.lower()
    assert git(pushed, "rev-parse", "--short", "HEAD") in result.stderr


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
def test_a_push_naming_a_private_project_in_a_message_is_refused(pushed):
    names(pushed, "secret-ledger\n")
    (pushed / "a.txt").write_text("fine\n")
    git(pushed, "add", "a.txt")
    git(pushed, "commit", "-qm", "Fix the watchdog, seen in secret-ledger")
    assert push(pushed).returncode == 1


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
@pytest.mark.parametrize("listed", [None, "", "# only a comment\n\n"])
def test_no_names_checks_nothing(pushed, listed):
    if listed is not None:
        names(pushed, listed)
    commit(pushed, "a.txt", "anything at all\n")
    assert push(pushed).returncode == 0


@pytest.mark.skipif(not GITLEAKS, reason="gitleaks isn't installed")
def test_a_push_without_the_names_passes(pushed):
    names(pushed, "secret-ledger\nother-thing\n")
    commit(pushed, "a.txt", "a public change\n")
    assert push(pushed).returncode == 0

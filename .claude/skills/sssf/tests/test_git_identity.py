"""Every test runs with a git identity of its own, from conftest.py's git_identity fixture,
so a temp repo commits on a machine with no ~/.gitconfig, such as an OpenShell VM.

Run from the skill folder:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_identity.py
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import conftest


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert git(repo, "init", "-q").returncode == 0
    return repo


def _author(repo):
    out = git(repo, "log", "-1", "--format=%an|%ae|%cn|%ce")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_a_fresh_repo_commits_with_no_global_config(tmp_path, monkeypatch):
    home = tmp_path / "emptyhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "EMAIL", "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    repo = _repo(tmp_path)
    done = git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    assert done.returncode == 0, done.stderr
    n, e = conftest.TEST_GIT_NAME, conftest.TEST_GIT_EMAIL
    assert _author(repo) == f"{n}|{e}|{n}|{e}"


def test_a_repos_own_identity_wins(tmp_path):
    repo = _repo(tmp_path)
    git(repo, "config", "user.email", "own@example.com")
    assert git(repo, "commit", "-q", "--allow-empty", "-m", "x").returncode == 0
    n = conftest.TEST_GIT_NAME
    assert _author(repo) == f"{n}|own@example.com|{n}|own@example.com"
    git(repo, "config", "user.name", "own")
    assert git(repo, "commit", "-q", "--allow-empty", "-m", "y").returncode == 0
    assert _author(repo) == "own|own@example.com|own|own@example.com"


def test_a_command_line_identity_wins(tmp_path):
    repo = _repo(tmp_path)
    done = git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
               "commit", "-q", "--allow-empty", "-m", "x")
    assert done.returncode == 0, done.stderr
    assert _author(repo) == "t|t@example.com|t|t@example.com"


def test_the_global_config_is_the_tests_own_file(git_identity, tmp_path):
    assert os.environ["GIT_CONFIG_GLOBAL"] == str(git_identity)
    assert Path.home() not in git_identity.parents
    out = git(tmp_path, "config", "--global", "--list")
    assert out.stdout.splitlines() == [f"user.name={conftest.TEST_GIT_NAME}",
                                       f"user.email={conftest.TEST_GIT_EMAIL}"]


def test_a_global_write_stays_in_its_test_part_1(tmp_path):
    assert git(tmp_path, "config", "--global", "core.hooksPath", "/nowhere").returncode == 0


def test_a_global_write_stays_in_its_test_part_2(tmp_path):
    assert git(tmp_path, "config", "--global", "--get", "core.hooksPath").returncode == 1


def test_a_test_home_takes_its_gitconfig_back(tmp_path, monkeypatch, git_identity):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = t\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert git(tmp_path, "config", "--global", "user.name").stdout.strip() == "t"
    assert git(tmp_path, "config", "--global", "core.x", "y").returncode == 0
    assert "x = y" in (home / ".gitconfig").read_text()
    assert "core" not in git_identity.read_text()

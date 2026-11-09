"""repo.yaml's `sandbox`: where a chain that takes --sandbox does a new run's
work when the flag is left out. Design: docs/designs/remote-sandboxes.md.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import repo_settings, session
from adw_modules.tracer import Tracer

DB = "adws/adw_data/sssf.db"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "adws/adw_sssf_config").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    monkeypatch.chdir(root)
    return root


def setting(root: Path, value: str) -> None:
    (root / repo_settings.PATH).write_text(f"sandbox: {value}\n")


def test_no_setting_and_no_flag_runs_here(repo):
    assert session.choose_sandbox(None, DB, None) is None


def test_the_setting_picks_for_a_new_run(repo):
    setting(repo, "e2b")
    assert session.choose_sandbox(None, DB, None) == "e2b"
    assert session.choose_sandbox(None, DB, "a1b2c3d4") == "e2b"   # pinned, not yet run


def test_the_flag_wins_and_none_means_here(repo):
    setting(repo, "e2b")
    assert session.choose_sandbox("vercel", DB, None) == "vercel"
    assert session.choose_sandbox("none", DB, None) is None


def test_a_run_that_exists_is_not_moved_by_the_setting(repo):
    tracer = Tracer(repo / DB, repo / "events.jsonl", data_root=repo)
    tracer.session_start("a1b2c3d4", "T")
    setting(repo, "e2b")
    assert session.choose_sandbox(None, DB, "a1b2c3d4") is None


def test_the_setting_is_read_from_the_main_checkout_inside_a_worktree(repo, monkeypatch):
    setting(repo, "vercel")
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=T", "commit",
                    "-q", "--allow-empty", "-m", "start"], cwd=repo, check=True)
    tree = repo.parent / "tree"
    subprocess.run(["git", "worktree", "add", "-q", str(tree)], cwd=repo, check=True)
    monkeypatch.chdir(tree)
    assert session.choose_sandbox(None, DB, None) == "vercel"


def test_a_misspelled_provider_fails(repo):
    setting(repo, "e2bb")
    with pytest.raises(ValueError):
        session.choose_sandbox(None, DB, None)


def test_the_template_names_the_default():
    text = (SKILL / "templates/repo.yaml").read_text()
    assert repo_settings.RepoSettings.model_validate(
        __import__("yaml").safe_load(text)).sandbox == "none"

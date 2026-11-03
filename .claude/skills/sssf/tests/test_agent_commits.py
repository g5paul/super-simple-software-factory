"""An agent that commits its own work is undone, then checked as usual.

In a project's run e257f6d5 the documenter ran `git add ... && git commit`
itself. commit_docs then found nothing to commit and failed the run. Worse,
permissions.enforce compares the working tree against HEAD, so a commit hid
the agent's changes from it entirely: a builder could have committed a
protected file and passed. permissions.undo_commits now resets the
agent's commits before enforce runs, so its changes are back in the tree.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
PROMPTS = SKILL / "templates" / "prompt_engineering"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import git_helper, permissions
from adw_modules.data_types import AgentConfig, ConfigDefaults, PromptEngineering, SSSFConfig


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def agent(name: str, writes=None) -> AgentConfig:
    return AgentConfig(name=name, writes=writes,
                       prompt_engineering=PromptEngineering(system="s.md", user="u.md"))


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    for path in ("adws/adw_modules/quality.py", "src/app.py"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("old\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    cfg = SSSFConfig(defaults=ConfigDefaults(data_dir="adws/adw_data",
                                             protected_files=["adws/adw_modules/"]))
    return SimpleNamespace(cfg=cfg, repo_root=tmp_path)


def commit(run, path: str, text: str = "new\n") -> None:
    target = run.repo_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git("add", path)
    git("commit", "-qm", f"agent wrote {path}")


def test_no_commit_undoes_nothing(run):
    before = permissions.head(run)
    (run.repo_root / "src/app.py").write_text("new\n")
    assert permissions.undo_commits(run, before) == []


def test_a_permitted_commit_is_undone_and_its_change_kept(run):
    base = git("rev-parse", "HEAD")
    tree, before = permissions.snapshot(run), permissions.head(run)
    commit(run, "app_docs/note.md")
    commit(run, "app_docs/more.md")

    undone = permissions.undo_commits(run, before)

    assert len(undone) == 2
    assert git("rev-parse", "HEAD") == base
    touched = permissions.enforce(run, None, agent("documenter", ["app_docs/"]), tree)
    assert touched == ["app_docs/more.md", "app_docs/note.md"]
    # the chain's own commit phase now has something to commit
    git_helper.commit_all("Document the change")
    assert git("log", "-1", "--format=%s") == "Document the change"


def test_a_committed_protected_file_is_caught_and_rolled_back(run):
    tree, before = permissions.snapshot(run), permissions.head(run)
    commit(run, "adws/adw_modules/quality.py")

    permissions.undo_commits(run, before)
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, agent("builder"), tree)

    assert "adws/adw_modules/quality.py" in str(breach.value)
    assert (run.repo_root / "adws/adw_modules/quality.py").read_text() == "old\n"


def test_switching_branches_stops_the_run(run):
    before = permissions.head(run)
    git("switch", "-qc", "elsewhere")
    commit(run, "src/other.py")

    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.undo_commits(run, before)

    assert "refs/heads/main" in str(breach.value) and "refs/heads/elsewhere" in str(breach.value)
    assert git("rev-parse", "--abbrev-ref", "HEAD") == "elsewhere"   # nothing was undone


def test_the_first_commit_in_an_empty_repo_is_undone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    run = SimpleNamespace(repo_root=tmp_path)
    before = permissions.head(run)
    commit(run, "src/app.py")
    assert len(permissions.undo_commits(run, before)) == 1
    assert git("status", "--porcelain") == "?? src/"


def test_a_reset_past_the_start_stops_the_run(run):
    commit(run, "src/second.py")
    before = permissions.head(run)
    git("reset", "-q", "--hard", "HEAD~1")

    with pytest.raises(permissions.PermissionBreach, match="doesn't descend"):
        permissions.undo_commits(run, before)


@pytest.mark.parametrize("name", ["planner", "builder", "documenter", "ticket_builder"])
def test_every_writing_agent_is_told_not_to_commit(name):
    assert "Do not commit" in (PROMPTS / name / "system.md").read_text()

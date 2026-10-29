"""`just sssf-update` refreshes a repo's skill copy and runs --update in one step.

Every update used to be the same hand-typed steps: rsync from the skill's
source, clear __pycache__, run --update, run it again to confirm. Typing them
went wrong twice: a wrong list of expected files, and an rsync that deleted a
running visualizer's page. The recipe reads the source from SSSF_SOURCE in
.env, and refuses to start while the tree has uncommitted changes or a chain is
running, so the update always gets its own commit.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("just", "rsync", "git", "uv")),
    reason="needs just, rsync, git, and uv")


def run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=False)


def git(cwd: Path, *args: str) -> None:
    result = run(cwd, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "shop"
    root.mkdir()
    git(root, "init", "-q")
    shutil.copytree(SKILL, root / ".claude/skills/sssf",
                    ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__", ".pytest_cache"))
    result = run(root, sys.executable, ".claude/skills/sssf/scripts/install.py",
                 "--coding-agent", "claude_code")
    assert result.returncode == 0, result.stderr
    (root / ".env").write_text(f"SSSF_SOURCE={SKILL}\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "install")
    return root


def test_update_refreshes_and_confirms(repo):
    stale = repo / ".claude/skills/sssf/cookbooks/install.md"
    stale.write_text("an older skill copy\n")
    git(repo, "commit", "-qam", "older copy")
    result = run(repo, "just", "sssf-update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert stale.read_text() == (SKILL / "cookbooks/install.md").read_text()
    assert "sssf updated in" in result.stdout
    assert "second pass" in result.stdout and "updated: 0" in result.stdout


def test_update_needs_the_source(repo):
    (repo / ".env").write_text("")
    result = run(repo, "just", "sssf-update")
    assert result.returncode != 0
    assert "SSSF_SOURCE" in result.stderr


def test_update_refuses_a_dirty_tree(repo):
    (repo / "README.md").write_text("work in progress\n")
    result = run(repo, "just", "sssf-update")
    assert result.returncode != 0
    assert "commit or stash" in result.stderr


def test_update_refuses_while_a_chain_runs(repo):
    db = repo / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table processes (pid integer, ended_at text)")
        conn.execute("insert into processes values (?, null)", (subprocess.os.getpid(),))
    result = run(repo, "just", "sssf-update")
    assert result.returncode != 0
    assert "still running" in result.stderr

"""The diff a documenter reads is the project's change, never SSSF's own files.

Updating SSSF on a branch is routine, so a diff against main often carries
adws/ and .claude/skills/sssf/. On yt-summarizer the documenter then wrote a
whole section on SSSF internals into the project's write-up (957a1d8f).

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
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import changes
from adw_modules.data_types import ChangeCapture


def git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


def write(path: str, text: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    for path in ("src/app.py", "adws/adw_x.py", ".claude/skills/sssf/SKILL.md"):
        write(path, "old\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    git("checkout", "-qb", "feature")
    for path in ("src/app.py", "adws/adw_x.py", ".claude/skills/sssf/SKILL.md"):
        write(path, "new\n")
    write("src/new.py", "x\n")
    write("adws/adw_new.py", "x\n")
    return SimpleNamespace(context_handoff_dir=tmp_path)


def test_the_documenters_diff_leaves_out_sssf(repo):
    found = changes.capture(repo, ChangeCapture(base="main"))
    assert found.files == ["src/app.py"]
    assert found.untracked == ["src/new.py"]
    assert (found.insertions, found.deletions) == (1, 1)
    text = Path(found.diff_path).read_text()
    assert "adws/" not in text.split("## diff")[1] and ".claude/skills/sssf" not in text.split("## diff")[1]
    assert "leaving out SSSF's own files: adws/, .claude/skills/sssf/" in text


def test_an_empty_exclude_keeps_everything(repo):
    found = changes.capture(repo, ChangeCapture(base="main", exclude=[]))
    assert sorted(found.files) == [".claude/skills/sssf/SKILL.md", "adws/adw_x.py", "src/app.py"]

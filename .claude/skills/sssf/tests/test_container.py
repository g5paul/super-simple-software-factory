"""`install.py --container` adds the operator container, and only on request.

On the host, Claude Code's auto mode blocks the command that launches a chain.
In sssf-sandbox, an operator session in a Podman container ran with
--dangerously-skip-permissions and finished run 1d56f792 with no prompt.
container/ packages that setup. It stays out of every repo that did not ask
for it, and an update keeps it current in every repo that did.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
STAMP = Path("adws/adw_sssf_config/stamp.json")
FILES = ("container/Containerfile", "container/build.sh", "container/run.sh")


def install(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), *args],
                            capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_plain_install_has_no_container(repo):
    install("--coding-agent", "claude_code")
    assert not Path("container").exists()
    install("--update")
    assert not Path("container").exists()


def test_container_is_stamped_and_runnable(repo):
    out = install("--coding-agent", "claude_code", "--container").stdout
    for rel in FILES:
        assert Path(rel).is_file(), rel
    assert os.access("container/run.sh", os.X_OK) and os.access("container/build.sh", os.X_OK)
    assert set(FILES) <= set(json.loads(STAMP.read_text())["files"])
    assert "container/run.sh" in out


def test_container_can_be_added_to_an_installed_repo(repo):
    install("--coding-agent", "claude_code")
    install("--container")
    assert all(Path(rel).is_file() for rel in FILES)


def test_an_update_keeps_the_container_current(repo):
    install("--coding-agent", "claude_code", "--container")
    run = Path("container/run.sh")
    run.write_text(run.read_text() + "\n# older template\n")
    record = json.loads(STAMP.read_text())
    import hashlib
    record["files"]["container/run.sh"]["sha256"] = hashlib.sha256(run.read_bytes()).hexdigest()
    STAMP.write_text(json.dumps(record))
    install("--update")
    assert run.read_text() == (SKILL / "templates/container/run.sh").read_text()


def test_a_pi_roster_cannot_have_the_container(repo):
    result = install("--container", ok=False)
    assert "Claude Code roster" in result.stderr
    assert not Path("container").exists()

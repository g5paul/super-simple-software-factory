"""The engineer's recipes refuse inside a run, and the justfile loads local.just.

`just container`, `just e2b-template` and `just agentsh-install` start the operator
container, spend the E2B key, or write a root-installed policy, so each refuses
while SSSF_ADW_ID is set, as `just openshell-setup` does. Every command they run
is a stand-in that logs its call, so nothing here starts a container or calls E2B.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
JUST = shutil.which("just")
pytestmark = pytest.mark.skipif(not JUST, reason="just isn't installed")

RECIPES = ["container", "e2b-template", "agentsh-install"]


def stand_in(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\necho "$(basename "$0") $*" >> "$STANDIN_LOG"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def repo_with_justfile(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    shutil.copy(SKILL / "templates" / "justfile", repo / "justfile")
    stand_in(repo / "container" / "run.sh")
    return repo


def run(tmp_path: Path, repo: Path, *argv: str, adw_id: str | None = None):
    bin_dir = tmp_path / "bin"
    stand_in(bin_dir / "uv")
    env = dict(os.environ)
    env.pop("SSSF_ADW_ID", None)
    env.pop("SSSF_AGENT", None)
    env.update(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               STANDIN_LOG=str(tmp_path / "standin.log"),
               SSSF_SOURCE=str(tmp_path / "hub" / ".claude" / "skills" / "sssf"))
    (tmp_path / "hub" / ".claude" / "skills" / "sssf").mkdir(parents=True, exist_ok=True)
    if adw_id is not None:
        env["SSSF_ADW_ID"] = adw_id
    return subprocess.run([JUST, *argv], cwd=repo, capture_output=True, text=True, env=env)


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "standin.log"
    return log.read_text().splitlines() if log.exists() else []


@pytest.mark.parametrize("recipe", RECIPES)
def test_recipe_refuses_inside_a_run(tmp_path, recipe):
    repo = repo_with_justfile(tmp_path)
    done = run(tmp_path, repo, recipe, adw_id="abc12345")
    assert done.returncode != 0
    lines = [line for line in done.stderr.splitlines() if not line.startswith("error: recipe")]
    assert len(lines) == 1, done.stderr
    assert "engineer's to run" in lines[0] and "outside an SSSF run" in lines[0]
    assert calls(tmp_path) == []


def test_container_runs_run_sh_with_its_arguments(tmp_path):
    repo = repo_with_justfile(tmp_path)
    done = run(tmp_path, repo, "container", "-p", "a request")
    assert done.returncode == 0, done.stderr
    assert calls(tmp_path) == ["run.sh -p a request"]


def test_container_without_run_sh_says_how_to_stamp_it(tmp_path):
    repo = repo_with_justfile(tmp_path)
    (repo / "container" / "run.sh").unlink()
    done = run(tmp_path, repo, "container")
    assert done.returncode != 0
    assert "install.py --container" in done.stderr
    assert calls(tmp_path) == []


def test_e2b_template_runs_the_script(tmp_path):
    repo = repo_with_justfile(tmp_path)
    done = run(tmp_path, repo, "e2b-template", "--name", "x")
    assert done.returncode == 0, done.stderr
    assert calls(tmp_path) == ["uv run .claude/skills/sssf/scripts/e2b_template.py --name x"]


def test_agentsh_install_finds_the_hub_and_the_main_checkout(tmp_path):
    repo = repo_with_justfile(tmp_path)
    (repo / "sub").mkdir()
    done = run(tmp_path, repo / "sub", "agentsh-install")
    assert done.returncode == 0, done.stderr
    hub, main = (tmp_path / "hub").resolve(), repo.resolve()
    assert calls(tmp_path) == [
        f"uv run --directory {hub} .claude/skills/sssf/scripts/agentsh_install.py --repo {main}"]


def test_local_just_is_loaded_when_it_exists(tmp_path):
    repo = repo_with_justfile(tmp_path)
    assert run(tmp_path, repo, "--list").returncode == 0
    (repo / "local.just").write_text("mine:\n    @echo mine\n")
    done = run(tmp_path, repo, "mine")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "mine"

"""`just openshell-setup` and scripts/openshell_image.py refuse while SSSF_ADW_ID is set.

Every command the setup runs is a stand-in that logs its call, and HOME is a temp
folder, so nothing here touches the real home, gateway service or Podman store.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "openshell_image.py"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import sandbox_openshell as osh  # noqa: E402

JUST = shutil.which("just")
needs_just = pytest.mark.skipif(not JUST, reason="just isn't installed")

RECIPE_TOOLS = ["systemctl", "podman", "openshell", "openshell-gateway", "uv"]


def standins(tmp_path: Path, names: list[str]) -> Path:
    """Executables that log their call to $STANDIN_LOG and exit 0."""
    folder = tmp_path / "bin"
    folder.mkdir()
    for name in names:
        path = folder / name
        path.write_text('#!/bin/sh\necho "$(basename "$0") $*" >> "$STANDIN_LOG"\n')
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return folder


def environment(tmp_path: Path, bin_dir: Path, adw_id: str | None = None) -> dict[str, str]:
    """The suite runs inside an agent, so SSSF_ADW_ID and SSSF_AGENT come out first."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env.pop("SSSF_ADW_ID", None)
    env.pop("SSSF_AGENT", None)
    env.update(HOME=str(home), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
               STANDIN_LOG=str(tmp_path / "standin.log"))
    if adw_id is not None:
        env["SSSF_ADW_ID"] = adw_id
    return env


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "standin.log"
    return log.read_text().splitlines() if log.exists() else []


def recipe_repo(tmp_path: Path, module: bool) -> Path:
    """A temp repo holding the shipped justfile, with /dev/kvm swapped for a real file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    text = (SKILL / "templates" / "justfile").read_text().replace("/dev/kvm", str(kvm))
    if module:
        (repo / "sssf.just").write_text(text)
        (repo / "justfile").write_text("mod sssf 'sssf.just'\n")
    else:
        (repo / "justfile").write_text(text)
    return repo


def just_call(how: str, repo: Path) -> list[str]:
    if how == "direct":
        return [JUST, "openshell-setup"]
    if how == "module":
        return [JUST, "sssf", "openshell-setup"]
    script = repo / "setup.sh"
    script.write_text(f"#!/bin/sh\n{JUST} openshell-setup\n")
    script.chmod(0o755)
    return ["sh", str(script)]


@needs_just
@pytest.mark.parametrize("how", ["direct", "module", "script"])
def test_recipe_refuses_inside_a_run(tmp_path, how):
    repo = recipe_repo(tmp_path, module=how == "module")
    bin_dir = standins(tmp_path, RECIPE_TOOLS)
    done = subprocess.run(just_call(how, repo), cwd=repo, capture_output=True, text=True,
                          env=environment(tmp_path, bin_dir, adw_id="abc12345"))
    assert done.returncode != 0
    lines = [line for line in done.stderr.splitlines() if not line.startswith("error: recipe")]
    assert len(lines) == 1, done.stderr
    assert "engineer's to run" in lines[0] and "outside an SSSF run" in lines[0]
    assert calls(tmp_path) == []
    assert list((tmp_path / "home").rglob("*")) == []


@needs_just
@pytest.mark.parametrize("module", [False, True])
def test_recipe_gets_past_the_check_without_the_variable(tmp_path, module):
    repo = recipe_repo(tmp_path, module)
    bin_dir = standins(tmp_path, RECIPE_TOOLS)
    argv = [JUST, "sssf", "openshell-setup"] if module else [JUST, "openshell-setup"]
    done = subprocess.run(argv, cwd=repo, capture_output=True, text=True,
                          env=environment(tmp_path, bin_dir))
    assert done.returncode == 0, done.stderr
    assert "Refusing" not in done.stderr
    log = calls(tmp_path)
    assert "systemctl --user enable --now openshell-gateway" in log
    assert "uv run .claude/skills/sssf/scripts/openshell_image.py --repo ." in log
    assert "openshell status" in log
    home = tmp_path / "home"
    assert (home / ".config/systemd/user/openshell-gateway.service").exists()
    assert (home / ".config/openshell/gateway.toml").exists()


@needs_just
def test_recipe_treats_an_empty_variable_as_unset(tmp_path):
    repo = recipe_repo(tmp_path, module=False)
    bin_dir = standins(tmp_path, RECIPE_TOOLS)
    done = subprocess.run([JUST, "openshell-setup"], cwd=repo, capture_output=True, text=True,
                          env=environment(tmp_path, bin_dir, adw_id=""))
    assert done.returncode == 0, done.stderr
    assert "openshell status" in calls(tmp_path)


def run_script(tmp_path: Path, names: list[str], adw_id: str | None, *extra: str):
    bin_dir = standins(tmp_path, names)
    env = environment(tmp_path, bin_dir, adw_id)
    repo = tmp_path / "home" / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    config = repo / osh.CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text((SKILL / "templates" / "sssf.config.yaml").read_text())
    return subprocess.run([sys.executable, "-I", str(SCRIPT), "--repo", str(repo), *extra],
                          env=env, capture_output=True, text=True)


def test_script_refuses_inside_a_run(tmp_path):
    done = run_script(tmp_path, ["podman", "uv"], "abc12345")
    assert done.returncode != 0
    lines = done.stderr.splitlines()
    assert len(lines) == 1, done.stderr
    assert "engineer's to run" in lines[0] and "outside an SSSF run" in lines[0]
    assert done.stdout == ""
    assert calls(tmp_path) == []


def test_script_print_works_inside_a_run(tmp_path):
    done = run_script(tmp_path, ["podman"], "abc12345", "--print")
    assert done.returncode == 0, done.stderr
    assert re.match(r"localhost/sssf-openshell:[0-9a-f]{12}", done.stdout.splitlines()[0])
    assert calls(tmp_path) == []


def test_script_builds_without_the_variable(tmp_path):
    done = run_script(tmp_path, ["podman"], None)
    assert done.returncode == 0, done.stderr
    builds = [c for c in calls(tmp_path) if c.startswith("podman build -t localhost/sssf-openshell:")]
    assert len(builds) == 1

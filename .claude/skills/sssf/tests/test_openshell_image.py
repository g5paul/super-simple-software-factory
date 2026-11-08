"""scripts/openshell_image.py: the image tag and Containerfile `--sandbox openshell`
uses. `--print` runs no Podman.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "openshell_image.py"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import sandbox_openshell as osh  # noqa: E402


def run_script(tmp_path, data_dir=None):
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if data_dir:
        config = repo / osh.CONFIG
        config.parent.mkdir(parents=True)
        config.write_text(f"defaults:\n  data_dir: {data_dir}\n")
    env = {**os.environ, "HOME": str(home)}
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), "--repo", str(repo), "--print"],
                          env=env, capture_output=True, text=True)
    return repo, home, done


def test_refuses_a_layout_with_no_shared_folder(tmp_path):
    outside = tmp_path / "outside"
    _, _, done = run_script(tmp_path, str(outside))
    assert done.returncode == 2
    assert "share no folder below the home folder" in done.stderr


def test_prints_the_image_for_a_normal_layout(tmp_path):
    repo, _, done = run_script(tmp_path)
    assert done.returncode == 0, done.stderr
    assert re.match(r"localhost/sssf-openshell:[0-9a-f]{12}\n", done.stdout)
    assert f"chown -R ubuntu:ubuntu {repo.resolve()}" in done.stdout


def test_the_script_and_provider_agree_on_the_tag(tmp_path):
    repo, home, done = run_script(tmp_path)
    os.environ["HOME"], old = str(home), os.environ["HOME"]
    try:
        tag = osh.image_tag(osh.folder_for(repo))
    finally:
        os.environ["HOME"] = old
    assert done.stdout.splitlines()[0] == tag

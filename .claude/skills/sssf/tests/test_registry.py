"""install.py records each repo it installs into, so one dashboard can show them all.

`just obs-all` serves every repo in the registry. Install and --update both
record the repo, so repos installed before the registry existed join it on
their next update. The registry holds the repo root and the trace db path.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def install(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), *args],
                            cwd=cwd, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def projects(registry: Path) -> dict:
    return json.loads(registry.read_text())["projects"]


def repo(tmp_path: Path, *parts: str) -> Path:
    path = tmp_path.joinpath(*parts)
    path.mkdir(parents=True)
    return path


def test_install_registers_the_repo(tmp_path, private_registry):
    root = repo(tmp_path, "shop")
    install(root)
    assert projects(private_registry) == {
        "shop": {"root": str(root), "db": "adws/adw_data/sssf.db"},
    }


def test_update_registers_a_repo_installed_before_the_registry(tmp_path, private_registry):
    root = repo(tmp_path, "shop")
    install(root)
    private_registry.unlink()
    install(root, "--update")
    assert projects(private_registry)["shop"]["root"] == str(root)


def test_registering_twice_keeps_one_entry(tmp_path, private_registry):
    root = repo(tmp_path, "shop")
    install(root)
    install(root, "--update")
    assert list(projects(private_registry)) == ["shop"]


def test_two_repos_with_the_same_folder_name_get_different_names(tmp_path, private_registry):
    first, second = repo(tmp_path, "a", "shop"), repo(tmp_path, "b", "shop")
    install(first)
    install(second)
    names = {entry["root"]: name for name, entry in projects(private_registry).items()}
    assert names == {str(first): "shop", str(second): "b-shop"}


def test_a_registry_that_cannot_be_written_does_not_fail_the_install(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("SSSF_REGISTRY", str(blocker / "projects.json"))
    out = install(repo(tmp_path, "shop")).stdout
    assert "could not record" in out


def test_install_and_update_say_where_the_repo_is_listed(tmp_path, private_registry):
    root = repo(tmp_path, "shop")
    assert f"registered as shop in {private_registry}" in install(root).stdout
    assert f"listed as shop in {private_registry}" in install(root, "--update").stdout

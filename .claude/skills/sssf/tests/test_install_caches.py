"""A tool's cache in the skill's templates is never stamped into a repo.

A ruff check run inside templates/adws/adw_modules once left a .ruff_cache
there, and the next update stamped its files into sssf-sandbox as SSSF code.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def load_install():
    spec = importlib.util.spec_from_file_location("install", SKILL / "scripts" / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tool_caches_in_the_templates_are_left_out(tmp_path, monkeypatch):
    install = load_install()
    modules = tmp_path / "templates" / "adws" / "adw_modules"
    for cache in (".ruff_cache/0.17.3", ".pytest_cache", ".mypy_cache", "__pycache__"):
        (modules / cache).mkdir(parents=True)
        (modules / cache / "junk").write_text("x")
    (modules / "agents.py").write_text("")
    monkeypatch.setattr(install, "TEMPLATES", tmp_path / "templates")
    stamped = [str(dest.relative_to(tmp_path / "repo")) for _, dest in install.targets(tmp_path / "repo", tmp_path / "roster.yaml")]
    assert "adws/adw_modules/agents.py" in stamped
    assert not [path for path in stamped if "cache" in path]

"""operator_env leaves out every `uv run` environment an ADW was launched through.

An ADW launched by another ADW sits inside two of them. `just trial` runs
adw_trial.py under `uv run`, and each attempt runs adw_simple_sdlc.py under a
second `uv run`. operator_env dropped only the inner one (VIRTUAL_ENV), so the
trial's bin stayed first on PATH and the agents' `python3` was the trial's.
Under local_guard: agentsh the agents can't read ~/.cache/uv, so that Python
couldn't start, and every Bash call in sssf-sandbox's Fable trials failed.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import utils


def venv(root: Path) -> str:
    """A venv folder like the ones uv makes: a bin/ beside a pyvenv.cfg."""
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\nuv = 0.9.0\n")
    return str(root / "bin")


def test_a_nested_uv_run_leaves_neither_environment_on_path(tmp_path, monkeypatch):
    cache = tmp_path / "uv-cache"
    outer = venv(cache / "environments-v2" / "adw-trial-1234")
    inner = venv(cache / "environments-v2" / "adw-simple-sdlc-5678")
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(inner).parent))
    monkeypatch.setenv("PATH", os.pathsep.join([inner, outer, "/usr/local/bin", "/usr/bin"]))
    assert utils.operator_env()["PATH"].split(os.pathsep) == ["/usr/local/bin", "/usr/bin"]


def test_the_engineers_own_venv_stays_on_path(tmp_path, monkeypatch):
    # A venv the engineer activated is part of what their terminal sees.
    cache = tmp_path / "uv-cache"
    adw = venv(cache / "environments-v2" / "adw-simple-sdlc-5678")
    own = venv(tmp_path / "project" / ".venv")
    monkeypatch.setenv("UV_CACHE_DIR", str(cache))
    monkeypatch.setenv("VIRTUAL_ENV", str(Path(adw).parent))
    monkeypatch.setenv("PATH", os.pathsep.join([adw, own, "/usr/bin"]))
    assert utils.operator_env()["PATH"].split(os.pathsep) == [own, "/usr/bin"]

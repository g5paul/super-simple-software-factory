"""The two starter rosters install, validate, and agree on everything but the harness.

`install.py --coding-agent claude_code` stamps a roster that runs on Claude
Code without edits. The Pi roster stays the default. Both rosters must give
each agent the same prompts and the same write boundary: switching harness
must never widen what an agent may change.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents

CONFIG = Path("adws/adw_sssf_config/sssf.config.yaml")
ROSTER = ["planner", "builder", "scout", "reviewer", "documenter"]


def script(name: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SKILL / "scripts" / name), *args],
                          capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_install_defaults_to_the_pi_roster(repo):
    assert script("install.py").returncode == 0
    cfg = agents.load_config(str(CONFIG))
    assert {agent.coding_agent for agent in cfg.agents} == {"pi"}


def test_install_stamps_a_claude_code_roster_that_validates(repo):
    result = script("install.py", "--coding-agent", "claude_code")
    assert result.returncode == 0, result.stderr
    cfg = agents.load_config(str(CONFIG))
    assert {agent.coding_agent for agent in cfg.agents} == {"claude_code"}
    agents.validate(cfg, ROSTER)              # raises SystemExit on any problem


def test_install_tells_a_claude_code_user_no_key_is_needed(repo):
    out = script("install.py", "--coding-agent", "claude_code").stdout
    assert "claude" in out and "no API key" in out


def test_make_config_writes_the_claude_code_roster(repo):
    assert script("make_config.py", "--coding-agent", "claude_code").returncode == 0
    assert yaml.safe_load(CONFIG.read_text())["defaults"]["coding_agent"] == "claude_code"


def test_unknown_coding_agent_is_refused(repo):
    assert script("install.py", "--coding-agent", "codex").returncode != 0


def _roster(name: str) -> dict:
    return yaml.safe_load((SKILL / "templates" / name).read_text())


def test_rosters_agree_on_prompts_and_write_boundaries():
    pi, cc = _roster("sssf.config.yaml"), _roster("sssf.config.claude_code.yaml")
    assert cc["defaults"]["protected_files"] == pi["defaults"]["protected_files"]
    assert cc["defaults"]["data_dir"] == pi["defaults"]["data_dir"]
    by_name = {agent["name"]: agent for agent in pi["agents"]}
    assert [agent["name"] for agent in cc["agents"]] == list(by_name)
    for agent in cc["agents"]:
        twin = by_name[agent["name"]]
        assert agent["prompt_engineering"] == twin["prompt_engineering"], agent["name"]
        assert agent.get("writes") == twin.get("writes"), agent["name"]
        assert agent["purpose"] == twin["purpose"], agent["name"]

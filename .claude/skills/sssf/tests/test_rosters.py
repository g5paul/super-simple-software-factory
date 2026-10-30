"""The starter rosters install, validate, and agree on everything but the harness.

`install.py --coding-agent claude_code` stamps a roster that runs on Claude
Code without edits, and `install.py --chatgpt` one that runs on Pi signed into
a ChatGPT subscription. The Pi roster stays the default. Both rosters must give
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


def test_install_stamps_a_chatgpt_roster_that_validates(repo):
    result = script("install.py", "--chatgpt")
    assert result.returncode == 0, result.stderr
    cfg = agents.load_config(str(CONFIG))
    assert {agent.coding_agent for agent in cfg.agents} == {"pi"}
    assert all(agent.model.startswith("openai/") for agent in cfg.agents)
    agents.validate(cfg, ROSTER)


def test_the_chatgpt_roster_uses_the_tested_models(repo):
    # README "Models as of 2026-10-29": sol for the agents that plan, build,
    # and review; terra for the scout and documenter.
    script("install.py", "--chatgpt")
    models = {agent.name: agent.model for agent in agents.load_config(str(CONFIG)).agents}
    assert models["scout"] == models["documenter"] == "openai/gpt-6.1-terra"
    for name in ("planner", "builder", "ticket_builder", "reviewer", "standards_reviewer", "spec_reviewer"):
        assert models[name] == "openai/gpt-6.1-sol", name


def test_install_tells_a_chatgpt_user_how_to_sign_in_and_guard_keys(repo):
    out = script("install.py", "--chatgpt").stdout
    assert "/login" in out and "SSSF_PI_AUTH=subscription" in out


def test_chatgpt_runs_on_pi_only(repo):
    result = script("install.py", "--chatgpt", "--coding-agent", "claude_code")
    assert result.returncode != 0
    assert not CONFIG.exists()


def test_update_keeps_the_chatgpt_roster(repo):
    script("install.py", "--chatgpt")
    CONFIG.unlink()
    assert script("install.py", "--update").returncode == 0
    assert yaml.safe_load(CONFIG.read_text())["agents"][0]["model"].startswith("openai/")


def test_make_config_writes_the_chatgpt_roster(repo):
    assert script("make_config.py", "--chatgpt").returncode == 0
    assert yaml.safe_load(CONFIG.read_text())["defaults"]["model"].startswith("openai/")


def test_unknown_coding_agent_is_refused(repo):
    assert script("install.py", "--coding-agent", "codex").returncode != 0


def _roster(name: str) -> dict:
    return yaml.safe_load((SKILL / "templates" / name).read_text())


@pytest.mark.parametrize("other", ["sssf.config.claude_code.yaml", "sssf.config.chatgpt.yaml"])
def test_rosters_agree_on_prompts_and_write_boundaries(other):
    pi, cc = _roster("sssf.config.yaml"), _roster(other)
    assert cc["defaults"]["protected_files"] == pi["defaults"]["protected_files"]
    assert cc["defaults"]["data_dir"] == pi["defaults"]["data_dir"]
    by_name = {agent["name"]: agent for agent in pi["agents"]}
    assert [agent["name"] for agent in cc["agents"]] == list(by_name)
    for agent in cc["agents"]:
        twin = by_name[agent["name"]]
        assert agent["prompt_engineering"] == twin["prompt_engineering"], agent["name"]
        assert agent.get("writes") == twin.get("writes"), agent["name"]
        assert agent["purpose"] == twin["purpose"], agent["name"]


def test_install_ignores_the_built_visualizer(repo):
    # `just obs` builds the UI into the skill's copy. Commit phases run
    # `git add -A`, so the build must be ignored or it lands in a commit.
    script("install.py")
    assert ".claude/skills/sssf/apps/visualizer/dist/" in Path(".gitignore").read_text().splitlines()


def test_install_says_to_exclude_sssf_from_the_projects_tools(repo):
    # Repo-wide linters and package builds pick up the stamped code. The
    # project owns those configs, so the installer only says what to do.
    out = script("install.py").stdout
    assert "exclude adws/ and .claude/" in out


@pytest.mark.parametrize("roster", ["sssf.config.yaml", "sssf.config.claude_code.yaml", "sssf.config.chatgpt.yaml"])
def test_only_the_planner_may_change_a_spec(roster):
    # commit_plan puts the spec on record before any code exists. A later edit,
    # even a formatter's, would land in the code commit under `git add -A`.
    from adw_modules import permissions
    cfg = agents.load_config(str(SKILL / "templates" / roster))
    spec = "specs/abc123_feature.md"
    by_name = {agent.name: agent for agent in cfg.agents}
    assert permissions.permitted(spec, by_name["planner"], cfg)
    for name in ("builder", "documenter", "reviewer", "scout"):
        assert not permissions.permitted(spec, by_name[name], cfg), name


def test_a_broad_glob_does_not_unlock_a_protected_path():
    from adw_modules import permissions
    from adw_modules.data_types import AgentConfig
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.claude_code.yaml"))
    documenter = next(agent for agent in cfg.agents if agent.name == "documenter")
    assert permissions.permitted("README.md", documenter, cfg)
    assert permissions.permitted("app_docs/abc123_feature.md", documenter, cfg)
    assert not permissions.permitted("specs/abc123_feature.md", documenter, cfg)
    # A path inside a protected directory names it, so it still unlocks.
    narrow = documenter.model_copy(update={"writes": ["specs/notes/"]})
    assert isinstance(narrow, AgentConfig)
    assert permissions.permitted("specs/notes/x.md", narrow, cfg)
    assert not permissions.permitted("specs/abc123_feature.md", narrow, cfg)

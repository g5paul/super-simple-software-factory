"""A task that needs a protected edit gets planned around it, not into a breach.

In pi-agent-observability's run e19b9a8c the task asked for a new block in
quality.py. Nothing told the planner that path was protected, so it put the
edit in the builder's steps. The builder made it, permissions.enforce rolled it
back and stopped the run, and ten good files were left uncommitted with no word
on which ones. The planner and builder now see the protected paths, and a
breach names the work it leaves behind.

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
PROMPTS = SKILL / "templates" / "prompt_engineering"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, permissions
from adw_modules.data_types import (AgentCall, AgentConfig, ConfigDefaults,
                                    PlanOutput, PromptEngineering, SSSFConfig)

PROTECTED = ["adws/adw_modules/", "specs/"]


def config() -> SSSFConfig:
    return SSSFConfig(defaults=ConfigDefaults(data_dir="adws/adw_data",
                                              protected_files=PROTECTED))


def builder() -> AgentConfig:
    return AgentConfig(name="builder",
                       prompt_engineering=PromptEngineering(system="s.md", user="u.md"))


def test_every_prompt_can_name_the_protected_paths(tmp_path):
    run = SimpleNamespace(cfg=config(), context_handoff_dir=tmp_path)
    phase = SimpleNamespace(params=SimpleNamespace(name="plan"))
    call = AgentCall(output_type=PlanOutput, prompt="x")
    variables = agents.prompt_variables(run, phase, call)
    assert variables["protected_files"] == "- `adws/adw_modules/`\n- `specs/`"


@pytest.mark.parametrize("agent", ["planner", "builder", "ticket_builder"])
def test_builders_and_the_planner_are_shown_the_protected_paths(agent):
    assert "{{protected_files}}" in (PROMPTS / agent / "user.md").read_text()


def test_planner_hands_protected_edits_to_the_engineer():
    text = (PROMPTS / "planner" / "system.md").read_text()
    assert "## For the engineer" in text
    assert "tests that import or exercise protected code" in text


def test_builder_leaves_protected_edits_alone():
    text = (PROMPTS / "builder" / "system.md").read_text()
    assert "Never modify a path in `protected_files`" in text
    assert "## For the engineer" in text


def test_ticket_builder_leaves_protected_edits_to_the_engineer():
    text = (PROMPTS / "ticket_builder" / "system.md").read_text()
    assert "Never modify a path in `protected_files`" in text
    assert "say what the engineer must change" in text


def git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True)


def test_a_breach_names_the_work_it_leaves_uncommitted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    for path in ("adws/adw_modules/quality.py", "src/db.ts"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("old\n")
    git("add", "-A")
    git("commit", "-qm", "base")

    run = SimpleNamespace(cfg=config(), repo_root=tmp_path)
    before = permissions.snapshot(run)
    (tmp_path / "adws/adw_modules/quality.py").write_text("new\n")
    (tmp_path / "src/db.ts").write_text("new\n")
    (tmp_path / "tsconfig.json").write_text("{}\n")

    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, builder(), before)

    message = str(breach.value)
    assert "adws/adw_modules/quality.py — rolled back" in message
    kept = message.split("left uncommitted and unreviewed:", 1)[1]
    assert "src/db.ts" in kept and "tsconfig.json" in kept
    assert "quality.py" not in kept
    assert (tmp_path / "adws/adw_modules/quality.py").read_text() == "old\n"

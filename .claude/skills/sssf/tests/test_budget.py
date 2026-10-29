"""Cost caps: SSSF stops spending before an unattended run gets expensive.

Two caps, both in the roster:

- `max_call_cost` on an agent is passed to `claude -p --max-budget-usd`, so a
  runaway call ends itself. Claude Code counts it per call, not per session,
  and checks it after each API request (tested against a real subscription
  login, 2026-10-29). A call that hits it fails with no retry, and what it
  spent still counts toward the run.
- `defaults.max_run_cost` is checked before EVERY send, retries and
  corrections included, against what the run has spent so far. Checking only
  when an agent starts would let a phase's retries spend the call cap again
  and again.

Every stop records why in the trace, and when the tree holds uncommitted work
it prints how to look at it, keep it, or drop it. Nothing is discarded.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agents, budget, session
from adw_modules.data_types import (AgentCall, AgentConfig, AgentResult, ConfigDefaults,
                                    GenericOutput, ObservabilityConfig, PhaseParams, SSSFConfig)

from test_agent_cc import fake, request  # noqa: F401  (fixture and helper)

OK = json.dumps({"status": "success", "summary": "done"})


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    """A real Run in a temp repo, with a scripted coding agent behind it."""
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("hi\n")
    prompt = tmp_path / "p.md"
    prompt.write_text("{{prompt}}")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    calls: list = []

    def make(script: list, max_run_cost=5.0, max_call_cost=1.5):
        def run(req, on_event=None, on_spawn=None, on_exit=None):
            calls.append(req)
            step = script[len(calls) - 1]
            if isinstance(step, Exception):
                raise step
            return step
        monkeypatch.setattr(agents, "_interface",
                            lambda agent: SimpleNamespace(run=run, ToolCallTracker=agent_cc.ToolCallTracker))
        cfg = SSSFConfig(
            defaults=ConfigDefaults(data_dir="adws/adw_data", max_run_cost=max_run_cost),
            observability=ObservabilityConfig(db="adws/adw_data/sssf.db"),
            agents=[AgentConfig(name="builder", coding_agent="claude_code",
                                model="anthropic/claude-sonnet-5-5", max_call_cost=max_call_cost,
                                prompt_engineering={"system": str(prompt), "user": str(prompt)})])
        return session.ensure(cfg, None)

    make.calls = calls
    return make


def build(run):
    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Build the thing for the budget tests")) as ph:
        return ph.call(AgentCall(output_type=GenericOutput, prompt="build it"))


def events(run, name: str) -> list[dict]:
    with sqlite3.connect("adws/adw_data/sssf.db") as conn:
        rows = conn.execute("select payload_json from events where adw_id=? and name=?",
                            (run.adw_id, name)).fetchall()
    return [json.loads(r[0]) for r in rows]


def result(cost: float, text: str = OK) -> AgentResult:
    return AgentResult(text=text, cost=cost, tokens=100)


# ── the roster ───────────────────────────────────────────────────────────────

def test_a_run_is_capped_at_five_dollars_by_default():
    assert ConfigDefaults().max_run_cost == 5.0


def test_every_claude_agent_has_a_call_cap_and_the_run_has_one():
    roster = yaml.safe_load((SKILL / "templates" / "sssf.config.claude_code.yaml").read_text())
    assert roster["defaults"]["max_run_cost"] == 5.0
    for agent in roster["agents"]:
        assert agent.get("max_call_cost", 0) > 0, agent["name"]


# ── the call cap, in agent_cc ────────────────────────────────────────────────

def test_the_call_cap_reaches_claude(tmp_path):
    argv = agent_cc._command(request(tmp_path, max_cost=1.5), resume=False)
    assert argv[argv.index("--max-budget-usd") + 1] == "1.5"
    assert "--max-budget-usd" not in agent_cc._command(request(tmp_path), resume=False)


def test_a_call_that_hits_its_cap_says_so_and_keeps_its_cost(tmp_path, fake):
    lines = (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()
    final = json.loads(lines[-1])
    final.update(subtype="error_max_budget_usd", is_error=True,
                 errors=["Reached maximum budget ($0.5)"])
    turn = tmp_path / "turn.jsonl"
    turn.write_text("\n".join([*lines[:-1], json.dumps(final)]) + "\n")
    fake(turn)
    with pytest.raises(budget.CallBudgetStop, match=r"\$0.5") as stop:
        agent_cc.run(request(tmp_path, max_cost=0.5))
    assert stop.value.result.cost == pytest.approx(final["total_cost_usd"])


# ── both caps, in a real run ─────────────────────────────────────────────────

def test_execute_passes_the_agents_call_cap(factory):
    run = factory([result(0.1)], max_call_cost=0.75)
    build(run)
    assert factory.calls[0].max_cost == 0.75


def test_a_spent_run_stops_before_the_next_agent(factory):
    run = factory([result(0.1)], max_run_cost=2.0)
    run.add_usage(0, 2.0)
    with pytest.raises(budget.RunBudgetStop, match=r"\$2.00 of max_run_cost \$2.00.*builder"):
        build(run)
    assert factory.calls == []
    assert events(run, "budget_stop")[0]["spent"] == 2.0


def test_the_run_cap_is_checked_before_every_send(factory):
    # The first send returns bad JSON and spends past the cap; the correction
    # it would earn must not be sent.
    run = factory([result(2.5, text="not json"), result(0.1)], max_run_cost=2.0)
    with pytest.raises(budget.RunBudgetStop):
        build(run)
    assert len(factory.calls) == 1


def test_a_call_cap_stop_counts_its_cost_and_is_not_retried(factory):
    stop = budget.CallBudgetStop("builder reached max_call_cost", result=result(1.6, text=""))
    run = factory([stop, result(0.1)])
    with pytest.raises(budget.CallBudgetStop):
        build(run)
    assert len(factory.calls) == 1
    assert run.cost == pytest.approx(1.6)
    assert events(run, "call_budget_stop")[0]["agent"] == "builder"


def test_a_stop_says_how_to_handle_uncommitted_work(factory):
    run = factory([result(0.1)], max_run_cost=1.0)
    run.add_usage(0, 1.0)
    Path("README.md").write_text("half done\n")
    with pytest.raises(budget.RunBudgetStop) as stop:
        build(run)
    message = str(stop.value)
    assert "git diff" in message and "git stash" in message and "git checkout ." in message


def test_a_stop_on_a_clean_tree_does_not_mention_git(factory):
    run = factory([result(0.1)], max_run_cost=1.0)
    run.add_usage(0, 1.0)
    with pytest.raises(budget.RunBudgetStop) as stop:
        build(run)
    assert "git stash" not in str(stop.value)


def test_none_turns_the_run_cap_off(factory):
    run = factory([result(0.1)], max_run_cost=None)
    run.add_usage(0, 100.0)
    assert build(run).status == "success"

"""Cost caps: SSSF stops spending before an unattended run gets expensive.

Two caps are in the roster, and two more, max_live_cost and max_daily_cost,
in repo.yaml (tested at the end):

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
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agents, budget, session
from adw_modules.tracer import Tracer
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


def test_a_run_records_its_caps_so_the_dashboard_can_draw_them(factory):
    run = factory([result(0.1)], max_run_cost=3.0)
    assert events(run, "run_caps")[-1] == {"max_run_cost": 3.0, "rate_limit_stop": 0.8, "max_live_cost": None,
                                           "max_daily_cost": None,
                                           "machine_daily_cost": None}


# ── max_live_cost: what live runs spend together (parallel-worktree-runs 39 to 48) ──

def live_cap(cap: float) -> None:
    path = Path("adws/adw_sssf_config/repo.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"max_live_cost: {cap}\n")


def live_run(adw_id: str, spent: float, earlier: float = 0.0, pid: int | None = None) -> None:
    """Another run in the trace: its process started at 00:01, it spent `spent`
    since, and `earlier` in an attempt before that one."""
    import os
    import socket
    with sqlite3.connect("adws/adw_data/sssf.db") as conn:
        conn.execute("insert into sessions (adw_id, status, engineer, started_at) "
                     "values (?, 'running', 't', '2026-01-01T00:00:00.000+00:00')", (adw_id,))
        conn.execute("insert into processes (adw_id, kind, name, pid, command, host, started_at) "
                     "values (?, 'adw', '', ?, 'adw_x.py', ?, '2026-01-01T00:01:00.000+00:00')",
                     (adw_id, pid or os.getpid(), socket.gethostname()))
        for i, (cost, at) in enumerate(((earlier, "00:00:30"), (spent, "00:02:00"))):
            conn.execute("insert into events (event_id, adw_id, phase_id, type, name, payload_json, "
                         "started_at) values (?, ?, '', 'agent_end', 'builder', ?, ?)",
                         (f"evt_{adw_id}_{i}", adw_id, json.dumps({"cost": cost}),
                          f"2026-01-01T{at}.000+00:00"))


def test_live_runs_stop_together_at_max_live_cost(factory):
    run = factory([result(0.1)])
    live_cap(1.0)
    live_run("other111", 1.0)
    with pytest.raises(budget.LiveBudgetStop, match=r"max_live_cost \$1.00.*other111 \$1.00"):
        build(run)
    assert factory.calls == []
    assert events(run, "live_budget_stop")[0]["cap"] == 1.0


def test_this_runs_own_spend_counts_and_is_checked_before_every_send(factory):
    run = factory([result(0.5, text="not json"), result(0.1)])
    live_cap(1.0)
    live_run("other111", 0.6)
    with pytest.raises(budget.LiveBudgetStop, match=rf"{run.adw_id} \$0.50"):
        build(run)
    assert len(factory.calls) == 1


def test_only_each_runs_current_attempt_counts(factory):
    run = factory([result(0.1)])
    live_cap(1.0)
    live_run("other111", 0.5, earlier=5.0)
    build(run)


def test_a_dead_run_does_not_count(factory):
    done = subprocess.Popen(["true"])
    done.wait()
    run = factory([result(0.1)])
    live_cap(1.0)
    live_run("other111", 5.0, pid=done.pid)
    build(run)


def test_max_live_cost_is_off_by_default(factory):
    run = factory([result(0.1)])
    live_run("other111", 100.0)
    build(run)


def test_a_launch_at_the_cap_is_refused_before_any_worktree(factory):
    run = factory([])
    live_cap(1.0)
    live_run("other111", 1.0)
    with pytest.raises(SystemExit, match=r"LiveBudgetStop: .*other111 \$1.00"):
        session.ensure(run.cfg, None, use_worktree=True)
    assert not Path("adws/adw_data/worktrees").exists()


def test_a_run_records_max_live_cost_for_the_dashboard(factory):
    live_cap(2.5)
    run = factory([])
    assert events(run, "run_caps")[-1]["max_live_cost"] == 2.5


# ── max_daily_cost: what the repo spent in the last 24 hours (parallel-worktree-runs 49 to 57) ──

def daily_cap(cap: float) -> None:
    path = Path("adws/adw_sssf_config/repo.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"max_daily_cost: {cap}\n")


def spent_ago(adw_id: str, cost: float, hours: float) -> datetime:
    """A finished run's call that ended `hours` ago. Returns when."""
    at = datetime.now(timezone.utc) - timedelta(hours=hours)
    with sqlite3.connect("adws/adw_data/sssf.db") as conn:
        conn.execute("insert or ignore into sessions (adw_id, status, engineer, started_at) "
                     "values (?, 'success', 't', ?)", (adw_id, at.isoformat(timespec="milliseconds")))
        conn.execute("insert into events (event_id, adw_id, phase_id, type, name, payload_json, "
                     "started_at) values (?, ?, '', 'agent_end', 'builder', ?, ?)",
                     (f"evt_{adw_id}_{hours}", adw_id, json.dumps({"cost": cost}),
                      at.isoformat(timespec="milliseconds")))
    return at


def test_finished_runs_in_the_last_24_hours_stop_a_run(factory):
    run = factory([result(0.1)])
    daily_cap(1.0)
    spent_ago("done1111", 1.0, hours=2)
    with pytest.raises(budget.DailyBudgetStop, match=r"\$1.00 in the last 24 hours.*max_daily_cost \$1.00"):
        build(run)
    assert factory.calls == []
    assert events(run, "daily_budget_stop")[0]["cap"] == 1.0


def test_spend_older_than_24_hours_does_not_count(factory):
    run = factory([result(0.1)])
    daily_cap(1.0)
    spent_ago("done1111", 5.0, hours=25)
    build(run)


def test_the_stop_says_when_enough_spend_ages_out(factory):
    run = factory([result(0.1)])
    daily_cap(1.0)
    oldest = spent_ago("done1111", 0.6, hours=20)
    spent_ago("done2222", 0.6, hours=2)
    clears = oldest + timedelta(hours=24)
    with pytest.raises(budget.DailyBudgetStop, match=f"drops under at {clears:%Y-%m-%d %H:%M} UTC"):
        build(run)


def test_this_runs_own_spend_counts_toward_the_day_before_every_send(factory):
    run = factory([result(0.5, text="not json"), result(0.1)])
    daily_cap(1.0)
    spent_ago("done1111", 0.6, hours=2)
    with pytest.raises(budget.DailyBudgetStop, match=r"\$1.10 in the last 24 hours"):
        build(run)
    assert len(factory.calls) == 1


def test_max_daily_cost_is_off_by_default(factory):
    run = factory([result(0.1)])
    spent_ago("done1111", 100.0, hours=1)
    build(run)


def test_a_launch_over_the_day_is_refused_before_any_worktree(factory):
    run = factory([])
    daily_cap(1.0)
    spent_ago("done1111", 1.0, hours=1)
    with pytest.raises(SystemExit, match=r"DailyBudgetStop: .*drops under at .* UTC"):
        session.ensure(run.cfg, None, use_worktree=True)
    assert not Path("adws/adw_data/worktrees").exists()


def test_a_run_records_max_daily_cost_for_the_dashboard(factory):
    daily_cap(20.0)
    run = factory([])
    assert events(run, "run_caps")[-1]["max_daily_cost"] == 20.0


# ── max_daily_cost in limits.yaml: every repo on the machine (parallel-worktree-runs 73 to 82) ──

def machine_cap(registry: Path, cap: float) -> None:
    (registry.parent / "limits.yaml").write_text(f"max_daily_cost: {cap}\n")


def other_repo(registry: Path, tmp_path: Path, name: str, cost: float, hours: float = 1) -> Path:
    """A registered repo whose trace holds one call that ended `hours` ago."""
    root = tmp_path.parent / f"{tmp_path.name}-{name}"
    db = root / "adws" / "adw_data" / "sssf.db"
    db.parent.mkdir(parents=True)
    Tracer(str(db), os.devnull).conn.close()
    at = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="milliseconds")
    with sqlite3.connect(db) as conn:
        conn.execute("insert into events (event_id, adw_id, phase_id, type, name, payload_json, "
                     "started_at) values (?, 'other111', '', 'agent_end', 'builder', ?, ?)",
                     (f"evt_{name}", json.dumps({"cost": cost}), at))
    register(registry, name, root)
    return root


def register(registry: Path, name: str, root: Path) -> None:
    data = json.loads(registry.read_text()) if registry.exists() else {"projects": {}}
    data["projects"][name] = {"root": str(root)}
    registry.write_text(json.dumps(data))


def test_every_registered_repos_day_stops_a_run(factory, private_registry, tmp_path):
    run = factory([result(0.1)])
    machine_cap(private_registry, 2.0)
    other_repo(private_registry, tmp_path, "yt", 1.5)
    spent_ago("done1111", 0.6, hours=2)
    with pytest.raises(budget.MachineBudgetStop,
                       match=r"runs on this machine have spent \$2.10 in the last 24 hours "
                             r"\(yt \$1.50, .* \$0.60\).*max_daily_cost \$2.00 in .*limits.yaml"):
        build(run)
    assert factory.calls == []
    assert events(run, "machine_budget_stop")[0]["cap"] == 2.0


def test_the_launching_repo_counts_though_the_registry_lacks_it(factory, private_registry):
    run = factory([result(0.1)])
    machine_cap(private_registry, 1.0)
    spent_ago("done1111", 1.0, hours=2)
    with pytest.raises(budget.MachineBudgetStop):
        build(run)


def test_a_registered_repo_that_cant_be_read_counts_nothing(factory, private_registry, tmp_path):
    run = factory([result(0.1)])
    machine_cap(private_registry, 1.0)
    register(private_registry, "gone", tmp_path.parent / "no-such-repo")
    broken = tmp_path.parent / f"{tmp_path.name}-broken"
    (broken / "adws" / "adw_data").mkdir(parents=True)
    (broken / "adws" / "adw_data" / "sssf.db").write_text("not a database")
    register(private_registry, "broken", broken)
    build(run)
    assert not (tmp_path.parent / "no-such-repo").exists()


def test_the_machine_cap_is_off_without_limits_yaml(factory, private_registry, tmp_path):
    run = factory([result(0.1)])
    other_repo(private_registry, tmp_path, "yt", 100.0)
    build(run)


def test_a_launch_over_the_machines_day_is_refused(factory, private_registry, tmp_path):
    run = factory([])
    machine_cap(private_registry, 1.0)
    other_repo(private_registry, tmp_path, "yt", 1.0)
    with pytest.raises(SystemExit, match=r"MachineBudgetStop: .*drops under at .* UTC"):
        session.ensure(run.cfg, None, use_worktree=True)
    assert not Path("adws/adw_data/worktrees").exists()


def test_a_run_records_the_machine_cap_for_the_dashboard(factory, private_registry):
    machine_cap(private_registry, 30.0)
    run = factory([])
    assert events(run, "run_caps")[-1]["machine_daily_cost"] == 30.0


def test_a_broken_limits_yaml_stops_runs_instead_of_turning_the_cap_off(factory, private_registry):
    run = factory([result(0.1)])
    (private_registry.parent / "limits.yaml").write_text("max_daily_cost: lots\n")
    with pytest.raises(budget.MachineBudgetStop, match=r"limits.yaml can't be read .*positive number"):
        build(run)
    assert factory.calls == []

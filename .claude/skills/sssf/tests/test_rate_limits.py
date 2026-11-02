"""SSSF stops before an agent call that would run into a Claude usage limit.

A subscription login has a 5-hour and a 7-day usage window. Claude Code reports
how full each one is in a `rate_limit_event` on every send. agent_cc records
the newest report in a file beside the project registry, so every repo on the
machine sees the same account. Before a Claude Code agent starts, agents.py
reads it, and stops the run when a window is at or above
`defaults.rate_limit_stop`. Stopping at a phase boundary beats a run that dies
halfway with a rate-limit error.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import account, agent_cc, agents
from adw_modules.data_types import ConfigDefaults

from test_agent_cc import fake, request  # noqa: F401  (fixture and helper)

NOW = 1_793_290_000


def info(five_hour: float, seven_day: float = 0.05, status: str = "allowed",
         reset_in: int = 3600) -> dict:
    return {"status": status, "resetsAt": NOW + reset_in, "rateLimitType": "five_hour",
            "overageStatus": "rejected", "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": five_hour, "resetsAt": NOW + reset_in, "status": status},
                "seven_day": {"utilization": seven_day, "resetsAt": NOW + 86_400, "status": "allowed"}}}


def test_the_sample_sits_beside_the_registry(private_registry):
    assert account.path() == private_registry.parent / "rate_limits.json"


def test_record_then_latest(private_registry):
    account.record(info(0.42), at="2026-10-29T16:00:00Z", source="yt-summarizer")
    sample = account.latest()
    assert sample["info"]["unifiedWindows"]["five_hour"]["utilization"] == 0.42
    assert sample["recorded_at"] == "2026-10-29T16:00:00Z"
    assert sample["source"] == "yt-summarizer"


def test_an_older_sample_never_replaces_a_newer_one(private_registry):
    account.record(info(0.50), at="2026-10-29T16:00:00Z")
    account.record(info(0.10), at="2026-10-29T15:00:00Z")
    assert account.latest()["info"]["unifiedWindows"]["five_hour"]["utilization"] == 0.50


def test_no_sample_never_stops(private_registry):
    account.check(0.8, now=NOW)


def test_below_the_threshold_runs(private_registry):
    account.record(info(0.79), at="2026-10-29T16:00:00Z")
    account.check(0.8, now=NOW)


def test_at_the_threshold_stops_and_says_when_it_resets(private_registry):
    account.record(info(0.8), at="2026-10-29T16:00:00Z")
    with pytest.raises(account.RateLimitStop, match=r"5-hour window is at 80%.*rate_limit_stop.*resets"):
        account.check(0.8, now=NOW)


def test_the_seven_day_window_stops_too(private_registry):
    account.record(info(0.1, seven_day=0.95), at="2026-10-29T16:00:00Z")
    with pytest.raises(account.RateLimitStop, match="7-day window is at 95%"):
        account.check(0.8, now=NOW)


def test_a_window_that_has_reset_no_longer_counts(private_registry):
    account.record(info(0.99, reset_in=-60), at="2026-10-29T11:00:00Z")
    account.check(0.8, now=NOW)


def test_a_rejected_status_stops_at_any_threshold(private_registry):
    account.record(info(0.5, status="rejected"), at="2026-10-29T16:00:00Z")
    with pytest.raises(account.RateLimitStop, match="rejected"):
        account.check(1.0, now=NOW)


def test_an_account_on_usage_credits_stops(private_registry):
    # Past the plan's limit, credits bill at API rates: real money.
    account.record({**info(0.5), "isUsingOverage": True}, at="2026-10-29T16:00:00Z")
    with pytest.raises(account.RateLimitStop, match="usage credits"):
        account.check(1.0, now=NOW)
    account.check(1.0, now=NOW + 7200)      # the limit has reset since


def test_a_plan_without_a_five_hour_window_reads_its_week(private_registry):
    # A verified Ultra account reports only the 7-day window.
    ultra = info(0.0, seven_day=0.9)
    del ultra["unifiedWindows"]["five_hour"]
    account.record(ultra, at="2026-10-29T16:00:00Z")
    with pytest.raises(account.RateLimitStop, match="7-day window is at 90%"):
        account.check(0.8, now=NOW)


def _onto_credits(tmp_path) -> Path:
    """A turn whose rate_limit_event, after the first message, says the plan moved onto credits."""
    turn = tmp_path / "turn.jsonl"
    event = {"type": "rate_limit_event", "rate_limit_info": {**info(1.0), "isUsingOverage": True},
             "uuid": "x", "session_id": "s"}
    lines = (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()
    turn.write_text("\n".join([*lines[:-1], json.dumps(event), lines[-1]]) + "\n")
    return turn


def test_a_claude_call_stops_when_the_plan_moves_onto_credits(tmp_path, fake, private_registry):
    fake(_onto_credits(tmp_path))
    with pytest.raises(account.RateLimitStop, match="usage credits") as stop:
        agent_cc.run(request(tmp_path))
    assert stop.value.partial is not None and stop.value.partial.cost_reported is False


def test_a_run_that_allows_credits_keeps_going(tmp_path, fake, private_registry):
    fake(_onto_credits(tmp_path))
    agent_cc.run(request(tmp_path, stop_on_usage_credits=False))


def test_none_turns_the_guard_off(private_registry):
    account.record(info(1.0, status="rejected"), at="2026-10-29T16:00:00Z")
    account.check(None, now=NOW)


def test_the_default_threshold_is_80_percent():
    assert ConfigDefaults().rate_limit_stop == 0.8


def test_a_claude_send_records_the_account_sample(tmp_path, fake, private_registry):
    turn = tmp_path / "turn.jsonl"
    event = {"type": "rate_limit_event", "rate_limit_info": info(0.33),
             "uuid": "x", "session_id": "s"}
    lines = (FIXTURES / "cc_turn1.jsonl").read_text().splitlines()
    turn.write_text("\n".join([lines[0], json.dumps(event), *lines[1:]]) + "\n")
    fake(turn)
    agent_cc.run(request(tmp_path))
    sample = account.latest()
    assert sample["info"]["unifiedWindows"]["five_hour"]["utilization"] == 0.33
    assert sample["source"] == str(tmp_path)


def test_execute_stops_a_claude_agent_before_it_spawns(tmp_path, private_registry, monkeypatch):
    account.record(info(0.9, reset_in=3600), at="2026-10-29T16:00:00Z")
    monkeypatch.setattr(account.time, "time", lambda: NOW)
    spawned = []
    monkeypatch.setattr(agent_cc, "run", lambda *a, **k: spawned.append(1))
    from types import SimpleNamespace
    from adw_modules.data_types import AgentConfig, SSSFConfig
    prompt = tmp_path / "p.md"
    prompt.write_text("hi")
    builder = AgentConfig(name="builder", coding_agent="claude_code", model="anthropic/claude-sonnet-5-5",
                          prompt_engineering={"system": str(prompt), "user": str(prompt)})
    events = []
    run = SimpleNamespace(cfg=SSSFConfig(agents=[builder]), adw_id="abc", session_dir=tmp_path,
                          tracer=SimpleNamespace(event=events.append))
    phase = SimpleNamespace(phase_id="p1", params=SimpleNamespace(owner="builder", name="build"))
    with pytest.raises(account.RateLimitStop):
        agents.execute(run, phase, SimpleNamespace(prompt="x", previous=None))
    assert not spawned
    assert [(e.type, e.name) for e in events] == [("error", "rate_limit_stop")]

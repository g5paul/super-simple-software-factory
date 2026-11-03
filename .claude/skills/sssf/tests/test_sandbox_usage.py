"""`just sandbox-usage`: what remote VM runs used of each provider's free
allowance, added up over the traces that share an account.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import sandbox_usage
from adw_modules.data_types import EventRecord
from adw_modules.tracer import Tracer

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)


def trace(path: Path, ends: list[tuple[int, dict]]) -> Path:
    """A trace with one sandbox_end per (days ago, payload)."""
    tracer = Tracer(str(path), str(path.with_suffix(".jsonl")))
    for i, (days, end) in enumerate(ends):
        adw_id = f"run{i}"
        tracer.session_start(adw_id, "t")
        tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="sandbox_end",
                                 payload=end))
    import sqlite3
    with sqlite3.connect(path) as conn:
        for i, (days, _) in enumerate(ends):
            conn.execute("update events set started_at=? where adw_id=? and name='sandbox_end'",
                         ((NOW - timedelta(days=days)).isoformat(), f"run{i}"))
    return path


def vercel(vm: str, seconds: float, cpu: float | None, gb_hours: float | None = None) -> dict:
    end = {"provider": "vercel", "vm": vm, "vm_seconds": seconds, "cpu_seconds": cpu}
    if gb_hours is not None:
        end["memory_gb_hours"] = gb_hours
    return end


def e2b(vm: str, seconds: float, cpu: float | None, cost: float | None) -> dict:
    end = {"provider": "e2b", "vm": vm, "vm_seconds": seconds, "cpu_seconds": cpu}
    if cost is not None:
        end["vm_cost_usd"] = cost
    return end


def test_vercel_counts_the_last_30_days_and_e2b_counts_all_time(tmp_path):
    db = trace(tmp_path / "a.db", [
        (40, vercel("v-old", 3600, 900, 4.0)),
        (2, vercel("v-new", 1800, 360, 2.0)),
        (40, e2b("e-old", 1000, 50, 0.046)),
        (2, e2b("e-new", 1000, 50, 0.046)),
    ])
    found = sandbox_usage.totals([db], NOW)
    assert found["vercel"]["window"].runs == 1
    assert found["vercel"]["window"].cpu_seconds == 360 and found["vercel"]["window"].gb_hours == 2.0
    assert found["e2b"]["all"].cost_usd == pytest.approx(0.092)


def test_traces_from_two_repos_add_up_and_one_trace_twice_counts_once(tmp_path):
    a = trace(tmp_path / "a.db", [(1, e2b("e1", 100, 10, 0.01))])
    b = trace(tmp_path / "b.db", [(1, e2b("e2", 100, 10, 0.02))])
    assert sandbox_usage.totals([a, b], NOW)["e2b"]["all"].cost_usd == pytest.approx(0.03)
    assert sandbox_usage.totals([a, a], NOW)["e2b"]["all"].runs == 1


def test_older_runs_without_readings_are_estimated_or_counted_unknown(tmp_path):
    db = trace(tmp_path / "a.db", [(1, vercel("v1", 3600, None)), (1, e2b("e1", 1000, None, None))])
    found = sandbox_usage.totals([db], NOW)
    assert found["vercel"]["window"].gb_hours == pytest.approx(4.0)
    assert found["vercel"]["window"].cpu_unknown == 1 and found["vercel"]["window"].estimated == 1
    assert found["e2b"]["all"].cost_usd == pytest.approx(1000 * sandbox_usage.E2B_USD_PER_SECOND)


def test_the_report_shows_shares_of_each_allowance_and_runs_left(tmp_path):
    db = trace(tmp_path / "a.db", [(1, vercel("v1", 360, 36, 0.4)), (1, e2b("e1", 360, 30, 0.0166))])
    text = "\n".join(sandbox_usage.report([db], NOW))
    assert "active CPU   0.010 h     0.2% of 5 h" in text
    assert "5 hours covers about 500 runs a month" in text
    assert "$0.0166" in text and "covers about 6023 runs" in text


def test_cpu_with_no_readings_is_unknown_not_zero(tmp_path):
    db = trace(tmp_path / "a.db", [(1, e2b("e1", 100, None, 0.01)), (1, vercel("v1", 100, None, 0.1))])
    text = "\n".join(sandbox_usage.report([db], NOW))
    assert "CPU unknown" in text and "active CPU   unknown" in text
    assert "Hobby's one-time credit" in text and "not a setting" in text


def test_a_trace_with_no_vm_runs_says_so(tmp_path):
    db = trace(tmp_path / "a.db", [])
    assert sandbox_usage.report([db], NOW)[-1] == "no remote VM runs in these traces"


def test_a_missing_trace_is_an_error_not_an_empty_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        sandbox_usage.report([tmp_path / "nope.db"], NOW)

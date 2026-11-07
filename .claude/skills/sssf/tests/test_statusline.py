"""scripts/statusline.py records Claude Code's statusline windows for SSSF."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws" / "adw_modules"))
import account  # noqa: E402

spec = importlib.util.spec_from_file_location("statusline", SKILL / "scripts" / "statusline.py")
statusline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(statusline)

FIVE = 1_900_000_000
WEEK = 1_900_400_000


def run(payload) -> str:
    out = io.StringIO()
    stdin = io.StringIO(payload if isinstance(payload, str) else json.dumps(payload))
    assert statusline.main(stdin=stdin, out=out) == 0
    return out.getvalue().strip()


def payload(**limits) -> dict:
    return {"model": {"display_name": "Opus"}, "cost": {"total_cost_usd": 0.4213}, "rate_limits": limits}


def test_it_records_both_windows_and_prints_a_line(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_REGISTRY", str(tmp_path / "projects.json"))
    said = run(payload(five_hour={"used_percentage": 23.5, "resets_at": FIVE},
                       seven_day={"used_percentage": 41.2, "resets_at": WEEK}))
    assert said == "Opus · 5h 24% · 7d 41% · $0.42 session"
    sample = account.latest()
    assert sample["source"] == "statusline"
    assert sample["info"]["unifiedWindows"] == {
        "five_hour": {"utilization": 0.235, "resetsAt": FIVE},
        "seven_day": {"utilization": 0.412, "resetsAt": WEEK}}


def test_it_keeps_what_an_agent_call_reported_that_a_statusline_doesnt(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_REGISTRY", str(tmp_path / "projects.json"))
    account.record({"status": "allowed_warning", "isUsingOverage": True, "resetsAt": WEEK,
                    "unifiedWindows": {"five_hour": {"utilization": 0.1, "resetsAt": FIVE}}},
                   at="2026-01-01T00:00:00Z", source="/repo")
    run(payload(seven_day={"used_percentage": 50, "resets_at": WEEK}))
    info = account.latest()["info"]
    assert info["isUsingOverage"] is True and info["status"] == "allowed_warning"
    assert info["unifiedWindows"]["five_hour"] == {"utilization": 0.1, "resetsAt": FIVE}
    assert info["unifiedWindows"]["seven_day"] == {"utilization": 0.5, "resetsAt": WEEK}


def test_a_reading_with_no_windows_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_REGISTRY", str(tmp_path / "projects.json"))
    assert run({"model": {"display_name": "Opus"}}) == "Opus"
    assert account.latest() is None


def test_bad_input_still_prints_and_exits_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_REGISTRY", str(tmp_path / "projects.json"))
    assert run("not json") == "Claude"
    assert run(payload(five_hour={"used_percentage": "lots", "resets_at": FIVE})) == "Opus · $0.42 session"


def test_an_older_reading_never_replaces_a_newer_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_REGISTRY", str(tmp_path / "projects.json"))
    account.record({"unifiedWindows": {"seven_day": {"utilization": 0.9, "resetsAt": WEEK}}},
                   at="2999-01-01T00:00:00Z", source="/repo")
    run(payload(seven_day={"used_percentage": 10, "resets_at": WEEK}))
    assert account.latest()["info"]["unifiedWindows"]["seven_day"]["utilization"] == 0.9

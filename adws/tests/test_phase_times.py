"""phase_times: each chain's usual phase times, and the runs past them, from the traces, read-only.

Run from the hub root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import phase_times as pt  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def iso(moment):
    return moment.isoformat(timespec="milliseconds")


def trace(root, runs):
    """runs: (adw_id, chain, status, start, cost, [(name, status, seconds or None)])."""
    db = root / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        create table sessions (adw_id text primary key, adw_name text, request text, status text,
            engineer text, started_at text, ended_at text, total_tokens integer, total_cost real,
            archived integer);
        create table phases (phase_id text primary key, adw_id text, seq integer, name text, kind text,
            owner text, description text, status text, attempt integer, retries integer, error text,
            started_at text, ended_at text);""")
    for adw_id, chain, status, start, cost, phases in runs:
        moment = start
        for seq, (name, pstatus, seconds) in enumerate(phases):
            end = None if seconds is None else moment + timedelta(seconds=seconds)
            conn.execute("insert into phases values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (f"{adw_id}_{seq}", adw_id, seq, name, "agent", "x", "", pstatus, 0, 0, None,
                          iso(moment), end and iso(end)))
            moment = end or moment
        ended = None if status == "running" else iso(moment)
        conn.execute("insert into sessions values (?,?,?,?,?,?,?,?,?,?)",
                     (adw_id, chain, "", status, "e", iso(start), ended, 0, cost, 0))
    conn.commit()
    conn.close()
    return db


def usual(n, start, build=300.0):
    return [(f"r{i:02d}", "adw_x", "success", start + timedelta(hours=i), 1.0,
             [("plan", "success", 60.0), ("build", "success", build), ("review_1", "success", 120.0),
              ("commit", "success", 1.0)]) for i in range(n)]


def lines(db, **kw):
    phases, runs = pt.read(db, NOW)
    args = dict(history=50, min_runs=10, days=14, factor=2.0, now=NOW) | kw
    return pt.report("repo", phases, runs, **args)


def test_a_phase_past_twice_its_p90_is_listed_and_numbered_rounds_share_a_row(tmp_path):
    start = NOW - timedelta(days=5)
    runs = usual(12, start) + [("slow", "adw_x", "success", NOW - timedelta(hours=2), 1.0,
                                [("plan", "success", 60.0), ("build", "success", 700.0),
                                 ("review_2", "success", 120.0)])]
    out = "\n".join(lines(trace(tmp_path, runs)))
    assert "review_N" in out and "review_1" not in out.split("past")[0]
    assert "slow  adw_x  build: 11.7 min, 2.3x its p90 of 5.0 (success)" in out
    assert "plan:" not in out.split("past")[1]


def test_a_quick_phase_never_counts_as_slow_under_a_minute(tmp_path):
    runs = usual(12, NOW - timedelta(days=5)) + [("q", "adw_x", "success", NOW - timedelta(hours=1), 1.0,
                                                  [("commit", "success", 30.0)])]
    out = "\n".join(lines(trace(tmp_path, runs)))
    assert "commit:" not in out
    assert "commit " not in out.split("past")[0]            # under a minute, so hidden without --all
    assert "commit " in "\n".join(lines(trace(tmp_path / "b", runs), every=True))


def test_too_few_runs_judge_nothing(tmp_path):
    runs = usual(5, NOW - timedelta(days=5)) + [("slow", "adw_x", "success", NOW - timedelta(hours=1), 1.0,
                                                 [("build", "success", 5000.0)])]
    out = "\n".join(lines(trace(tmp_path, runs)))
    assert "nothing is judged" in out and "past 2x the p90, last 14 days: 0" in out


def test_a_running_phase_counts_to_now_and_a_stale_one_is_listed_apart(tmp_path):
    runs = usual(12, NOW - timedelta(days=5)) + [
        ("live", "adw_x", "running", NOW - timedelta(minutes=30), 0.0, [("build", "running", None)]),
        ("dead", "adw_x", "fail", NOW - timedelta(days=1), 0.0, [("build", "running", None)])]
    out = "\n".join(lines(trace(tmp_path, runs)))
    assert "live  adw_x  build: 30.0 min so far" in out
    assert "dead  adw_x  build:" not in out
    assert "phases still marked running in a run that ended: 1" in out and "dead  adw_x  build, started" in out


def test_a_run_past_its_cost_p90_is_listed(tmp_path):
    runs = usual(12, NOW - timedelta(days=5)) + [("dear", "adw_x", "success", NOW - timedelta(hours=1), 9.0,
                                                  [("plan", "success", 60.0)])]
    assert any("dear  adw_x  cost: $9.00" in line for line in lines(trace(tmp_path, runs)))


def test_main_reads_the_registry_and_names_a_missing_repo(tmp_path, capsys):
    root = tmp_path / "repo"
    trace(root, usual(12, NOW - timedelta(days=5)))
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"repo": {"root": str(root)}}}))
    assert pt.main(["--registry", str(registry), "--now", iso(NOW)]) == 0
    assert "adw_x" in capsys.readouterr().out
    assert pt.main(["--registry", str(registry), "--repo", "nope"]) == 1

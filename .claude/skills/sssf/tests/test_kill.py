"""`just kill`: stop a run by adw_id, and never a process that only reuses its pid.

The processes here are real: a stand-in chain script and a stand-in `claude`
binary that sleep, recorded in a real trace db the way a run records them.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import kill
from adw_modules.tracer import Tracer

SLEEPER = "import time\ntime.sleep(60)\n"


@pytest.fixture
def trace(tmp_path):
    db = tmp_path / "sssf.db"
    tracer = Tracer(db, tmp_path / "events.jsonl")
    tracer.session_start("k1", "t", adw_name="adw_fake")
    return db, tracer


def spawn(tmp_path: Path, name: str) -> subprocess.Popen:
    script = tmp_path / name
    script.write_text(SLEEPER)
    return subprocess.Popen([sys.executable, str(script)])


def status(db: Path) -> str:
    import sqlite3
    with sqlite3.connect(db) as conn:
        return conn.execute("select status from sessions where adw_id='k1'").fetchone()[0]


def test_it_stops_the_chain_and_its_agent_and_closes_the_run(tmp_path, trace):
    db, tracer = trace
    chain, agent = spawn(tmp_path, "adw_fake.py"), spawn(tmp_path, "claude")
    tracer.process_start("k1", "adw", "", chain.pid, "adw_fake.py do the thing")
    tracer.process_start("k1", "agent", "builder", agent.pid, "claude_code builder some/model")
    report = kill.kill(str(db), "k1")
    assert report == [f"adw {chain.pid}: stopped", f"agent builder {agent.pid}: stopped"]
    assert chain.wait(5) is not None and agent.wait(5) is not None
    assert status(db) == "fail"


def test_a_reused_pid_running_something_else_is_left_alone(tmp_path, trace):
    db, tracer = trace
    other = spawn(tmp_path, "someone_elses.py")
    tracer.process_start("k1", "adw", "", other.pid, "adw_fake.py do the thing")
    try:
        [line] = kill.kill(str(db), "k1")
        assert "NOT stopped" in line and other.poll() is None
    finally:
        other.kill()


def test_a_process_younger_than_its_row_is_left_alone(tmp_path, trace):
    db, tracer = trace
    chain = spawn(tmp_path, "adw_fake.py")
    tracer.process_start("k1", "adw", "", chain.pid, "adw_fake.py do the thing")
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="milliseconds")
    tracer.conn.execute("update processes set started_at=? where pid=?", (hour_ago, chain.pid))
    try:
        [line] = kill.kill(str(db), "k1")
        assert "pid was reused" in line and chain.poll() is None
    finally:
        chain.kill()


def test_a_dead_run_is_closed_without_signalling_anything(tmp_path, trace):
    db, tracer = trace
    gone = spawn(tmp_path, "adw_fake.py")
    gone.kill()
    gone.wait()
    tracer.process_start("k1", "adw", "", gone.pid, "adw_fake.py do the thing")
    assert kill.kill(str(db), "k1") == [f"adw {gone.pid}: already gone"]
    assert status(db) == "fail"


def test_nothing_recorded_is_said_plainly(trace):
    db, _ = trace
    assert kill.kill(str(db), "nope") == ["no live processes recorded for nope"]


@pytest.mark.parametrize("etime,seconds", [("05", 5), ("01:05", 65), ("02:01:05", 7265),
                                           ("3-02:01:05", 266465)])
def test_ps_elapsed_time_parses(etime, seconds):
    assert kill._seconds(etime) == seconds

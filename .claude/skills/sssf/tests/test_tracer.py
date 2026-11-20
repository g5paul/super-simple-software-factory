"""The tracer closes what a run leaves open when it finishes."""
from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules.data_types import EventRecord  # noqa: E402
from adw_modules.tracer import Tracer  # noqa: E402


def phase(tracer, phase_id, status, started, ended=None):
    tracer.conn.execute("INSERT INTO phases (phase_id, adw_id, seq, name, kind, owner, description, status,"
                        " started_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (phase_id, "a1b2c3d4", 1, phase_id, "agent", "builder", "d", status, started, ended))


def test_finish_closes_a_phase_left_running_at_its_last_event(tmp_path):
    tracer = Tracer(tmp_path / "sssf.db", tmp_path / "events.jsonl", data_root=tmp_path)
    tracer.session_start("a1b2c3d4", "engineer")
    phase(tracer, "killed", "running", "2026-11-11T03:46:05.053+00:00")
    phase(tracer, "quiet", "running", "2026-11-11T03:50:00.000+00:00")
    phase(tracer, "done", "success", "2026-11-11T03:40:00.000+00:00", "2026-11-11T03:45:00.000+00:00")
    tracer.event(EventRecord(adw_id="a1b2c3d4", phase_id="killed", type="log", name="x", payload={},
                             started_at="2026-11-11T04:40:02.737+00:00"))
    tracer.session_finish("a1b2c3d4", ok=False)
    rows = dict((r[0], r[1:]) for r in tracer.conn.execute(
        "SELECT phase_id, status, error, ended_at FROM phases"))
    assert rows["killed"] == ("fail", "the run ended with this phase still open", "2026-11-11T04:40:02.737+00:00")
    assert rows["quiet"] == ("fail", "the run ended with this phase still open", "2026-11-11T03:50:00.000+00:00")
    assert rows["done"] == ("success", None, "2026-11-11T03:45:00.000+00:00")


def test_finish_leaves_another_runs_open_phase_alone(tmp_path):
    tracer = Tracer(tmp_path / "sssf.db", tmp_path / "events.jsonl", data_root=tmp_path)
    tracer.session_start("a1b2c3d4", "engineer")
    tracer.session_start("e5f6a7b8", "engineer")
    tracer.conn.execute("INSERT INTO phases (phase_id, adw_id, seq, name, status, started_at)"
                        " VALUES ('other', 'e5f6a7b8', 1, 'build', 'running', '2026-11-11T03:00:00+00:00')")
    tracer.session_finish("a1b2c3d4", ok=True)
    assert tracer.conn.execute("SELECT status FROM phases WHERE phase_id='other'").fetchone() == ("running",)


START_LOOKUP = ("SELECT payload_json FROM events WHERE adw_id = ? AND name = ? AND type = 'agent_start'"
                " AND started_at <= ? ORDER BY started_at DESC LIMIT 1")


def plan(conn, sql, args):
    return " ".join(row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", args))


def test_an_agents_start_is_found_by_index_not_by_scanning_every_event(tmp_path):
    tracer = Tracer(tmp_path / "sssf.db", tmp_path / "events.jsonl", data_root=tmp_path)
    assert "USING INDEX" in plan(tracer.conn, START_LOOKUP, ("a1b2c3d4", "builder", "2026-11-19"))


def test_a_trace_from_before_the_index_gets_it_when_opened(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "sssf.db")
    old.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, adw_id TEXT, phase_id TEXT, parent_id TEXT,"
                " type TEXT, name TEXT, payload_json TEXT, tokens INTEGER, started_at TEXT, ended_at TEXT)")
    old.execute("INSERT INTO events (event_id, adw_id, type, name, started_at) VALUES"
                " ('e1', 'a1b2c3d4', 'agent_start', 'builder', '2026-11-19T01:00:00+00:00')")
    old.commit()
    assert "USING INDEX" not in plan(old, START_LOOKUP, ("a1b2c3d4", "builder", "2026-11-19"))
    old.close()
    tracer = Tracer(tmp_path / "sssf.db", tmp_path / "events.jsonl", data_root=tmp_path)
    assert "USING INDEX" in plan(tracer.conn, START_LOOKUP, ("a1b2c3d4", "builder", "2026-11-19"))
    assert tracer.conn.execute("SELECT count(*) FROM events").fetchone() == (1,)

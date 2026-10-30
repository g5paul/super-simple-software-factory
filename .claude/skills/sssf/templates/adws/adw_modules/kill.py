"""Stop a run by adw_id: the chain first, then its coding agents.

The trace records every process a run starts (`processes`, see tracer.py), so
the adw_id is enough to find them. Two things decide the order and the checks:

- The chain goes first. Stopped first, an agent's exit would look like a
  failed call, and a phase with retries would start a new one. The chain turns
  SIGTERM into a clean exit that marks the run `fail` (session._finalize_when_killed).
- That exit also marks every process row ended, while the agent it was waiting
  on is still alive. So the rows are read before any signal is sent.

A pid can be reused once its process is gone, so each one is checked before it
is signalled: its command has to name what was recorded (the chain's script,
or the agent's binary), and it has to be at least as old as its trace row. A
process that fails either check is left alone and reported.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .agent_cc import CLAUDE_PATH
from .agent_pi import PI_PATH
from .tracer import Tracer

BINARIES = {"claude_code": Path(CLAUDE_PATH).name, "pi": Path(PI_PATH).name}
CLOCK_SLACK_SECONDS = 5     # the row is written just after the spawn, not before
WAIT_SECONDS = 10


def _rows(db: str, adw_id: str) -> list[tuple[str, str, int, str, str]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "select kind, name, pid, command, started_at from processes "
            "where adw_id=? and ended_at is null order by case kind when 'adw' then 0 else 1 end, id",
            (adw_id,)).fetchall()


def _seconds(etime: str) -> int:
    """`ps -o etime` reads [[dd-]hh:]mm:ss, on Linux and macOS alike."""
    days, _, clock = etime.strip().rpartition("-")
    parts = [int(p) for p in clock.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    hours, minutes, seconds = parts
    return (int(days or 0) * 24 + hours) * 3600 + minutes * 60 + seconds


def _live(pid: int) -> tuple[int, str] | None:
    """(age in seconds, command line) for a running pid, or None. A zombie has
    exited and only waits for its parent to reap it, so it counts as gone."""
    done = subprocess.run(["ps", "-o", "stat=", "-o", "etime=", "-o", "args=", "-p", str(pid)],
                          capture_output=True, text=True)
    fields = done.stdout.split(None, 2)
    if done.returncode != 0 or len(fields) < 2 or fields[0].startswith("Z"):
        return None
    return _seconds(fields[1]), fields[2].strip() if len(fields) > 2 else ""


def _expected(kind: str, command: str) -> str:
    """The name the live command line must contain for this row."""
    first = command.split()[0] if command.split() else ""
    return first if kind == "adw" else BINARIES.get(first, first)


def identify(kind: str, command: str, started_at: str, live: tuple[int, str]) -> str:
    """Why this live process is not the recorded one, or "" when it is."""
    age, args = live
    want = _expected(kind, command)
    if not want or want not in {Path(token).name for token in args.split()}:
        return f"its command is now '{args[:120]}', not {want or 'what was recorded'}"
    recorded = datetime.fromisoformat(started_at)
    if age + CLOCK_SLACK_SECONDS < (datetime.now(timezone.utc) - recorded).total_seconds():
        return "it started after the run recorded it, so the pid was reused"
    return ""


def _gone(pid: int) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if _live(pid) is None:
            return True
        time.sleep(0.2)
    return False


def kill(db: str, adw_id: str) -> list[str]:
    """Stop every live process the trace records for this run. Returns one line each."""
    if not Path(db).is_file():
        return [f"no trace at {db}"]
    rows = _rows(db, adw_id)
    if not rows:
        return [f"no live processes recorded for {adw_id}"]
    report = []
    for kind, name, pid, command, started_at in rows:
        label = f"{kind} {name} {pid}".replace("  ", " ")
        live = _live(pid)
        if live is None:
            report.append(f"{label}: already gone")
            continue
        why = identify(kind, command, started_at, live)
        if why:
            report.append(f"{label}: NOT stopped, {why}")
            continue
        os.kill(pid, signal.SIGTERM)
        report.append(f"{label}: stopped" if _gone(pid) else
                      f"{label}: sent SIGTERM, still running after {WAIT_SECONDS}s")
    # A chain that was already dead, or was killed some other way, never
    # finalized. Close the run so the trace stops saying it is in flight.
    with sqlite3.connect(db) as conn:
        status = conn.execute("select status from sessions where adw_id=?", (adw_id,)).fetchone()
    if status and status[0] == "running" and all(_live(r[2]) is None for r in rows if r[0] == "adw"):
        Tracer(db, os.devnull).session_finish(adw_id, ok=False)
    return report

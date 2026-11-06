"""levers: the four levers per ISO week, from the traces, read-only.

Run from the hub root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import levers  # noqa: E402

NOW = "2026-11-06T12:00:00+00:00"    # a Friday in 2026-W45


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    """Keep git away from the real ~/.gitconfig."""
    home = tmp_path / "home"
    (home / "tmp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(home / "tmp"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("XDG_CONFIG_HOME", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "SSSF_REGISTRY"):
        monkeypatch.delenv(name, raising=False)


def git(root, *args, when=None):
    env = {**os.environ, **({"GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when} if when else {})}
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)


def trace(root, runs):
    """runs: (adw_id, start, end, agents, tool_calls, engineer phases, status, cost)."""
    db = root / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table sessions (adw_id, started_at, ended_at, status, total_cost)")
        conn.execute("create table phases (adw_id, kind, owner)")
        conn.execute("create table events (adw_id, type, name)")
        for adw_id, start, end, agents, calls, engineer, status, cost in runs:
            conn.execute("insert into sessions values (?, ?, ?, ?, ?)",
                         (adw_id, start, end, status, cost))
            for i in range(agents):
                conn.execute("insert into phases values (?, 'agent', ?)", (adw_id, f"a{i}"))
                conn.execute("insert into phases values (?, 'agent', ?)", (adw_id, f"a{i}"))
            for _ in range(engineer):
                conn.execute("insert into phases values (?, 'engineer', 'me')", (adw_id,))
            for _ in range(calls):
                conn.execute("insert into events values (?, 'tool_call', 'Bash')", (adw_id,))
            conn.execute("insert into events values (?, 'tool_call', 'quality:test')", (adw_id,))


@pytest.fixture
def world(tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir()
    git(hub, "init", "-q", "-b", "main")
    proj = tmp_path / "proj"
    proj.mkdir()
    git(proj, "init", "-q", "-b", "main")
    git(proj, "config", "user.email", "t@example.com")
    git(proj, "config", "user.name", "t")
    git(proj, "commit", "-q", "--allow-empty", "-m", "base", when="2026-11-02T08:00:00+00:00")
    git(proj, "switch", "-q", "-c", "side")
    git(proj, "commit", "-q", "--allow-empty", "-m", "work", when="2026-11-03T08:00:00+00:00")
    git(proj, "switch", "-q", "main")
    git(proj, "merge", "-q", "--no-ff", "side", "-m", "merge", when="2026-11-04T08:00:00+00:00")
    trace(proj, [
        ("r1", "2026-11-03T10:00:00+00:00", "2026-11-03T10:10:00+00:00", 3, 30, 1, "success", 1.0),
        ("r2", "2026-11-03T10:05:00+00:00", "2026-11-03T10:25:00+00:00", 1, 10, 1, "fail", 2.0),
        ("q1", "2026-11-03T10:06:00+00:00", "2026-11-03T10:07:00+00:00", 0, 0, 1, "success", 0.0),
        ("old", "2026-10-01T10:00:00+00:00", "2026-10-01T11:00:00+00:00", 2, 99, 1, "success", 9.0),
    ])
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"proj": {"root": str(proj)}}}))
    return hub, registry


def test_a_week_counts_width_time_depth_and_attention(world, capsys):
    hub, registry = world
    assert levers.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                        "--weeks", "2"]) == 0
    rows = [l.split() for l in capsys.readouterr().out.splitlines() if l.startswith("2026-W45")]
    # 3 runs, 3 at once at 10:06; 2 agent runs, median 15 min, 20 calls each,
    # median 2 agents; touches = 2 agent-run requests + 1 merge; 40 calls / 3;
    # 1 accepted; $3 spent on it, the failed run's $2 included; longest 20 min.
    assert rows == [["2026-W45", "3", "3", "2", "15.0", "20.0", "2", "3", "13.3",
                     "1", "3.00", "20.0"]]


def test_runs_before_the_window_are_left_out(world, capsys):
    hub, registry = world
    levers.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW, "--weeks", "4"])
    out = capsys.readouterr().out
    assert "99" not in out and "2026-W42" in out and "2026-W40" not in out


def test_listed_hides_a_private_repo_and_refuses_without_names(world, tmp_path, capsys):
    hub, registry = world
    assert levers.main(["--hub", str(hub), "--registry", str(registry), "--listed"]) == 1
    assert "refusing" in capsys.readouterr().err
    (hub / ".git" / "sssf-private-names").write_text("proj\n")
    assert levers.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                        "--listed", "--by-repo"]) == 0
    out = capsys.readouterr().out
    assert "\nproj\n" not in out and "1 unlisted repo(s) left out" in out
    assert out.startswith("0 repos")


def test_real_drops_quality_only_runs_and_test_beds(world, tmp_path, capsys):
    hub, registry = world
    bed = tmp_path / "bed"
    bed.mkdir()
    git(bed, "init", "-q", "-b", "main")
    trace(bed, [("b1", "2026-11-03T09:00:00+00:00", "2026-11-03T09:01:00+00:00", 1, 5, 1,
                 "success", 0.1)])
    data = json.loads(registry.read_text())
    data["projects"]["bed"] = {"root": str(bed)}
    registry.write_text(json.dumps(data))
    (hub / ".git" / "sssf-test-beds").write_text("# test beds\nBED\n")
    assert levers.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                        "--weeks", "1", "--real"]) == 0
    out = capsys.readouterr().out
    row = [l.split() for l in out.splitlines() if l.startswith("2026-W45")][0]
    # q1 and the bed's run are gone: 2 runs, at most 2 at once.
    assert row[1:3] == ["2", "2"] and out.startswith("1 repos")
    assert "1 test bed(s) left out" in out


def test_real_without_a_test_beds_file_still_drops_quality_only_runs(world, capsys):
    hub, registry = world
    assert levers.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                        "--weeks", "1", "--real"]) == 0
    out = capsys.readouterr().out
    assert [l.split() for l in out.splitlines() if l.startswith("2026-W45")][0][1] == "2"
    assert "0 test bed(s) left out" in out

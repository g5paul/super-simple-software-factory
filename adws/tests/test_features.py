"""features: runs joined to the commits that landed on the main line.

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

import features  # noqa: E402

NOW = "2026-11-06T12:00:00+00:00"


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


class Repo:
    def __init__(self, root):
        self.root = root
        self.clock = 0
        root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.commit("base")

    def git(self, *args):
        self.clock += 1
        when = f"2026-11-0{3 + self.clock // 50}T{self.clock % 24:02d}:00:00+00:00"
        env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when}
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True, env=env).stdout.strip()

    def commit(self, subject, name=None):
        (self.root / (name or subject.replace(" ", "_"))).write_text(subject + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", subject)
        return self.git("rev-parse", "--short", "HEAD")


def trace(root, runs):
    """runs: (adw_id, status, cost, [(sha, message)])."""
    db = root / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table sessions (adw_id, adw_name, started_at, status, total_cost)")
        conn.execute("create table phases (adw_id, kind)")
        conn.execute("create table events (adw_id, payload_json, started_at)")
        for adw_id, status, cost, commits in runs:
            conn.execute("insert into sessions values (?, 'adw_simple_sdlc', ?, ?, ?)",
                         (adw_id, "2026-11-03T00:30:00+00:00", status, cost))
            conn.execute("insert into phases values (?, 'agent')", (adw_id,))
            for sha, message in commits:
                conn.execute("insert into events values (?, ?, '2026-11-03T01:00:00+00:00')",
                             (adw_id, json.dumps({"sha": sha, "message": message})))
    (root / ".gitignore").write_text("adws/adw_data/\n")


@pytest.fixture
def world(tmp_path):
    repo = Repo(tmp_path / "proj")
    runs = []

    # Two runs on one branch, merged with a merge commit: one feature.
    repo.git("switch", "-q", "-c", "feat")
    a = repo.commit("Add spec for A")
    b = repo.commit("Build A")
    c = repo.commit("Fix A")
    repo.git("switch", "-q", "main")
    repo.git("merge", "-q", "--no-ff", "feat", "-m", "Merge feat: A")
    runs += [("r1", "fail", 1.0, [(a, "Add spec for A"), (b, "Build A")]),
             ("r2", "success", 2.0, [(c, "Fix A")])]

    # A run fast-forwarded onto main: its last commit names the feature.
    d = repo.commit("Add spec for B")
    e = repo.commit("Build B")
    runs.append(("r3", "success", 0.5, [(d, "Add spec for B"), (e, "Build B")]))

    # A run rebased before landing: found by patch-id.
    repo.git("switch", "-q", "-c", "rb", "HEAD~1")
    f = repo.commit("Build C")
    repo.git("switch", "-q", "main")
    repo.git("cherry-pick", f)
    repo.git("branch", "-q", "-D", "rb")
    runs.append(("r4", "success", 0.25, [(f, "Build C")]))

    # A run still on an open branch: pending.
    repo.git("switch", "-q", "-c", "open")
    g = repo.commit("Build D")
    repo.git("switch", "-q", "main")
    runs.append(("r5", "success", 0.75, [(g, "Build D")]))

    # A run whose branch was deleted: gone.
    repo.git("switch", "-q", "-c", "dead")
    h = repo.commit("Build E")
    repo.git("switch", "-q", "main")
    repo.git("branch", "-q", "-D", "dead")
    runs.append(("r6", "fail", 3.0, [(h, "Build E")]))

    # A run that never committed.
    runs.append(("r7", "success", 0.01, []))
    trace(repo.root, runs)
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {"proj": {"root": str(repo.root)}}}))
    hub = tmp_path / "hub"
    hub.mkdir()
    subprocess.run(["git", "-C", str(hub), "init", "-q"], check=True)
    return repo, registry, hub


def joined(repo):
    runs = features.agent_runs(repo.root / "adws/adw_data/sssf.db",
                               features.levers._when("2026-11-01T00:00:00+00:00"))
    return features.join(repo.root, "main", runs, features.levers._when("2026-11-01T00:00:00+00:00"))


def test_runs_merged_together_are_one_feature_with_all_their_cost(world):
    found, *_ = joined(world[0])
    merged = [f for f in found if f.landing.subject == "Merge feat: A"]
    assert len(merged) == 1
    assert sorted(r.adw_id for r in merged[0].runs) == ["r1", "r2"] and merged[0].cost == 3.0


def test_a_fast_forwarded_run_is_named_by_its_last_commit(world):
    found, *_ = joined(world[0])
    assert [f.landing.subject for f in found if [r.adw_id for r in f.runs] == ["r3"]] == ["Build B"]


def test_a_rebased_run_is_found_by_patch_id(world):
    found, *_ = joined(world[0])
    rebased = [f for f in found if [r.adw_id for r in f.runs] == ["r4"]]
    assert rebased and rebased[0].matched_by == {"patch-id"}


def test_unlanded_runs_split_into_pending_gone_and_no_commits(world):
    _, pending, gone, none = joined(world[0])
    assert [r.adw_id for r in pending] == ["r5"]
    assert [r.adw_id for r in gone] == ["r6"]
    assert [r.adw_id for r in none] == ["r7"]


def test_the_report_totals_landed_cost_per_feature(world, capsys):
    repo, registry, hub = world
    assert features.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW]) == 0
    out = capsys.readouterr().out
    assert "landed        3 feature(s) from 4 run(s)  $    3.75, $1.25 per feature" in out
    assert "gone          1 run(s)  $    3.00" in out


def test_listed_and_real_hide_repos(world, capsys):
    repo, registry, hub = world
    (hub / ".git" / "sssf-private-names").write_text("proj\n")
    assert features.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                          "--listed"]) == 0
    out = capsys.readouterr().out
    assert "proj" not in out.replace("1 unlisted repo(s) left out", "")
    (hub / ".git" / "sssf-private-names").unlink()
    (hub / ".git" / "sssf-test-beds").write_text("proj\n")
    assert features.main(["--hub", str(hub), "--registry", str(registry), "--now", NOW,
                          "--real"]) == 0
    assert "1 test bed(s) left out" in capsys.readouterr().out

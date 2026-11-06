"""check_projects: a project's update, checked against the hub, read-only.

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

import check_projects as cp  # noqa: E402


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


def sh(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def repo(path):
    path.mkdir(parents=True)
    sh(path, "init", "-q", "-b", "main")
    sh(path, "config", "user.email", "t@example.com")
    sh(path, "config", "user.name", "t")
    return path


def commit(root, files, message):
    for name, text in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if text is None:
            p.unlink()
        else:
            p.write_text(text)
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", message)
    return sh(root, "rev-parse", "--short", "HEAD")


def quality_run(root, status, started):
    db = root / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table if not exists sessions (adw_id text, adw_name text, "
                     "status text, started_at text)")
        conn.execute("insert into sessions values (?, 'adw_quality', ?, ?)",
                     (f"q{started[14:16]}", status, started))


@pytest.fixture
def world(tmp_path):
    hub = repo(tmp_path / "hub")
    skill = commit(hub, {".claude/skills/sssf/SKILL.md": "v1\n",
                         ".claude/skills/sssf/tests/t.py": "x\n"}, "skill")
    proj = repo(tmp_path / "proj")
    commit(proj, {"app.py": "app\n", ".gitignore": "adws/adw_data/\n"}, "app")
    commit(proj, {".claude/skills/sssf/SKILL.md": "v1\n", ".claude/skills/sssf/tests/t.py": "x\n",
                  cp.STAMP: json.dumps({"skill": skill})}, f"Update SSSF to skill {skill}")
    # Pin the update's commit time, so each test's quality run lands before or after it.
    subprocess.run(["git", "-C", str(proj), "commit", "-q", "--amend", "--no-edit"], check=True,
                   env={**os.environ, "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"})
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"projects": {
        "proj": {"root": str(proj), "db": "adws/adw_data/sssf.db"},
        "hub": {"root": str(hub), "db": "adws/adw_data/sssf.db"}}}))
    return hub, proj, skill, registry


def notes(hub, proj, skill):
    report = cp.check("proj", {"root": str(proj)}, hub, skill)
    return {c.name: (c.ok, c.note) for c in report.checks}


def test_an_updated_repo_with_a_quality_run_after_the_update_passes(world, capsys):
    hub, proj, skill, registry = world
    quality_run(proj, "success", "2026-01-01T00:05:00+00:00")
    assert cp.main(["--hub", str(hub), "--registry", str(registry)]) == 0
    out = capsys.readouterr().out
    assert "1 of 1 repos pass" in out and "hub (" not in out    # the hub itself is skipped


def test_a_stray_file_left_in_the_skill_copy_fails(world):
    hub, proj, skill, _ = world
    quality_run(proj, "success", "2026-01-01T00:05:00+00:00")
    commit(proj, {".claude/skills/sssf/r": ""}, "stray")
    ok, note = notes(hub, proj, skill)["skill"]
    assert not ok and "extra .claude/skills/sssf/r" in note


def test_a_missing_or_changed_skill_file_fails(world):
    hub, proj, skill, _ = world
    commit(proj, {".claude/skills/sssf/tests/t.py": None, ".claude/skills/sssf/SKILL.md": "v0\n"}, "x")
    ok, note = notes(hub, proj, skill)["skill"]
    assert not ok and "missing .claude/skills/sssf/tests/t.py" in note
    assert "changed .claude/skills/sssf/SKILL.md" in note


def test_an_older_stamp_and_commit_fail_against_a_newer_skill(world):
    hub, proj, _, _ = world
    newer = commit(hub, {".claude/skills/sssf/SKILL.md": "v2\n"}, "skill v2")
    got = notes(hub, proj, newer)
    assert not got["stamp"][0] and not got["commit"][0] and not got["skill"][0]


def test_quality_must_pass_and_start_after_the_update(world):
    hub, proj, skill, _ = world
    assert notes(hub, proj, skill)["quality"] == (False, "no trace")
    quality_run(proj, "success", "2025-12-31T23:59:00+00:00")
    assert not notes(hub, proj, skill)["quality"][0]
    quality_run(proj, "fail", "2026-01-01T00:05:00+00:00")
    assert notes(hub, proj, skill)["quality"] == (False, "q05 fail")


def test_an_uncommitted_change_fails_clean(world):
    hub, proj, skill, _ = world
    (proj / "app.py").write_text("edited\n")
    ok, note = notes(hub, proj, skill)["clean"]
    assert not ok and "app.py" in note


def test_listed_leaves_out_private_repos_and_never_names_them(world, tmp_path, capsys):
    hub, proj, skill, registry = world
    secret = repo(tmp_path / "secret-app")
    data = json.loads(registry.read_text())
    data["projects"]["secret-app"] = {"root": str(secret)}
    registry.write_text(json.dumps(data))
    (hub / ".git" / "sssf-private-names").write_text("# private\nSECRET-APP\n")
    quality_run(proj, "success", "2026-01-01T00:05:00+00:00")
    assert cp.main(["--hub", str(hub), "--registry", str(registry), "--listed"]) == 0
    out = capsys.readouterr().out
    assert "secret" not in out.lower() and "1 of 1 repos pass" in out
    assert "1 unlisted repo(s) left out" in out


def test_listed_refuses_without_the_names_file(world, capsys):
    hub, _, _, registry = world
    assert cp.main(["--hub", str(hub), "--registry", str(registry), "--listed"]) == 1
    captured = capsys.readouterr()
    assert "refusing" in captured.err and captured.out == ""


def test_repo_cannot_reach_a_repo_listed_hides(world, tmp_path, capsys):
    hub, _, _, registry = world
    (hub / ".git" / "sssf-private-names").write_text("proj\n")
    assert cp.main(["--hub", str(hub), "--registry", str(registry), "--listed",
                    "--repo", "proj"]) == 1
    assert "proj (" not in capsys.readouterr().out

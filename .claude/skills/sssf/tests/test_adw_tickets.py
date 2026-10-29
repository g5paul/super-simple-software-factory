"""adw_tickets end to end: a real repo, a real Run, a scripted coding agent.

The agent is the only fake. It reads which agent it is from the system prompt
and does what that agent would: the scout writes notes, a builder writes a
file for its ticket, a reviewer writes its review. Everything else is real:
the read step, git, quality.py's placeholder checks, the trace db.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

import adw_tickets
from adw_modules import agent_cc, agents, quality, tickets
from adw_modules.data_types import (AgentConfig, AgentResult, ConfigDefaults, ObservabilityConfig,
                                    QualityResult, SSSFConfig, UsageBreakdown)

from test_tickets import SPEC, ticket

NAMES = ["scout", "ticket_builder", "standards_reviewer", "spec_reviewer", "documenter"]
FOLDER = ".scratch/summaries"


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def subjects() -> list[str]:
    return git("log", "--reverse", "--format=%s").splitlines()[1:]


def field(user_text: str, name: str) -> str:
    return re.search(rf"^{name}: (.*)$", user_text, re.M).group(1)


class Agent:
    """Plays every agent in the roster. Records each call."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []      # (agent, session_id, user prompt)
        self.blocking = False                             # the spec reviewer blocks once
        self.where = "src/t01.py:1"                       # where its finding points

    def run(self, req, on_event=None, on_spawn=None, on_exit=None) -> AgentResult:
        name = req.system_prompt.strip()
        self.calls.append((name, req.session_id, req.prompt))
        return AgentResult(text=json.dumps(getattr(self, name)(req.prompt)), cost=0.1, tokens=10,
                           usage=UsageBreakdown(total_cost=0.1, total_tokens=10))

    def scout(self, prompt):
        notes = re.search(r"Write (\S+scout_findings\.md)", prompt).group(1)
        Path(notes).write_text("notes\n")
        return {"status": "success", "summary": "explored", "artifacts": [notes]}

    def ticket_builder(self, prompt):
        found = re.search(r"Build ticket (\d+)", prompt)
        name = f"src/t{found.group(1)}.py" if found else "src/fix.py"
        Path("src").mkdir(exist_ok=True)
        Path(name).write_text(Path(name).read_text() + "x\n" if Path(name).exists() else "x\n")
        return {"status": "success", "summary": "built", "changed_files": [name],
                "commit_message": f"Add {name}"}

    def _review(self, prompt, blocking):
        path = Path(field(prompt, "handoff")) / f"{field(prompt, 'phase')}.md"
        path.write_text("review\n")
        findings = [{"where": self.where, "problem": "wrong", "source": "spec line",
                     "blocking": True}] if blocking else []
        return {"status": "success", "summary": "reviewed", "findings": findings,
                "artifacts": [str(path)]}

    def standards_reviewer(self, prompt):
        return self._review(prompt, False)

    def spec_reviewer(self, prompt):
        return self._review(prompt, self.blocking)

    def documenter(self, prompt):
        Path("app_docs").mkdir(exist_ok=True)
        Path("app_docs/summaries.md").write_text("doc\n")
        return {"status": "success", "summary": "documented", "artifacts": ["app_docs/summaries.md"],
                "commit_message": "Document summaries"}

    def of(self, name: str) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if c[0] == name]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["adws/adw_tickets.py"])
    git("init", "-q", "-b", "feature")
    Path(".gitignore").write_text("adws/adw_data/\n")
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "user.md").write_text(
        "{{prompt}}\nprevious: {{previous_envelope}}\nhandoff: {{context_handoff_dir}}\nphase: {{phase_name}}")
    for name in NAMES:
        (prompts / f"{name}.md").write_text(name)
    folder = Path(FOLDER)
    (folder / "issues").mkdir(parents=True)
    (folder / "spec.md").write_text(SPEC)
    (folder / "issues" / "01-a.md").write_text(ticket("A", number="01"))
    (folder / "issues" / "02-b.md").write_text(ticket("B", blocked_by="03", number="02"))
    (folder / "issues" / "03-c.md").write_text(ticket("C", blocked_by="01", number="03"))
    git("add", "-A")
    git("commit", "-qm", "init")

    agent = Agent()
    monkeypatch.setattr(agents, "_interface",
                        lambda a: SimpleNamespace(run=agent.run, ToolCallTracker=agent_cc.ToolCallTracker))
    cfg = SSSFConfig(
        defaults=ConfigDefaults(data_dir="adws/adw_data", protected_files=[".scratch/"]),
        observability=ObservabilityConfig(db="adws/adw_data/sssf.db"),
        agents=[AgentConfig(name=n, coding_agent="claude_code", model="anthropic/claude-sonnet-5-5",
                            prompt_engineering={"system": str(prompts / f"{n}.md"),
                                                "user": str(prompts / "user.md")})
                for n in NAMES])
    monkeypatch.setattr(agents, "load_config", lambda path: cfg)
    return agent


def run() -> int:
    return adw_tickets.main(FOLDER)


def test_a_feature_is_built_blockers_first_one_commit_per_ticket(repo):
    assert run() == 0
    assert subjects() == ["01: A", "03: C", "02: B", "Document summaries"]
    assert all(t.done for t in tickets.load(FOLDER).tickets)
    assert git("log", "-1", "--format=%(trailers:key=SSSF-Step,valueonly)").strip() == "documented"
    assert len(repo.of("scout")) == 1
    assert [len(repo.of(n)) for n in ("standards_reviewer", "spec_reviewer")] == [1, 1]


def test_every_ticket_gets_a_fresh_builder_session(repo):
    run()
    sessions = [session for _, session, _ in repo.of("ticket_builder")]
    assert len(sessions) == 3 and len(set(sessions)) == 3


def test_the_builder_gets_its_ticket_the_spec_and_the_scouts_notes(repo):
    run()
    prompt = repo.of("ticket_builder")[1][2]
    assert "issues/03-c.md" in prompt and "spec.md" in prompt and "scout_findings.md" in prompt
    assert "01: A" in prompt                       # what is already committed


def test_the_standards_reviewer_never_sees_the_spec(repo):
    run()
    assert "spec.md" not in repo.of("standards_reviewer")[0][2]
    assert "spec.md" in repo.of("spec_reviewer")[0][2]


def test_a_finished_feature_is_left_alone(repo):
    run()
    calls = len(repo.calls)
    assert run() == 0
    assert len(repo.calls) == calls


def test_a_rerun_resumes_and_reviews_the_whole_feature(repo, monkeypatch):
    real = quality.run_quality
    monkeypatch.setattr(quality, "run_quality",
                        lambda r: QualityResult(passed=not Path("src/t02.py").exists(), failures=["red"]))
    assert run() == 1                              # 02 never goes green
    assert subjects() == ["01: A", "03: C"]
    first = git("rev-parse", "--short", "HEAD~2")
    Path("src/t02.py").unlink()
    monkeypatch.setattr(quality, "run_quality", real)
    builds = len(repo.of("ticket_builder"))
    assert run() == 0
    assert subjects() == ["01: A", "03: C", "02: B", "Document summaries"]
    assert len(repo.of("ticket_builder")) == builds + 1
    assert f"git diff {first}..HEAD" in repo.of("spec_reviewer")[-1][2]


def test_a_ticket_that_stays_red_stops_the_run_and_keeps_its_work(repo, monkeypatch, capsys):
    monkeypatch.setattr(quality, "run_quality",
                        lambda r: QualityResult(passed=not Path("src/t03.py").exists(), failures=["red"]))
    assert run() == 1
    assert subjects() == ["01: A"]
    assert Path("src/t03.py").exists()             # uncommitted, not discarded
    t03 = [s for _, s, p in repo.of("ticket_builder") if "Build ticket 03" in p or "checks" in p]
    assert len(set(t03)) == 1                      # the fixes stayed in 03's session
    assert not tickets.load(FOLDER).tickets[1].done
    assert "git stash" in capsys.readouterr().out


def test_a_blocking_finding_gets_one_fix_and_its_own_commit(repo):
    repo.blocking = True
    assert run() == 0
    assert len(repo.of("ticket_builder")) == 4
    assert subjects()[3] == "Add src/fix.py"
    assert git("log", "-1", "--skip=1", "--format=%(trailers:key=SSSF-Step,valueonly)").strip() == "review-fix"


@pytest.mark.parametrize("setup, problem", [
    (lambda: git("checkout", "-qb", "main"), "refusing to build on main"),
    (lambda: Path("stray.txt").write_text("x"), "uncommitted changes"),
    (lambda: (Path(".gitignore").write_text("adws/adw_data/\n.scratch/\n"),
              git("rm", "-rq", "--cached", ".scratch"), git("commit", "-qam", "ignore")),
     "gitignored"),
])
def test_the_read_step_refuses_before_any_agent_runs(repo, setup, problem):
    setup()
    with pytest.raises(tickets.FeatureError, match=problem):
        run()
    assert repo.calls == []


def test_the_estimate_learns_from_past_runs(repo):
    db = "adws/adw_data/sssf.db"
    run()
    assert tickets.spent_on(db, FOLDER) == pytest.approx(0.1 * 7)
    assert tickets.estimate(db, 0) == "no tickets left to build"
    assert tickets.estimate(db, 2).startswith("about $0.60: 2 ticket(s) at $0.10 each from 3 past")


def test_the_run_records_its_tickets_for_the_dashboard(repo):
    import sqlite3
    run()
    with sqlite3.connect("adws/adw_data/sssf.db") as conn:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM events WHERE name = 'tickets'").fetchone()[0])
    assert payload["feature"] == "summaries"
    assert [(t["number"], t["title"], t["done"]) for t in payload["tickets"]] == [
        ("01", "A", False), ("03", "C", False), ("02", "B", False)]


def test_the_fixer_is_told_what_it_may_not_touch(repo):
    repo.blocking = True
    run()
    fix_prompt = repo.of("ticket_builder")[-1][2]
    assert "Never change these protected paths: .scratch/" in fix_prompt


def test_a_blocking_finding_in_a_protected_file_goes_to_the_engineer(repo, capsys):
    repo.blocking = True
    repo.where = f"{FOLDER}/issues/01-a.md:3"
    assert run() == 0
    assert len(repo.of("ticket_builder")) == 3             # no fix pass
    out = capsys.readouterr().out
    assert "for the engineer" in out and "01-a.md:3" in out


def test_a_permission_breach_stops_cleanly(repo, monkeypatch, capsys):
    from adw_modules.permissions import PermissionBreach
    def breach(run, folder):
        raise PermissionBreach("ticket_builder is barred from ['.scratch/']")
    monkeypatch.setattr(adw_tickets, "build_feature", breach)
    assert run() == 1
    assert "stopped: ticket_builder is barred" in capsys.readouterr().out


def test_the_reviewers_never_see_an_sssf_update(repo, monkeypatch):
    # Updating SSSF on the feature branch is routine between runs, and not the
    # feature's work: a reviewer that sees it calls it scope creep.
    real = quality.run_quality
    monkeypatch.setattr(quality, "run_quality",
                        lambda r: QualityResult(passed=not Path("src/t02.py").exists(), failures=["red"]))
    run()
    Path("src/t02.py").unlink()
    for path in ("adws/adw_modules/new.py", ".claude/skills/sssf/SKILL.md"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("update\n")
    git("add", "-A")
    git("commit", "-qm", "Update SSSF to skill abc1234")
    monkeypatch.setattr(quality, "run_quality", real)
    assert run() == 0
    for name in ("standards_reviewer", "spec_reviewer"):
        prompt = repo.of(name)[-1][2]
        assert "Update SSSF" not in prompt and "02: B" in prompt
        assert "':(exclude)adws/' ':(exclude).claude/skills/sssf/'" in prompt

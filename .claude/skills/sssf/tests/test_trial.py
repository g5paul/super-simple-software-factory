"""Trials: one request under several rosters, a blind panel, the engineer's pick.

The attempts here are real worktree runs in a real git repo with a real trace,
made the way session.ensure makes them, with their agents faked: a commit in
the worktree stands for the build, and a verify event for the checks. The
panel's agents are faked at agents.execute. Design: docs/designs/trials.md.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agents, gates, session, trial, worktree
from adw_modules.data_types import (AgentConfig, ConfigDefaults, EventRecord, ObservabilityConfig,
                                    PanelOutput, PanelVerdict, SSSFConfig)
from adw_modules.tracer import Tracer

DB = "adws/adw_data/sssf.db"


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def roster(harness: str, model: str) -> str:
    agents_yaml = "".join(
        f"  - name: {name}\n    prompt_engineering:\n      system: p.md\n      user: p.md\n"
        for name in trial.REQUIRED_AGENTS)
    return (f"defaults:\n  coding_agent: {harness}\n  model: {model}\n"
            f"observability:\n  db: {DB}\nagents:\n{agents_yaml}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    (root / ".gitignore").write_text("adws/adw_data/sessions/\nadws/adw_data/sssf.db*\n"
                                     "adws/adw_data/worktrees/\n")
    (root / "app.txt").write_text("one\n")
    (root / "p.md").write_text("{{prompt}}\n{{previous_envelope}}\n")
    config = root / trial.CONFIG_DIR
    config.mkdir(parents=True)
    (config / "sssf.config.yaml").write_text(roster("claude_code", "anthropic/claude-sonnet-5-5"))
    (config / "sssf.config.chatgpt.yaml").write_text(roster("pi", "openai/gpt-6.1-sol"))
    git("add", "-A", cwd=root)
    git("commit", "-qm", "start", cwd=root)
    monkeypatch.chdir(root)
    monkeypatch.setattr(trial, "pi_problems", lambda made_by: [])
    return root


def attempt(trial_id: str, adw_id: str, name: str, *, cost: float = 0.5, ok: bool = True,
            checks: bool = True, text: str = "two\n", index: int = 0) -> None:
    """A finished attempt: a worktree run tagged like `just trial` tags one."""
    root = worktree.main_checkout()
    fam = "claude" if name == "main" else "openai"
    tag = {"trial": trial_id, "index": index, "roster": name, "roster_file": trial.roster_file(name),
           "roster_sha256": trial.sha256(root / trial.roster_file(name)), "family": fam,
           "coding_agent": "Claude Code" if name == "main" else "pi",
           "base": git("rev-parse", "HEAD", cwd=root)}
    cfg = SSSFConfig()
    place = worktree.enter(cfg, adw_id, True)
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_root}/sessions/{adw_id}/events.jsonl")
    tracer.session_start(adw_id, "t", adw_name="adw_simple_sdlc")
    tracer.session_request(adw_id, "make app.txt say two")
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="worktree",
                             payload={k: v for k, v in place.items() if k != "resumed"}))
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="trial", payload=tag))
    Path("app.txt").write_text(text)
    git("commit", "-qam", f"build {adw_id}")
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="verify_1",
                             payload={"passed": checks}))
    tracer.session_add_usage(adw_id, 1000, cost)
    tracer.session_finish(adw_id, ok=ok)
    os.chdir(root)


def branches() -> str:
    return git("branch", "--list", "sssf/*", "--format=%(refname:short)")


def events(name: str) -> list[tuple[str, dict]]:
    with sqlite3.connect(DB) as conn:
        rows = conn.execute("select adw_id, payload_json from events where name=? order by rowid",
                            (name,)).fetchall()
    return [(a, json.loads(p)) for a, p in rows]


# ── combining the panel's verdicts ───────────────────────────────────────────

def row(adw_id: str, cost: float, family: str = "claude", status: str = "success",
        checks: bool = True) -> dict:
    return {"adw_id": adw_id, "roster": adw_id, "cost": cost, "family": family,
            "status": status, "checks": checks}


def ruling(family: str, labels: dict, approved: dict, ranking: list[str]) -> dict:
    return {"family": family, "labels": labels, "ranking": ranking,
            "verdicts": [{"label": lab, "approved": approved[lab],
                          "blocking": [] if approved[lab] else ["missing the endpoint"],
                          "findings": []} for lab in labels]}


def test_the_suggestion_is_the_cheapest_attempt_to_pass_every_gate():
    rows = [row("aaa", 0.9), row("bbb", 0.4, "openai"), row("ccc", 0.1, status="fail")]
    labels = {"A": "aaa", "B": "bbb"}
    card = trial.combine(rows, ["claude", "openai"],
                         [ruling("claude", labels, {"A": True, "B": True}, ["A", "B"]),
                          ruling("openai", {"A": "bbb", "B": "aaa"}, {"A": True, "B": True},
                                 ["A", "B"])], True)
    assert card["suggested"] == "bbb"
    by = {c["adw_id"]: c for c in card["attempts"]}
    assert by["ccc"]["review"] is None and not by["ccc"]["passed"]
    # aaa is first for claude, second for openai; without its own family's vote, 2.
    assert by["aaa"]["rank"] == 1.5 and by["aaa"]["rank_others"] == 2
    assert by["bbb"]["rank"] == 1.5 and by["bbb"]["rank_others"] == 2


def test_one_reviewer_blocking_fails_the_review_gate():
    rows = [row("aaa", 0.2), row("bbb", 0.9, "openai")]
    card = trial.combine(rows, ["claude", "openai"],
                         [ruling("claude", {"A": "aaa", "B": "bbb"}, {"A": True, "B": True}, ["A", "B"]),
                          ruling("openai", {"A": "aaa", "B": "bbb"}, {"A": False, "B": True},
                                 ["B", "A"])], True)
    by = {c["adw_id"]: c for c in card["attempts"]}
    assert by["aaa"]["review"] == "block"
    assert by["aaa"]["rulings"][1]["blocking"] == ["missing the endpoint"]
    assert card["suggested"] == "bbb"


def test_a_reviewer_that_never_ruled_leaves_the_gate_unknown_and_no_suggestion():
    rows = [row("aaa", 0.2), row("bbb", 0.9, "openai")]
    card = trial.combine(rows, ["claude", "openai"],
                         [ruling("claude", {"A": "aaa", "B": "bbb"}, {"A": True, "B": True}, ["A", "B"])],
                         True)
    assert {c["review"] for c in card["attempts"]} == {"unknown"}
    assert card["suggested"] is None and "did not rule" in card["why"]


def test_with_one_family_the_rank_without_self_votes_is_empty():
    rows = [row("aaa", 0.2), row("bbb", 0.3)]
    card = trial.combine(rows, ["claude"],
                         [ruling("claude", {"A": "bbb", "B": "aaa"}, {"A": True, "B": True}, ["A", "B"])],
                         True)
    assert [c["rank_others"] for c in card["attempts"]] == [None, None]
    assert card["suggested"] == "aaa"


def test_no_attempt_passing_means_no_suggestion():
    card = trial.combine([row("aaa", 0.2, checks=False), row("bbb", 0.3, status="fail")],
                         ["claude"], [], True)
    assert card["suggested"] is None and card["why"] == "no attempt passed every gate"


# ── the panel's gate ─────────────────────────────────────────────────────────

def panel(verdicts: list[PanelVerdict], ranking: list[str]) -> PanelOutput:
    return PanelOutput(status="success", verdicts=verdicts, ranking=ranking)


def test_the_panel_gate_wants_a_verdict_on_every_label_and_a_full_ranking():
    gate = gates.panel_complete(["A", "B"])
    ok = gate(panel([PanelVerdict(label="A", approved=True),
                     PanelVerdict(label="B", blocking=["no tests"])], ["B", "A"]), None)
    assert ok.passed
    missing = gate(panel([PanelVerdict(label="A", approved=True)], ["A"]), None)
    assert any("verdict on B" in v for v in missing.violations)
    assert any("ranking" in v for v in missing.violations)
    odd = gate(panel([PanelVerdict(label="A", approved=True, blocking=["x"]),
                      PanelVerdict(label="B"), PanelVerdict(label="C", approved=True)],
                     ["A", "B"]), None)
    assert any("approved with blocking" in v for v in odd.violations)
    assert any("names a problem" in v for v in odd.violations)
    assert any("unknown: C" in v for v in odd.violations)


# ── before anything starts ───────────────────────────────────────────────────

def test_a_roster_is_labelled_with_what_its_agents_run_on(repo):
    main = agents.load_config(str(trial.roster_path(repo, "main")))
    assert trial.coding_agent(main) == "Claude Code"
    assert trial.coding_agent(agents.load_config(str(trial.roster_path(repo, "chatgpt")))) == "pi"
    main.agents[0].coding_agent = "pi"
    assert trial.coding_agent(main) == "Claude Code + pi"
    assert trial.roster_label({"roster": "main", "coding_agent": "Claude Code"}) == "main (Claude Code)"
    # A trial from before the coding agent was recorded.
    assert trial.roster_label({"roster": "main"}) == "main"


def test_a_trial_needs_two_attempts(repo):
    with pytest.raises(trial.TrialError, match="at least two"):
        trial.check(repo, ["main"])


def test_an_unknown_roster_names_the_ones_there(repo):
    with pytest.raises(trial.TrialError, match="Rosters here: main, chatgpt"):
        trial.check(repo, ["main", "gemini"])


def test_a_roster_that_is_not_signed_in_refuses_the_whole_trial(repo, monkeypatch):
    def logged_out():
        raise agent_cc.NotLoggedIn("claude has no login")
    monkeypatch.setattr(agent_cc, "check_login", logged_out)
    monkeypatch.setattr(trial, "pi_problems", lambda made_by: [f"pi is not signed in to {made_by}"])
    with pytest.raises(trial.TrialError) as error:
        trial.check(repo, ["main", "chatgpt"])
    assert "main: claude has no login" in str(error.value)
    assert "chatgpt: pi is not signed in to openai" in str(error.value)
    assert not Path(DB).exists() or not events("trial")


def test_pi_on_openai_needs_the_chatgpt_sign_in_and_subscription_auth(monkeypatch):
    def answer(status):
        return lambda *a, **k: SimpleNamespace(stdout=json.dumps(status))
    monkeypatch.setattr(trial.subprocess, "run", answer({"status": "ready", "authType": "api_key"}))
    assert "API key" in trial.pi_problems("openai")[0]
    monkeypatch.setattr(trial.subprocess, "run", answer({"status": "ready", "authType": "oauth"}))
    monkeypatch.delenv("SSSF_PI_AUTH", raising=False)
    assert "SSSF_PI_AUTH" in trial.pi_problems("openai")[0]
    monkeypatch.setenv("SSSF_PI_AUTH", "subscription")
    assert trial.pi_problems("openai") == []
    monkeypatch.setattr(trial.subprocess, "run",
                        answer({"status": "not_ready", "reason": "provider_not_found"}))
    assert "provider_not_found" in trial.pi_problems("google")[0]


def test_too_few_worktree_slots_refuses_naming_the_runs_that_hold_them(repo):
    cfg = SSSFConfig()
    place = worktree.enter(cfg, "busy0001", True)
    tracer = Tracer(cfg.observability.db, os.devnull)
    tracer.session_start("busy0001", "t", adw_name="adw_build")
    tracer.event(EventRecord(adw_id="busy0001", phase_id="", type="log", name="worktree",
                             payload={k: v for k, v in place.items() if k != "resumed"}))
    tracer.process_start("busy0001", "adw", "", os.getpid(), "adw_build.py")
    os.chdir(repo)
    with pytest.raises(trial.TrialError, match="needs 2 worktree slots at once, and busy0001 holds"):
        trial.check(repo, ["main", "chatgpt"])


def test_two_attempts_on_one_plan_are_warned_about(repo):
    _, _, warnings = trial.check(repo, ["main", "main"])
    assert warnings == ["main, main all run on claude, so they share one plan's usage window"]
    _, _, warnings = trial.check(repo, ["main", "chatgpt"])
    assert warnings == []


def test_an_attempt_that_never_opens_its_run_is_refused(repo, monkeypatch):
    Tracer(DB, os.devnull).session_start("ran00001", "t", adw_name="adw_simple_sdlc")
    exited = SimpleNamespace(poll=lambda: 1)
    running = SimpleNamespace(poll=lambda: None)
    launched = [({"adw_id": "ran00001"}, running, None), ({"adw_id": "gone0001"}, exited, None)]
    assert trial._wait_for_starts(DB, launched) == ["gone0001"]


def test_an_attempt_records_its_trial_at_its_start(repo, monkeypatch):
    tag = {"trial": "t0000001", "roster": "chatgpt", "roster_sha256": "abc", "family": "openai"}
    monkeypatch.setenv("SSSF_TRIAL", json.dumps(tag))
    cfg = SSSFConfig(defaults=ConfigDefaults(data_dir="adws/adw_data"),
                     observability=ObservabilityConfig(db=DB))
    run = session.ensure(cfg, None, use_worktree=True)
    os.chdir(repo)
    assert events("trial") == [(run.adw_id, tag)]


# ── the panel ────────────────────────────────────────────────────────────────

def fake_panel(monkeypatch, fail_family: str | None = None, block: str | None = None):
    """Each reviewer approves every attempt except `block`'s, and ranks the
    attempt whose diff says "good" first."""
    seen = []

    def execute(run, phase, call):
        fam = phase.params.owner.removeprefix("referee_")
        seen.append((fam, call))
        if fam == fail_family:
            raise agents.GateFailure("the reviewer's JSON never parsed")
        diffs = {a.label: Path(a.diff_path).read_text() for a in call.previous.attempts}
        verdicts = [PanelVerdict(label=lab, approved=not (block and block in text),
                                 blocking=["wrong text"] if block and block in text else [])
                    for lab, text in diffs.items()]
        ranking = sorted(diffs, key=lambda lab: "good" not in diffs[lab])
        return PanelOutput(status="success", verdicts=verdicts, ranking=ranking)

    monkeypatch.setattr(agents, "execute", execute)
    return seen


def test_the_panel_reviews_blind_and_the_scorecard_suggests_the_cheapest_pass(repo, monkeypatch):
    attempt("t0000001", "aaaa0001", "main", cost=0.9, text="good\n", index=0)
    attempt("t0000001", "bbbb0001", "chatgpt", cost=0.3, text="fine\n", index=1)
    attempt("t0000001", "cccc0001", "chatgpt", cost=0.1, ok=False, checks=False, text="bad\n", index=2)
    seen = fake_panel(monkeypatch)
    out = []
    assert trial.review("t0000001", out=SimpleNamespace(write=out.append, flush=lambda: None)) == 0

    assert [fam for fam, _ in seen] == ["claude", "openai"]
    for _, call in seen:
        shown = call.previous.model_dump_json()
        # Only the two clean attempts, by label, with nothing that names a roster.
        assert sorted(a.label for a in call.previous.attempts) == ["A", "B"]
        assert "chatgpt" not in shown and "main" not in shown
        assert "Claude Code" not in shown and "(pi)" not in shown
        assert call.prompt == "make app.txt say two"
    (_, sizes), = events("trial_diffs")
    assert sizes["sizes"] == {"aaaa0001": {"added": 1, "removed": 1, "files": 1},
                              "bbbb0001": {"added": 1, "removed": 1, "files": 1}}
    (_, card), = events("trial_scorecard")
    assert card["suggested"] == "bbbb0001" and card["panel_status"] == "success"
    by = {a["adw_id"]: a for a in card["attempts"]}
    assert by["aaaa0001"]["rank"] == 1 and by["bbbb0001"]["rank"] == 2
    assert by["cccc0001"]["review"] is None
    assert card["total_cost"] == pytest.approx(1.3)
    text = "".join(out)
    assert "suggested: bbbb0001, chatgpt (pi), the cheapest attempt to pass every gate" in text
    assert "aaaa0001    main (Claude Code)" in text


def test_a_failed_panel_still_records_the_scorecard_with_no_suggestion(repo, monkeypatch):
    attempt("t0000002", "aaaa0002", "main", text="good\n", index=0)
    attempt("t0000002", "bbbb0002", "chatgpt", text="fine\n", index=1)
    fake_panel(monkeypatch, fail_family="openai")
    with pytest.raises(agents.GateFailure):
        trial.review("t0000002", out=SimpleNamespace(write=lambda s: None, flush=lambda: None))
    (_, card), = events("trial_scorecard")
    assert card["suggested"] is None and card["panel_status"] == "fail"
    assert {a["review"] for a in card["attempts"]} == {"unknown"}
    assert "trial-review t0000002" in trial.render(trial.scorecard(DB, "t0000002"))


def test_the_panel_waits_for_every_attempt_to_end(repo):
    attempt("t0000003", "aaaa0003", "main", index=0)
    Tracer(DB, os.devnull).process_start("aaaa0003", "adw", "", os.getpid(), "adw_simple_sdlc.py")
    with pytest.raises(trial.TrialError, match="still going"):
        trial.review("t0000003")


# ── landing the pick ─────────────────────────────────────────────────────────

def test_merge_lands_the_pick_and_drops_the_rest(repo):
    attempt("t0000004", "aaaa0004", "main", text="good\n", index=0)
    attempt("t0000004", "bbbb0004", "chatgpt", text="fine\n", index=1)
    done = trial.merge("t0000004", "bbbb0004", ff_only=False)
    assert "merged sssf/bbbb0004 into main" in done and "discarded sssf/aaaa0004" in done
    assert Path("app.txt").read_text() == "fine\n"
    assert branches() == ""
    assert events("trial_merged") == [("bbbb0004", {"trial": "t0000004"})]
    assert trial.scorecard(DB, "t0000004")["merged"] == "bbbb0004"


def test_merge_refuses_a_run_from_outside_the_trial_and_a_failed_merge_drops_nothing(repo):
    attempt("t0000005", "aaaa0005", "main", index=0)
    attempt("t0000005", "bbbb0005", "chatgpt", ok=False, index=1)
    with pytest.raises(trial.TrialError, match="not an attempt of trial t0000005"):
        trial.merge("t0000005", "zzzz0005", ff_only=False)
    with pytest.raises(worktree.WorktreeError, match="only a successful run merges"):
        trial.merge("t0000005", "bbbb0005", ff_only=False)
    assert branches().split() == ["sssf/aaaa0005", "sssf/bbbb0005"]


def test_discard_drops_every_attempt_and_keeps_the_scorecard(repo):
    attempt("t0000006", "aaaa0006", "main", index=0)
    attempt("t0000006", "bbbb0006", "chatgpt", index=1)
    assert trial.discard("t0000006").count("discarded") == 2
    assert branches() == ""
    assert len(trial.scorecard(DB, "t0000006")["attempts"]) == 2


# ── adding a roster ──────────────────────────────────────────────────────────

def install(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), *args],
                          capture_output=True, text=True, check=False)


def test_add_roster_stamps_a_starter_beside_the_repos_own_and_updates_keep_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert install("--coding-agent", "claude_code").returncode == 0
    added = install("--add-roster", "chatgpt")
    assert added.returncode == 0, added.stderr
    path = Path("adws/adw_sssf_config/sssf.config.chatgpt.yaml")
    assert path.read_text() == (SKILL / "templates" / "sssf.config.chatgpt.yaml").read_text()
    record = json.loads(Path("adws/adw_sssf_config/stamp.json").read_text())
    assert record["files"][str(path)]["source"] == "sssf.config.chatgpt.yaml"
    assert install("--add-roster", "chatgpt").returncode == 1
    # An update keeps it like any stamped file: current, and kept when edited.
    update = install("--update")
    assert "sssf.config.chatgpt.yaml" not in update.stdout
    path.write_text(path.read_text() + "# ours\n")
    assert "= adws/adw_sssf_config/sssf.config.chatgpt.yaml" in install("--update").stdout
    assert path.read_text().endswith("# ours\n")

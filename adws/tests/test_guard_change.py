"""The guard-change chain's own code: the state check, the snapshot, the gates.

Run from the hub root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests
"""

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adw_guard_change as gc  # noqa: E402
from adw_modules.data_types import (BuildOutput, EnvelopeBase, PlanOutput,  # noqa: E402
                                    QualityResult, ReviewOutput)


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    """Keep git away from the real ~/.gitconfig."""
    home = tmp_path / "home"
    (home / "tmp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(home / "tmp"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("XDG_CONFIG_HOME", "GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    guard = path / gc.GUARD_DIR
    guard.mkdir(parents=True)
    (guard / "permissions.py").write_text("LIMIT = 3\n\n\ndef enforce():\n    return LIMIT\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "base")
    monkeypatch.chdir(path)
    return path


# ── new_state ────────────────────────────────────────────────────────────────

def test_a_new_module_level_name_is_state():
    assert gc.new_state("A = 1\n", "A = 1\n_PIN = None\n", "p.py") == ["p.py: module-level _PIN"]


def test_a_new_global_statement_is_state():
    old = "X = 1\ndef f():\n    return X\n"
    new = "X = 1\ndef f():\n    global X\n    X = 2\n"
    assert gc.new_state(old, new, "p.py") == ["p.py: global X"]


def test_state_set_inside_a_top_level_try_counts():
    new = "try:\n    import fcntl\n    _LOCK = 1\nexcept ImportError:\n    _LOCK = 0\n"
    assert gc.new_state("", new, "p.py") == ["p.py: module-level _LOCK"]


def test_a_changed_value_or_a_new_function_is_not_state():
    old = "A = 1\ndef f():\n    x = 1\n    return x\n"
    new = "A = 2\ndef f():\n    y = 1\n    return y\ndef g():\n    z = 3\n"
    assert gc.new_state(old, new, "p.py") == []


def test_the_post_mortem_mechanisms_would_trip_it():
    """The git-directory guard's config pin, keep mode and shelter."""
    new = "_PIN: list[str] = []\n_UNSURE = False\n_EXISTED: dict = {}\n"
    assert gc.new_state("", new, "permissions.py") == [
        "permissions.py: module-level _EXISTED", "permissions.py: module-level _PIN",
        "permissions.py: module-level _UNSURE"]


def test_a_new_top_level_let_in_typescript_is_state():
    old = "const RULES = [];\nfunction f() { let x = 1; }\n"
    new = old + "let seen = new Set();\nexport var mode = 'keep';\n"
    assert gc.new_state(old, new, "sssf_rules.ts") == [
        "sssf_rules.ts: top-level mode", "sssf_rules.ts: top-level seen"]


def test_an_unparseable_module_reports_nothing_rather_than_crashing():
    assert gc.new_state("A = 1\n", "def (:\n", "p.py") == []


# ── snapshot and mechanisms ──────────────────────────────────────────────────

def test_a_snapshot_holds_untracked_files_and_leaves_the_tree_alone(repo):
    (repo / "new_test.py").write_text("def test_x(): pass\n")
    (repo / gc.GUARD_DIR / "permissions.py").write_text("LIMIT = 4\n")
    head, status = git(repo, "rev-parse", "HEAD"), git(repo, "status", "--porcelain")

    sha = gc.snapshot("round 1", cwd=str(repo))

    assert git(repo, "show", f"{sha}:new_test.py") == "def test_x(): pass"
    assert git(repo, "rev-parse", f"{sha}^") == head
    assert git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") == status


def test_the_spec_commit_leaves_an_earlier_build_in_the_tree(repo):
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@t")
    guard = repo / gc.GUARD_DIR / "permissions.py"
    guard.write_text("LIMIT = 4\n")                   # a failed attempt's uncommitted build
    (repo / "specs").mkdir()
    (repo / "specs" / "a1b2c3d4_guard_v2.md").write_text("# spec\n")
    gc.commit_spec("Add spec v2", ["specs/a1b2c3d4_guard_v2.md"])
    assert git(repo, "show", "--name-only", "--format=", "HEAD") == "specs/a1b2c3d4_guard_v2.md"
    assert git(repo, "status", "--porcelain") == f"M {gc.GUARD_DIR}/permissions.py"


def test_an_unchanged_spec_commits_nothing(repo):
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", "t@t")
    (repo / "specs").mkdir()
    (repo / "specs" / "s.md").write_text("# spec\n")
    first = gc.commit_spec("Add spec", ["specs/s.md"])
    assert gc.commit_spec("Add spec again", ["specs/s.md"]) == first


def test_tidy_clears_what_the_launch_walk_refuses(tmp_path):
    review = tmp_path / "bypass_review_1"
    (review / "work").mkdir(parents=True)
    (review / "work" / "probe.py").write_text("print(1)\n")
    (review / "locked" / "inner").mkdir(parents=True)
    (review / "locked").chmod(0o300)
    deep = review / "cases"
    deep.mkdir()
    cwd = os.getcwd()
    try:
        os.chdir(deep)
        for _ in range(22):                   # relative steps, so no call sees the whole path
            os.mkdir("d" * 200)
            os.chdir("d" * 200)
        Path("f").write_text("x")
    finally:
        os.chdir(cwd)
    outside = tmp_path / "cache-f"
    outside.write_text("cached\n")
    os.link(outside, review / "work" / "linked")
    (tmp_path / "bypass_review_1.md").write_text("# report\n")

    removed = gc.tidy_review_scratch(review)

    assert removed == ["cases", "1 hard-linked files"]
    assert (review / "work" / "probe.py").is_file() and not (review / "work" / "linked").exists()
    assert os.access(review / "locked", os.R_OK | os.X_OK)
    assert outside.read_text() == "cached\n" and (tmp_path / "bypass_review_1.md").is_file()


def test_tidy_takes_names_that_are_not_utf8_or_hold_a_newline(tmp_path):
    review = tmp_path / "bypass_review_1"
    names = review / "prune" / "names"
    names.mkdir(parents=True)
    for raw in (b"audit.jsonl.\xff", b"audit.jsonl.1\n", "audit.jsonl.\u0661".encode()):
        with open(os.path.join(os.fsencode(names), raw), "wb") as f:
            f.write(b"x")
    outside = tmp_path / "cache-f"
    outside.write_text("cached\n")
    os.link(outside, os.path.join(os.fsencode(names), b"linked\n\xfe"))

    assert gc.tidy_review_scratch(review) == ["1 hard-linked files"]
    assert len(os.listdir(os.fsencode(names))) == 3 and outside.read_text() == "cached\n"


def test_tidy_leaves_a_missing_folder_alone(tmp_path):
    assert gc.tidy_review_scratch(tmp_path / "bypass_review_9") == []


def test_mechanisms_names_state_a_fix_adds_to_a_guard_module(repo):
    first = gc.snapshot("round 1", cwd=str(repo))
    path = repo / gc.GUARD_DIR / "permissions.py"
    path.write_text(path.read_text() + "_SHELTER: set[str] = set()\n")
    second = gc.snapshot("round 2", cwd=str(repo))

    assert gc.mechanisms(first, second) == [f"{gc.GUARD_DIR}/permissions.py: module-level _SHELTER"]
    assert gc.mechanisms(first, first) == []


def test_mechanisms_counts_a_new_guard_module_whole(repo):
    first = gc.snapshot("round 1", cwd=str(repo))
    (repo / gc.GUARD_DIR / "gates.py").write_text("SEEN = set()\n")
    second = gc.snapshot("round 2", cwd=str(repo))
    assert gc.mechanisms(first, second) == [f"{gc.GUARD_DIR}/gates.py: module-level SEEN"]


# ── the reviewer's report ────────────────────────────────────────────────────

def finding(**kw):
    return gc.Finding(**{"candidate": "'git push'", "check": "rules.breach",
                         "base": "stopped", "head": "passes", **kw})


def review(**kw):
    return gc.BypassReview(**{"status": "success", "candidates": 12, "checks": 3, **kw})


def test_a_complete_review_passes_the_gate():
    assert gc.review_well_formed(review(bypasses=[finding()]), None).violations == []


def test_the_gate_wants_both_verdicts_on_a_bypass():
    report = gc.review_well_formed(review(bypasses=[finding(base="")]), None)
    assert report.violations == ["bypasses[0] verdicts: give the verdict on base and on head"]


def test_the_gate_wants_the_mechanism_named_and_the_counts():
    report = gc.review_well_formed(review(adds_mechanism=True, candidates=0), None)
    assert [v.split(":")[0] for v in report.violations] == ["mechanism", "counts"]


def test_an_old_hole_needs_no_verdict_pair():
    hole = gc.Finding(candidate="'x'", check="enforce")
    assert gc.review_well_formed(review(old_holes=[hole]), None).violations == []


def test_only_bypasses_harms_and_false_positives_block():
    quiet = review(broken_promises=[finding()], old_holes=[finding()])
    assert gc.blocking(quiet) == []
    loud = review(harms=[finding()], false_positives=[finding(candidate="'cat > plan.md'")])
    assert len(gc.blocking(loud)) == 2
    assert len(gc.blocking(loud, allow_false_positives=True)) == 1


# ── the spec ─────────────────────────────────────────────────────────────────

SHA = "0146630aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def spec(**override):
    sections = {
        "Threat model": "The agent runs as the engineer and can write any file it reaches.",
        "Baseline": "main at 0146630, the commit every finding is judged against.",
        "Bar": "No agent file kept, no agent code run, no engineer file deleted.",
        "Out of scope": "Submodules and hard-linked gitconfig files stay unwatched here.",
        **override,
    }
    return "# Spec\n\n" + "".join(f"## {h}\n\n{b}\n\n" for h, b in sections.items() if b is not None)


def plan_envelope(tmp_path, text):
    path = tmp_path / "plan.md"
    path.write_text(text)
    return EnvelopeBase(status="success", artifacts=[str(path)])


def test_a_spec_with_all_four_sections_passes(tmp_path):
    report = gc.spec_sections(SHA)(plan_envelope(tmp_path, spec()), None)
    assert report.violations == []


def test_a_missing_or_thin_section_fails(tmp_path):
    report = gc.spec_sections(SHA)(plan_envelope(tmp_path, spec(Bar=None, **{"Out of scope": "None."})),
                                   None)
    assert [v.split(":")[0] for v in report.violations] == ["Bar", "Out of scope"]


def test_the_baseline_must_name_the_commit(tmp_path):
    text = spec(Baseline="main, as it was when the run started this morning.")
    report = gc.spec_sections(SHA)(plan_envelope(tmp_path, text), None)
    assert report.violations == ["baseline commit: the Baseline section must name the commit 0146630"]


def test_a_longer_heading_still_counts(tmp_path):
    text = spec().replace("## Bar\n", "## Bar: what the change must do\n")
    assert gc.spec_sections(SHA)(plan_envelope(tmp_path, text), None).violations == []


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_lists_every_finding_and_the_outcome(tmp_path):
    path = tmp_path / "guard_report.md"
    gc.write_report(path, review(old_holes=[finding(new_to_known_limits=True)],
                                 adds_mechanism=True, mechanism="a keep mode"),
                    "round 2's fix adds a mechanism", ["a" * 40, "b" * 40],
                    ["permissions.py: module-level _KEEP"])
    text = path.read_text()
    for part in ("Outcome: round 2's fix adds a mechanism", "`aaaaaaaaaaaa`, `bbbbbbbbbbbb`",
                 "_KEEP", "a keep mode", "## Old holes (1)", "New to the known limits.",
                 "## Bypasses (0)", "12 candidates, 3 checks."):
        assert part in text


# ── the chain, with scripted agents ──────────────────────────────────────────

class FakeRun:
    """Runs phases in order and hands each agent phase its next scripted reply."""

    def __init__(self, tmp_path, replies):
        self.adw_id, self.engineer = "a1b2c3d4", "engineer"
        self.repo_root = tmp_path
        self.context_handoff_dir = tmp_path / "handoff"
        self.context_handoff_dir.mkdir()
        self.replies = {k: list(v) for k, v in replies.items()}
        self.phases, self.calls, self.finished = [], [], None

    @contextmanager
    def phase(self, params):
        self.phases.append(params.name)
        run = self

        class Handle:
            def log(self, **payload):
                pass

            def call(self, call):
                run.calls.append((params.owner, params.name, call))
                return run.replies[params.owner].pop(0)

        yield Handle()

    def finish(self, accepted=True, reason=""):
        self.finished = (accepted, reason)
        return 0 if accepted else 1


PLAN = PlanOutput(status="success", artifacts=["/h/plan.md", "specs/a1b2c3d4_guard.md"],
                  commit_message="Add spec")
BUILD = BuildOutput(status="success", commit_message="Tighten the guard")
APPROVED = ReviewOutput(status="success", approved=True)
REJECTED = ReviewOutput(status="success", approved=False, blocking=["the Bar is vague"])


def chain(tmp_path, monkeypatch, reviews, spec_reviews=(APPROVED,), state=None, branch="guard-x"):
    """Run main() with every agent scripted. `state` maps a round to the
    mechanisms the code check finds going into it."""
    run = FakeRun(tmp_path, {"planner": [PLAN] * 3, "guard_spec_reviewer": list(spec_reviews),
                             "guard_builder": [BUILD] * 10, "bypass_reviewer": list(reviews)})
    shas = iter(f"{n:040d}" for n in range(1, 10))
    rounds = iter(range(1, 10))
    monkeypatch.setattr(gc.agents, "load_config", lambda path: None)
    run.validated = []
    monkeypatch.setattr(gc.agents, "validate", lambda cfg, names: run.validated.append(list(names)))
    monkeypatch.setattr(gc.session, "ensure", lambda *a, **k: run)
    monkeypatch.setattr(gc.git_helper, "current_branch", lambda: branch)
    monkeypatch.setattr(gc.git_helper, "rev", lambda ref: "f" * 40)
    monkeypatch.setattr(gc.git_helper, "commit_all", lambda message: "abc1234")
    monkeypatch.setattr(gc, "commit_spec", lambda message, paths: "abc1234")
    monkeypatch.setattr(gc.plan_checks, "verify", lambda run, plan: QualityResult(passed=True))
    monkeypatch.setattr(gc, "snapshot", lambda message, cwd=None: next(shas))
    monkeypatch.setattr(gc, "mechanisms", lambda old, new: (state or {}).get(next(rounds), []))
    code = gc.main("tighten the guard")
    return run, code


def owners(run, owner):
    return [name for who, name, _ in run.calls if who == owner]


def test_a_clean_first_round_commits_the_build(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review()])
    assert code == 0 and run.finished == (True, "round 1 found nothing that blocks")
    assert run.phases[-1] == "commit_build" and "report" in run.phases
    assert (run.context_handoff_dir / "guard_report.md").is_file()


def test_a_bypass_goes_back_to_the_builder_and_the_next_round_judges_the_fix(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review(bypasses=[finding()]), review()])
    assert code == 0
    assert owners(run, "guard_builder") == ["build", "fix_round_1"]
    second = run.calls[-1][2].prompt
    assert f"head: `{2:040d}`" in second and f"git diff {1:040d} {2:040d}" in second


def test_the_build_and_fix_phases_run_as_the_guard_builder(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review(bypasses=[finding()]), review()])
    assert code == 0
    assert owners(run, "guard_builder") == ["build", "fix_round_1"]
    assert owners(run, "builder") == []


def test_the_config_check_requires_the_guard_builder(tmp_path, monkeypatch):
    assert "guard_builder" in gc.REQUIRED_AGENTS and "builder" not in gc.REQUIRED_AGENTS
    real_validate, real_load = gc.agents.validate, gc.agents.load_config
    run, _ = chain(tmp_path, monkeypatch, [review()])
    assert run.validated == [gc.REQUIRED_AGENTS]
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    cfg = real_load(str(root / "adws/adw_sssf_config/sssf.config.yaml"))
    real_validate(cfg, gc.REQUIRED_AGENTS)


def test_the_plan_brief_plans_the_listed_paths_for_the_guard_builder():
    paragraph = ("This chain's builder is `guard_builder`. Its `writes` names every "
                 "`protected_files` path under `.claude/skills/sssf/`, so plan changes to those "
                 "paths as builder steps. Every other protected path stays under "
                 "`## For the engineer`.")
    assert paragraph in gc.plan_brief("x", "main", "16ae2a5")


def test_the_build_brief_names_the_guard_builder():
    paragraph = ("This run's builder is `guard_builder`. Its `writes` names every "
                 "`protected_files` path under `.claude/skills/sssf/`, so those are yours to "
                 "change when the spec asks. Every other protected path stays off-limits.")
    assert paragraph in gc.build_brief("specs/x.md")


def test_the_reviewer_is_briefed_from_the_spec_and_the_baseline(tmp_path, monkeypatch):
    run, _ = chain(tmp_path, monkeypatch, [review()])
    brief = run.calls[-1][2].prompt
    assert "specs/a1b2c3d4_guard.md" in brief and "base: `" + "f" * 40 in brief
    assert "tighten the guard" not in brief


def test_three_blocking_rounds_stop_without_a_fourth(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review(harms=[finding()])] * 3)
    assert code == 1 and run.finished[1] == "1 blocking finding(s) left after 3 rounds"
    assert owners(run, "bypass_reviewer") == ["bypass_review_1", "bypass_review_2", "bypass_review_3"]
    assert "commit_build" not in run.phases


def test_a_fix_that_adds_state_stops_before_the_next_review(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review(bypasses=[finding()]), review()],
                      state={2: ["permissions.py: module-level _PIN"]})
    assert code == 1 and run.finished[1] == "round 1's fix added state to the guard modules"
    assert owners(run, "bypass_reviewer") == ["bypass_review_1"]
    assert "_PIN" in (run.context_handoff_dir / "guard_report.md").read_text()


def test_state_in_the_first_build_is_reported_but_does_not_stop(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review()], state={1: ["gates.py: module-level SEEN"]})
    assert code == 0
    assert "SEEN" in (run.context_handoff_dir / "guard_report.md").read_text()


def test_the_reviewer_flagging_a_mechanism_stops_even_a_clean_round(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch,
                      [review(false_positives=[finding()]), review(adds_mechanism=True,
                                                                  mechanism="a keep mode")])
    assert code == 1 and "adds a mechanism" in run.finished[1]
    assert "commit_build" not in run.phases


def test_a_false_positive_blocks_unless_allowed(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [review(false_positives=[finding()]), review()])
    assert owners(run, "guard_builder") == ["build", "fix_round_1"] and code == 0


def test_a_rejected_spec_is_revised_then_the_run_stops(tmp_path, monkeypatch):
    run, code = chain(tmp_path, monkeypatch, [], spec_reviews=[REJECTED, REJECTED])
    assert code == 1 and run.finished[1] == "the spec review never approved the spec"
    assert owners(run, "planner") == ["plan", "revise_plan_1"] and not owners(run, "guard_builder")
    assert "commit_plan" not in run.phases


def test_a_spec_review_that_narrows_gets_a_third_round(tmp_path, monkeypatch):
    two = ReviewOutput(status="success", approved=False, blocking=["the Bar is vague", "no tests"])
    run, code = chain(tmp_path, monkeypatch, [review()], spec_reviews=[two, REJECTED, APPROVED])
    assert owners(run, "planner") == ["plan", "revise_plan_1", "revise_plan_2"]
    assert "spec_review_3" in run.phases and code == 0


def test_a_spec_review_that_does_not_narrow_stops_at_two(tmp_path, monkeypatch):
    two = ReviewOutput(status="success", approved=False, blocking=["the Bar is vague", "no tests"])
    run, code = chain(tmp_path, monkeypatch, [], spec_reviews=[REJECTED, two, APPROVED])
    assert code == 1 and run.finished[1] == "the spec review never approved the spec"
    assert "spec_review_3" not in run.phases


def test_a_third_rejection_stops_the_run(tmp_path, monkeypatch):
    two = ReviewOutput(status="success", approved=False, blocking=["the Bar is vague", "no tests"])
    run, code = chain(tmp_path, monkeypatch, [], spec_reviews=[two, REJECTED, REJECTED])
    assert code == 1 and run.finished[1] == "the spec review never approved the spec"
    assert owners(run, "planner") == ["plan", "revise_plan_1", "revise_plan_2"]


def test_a_guard_change_refuses_to_run_on_main(tmp_path, monkeypatch):
    with pytest.raises(SystemExit):
        chain(tmp_path, monkeypatch, [review()], branch="main")


@pytest.mark.parametrize("role", ["bypass_reviewer", "guard_spec_reviewer"])
def test_the_new_prompts_render_with_every_placeholder_filled(role):
    from adw_modules import agents, prompts
    from adw_modules.data_types import AgentCall, SSSFConfig
    run = SimpleNamespace(context_handoff_dir=Path("/handoff"), cfg=SSSFConfig())
    phase = SimpleNamespace(params=SimpleNamespace(name=f"{role}_1"))
    variables = agents.prompt_variables(run, phase, AgentCall(output_type=gc.BypassReview,
                                                              prompt="the brief"))
    folder = Path(__file__).resolve().parents[1] / "adw_data" / "prompt_engineering" / role
    for name in ("system.md", "user.md"):
        text = prompts.render(folder / name, variables)
        assert "{{" not in text
    assert "the brief" in prompts.render(folder / "user.md", variables)


def test_the_guard_list_is_the_one_claude_md_names():
    hub = Path(__file__).resolve().parents[2]
    for name in gc.GUARD_MODULES:
        assert (hub / gc.GUARD_DIR / name).is_file(), name
    guard_line = next(line for line in (hub / "CLAUDE.md").read_text().splitlines()
                      if line.startswith("Guard code is what limits an agent"))
    named = [n for n in guard_line.split("`") if n.endswith((".py", ".ts"))]
    assert sorted(named) == sorted(gc.GUARD_MODULES)


def test_the_build_brief_copy_takes_what_git_sees_and_leaves_ignored_files_out(tmp_path):
    repo, dest = tmp_path / "repo", tmp_path / "copy"
    repo.mkdir()
    dest.mkdir()
    git(repo, "init", "-q")
    (repo / ".gitignore").write_text(".env\n")
    for name in ("kept.txt", "changed.txt", "gone.txt"):
        (repo / name).write_text("old\n")
    (repo / "sub").mkdir()
    (repo / "sub" / "deep.txt").write_text("deep\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    (repo / "changed.txt").write_text("new\n")
    (repo / "gone.txt").unlink()
    (repo / "added.txt").write_text("added\n")
    (repo / ".env").write_text("DECOY=1\n")
    assert "env" not in gc.COPY_TO_D and gc.COPY_TO_D in gc.build_brief("specs/x.md")
    subprocess.run(["bash", "-c", gc.COPY_TO_D], cwd=repo, check=True, env={**os.environ, "D": str(dest)})
    got = sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())
    assert got == [".gitignore", "added.txt", "changed.txt", "kept.txt", "sub/deep.txt"]
    assert (dest / "changed.txt").read_text() == "new\n"

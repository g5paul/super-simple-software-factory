"""A failed agent call is rolled back like a finished one, and no git runs while
the git directory isn't known to be back.

Every test drives agents.execute against a real git repo, the roster SSSF
ships (templates/sssf.config.yaml, never a test-only one) and the real
permissions.snapshot, head, undo_commits and enforce. Where a test says so, the
git-directory guard is a controlled fake or the real one.

The filter tests plant `[filter "x"]` in .git/config: git runs a filter in any
diff, checkout or reset, so a marker file shows whether SSSF ran git while the
agent's config was live.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi, agents, git_helper, permissions, repo_settings, rules, silence
from adw_modules.data_types import AgentCall, AgentResult, BuildOutput

PERMS = "adws/adw_modules/permissions.py"
REAL = {name: getattr(permissions, name)
        for name in ("guard_git_dir", "git_dir_changed", "restore_git_dir")}
OK = json.dumps({"status": "success", "summary": "done"})


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


class Quiet:
    """Any call is a no-op, except the ones a test records."""

    def __init__(self, **kept):
        self.__dict__.update(kept)

    def __getattr__(self, name):
        return lambda *a, **k: None


@pytest.fixture
def world(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    git(repo, "init", "-qb", "main")
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    files = {PERMS: "v0\n", "src/app.py": "old\n", ".gitignore": "adws/adw_data/sessions/\n"}
    for a in cfg.agents:
        files[a.prompt_engineering.system] = ""
        files[a.prompt_engineering.user] = ""
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")

    events, notes, advised = [], [], []
    world = SimpleNamespace(
        repo=repo, tmp=tmp_path, marker=tmp_path / "marker", events=events, notes=notes,
        advised=advised, base=git(repo, "rev-parse", "HEAD"), n=0, turn=None, gates=[],
        calls={"undo": 0, "enforce": 0})
    world.run = SimpleNamespace(
        cfg=cfg, adw_id="r1", session_dir=tmp_path / "s", data_root=tmp_path, repo_root=repo,
        context_handoff_dir=tmp_path / "h", agent_map={}, cost=0.0,
        tracer=Quiet(event=events.append),
        console=Quiet(note=notes.append, advise=advised.append),
        add_usage=lambda *a: None, save_agent_map=lambda *a: None)
    world.phase = SimpleNamespace(phase_id="r1_01",
                                  params=SimpleNamespace(owner="planner", name="plan", retries=0))

    def run_fake(request, **kw):
        world.n += 1
        return world.turn(world.n - 1, kw)

    fake = SimpleNamespace(run=run_fake, ToolCallTracker=agent_pi.ToolCallTracker)
    monkeypatch.setattr(agents, "_interface", lambda agent: fake)
    for name in ("check_run", "check_live", "check_daily", "check_machine"):
        monkeypatch.setattr(agents.budget, name, lambda *a, **k: None)
    monkeypatch.setattr(agents.agent_cc, "check_login", lambda *a, **k: None)
    monkeypatch.setattr(agents.account, "check", lambda *a, **k: None)
    monkeypatch.setattr(permissions, "guard_git_dir", lambda run: None)
    monkeypatch.setattr(permissions, "restore_git_dir", lambda *a, **k: [])

    def spy(name):
        real = getattr(permissions, name)
        key = {"undo_commits": "undo", "enforce": "enforce"}[name]

        def wrapped(*a, **k):
            world.calls[key] += 1
            return real(*a, **k)
        monkeypatch.setattr(permissions, name, wrapped)
    spy("undo_commits")
    spy("enforce")

    def execute():
        world.n = 0
        call = AgentCall(output_type=BuildOutput, prompt="x", gates=world.gates)
        return agents.execute(world.run, world.phase, call)
    world.execute = execute

    def names(name):
        return [e for e in events if e.name == name]
    world.named = names
    return world


def write(world, path: str, text: str) -> None:
    target = world.repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def plant_files(world) -> None:
    """What a failing agent leaves: two protected paths and one outside `writes`,
    one path inside it, all committed by the agent."""
    write(world, PERMS, "evil\n")
    write(world, "justfile", "evil:\n")
    write(world, "src/app.py", "evil\n")
    write(world, "specs/plan.md", "plan\n")
    git(world.repo, "add", "-A")
    git(world.repo, "commit", "-qm", "agent")


def plant_filter(world) -> None:
    """A filter that leaves a marker, a `* filter=x` attribute, and the writes
    a rollback would have to undo. Written with Python: running git here would
    run the filter itself."""
    config = world.repo / ".git" / "config"
    config.write_text(config.read_text()
                      + f'[filter "x"]\n\tclean = touch {world.marker}; cat\n'
                        f'\tsmudge = touch {world.marker}; cat\n')
    write(world, ".gitattributes", "* filter=x\n")
    write(world, "src/app.py", "evil\n")
    write(world, "justfile", "evil:\n")


def turns(world, plant, answers):
    """The agent plants on its first turn; `answers` is its text per turn, the
    last one repeating."""
    def turn(n, kw):
        if n == 0:
            plant(world)
        return AgentResult(text=answers[min(n, len(answers) - 1)], cost=0.1)
    world.turn = turn


# ── routes ────────────────────────────────────────────────────────────────────

def route_invalid(world):
    turns(world, plant_files, ["no json"])
    return RuntimeError


def route_gate(world):
    turns(world, plant_files, [OK])
    world.gates.append(lambda envelope, run: ["always wrong"])
    return agents.GateFailure


def route_rule(world):
    write(world, repo_settings.PATH, yaml.safe_dump(
        {"agent_rules": [{"rule": "Never run `git push`.", "forbid": [r"\bgit\s+push\b"]}]}))

    def turn(n, kw):
        plant_files(world)
        kw["on_event"]({"type": "tool_execution_start", "toolCallId": "c1", "toolName": "bash",
                        "args": {"command": "git push"}})
        return AgentResult(text=OK)
    world.turn = turn
    return rules.RuleBreach


def route_silent(world):
    def turn(n, kw):
        plant_files(world)
        raise silence.SilentStop("planner", 5)
    world.turn = turn
    return silence.SilentStop


def route_budget(world, monkeypatch):
    turns(world, plant_files, ["no json"])
    seen = []

    def check_run(*a, **k):
        seen.append(1)
        if len(seen) > 1:
            raise agents.budget.RunBudgetStop("run cap")
    monkeypatch.setattr(agents.budget, "check_run", check_run)
    return agents.budget.RunBudgetStop


def route_crash(world):
    def turn(n, kw):
        plant_files(world)
        raise RuntimeError("pi exited 137: ")
    world.turn = turn
    return RuntimeError


def route_repo_yaml(world, monkeypatch):
    real = agents.budget.check_live
    monkeypatch.setattr(agents.budget, "check_live", real, raising=True)

    def turn(n, kw):
        if n == 0:
            plant_files(world)
            write(world, repo_settings.PATH, "[]\n")
        return AgentResult(text="no json")
    world.turn = turn
    return Exception


def route_signal(exc):
    def route(world):
        def turn(n, kw):
            plant_files(world)
            raise exc
        world.turn = turn
        return type(exc)
    return route


ROUTES = {
    "invalid": route_invalid, "gate": route_gate, "rule": route_rule, "silent": route_silent,
    "budget": route_budget, "crash": route_crash, "repo_yaml": route_repo_yaml,
    "interrupt": route_signal(KeyboardInterrupt()), "exit": route_signal(SystemExit(3)),
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_every_failure_route_is_rolled_back_and_leaves_as_itself(world, monkeypatch, route):
    fn = ROUTES[route]
    expect = fn(world, monkeypatch) if route in ("budget", "repo_yaml") else fn(world)
    with pytest.raises(expect) as stopped:
        world.execute()
    if route != "repo_yaml":
        assert type(stopped.value) is expect or isinstance(stopped.value, expect)
    else:
        assert not isinstance(stopped.value, permissions.PermissionBreach)
    assert (world.repo / PERMS).read_text() == "v0\n"
    assert (world.repo / "src/app.py").read_text() == "old\n"
    assert not (world.repo / "justfile").exists()
    assert git(world.repo, "rev-parse", "HEAD") == world.base
    assert (world.repo / "specs/plan.md").read_text() == "plan\n"
    [event] = world.named("failed_call_rollback")
    assert len(event.payload["undone"]) == 1 and event.payload["failed_with"] == type(stopped.value).__name__
    for path in (PERMS, "justfile", "src/app.py"):
        assert path in event.payload["error"]
    assert len([n for n in world.notes if "SSSF undid 1 commit" in n]) == 1
    assert not world.named("permission_breach")


def test_a_failed_call_leaves_no_commit_in_the_next_phases_history(world):
    turns(world, plant_files, ["no json"])
    with pytest.raises(RuntimeError):
        world.execute()
    turns(world, lambda w: write(w, "specs/plan.md", "second\n"), [OK])
    world.execute()
    git_helper.commit_all("plan")
    assert "justfile" not in git(world.repo, "show", "--name-only", "--format=", "HEAD")
    assert not (world.repo / "justfile").exists()


def test_a_failed_call_never_touches_the_engineers_own_work(world):
    write(world, "src/app.py", "mine\n")
    write(world, PERMS, "mine too\n")
    write(world, "notes.txt", "scratch\n")
    turns(world, lambda w: write(w, "justfile", "evil:\n"), ["no json"])
    with pytest.raises(RuntimeError):
        world.execute()
    assert (world.repo / "src/app.py").read_text() == "mine\n"
    assert (world.repo / PERMS).read_text() == "mine too\n"
    assert (world.repo / "notes.txt").read_text() == "scratch\n"
    assert not (world.repo / "justfile").exists()


# ── a rollback that fails is loud ─────────────────────────────────────────────

@pytest.mark.parametrize("broken", ["enforce", "undo_commits"])
def test_a_failed_rollback_still_raises_the_original_and_says_so(world, monkeypatch, broken):
    turns(world, plant_files, ["no json"])
    boom = OSError("disk") if broken == "enforce" else permissions.PermissionBreach("HEAD moved")
    seen = []

    def fail(*a, **k):
        seen.append(broken)
        raise boom
    monkeypatch.setattr(permissions, broken, fail)
    with pytest.raises(RuntimeError) as stopped:
        world.execute()
    assert stopped.value.__cause__ is boom or isinstance(stopped.value.__cause__, type(boom))
    assert any("may still hold the agent's writes" in a for a in world.advised)
    [event] = world.named("failed_call_rollback_failed")
    assert "may still hold the agent's writes" in event.payload["error"]
    assert not world.named("failed_call_rollback")
    if broken == "undo_commits":
        assert world.calls["enforce"] == 0


def test_a_path_the_rollback_could_not_restore_is_named(world, monkeypatch):
    turns(world, plant_files, ["no json"])
    monkeypatch.setattr(permissions, "_roll_back", lambda *a, **k: "could not roll back")
    with pytest.raises(RuntimeError):
        world.execute()
    [event] = world.named("failed_call_rollback")
    assert "could not roll back" in event.payload["error"]
    assert any("could not roll back" in a for a in world.advised)


def test_no_baseline_means_no_rollback(world, monkeypatch):
    turns(world, plant_files, ["no json"])
    boom = OSError("no snapshot")

    def snapshot(run):
        raise boom
    monkeypatch.setattr(permissions, "snapshot", snapshot)
    with pytest.raises(OSError) as stopped:
        world.execute()
    assert stopped.value is boom
    assert world.calls == {"undo": 0, "enforce": 0}
    assert not world.named("failed_call_rollback") and not world.named("failed_call_rollback_failed")


# ── the success path is as it was ─────────────────────────────────────────────

def test_a_successful_call_that_planted_a_protected_file_is_one_breach(world):
    turns(world, lambda w: write(w, "justfile", "evil:\n"), [OK])
    with pytest.raises(permissions.PermissionBreach):
        world.execute()
    assert world.calls["enforce"] == 1
    assert len(world.named("permission_breach")) == 1
    assert not world.named("failed_call_rollback") and not world.notes
    assert not (world.repo / "justfile").exists()


# ── git fails closed, and no child starts in a submodule (-5) ─────────────────
# Nothing here starts a long command except the host_only param: that one never
# runs under agentsh, which refuses the command by argv_truncated.

def stops(world, ok: bool, plant, *, text: str = "") -> BaseException:
    """Run `execute` with `plant` as the agent's turn, on the success path (ok) or
    the failure path, and return what it raised: a PermissionBreach, or the
    original (a RuntimeError that is not one)."""
    turns(world, plant, [OK] if ok else ["no json"])
    with pytest.raises(permissions.PermissionBreach if ok else RuntimeError) as caught:
        world.execute()
    assert ok or not isinstance(caught.value, permissions.PermissionBreach)
    return caught.value


def long_names(path: Path, length: int, count: int) -> None:
    with path.open("a") as out:
        for i in range(count):
            out.write(f'[filter "{str(i).ljust(length, "n")}"]\n\tclean = cat\n')


def nested_sub(world) -> Path:
    sub = world.repo / "sub"
    sub.mkdir()
    git(sub, "init", "-qb", "main")
    (sub / "f").write_text("f\n")
    git(sub, "add", "-A")
    git(sub, "commit", "-qm", "sub")
    git(world.repo, "add", "sub")
    return sub


def bypass_plant(length: int, count: int, commit: bool = False):
    def plant(world):
        sub = nested_sub(world)
        write(world, PERMS, "evil\n")
        write(world, "justfile", "evil:\n")
        if commit:
            git(world.repo, "add", "-A")
            git(world.repo, "commit", "-qm", "agent")
        long_names(sub / ".git" / "config", length, count)
    return plant


def assert_bypass_closed(world):
    assert (world.repo / PERMS).read_text() == "v0\n"
    assert not (world.repo / "justfile").exists()
    assert git(world.repo, "rev-parse", "HEAD") == world.base
    assert "sub" not in git(world.repo, "ls-files", "-s").split()
    assert (world.repo / "sub" / "f").exists()


SIZES = [(40_000, 1), (200, 300),
         # host_only: passes only outside agentsh, which refuses the command by argv_truncated
         pytest.param(2_000, 1_000, marks=pytest.mark.host_only)]


@pytest.mark.parametrize("ok", [True, False])
@pytest.mark.parametrize("length,count", SIZES)
def test_a_long_named_filter_in_a_submodule_cant_hide_the_writes(world, ok, length, count):
    stops(world, ok, bypass_plant(length, count))
    assert_bypass_closed(world)


@pytest.mark.parametrize("ok", [True, False])
@pytest.mark.parametrize("length,count", SIZES[:2])
def test_a_long_named_filter_cant_hide_the_agents_commit(world, ok, length, count):
    stops(world, ok, bypass_plant(length, count, commit=True))
    assert_bypass_closed(world)


@pytest.mark.parametrize("length,count", SIZES[:2])
def test_a_planted_submodule_persisting_into_a_second_call_is_still_closed(world, length, count):
    def plant_only(w):
        long_names(nested_sub(w) / ".git" / "config", length, count)
        write(w, "src/app.py", "evil\n")
    stops(world, True, plant_only)
    stops(world, True, lambda w: write(w, "justfile", "evil:\n"))
    assert not (world.repo / "justfile").exists()


def fail_git(world, monkeypatch, how: str, armed) -> str:
    import errno
    box = permissions.sandbox.current()
    real = box.run

    def run(argv, **kw):
        if armed() and "diff" in argv and "git" in argv:
            if how == "oserror":
                raise OSError(errno.E2BIG, "Argument list too long")
            return subprocess.CompletedProcess(argv, 128, b"", b"boom")
        return real(argv, **kw)
    monkeypatch.setattr(box, "run", run)
    return "boom" if how == "exit" else "Argument list too long"


@pytest.mark.parametrize("how", ["exit", "oserror"])
def test_a_failing_git_after_a_successful_call_is_a_breach(world, monkeypatch, how):
    said = fail_git(world, monkeypatch, how, lambda: world.n > 0)
    caught = stops(world, True, lambda w: write(w, "justfile", "evil:\n"))
    assert "diff" in str(caught) and said in str(caught)


@pytest.mark.parametrize("how", ["exit", "oserror"])
def test_a_failing_git_after_a_failed_call_raises_the_original_and_says_so(world, monkeypatch, how):
    fail_git(world, monkeypatch, how, lambda: world.n > 0)
    stops(world, False, lambda w: write(w, "justfile", "evil:\n"))
    assert any("may still hold the agent's writes" in a for a in world.advised)
    [event] = world.named("failed_call_rollback_failed")
    assert "may still hold the agent's writes" in event.payload["error"]


@pytest.mark.parametrize("how", ["exit", "oserror"])
def test_a_failing_baseline_stops_before_the_agent(world, monkeypatch, how):
    fail_git(world, monkeypatch, how, lambda: True)
    turns(world, lambda w: write(w, "justfile", "evil:\n"), [OK])
    with pytest.raises(permissions.PermissionBreach, match="diff"):
        world.execute()
    assert world.n == 0 and not (world.repo / "justfile").exists()


@pytest.mark.parametrize("ok", [True, False])
def test_a_corrupted_index_is_caught_on_both_paths(world, ok):
    def plant(w):
        write(w, "justfile", "evil:\n")
        (w.repo / ".git" / "index").write_bytes(b"garbage")
    caught = stops(world, ok, plant)
    if ok:
        assert "diff" in str(caught)
    else:
        assert any("may still hold the agent's writes" in a for a in world.advised)


@pytest.mark.parametrize("ok", [True, False])
def test_a_marker_submodule_runs_nothing_and_the_gitlink_rolls_back(world, ok):
    def plant(w):
        sub = nested_sub(w)
        write(w, "justfile", "evil:\n")
        (sub / ".gitattributes").write_text("* filter=m\n")
        script = w.tmp / "mon.sh"
        script.write_text(f"#!/bin/sh\ntouch {w.marker}\n")
        script.chmod(0o755)
        with (sub / ".git" / "config").open("a") as out:
            out.write(f'[filter "m"]\n\tclean = touch {w.marker}; cat\n\tsmudge = touch {w.marker}; cat\n'
                      f'\tprocess = touch {w.marker}\n[core]\n\tfsmonitor = {script}\n')
        (sub / "f").write_text("f\n")
    stops(world, ok, plant)
    assert not world.marker.exists()
    assert not (world.repo / "justfile").exists()
    assert "sub" not in git(world.repo, "ls-files", "-s").split()


@pytest.mark.parametrize("ok", [True, False])
def test_an_agent_that_deletes_its_branch_ref_never_returns_normally(world, ok):
    def plant(w):
        write(w, "justfile", "evil:\n")
        git(w.repo, "add", "-A")
        git(w.repo, "commit", "-qm", "agent")
        git(w.repo, "update-ref", "-d", "refs/heads/main")
    caught = stops(world, ok, plant)
    if ok:
        assert "merge-base" in str(caught) and world.named("permission_breach")
    else:
        assert any("could not" in a or "may still hold" in a for a in world.advised)


@pytest.mark.parametrize("ok", [True, False])
def test_an_agent_that_checks_out_an_orphan_branch_stops_the_run(world, ok):
    def plant(w):
        write(w, "justfile", "evil:\n")
        git(w.repo, "checkout", "-q", "--orphan", "other")
        git(w.repo, "add", "-A")
        git(w.repo, "commit", "-qm", "agent")
    caught = stops(world, ok, plant)
    if ok:
        assert "switched HEAD from refs/heads/main to refs/heads/other" in str(caught)
        assert world.named("permission_breach")


NO_COMMIT_MESSAGE = (
    "HEAD has no commit (a new repo with no commit, or an orphan or unborn branch), so SSSF "
    "has no HEAD to snapshot the tree against and can't guard an agent's writes. SSSF runs "
    "no agent there. Make a first commit, with `git commit --allow-empty -m init` or a real "
    "one (or switch to a branch that has commits), then run again.")


def unborn_world(world, state: str) -> None:
    """The world's repo, with history, put on a HEAD that has no commit. The
    process stays in the first repo: the agents' prompt files are read from the
    working directory, and an orphan worktree is empty."""
    if state == "orphan_checkout":
        git(world.repo, "checkout", "-q", "--orphan", "fresh")
    elif state == "missing_branch":
        git(world.repo, "symbolic-ref", "HEAD", "refs/heads/nope")
    else:
        wt = world.tmp / "wt"
        git(world.repo, "worktree", "add", "-q", "--orphan", "-b", "lone", str(wt))
        world.first = world.repo
        world.repo = wt
        world.run.repo_root = wt


def work_files(repo: Path) -> dict:
    return {str(p.relative_to(repo)): p.read_bytes() for p in sorted(repo.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(repo).parts}


@pytest.mark.parametrize("state", ["orphan_checkout", "missing_branch", "orphan_worktree"])
def test_an_agent_is_refused_on_an_unborn_branch_in_a_repo_with_history(world, state):
    world.first = world.repo
    unborn_world(world, state)
    repo = world.repo
    files = work_files(repo)
    index = repo / git(repo, "rev-parse", "--git-path", "index")
    index_bytes = index.read_bytes() if index.exists() else None
    turns(world, lambda w: write(w, "specs/plan.md", "plan\n"), [OK])
    with pytest.raises(permissions.GitFailed) as caught:
        world.execute()
    assert str(caught.value) == NO_COMMIT_MESSAGE
    assert world.n == 0                                         # no agent started
    assert work_files(repo) == files
    assert not (repo / "specs" / "plan.md").exists()
    assert (index.read_bytes() if index.exists() else None) == index_bytes
    assert world.calls == {"undo": 0, "enforce": 0}
    assert git(world.first, "rev-parse", "refs/heads/main") == world.base
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=repo,
                          capture_output=True).returncode == 1


def no_commit_world(world, setup: str) -> None:
    """The world's repo with its history taken out: a tree and index, no commit."""
    if setup == "mine_staged":
        write(world, "mine.txt", "mine\n")
        git(world.repo, "add", "mine.txt")
    elif setup == "ignored":
        write(world, ".gitignore", "local.db\ndata/\nadws/adw_data/sessions/\n")
        write(world, "local.db", "mine\n")
        write(world, "data/x", "mine\n")
    elif setup == "foo_staged":
        write(world, "foo/x", "mine\n")
        git(world.repo, "add", "foo/x")
    git(world.repo, "update-ref", "-d", "refs/heads/main")


@pytest.mark.parametrize("setup", ["empty", "mine_staged", "ignored", "foo_staged"])
def test_an_agent_is_refused_in_a_repo_with_no_commit(world, setup):
    no_commit_world(world, setup)
    snap = {str(p.relative_to(world.repo)): p.read_bytes() for p in sorted(world.repo.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(world.repo).parts}
    index = (world.repo / ".git" / "index").read_bytes()
    turns(world, lambda w: write(w, "justfile", "evil:\n"), [OK])
    with pytest.raises(permissions.GitFailed, match=r"first commit.*git commit --allow-empty -m init") as caught:
        world.execute()
    if setup == "empty":
        assert str(caught.value) == NO_COMMIT_MESSAGE
    assert world.n == 0                                         # no agent started
    assert {str(p.relative_to(world.repo)): p.read_bytes() for p in sorted(world.repo.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(world.repo).parts} == snap
    assert (world.repo / ".git" / "index").read_bytes() == index
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=world.repo,
                          capture_output=True).returncode == 1
    assert world.calls == {"undo": 0, "enforce": 0}


def test_a_clean_successful_call_emits_no_rollback_and_no_breach(world):
    turns(world, lambda w: write(w, "specs/plan.md", "plan\n"), [OK])
    world.execute()
    assert not world.named("permission_breach") and not world.named("failed_call_rollback")
    assert not world.advised and not world.notes


# ── no git while the git directory isn't known to be back ────────────────────

def test_a_planted_filter_in_config_never_runs_on_the_rollback(world):
    """Was a control that the filter bites when nothing holds git back. SSSF's
    git now turns every configured filter off, so it runs nothing and still rolls back."""
    turns(world, plant_filter, ["no json"])
    with pytest.raises(RuntimeError):
        world.execute()
    assert not world.marker.exists()
    assert not (world.repo / "justfile").exists()


def controlled(world, monkeypatch, steps):
    """A restore that follows the real contract: a done guard returns [], and
    otherwise it marks `done` and runs the next step."""
    guard = {"done": False}
    monkeypatch.setattr(permissions, "guard_git_dir", lambda run: guard)
    monkeypatch.setattr(permissions, "git_dir_changed", lambda g: False)
    queue = list(steps)
    seen = []

    def restore(run, g, *, breach=True):
        seen.append(breach)
        if not g or g["done"]:
            return []
        g["done"] = True
        return queue.pop(0)(g)
    monkeypatch.setattr(permissions, "restore_git_dir", restore)
    return guard


def interrupt(g):
    raise KeyboardInterrupt()


def interrupt_after_breach(g):
    g["restored"] = False
    raise KeyboardInterrupt()


def crash(g):
    raise OSError("tar failed")


def no_git_ran(world):
    assert not world.marker.exists()
    assert world.calls == {"undo": 0, "enforce": 0}
    assert (world.repo / "justfile").exists()


@pytest.mark.parametrize("point", ["mid_restore", "after_breach"])
def test_an_interrupt_in_the_git_directory_check_runs_no_git(world, monkeypatch, point):
    controlled(world, monkeypatch, [interrupt if point == "mid_restore" else interrupt_after_breach])
    turns(world, plant_filter, [OK])
    with pytest.raises(KeyboardInterrupt):
        world.execute()
    no_git_ran(world)
    assert any("may still hold the agent's writes" in a for a in world.advised)
    [event] = world.named("failed_call_rollback_failed")
    assert "may still hold the agent's writes" in event.payload["error"]
    assert not world.named("failed_call_rollback")


def test_a_crashed_check_then_an_interrupt_in_the_undo_never_reaches_the_undo(world, monkeypatch):
    controlled(world, monkeypatch, [crash])

    def undo(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(permissions, "undo_commits", undo)
    turns(world, plant_filter, [OK])
    with pytest.raises(permissions.PermissionBreach, match="nothing was rolled back"):
        world.execute()
    assert not world.marker.exists() and world.calls["enforce"] == 0
    assert len(world.named("permission_breach")) == 1
    assert any("may still hold the agent's writes" in a for a in world.advised)


def test_an_interrupt_before_the_check_owns_the_guard_still_rolls_back(world, monkeypatch):
    guard = controlled(world, monkeypatch, [])
    breaches = []

    def restore(run, g, *, breach=True):
        breaches.append(breach)
        if len(breaches) == 1:          # the success path's check, before it marks `done`
            raise KeyboardInterrupt()
        g["done"], g["restored"] = True, True     # the failure path's own restore
        return []
    monkeypatch.setattr(permissions, "restore_git_dir", restore)
    turns(world, plant_files, [OK])
    with pytest.raises(KeyboardInterrupt):
        world.execute()
    assert not (world.repo / "justfile").exists()
    assert len(world.named("failed_call_rollback")) == 1
    assert not world.named("failed_call_rollback_failed")


@pytest.mark.parametrize("fails", ["not_put_back", "crash"])
def test_a_success_whose_git_directory_could_not_be_put_back_runs_no_git(world, monkeypatch, fails):
    def not_put_back(g):
        g["restored"] = False
        raise permissions.PermissionBreach("2 path(s), NOT all put back:\n  - .git/config")
    controlled(world, monkeypatch, [not_put_back if fails == "not_put_back" else crash])
    turns(world, plant_filter, [OK])
    with pytest.raises(permissions.PermissionBreach, match="nothing was rolled back"):
        world.execute()
    no_git_ran(world)
    assert len(world.named("permission_breach")) == 1
    assert any("may still hold the agent's writes" in a for a in world.advised)
    assert not world.named("failed_call_rollback") and not world.named("failed_call_rollback_failed")


def test_a_git_directory_that_was_put_back_still_gets_the_undo_and_the_rollback(world, monkeypatch):
    def put_back(g):
        g["restored"] = True
        raise permissions.PermissionBreach("1 path(s), put back as they were:\n  - .git/config")
    controlled(world, monkeypatch, [put_back])
    turns(world, lambda w: write(w, "justfile", "evil:\n"), [OK])
    with pytest.raises(permissions.PermissionBreach) as stopped:
        world.execute()
    assert world.calls == {"undo": 1, "enforce": 1}
    assert not (world.repo / "justfile").exists()
    assert "put back as they were" in str(stopped.value) and "justfile" in str(stopped.value)


def test_a_clean_git_directory_check_changes_nothing(world, monkeypatch):
    def clean(g):
        g["restored"] = True
        return []
    controlled(world, monkeypatch, [clean])
    turns(world, lambda w: write(w, "specs/plan.md", "plan\n"), [OK])
    world.execute()
    assert not world.advised and not world.named("permission_breach")


def test_git_dir_back_reads_the_guard():
    back = agents._git_dir_back
    assert back(None) is True
    assert back({"done": False}) is False and back({"done": True}) is False
    assert back({"done": True, "restored": False}) is False
    assert back({"done": True, "restored": True}) is True


# ── the real git-directory guard ──────────────────────────────────────────────

@pytest.fixture
def real_guard(world, monkeypatch):
    """The real guard and a home of its own; the agent's chmod is undone at the end."""
    home = world.tmp / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = t\n\temail = t@example.com\n")
    monkeypatch.setenv("HOME", str(home))
    for name, real in REAL.items():
        monkeypatch.setattr(permissions, name, real)
    yield world
    os.chmod(world.repo / ".git", 0o755)


def plant_and_lock(world):
    plant_filter(world)
    os.chmod(world.repo / ".git", 0o555)


def test_real_guard_success_with_an_unrestorable_git_directory_runs_no_filter(real_guard):
    turns(real_guard, plant_and_lock, [OK])
    with pytest.raises(permissions.PermissionBreach) as stopped:
        real_guard.execute()
    assert "NOT all put back" in str(stopped.value) and "nothing was rolled back" in str(stopped.value)
    assert not real_guard.marker.exists()


def test_real_guard_failure_with_an_unrestorable_git_directory_runs_no_filter(real_guard):
    turns(real_guard, plant_and_lock, ["no json"])
    with pytest.raises(RuntimeError):
        real_guard.execute()
    assert not real_guard.marker.exists()
    assert real_guard.named("failed_call_rollback_failed")


def test_real_guard_interrupt_in_place_of_the_breach_runs_no_filter(real_guard, monkeypatch):
    real = permissions.restore_git_dir

    def restore(run, guard, *, breach=True):
        try:
            return real(run, guard, breach=breach)
        except permissions.PermissionBreach:
            raise KeyboardInterrupt() from None
    monkeypatch.setattr(permissions, "restore_git_dir", restore)
    turns(real_guard, plant_and_lock, [OK])
    with pytest.raises(KeyboardInterrupt):
        real_guard.execute()
    assert not real_guard.marker.exists()
    assert any("may still hold the agent's writes" in a for a in real_guard.advised)


def test_real_restore_records_whether_the_directory_is_back(real_guard):
    run = real_guard.run
    guard = permissions.guard_git_dir(run)
    permissions.restore_git_dir(run, guard)
    assert guard["restored"] is True

    guard = permissions.guard_git_dir(run)
    config = real_guard.repo / ".git" / "config"
    config.write_text(config.read_text() + "[x]\n\ty = 1\n")
    permissions.restore_git_dir(run, guard, breach=False)
    assert guard["restored"] is True and "[x]" not in config.read_text()

    guard = permissions.guard_git_dir(run)
    config.write_text(config.read_text() + "[x]\n\ty = 1\n")
    os.chmod(real_guard.repo / ".git", 0o555)
    with pytest.raises(permissions.PermissionBreach, match="NOT all put back"):
        permissions.restore_git_dir(run, guard, breach=False)
    assert guard["restored"] is False


# ── a submodule's files are seen, and nothing under it is rolled back (-8) ─────

def vendor_lib(world) -> Path:
    """A committed submodule at vendor/lib, as the engineer would have it."""
    src = world.tmp / "libsrc"
    src.mkdir()
    git(src, "init", "-qb", "main")
    (src / "file.txt").write_text("lib\n")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "lib")
    git(world.repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(src),
        "vendor/lib")
    git(world.repo, "commit", "-qm", "add lib")
    return world.repo / "vendor" / "lib"


def edit_lib(world) -> None:
    (world.repo / "vendor" / "lib" / "file.txt").write_text("evil\n")


def said(world) -> str:
    return "\n".join(world.advised)


@pytest.mark.parametrize("ok", [True, False])
def test_an_edit_inside_a_submodule_stops_the_call_and_is_not_called_rolled_back(world, ok):
    vendor_lib(world)
    caught = stops(world, ok, edit_lib)
    text = str(caught) if ok else said(world)
    assert "vendor/lib — NOT rolled back: " + permissions.IN_SUBMODULE in text
    assert "`git -C vendor/lib status` and `git -C vendor/lib diff`" in text
    assert "vendor/lib — rolled back" not in text
    assert (world.repo / "vendor" / "lib" / "file.txt").read_text() == "evil\n"
    if not ok:
        note = next(n for n in world.notes if "call failed" in n)
        assert "rolled back" not in note and "vendor/lib" in note
        assert "The tree may still hold the agent's writes." in note


@pytest.mark.parametrize("ok", [True, False])
def test_a_staged_gitlink_edit_is_named_too(world, ok):
    vendor_lib(world)

    def plant(w):
        edit_lib(w)
        git(w.repo, "add", "-A")
    caught = stops(world, ok, plant)
    assert "vendor/lib — NOT rolled back" in (str(caught) if ok else said(world))


def test_a_gitlink_already_changed_before_the_call_gets_the_second_message(world):
    lib = vendor_lib(world)
    git(lib, "commit", "-q", "--allow-empty", "-m", "engineer moved the gitlink")   # kind M,sub
    caught = stops(world, True, lambda w: (w.repo / "vendor" / "lib" / "file.txt").write_text("again\n"))
    assert "left as-is (it was already changed before the call" in str(caught)
    assert permissions.IN_SUBMODULE in str(caught)


def test_a_file_a_submodule_check_cant_read_counts_as_changed(world):
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    lib = vendor_lib(world)
    (lib / "file.txt").chmod(0)
    try:
        caught = stops(world, True, lambda w: None)
    finally:
        (lib / "file.txt").chmod(0o644)
    assert "SSSF couldn't read file.txt under it, so it counts as changed." in str(caught)


def test_a_builder_may_edit_a_path_outside_protected_files(world):
    vendor_lib(world)
    world.phase.params.owner = "builder"
    turns(world, edit_lib, [OK])
    world.execute()
    assert (world.repo / "vendor" / "lib" / "file.txt").read_text() == "evil\n"


def test_a_box_without_python3_runs_no_agent_in_a_repo_with_a_submodule(world):
    vendor_lib(world)
    real = permissions.sandbox.current()

    class NoPython:
        def __getattr__(self, name):
            return getattr(real, name)

        def run(self, argv, *a, **k):
            if argv[:1] == ["python3"]:
                raise FileNotFoundError("python3")
            return real.run(argv, *a, **k)
    status = git(world.repo, "status", "--porcelain")
    permissions.sandbox.use(NoPython())
    try:
        turns(world, edit_lib, [OK])
        with pytest.raises(permissions.GitFailed, match="submodule digest command failed"):
            world.execute()
    finally:
        permissions.sandbox.use(real)
    assert world.n == 0
    assert git(world.repo, "status", "--porcelain") == status


# ── a flagged dirty file is still in the snapshot ──────────────────────────────

def test_an_engineers_flagged_dirty_file_the_agent_leaves_alone_is_no_breach(world):
    write(world, "src/app.py", "mine\n")        # the engineer's dirty file
    git(world.repo, "update-index", "--assume-unchanged", "src/app.py")
    # The agent does nothing: the path leaves the diff only because git is told
    # to stop reporting it, which is a flag, so it stays in the snapshot.
    turns(world, lambda w: None, [OK])
    world.execute()


def test_an_engineers_dirty_file_the_agent_restored_is_reported_as_lost(world):
    write(world, "src/app.py", "mine\n")
    turns(world, lambda w: git(w.repo, "checkout", "--", "src/app.py"), [OK])
    # planner's writes don't cover src/app.py, so the path is a breach
    with pytest.raises(permissions.PermissionBreach, match="REVERTED-BY-AGENT"):
        world.execute()


def test_an_engineers_dirty_file_the_agent_took_out_of_the_index_is_still_a_breach(world):
    write(world, "src/app.py", "mine\n")
    turns(world, lambda w: git(w.repo, "rm", "-q", "--cached", "src/app.py"), [OK])
    with pytest.raises(permissions.PermissionBreach, match="src/app.py"):
        world.execute()


# ── a config folder moved and linked back, with the same bytes (-8) ────────────

def xdg_home(world) -> Path:
    cfg = world.tmp / "home" / ".config" / "git"
    cfg.mkdir(parents=True)
    (cfg / "config").write_text("[core]\n\tabbrev = 9\n")
    return world.tmp / "home"


def move_and_link(home: Path, into: Path) -> None:
    into.parent.mkdir(parents=True, exist_ok=True)
    os.rename(home / ".config", into)
    os.symlink(into, home / ".config")


@pytest.mark.parametrize("ok", [True, False])
def test_a_moved_config_folder_with_the_same_bytes_still_gets_the_undo_and_rollback(real_guard, ok):
    home = xdg_home(real_guard)

    def plant(w):
        plant_files(w)
        move_and_link(home, w.tmp / "elsewhere")
    turns(real_guard, plant, [OK] if ok else ["no json"])
    with pytest.raises(permissions.PermissionBreach if ok else RuntimeError) as stopped:
        real_guard.execute()
    assert git(real_guard.repo, "rev-parse", "HEAD") == real_guard.base
    assert (real_guard.repo / PERMS).read_text() == "v0\n"
    assert not (real_guard.repo / "justfile").exists()
    assert (real_guard.repo / "specs" / "plan.md").exists()
    if ok:
        assert ".config" in str(stopped.value) and "NOT all put back" in str(stopped.value)


def test_a_moved_config_folder_now_inside_the_work_tree_runs_no_git(real_guard):
    home = xdg_home(real_guard)

    def plant(w):
        plant_files(w)
        move_and_link(home, w.repo / "specs" / "cfg")
    turns(real_guard, plant, [OK])
    with pytest.raises(permissions.PermissionBreach):
        real_guard.execute()
    assert git(real_guard.repo, "rev-parse", "HEAD") != real_guard.base       # no undo ran


def test_a_moved_config_folder_whose_bytes_changed_runs_no_git(real_guard):
    home = xdg_home(real_guard)

    def plant(w):
        plant_files(w)
        move_and_link(home, w.tmp / "elsewhere")
        (w.tmp / "elsewhere" / "git" / "config").write_text("[core]\n\tabbrev = 4\n")
    turns(real_guard, plant, [OK])
    with pytest.raises(permissions.PermissionBreach):
        real_guard.execute()
    assert git(real_guard.repo, "rev-parse", "HEAD") != real_guard.base


# ── a path that left the diff is always reported ───────────────────────────────

def leaves_the_diff(world, monkeypatch):
    """The engineer's dirty src/app.py, and a snapshot after the call that no
    longer lists it (what a forged index stat does)."""
    write(world, "src/app.py", "mine\n")
    real = permissions.snapshot
    calls = []

    def snap(run, *rest):
        calls.append(1)
        out = real(run, *rest)
        if len(calls) > 1:
            out.pop("src/app.py", None)
        return out
    monkeypatch.setattr(permissions, "snapshot", snap)
    turns(world, lambda w: None, [OK])


def test_a_dirty_file_with_no_filter_that_leaves_the_diff_is_still_reported(world, monkeypatch):
    leaves_the_diff(world, monkeypatch)
    with pytest.raises(permissions.PermissionBreach, match="REVERTED-BY-AGENT"):
        world.execute()


def test_the_agents_own_gitattributes_doesnt_explain_a_path_leaving_the_diff(world, monkeypatch):
    leaves_the_diff(world, monkeypatch)
    turns(world, lambda w: write(w, ".gitattributes", "* filter=x\n"), [OK])
    with pytest.raises(permissions.PermissionBreach):
        world.execute()


def test_a_dirty_file_under_a_defined_filter_that_leaves_the_diff_is_reported(world, monkeypatch):
    write(world, ".gitattributes", "src/app.py filter=x\n")
    git(world.repo, "add", ".gitattributes")
    git(world.repo, "commit", "-qm", "attrs")
    git(world.repo, "config", "filter.x.clean", "cat")
    git(world.repo, "config", "filter.x.smudge", "cat")
    leaves_the_diff(world, monkeypatch)
    with pytest.raises(permissions.PermissionBreach, match="REVERTED-BY-AGENT"):
        world.execute()


def test_a_filter_attribute_naming_no_defined_filter_explains_nothing(world, monkeypatch):
    write(world, ".gitattributes", "src/app.py filter=x\n")
    git(world.repo, "add", ".gitattributes")
    git(world.repo, "commit", "-qm", "attrs")
    leaves_the_diff(world, monkeypatch)
    with pytest.raises(permissions.PermissionBreach, match="REVERTED-BY-AGENT"):
        world.execute()


def forge_index(repo: Path, rel: str) -> None:
    """What an agent can do with the permissions it has: set the file's mtime
    100 s back, then rewrite that index entry's ctime, mtime, dev, ino and size to
    the file's lstat, leaving the blob, and fix the checksum. Git then holds the
    engineer's modified file as unchanged by its stat data. Ported from the
    round-3 reviewer's forge_index.py."""
    import hashlib
    import struct
    import time
    target = repo / rel
    old = time.time() - 100
    os.utime(target, (old, old), follow_symlinks=False)
    index = repo / ".git" / "index"
    data = bytearray(index.read_bytes())
    assert data[:4] == b"DIRC"
    version, count = struct.unpack(">II", data[4:12])
    assert version in (2, 3), version
    st = os.lstat(target)
    pos, found = 12, False
    for _ in range(count):
        start = pos
        flags = struct.unpack(">H", data[start + 60:start + 62])[0]
        extended = version == 3 and flags & 0x4000
        name_at = start + 62 + (2 if extended else 0)
        end = data.index(b"\0", name_at)
        name = bytes(data[name_at:end]).decode()
        pos = start + (((end - start) + 8) // 8) * 8
        if name == rel:
            m = 0xFFFFFFFF
            struct.pack_into(">6I", data, start, int(st.st_ctime) & m, st.st_ctime_ns % 10**9,
                             int(st.st_mtime) & m, st.st_mtime_ns % 10**9,
                             st.st_dev & m, st.st_ino & m)
            struct.pack_into(">I", data, start + 36, st.st_size & m)
            found = True
    assert found, rel
    data[-20:] = hashlib.sha1(bytes(data[:-20])).digest()
    tmp = index.with_name("index.forge")
    tmp.write_bytes(bytes(data))
    os.replace(tmp, index)


@pytest.mark.parametrize("filter_kind", ["none", "defined", "undefined"])
def test_a_forged_index_stops_at_the_first_call_with_the_engineers_file_intact(world, filter_kind):
    if filter_kind != "none":
        write(world, ".gitattributes", "src/app.py filter=x\n")
        git(world.repo, "add", ".gitattributes")
        git(world.repo, "commit", "-qm", "attrs")
    if filter_kind == "defined":
        git(world.repo, "config", "filter.x.clean", "cat")
        git(world.repo, "config", "filter.x.smudge", "cat")
    write(world, "src/app.py", "mine\n")
    ran = []

    def turn(n, kw):
        if n == 0:
            forge_index(world.repo, "src/app.py")
        else:
            ran.append(n)
            os.utime(world.repo / "src/app.py", (1, 1))
        return AgentResult(text=OK, cost=0.1)
    world.turn = turn
    with pytest.raises(permissions.PermissionBreach, match="src/app.py — REVERTED-BY-AGENT"):
        world.execute()
    assert (world.repo / "src/app.py").read_text() == "mine\n"
    assert ran == []


def test_git_status_under_a_byte_rewriting_filter_stops_the_run_the_decided_exception(world):
    """Brief -9's decided exception: a filtered file the index holds as stat-dirty
    leaves the diff when the agent only runs `git status`, and the run stops
    though nothing was lost. `main` passes this; the engineer accepted the stop
    because the only fix found let a forged index through."""
    write(world, ".gitattributes", "doc.txt filter=up\n")
    git(world.repo, "config", "filter.up.clean", "tr a-z A-Z")
    git(world.repo, "config", "filter.up.smudge", "tr A-Z a-z")
    write(world, "doc.txt", "hello\n")
    git(world.repo, "add", "-A")
    git(world.repo, "commit", "-qm", "doc")
    os.utime(world.repo / "doc.txt", (1000, 1000))
    turns(world, lambda w: git(w.repo, "status"), [OK])
    with pytest.raises(permissions.PermissionBreach, match="doc.txt — REVERTED-BY-AGENT"):
        world.execute()
    assert (world.repo / "doc.txt").read_text() == "hello\n"


@pytest.mark.parametrize("already_changed", [False, True])
def test_the_submodule_stop_names_the_gitfile_before_any_git_command(world, already_changed):
    lib = vendor_lib(world)
    if already_changed:
        git(lib, "commit", "-q", "--allow-empty", "-m", "engineer moved the gitlink")
    caught = stops(world, True, edit_lib)
    line = next(l for l in str(caught).splitlines() if "vendor/lib —" in l)
    first_git = line.index("git -C")
    assert 0 <= line.index("vendor/lib/.git") < first_git
    assert 0 <= line.index("cat vendor/lib/.git") < first_git
    assert ".git/modules/<name>" in line


# ── no object read main doesn't, old-gitlink digest, a true note (-10) ─────────

def deep_commit(world) -> None:
    write(world, "deep/a/b.txt", "x\n")
    git(world.repo, "add", "-A")
    git(world.repo, "commit", "-qm", "deep")


def delete_tree_object(world) -> None:
    """A protected write, then the loose object of a folder the agent never touched."""
    write(world, PERMS, "evil\n")
    oid = git(world.repo, "rev-parse", "HEAD:deep/a")
    loose = world.repo / ".git" / "objects" / oid[:2] / oid[2:]
    os.chmod(loose, 0o644)
    os.remove(loose)


def test_a_deleted_tree_object_of_an_untouched_folder_still_rolls_back(world):
    deep_commit(world)
    caught = stops(world, True, delete_tree_object)
    assert not isinstance(caught, permissions.GitFailed)
    assert f"{PERMS} — rolled back" in str(caught)
    assert (world.repo / PERMS).read_text() == "v0\n"


def test_a_deleted_tree_object_on_a_failed_call_is_rolled_back_or_said(world):
    deep_commit(world)
    stops(world, False, delete_tree_object)
    back = (world.repo / PERMS).read_text() == "v0\n"
    assert back or "The tree may still hold the agent's writes" in said(world)
    # Pinned to what the build shows: the write is rolled back, never silently kept.
    assert back


def test_snapshot_runs_no_ls_tree():
    import inspect
    assert "ls-tree" not in inspect.getsource(permissions.snapshot)


def leave_the_index(world, how: str) -> None:
    repo = world.repo
    if how == "file":
        git(repo, "rm", "-q", "--cached", "vendor/lib")
        shutil.rmtree(repo / "vendor" / "lib")
        write(world, "vendor/lib", "a file\n")
        git(repo, "add", "vendor/lib")
        return
    git(repo, "rm", "-q", "--cached", "vendor/lib")
    if how == "ignore_all":
        git(repo, "config", "submodule.vendor/lib.ignore", "all")


@pytest.mark.parametrize("how", ["rm_cached", "ignore_all", "file"])
def test_a_gitlink_that_left_the_index_is_still_listed_from_the_diff(world, how):
    vendor_lib(world)
    leave_the_index(world, how)
    if how == "file":
        caught = stops(world, True, lambda w: write(w, "vendor/lib", "evil\n"))
    else:
        caught = stops(world, True, edit_lib)
    assert "vendor/lib" in str(caught)


def broken_python3(world, monkeypatch) -> Path:
    bin_dir = world.tmp / "nopy"
    bin_dir.mkdir()
    script = bin_dir / "python3"
    script.write_text(f"#!/bin/sh\ntouch {world.marker}\nexit 127\n")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def new_gitlink(w) -> None:
    write(w, PERMS, "evil\n")
    nested = w.repo / "tools" / "x"
    nested.mkdir(parents=True)
    git(nested, "init", "-q")
    (nested / "a").write_text("a\n")
    git(nested, "add", "a")
    git(nested, "commit", "-qm", "n")
    git(w.repo, "add", "tools/x")


@pytest.mark.parametrize("ok", [True, False])
def test_a_new_gitlink_rolls_back_without_python3(world, monkeypatch, ok):
    broken_python3(world, monkeypatch)
    caught = stops(world, ok, new_gitlink)
    text = str(caught) if ok else said(world)
    assert not isinstance(caught, permissions.GitFailed)
    assert "digest command failed" not in text and "digest command failed" not in said(world)
    assert (world.repo / PERMS).read_text() == "v0\n"
    assert any(line.strip().startswith("- tools/x — unstaged") for line in text.splitlines())
    assert not world.marker.exists()


def test_an_old_gitlink_still_stops_the_run_when_python3_cant_start(world, monkeypatch):
    vendor_lib(world)
    broken_python3(world, monkeypatch)
    turns(world, edit_lib, [OK])
    with pytest.raises(permissions.GitFailed):
        world.execute()


def test_only_the_gitlinks_from_before_the_call_are_digested(world):
    vendor_lib(world)
    real = permissions.sandbox.current()
    digests: list[list[str]] = []

    class Recording:
        def __getattr__(self, name):
            return getattr(real, name)

        def run(self, argv, *a, **k):
            if argv[:1] == ["python3"]:
                digests.append(list(argv))
            return real.run(argv, *a, **k)
    permissions.sandbox.use(Recording())
    try:
        turns(world, new_gitlink, [OK])
        with pytest.raises(permissions.PermissionBreach):
            world.execute()
    finally:
        permissions.sandbox.use(real)
    assert len(digests) == 2
    after = digests[-1]
    assert "vendor/lib" in after and "tools/x" not in after


def fail_note(world) -> str:
    return next(n for n in world.notes if "call failed" in n)


def assert_unfinished_note(world, named: str) -> None:
    note = fail_note(world)
    assert "rolled back" not in note
    assert "The tree may still hold the agent's writes." in note
    assert named in note


def test_a_failed_call_that_could_not_delete_says_so_not_rolled_back(world):
    if os.geteuid() == 0:
        pytest.skip("root deletes in a read-only folder")

    def plant(w):
        write(w, "locked/f.py", "evil\n")
        os.chmod(w.repo / "locked", 0o555)
    try:
        stops(world, False, plant)
    finally:
        os.chmod(world.repo / "locked", 0o755)
    assert_unfinished_note(world, "locked/f.py")
    assert "could not delete" in said(world)


def test_a_failed_call_that_reverted_the_engineers_edit_says_so(world):
    write(world, "src/app.py", "mine\n")
    stops(world, False, lambda w: git(w.repo, "checkout", "-q", "--", "src/app.py"))
    assert_unfinished_note(world, "src/app.py")
    assert "REVERTED-BY-AGENT" in said(world)


def test_a_failed_call_that_only_moved_another_ref_says_so(world):
    git(world.repo, "branch", "other")

    def plant(w):
        env = dict(os.environ, GIT_INDEX_FILE=str(w.tmp / "idx"))
        run = lambda *args, **kw: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@e", *args],
                                                 cwd=w.repo, env=env, check=True, capture_output=True,
                                                 text=True, **kw).stdout.strip()
        blob = run("hash-object", "-w", "--stdin", input="evil:\n")
        run("read-tree", "HEAD")
        run("update-index", "--add", "--cacheinfo", f"100644,{blob},justfile")
        commit = run("commit-tree", run("write-tree"), "-p", "HEAD", "-m", "x")
        git(w.repo, "update-ref", "refs/heads/other", commit)
    stops(world, False, plant)
    assert_unfinished_note(world, "other refs")
    assert "refs/heads/other" in said(world)


def test_a_failed_call_that_was_fully_put_back_says_rolled_back(world):
    stops(world, False, plant_files)
    note = fail_note(world)
    assert note.endswith("and rolled back its unauthorized writes.")
    assert "undid 1 commit(s)" in note


def test_a_breach_with_no_list_is_not_called_rolled_back(world, monkeypatch):
    def enforce(*a, **k):
        raise permissions.PermissionBreach("x")
    monkeypatch.setattr(permissions, "enforce", enforce)
    stops(world, False, lambda w: None)
    assert_unfinished_note(world, "everything the agent wrote")

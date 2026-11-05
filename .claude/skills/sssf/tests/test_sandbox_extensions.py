"""adw_modules/sandbox.py extensions_in_clone: a VM run loads a pi roster's
harness_engineering extensions from its clone, and only ones committed at the
run's starting commit (docs/designs/remote-sandboxes.md, decisions 21, 22, 24, 27).

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, sandbox, session

SUBAGENTS = "adws/adw_data/harness_engineering/subagents.ts"
MARKER = ".pi/extensions/marker.ts"


def shipped():
    return agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))


def roster(*paths):
    cfg = shipped()
    for a in cfg.agents:
        a.harness_engineering = []
    agent = next(a for a in cfg.agents if a.name == "builder")
    assert agent.coding_agent == "pi"
    agent.harness_engineering = list(paths)
    return cfg, agent


class Repo(type(Path())):
    """The repo's path, with `git` and `commit` helpers hung on it."""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*a):
        return subprocess.run(["git", *a], cwd=root, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "t")
    git("config", "user.email", "t@t")

    def commit(*files):
        for f in files:
            (root / f).parent.mkdir(parents=True, exist_ok=True)
            (root / f).write_text(f"// {f}\n")
        git("add", *files) if files else None
        git("commit", "-q", "--allow-empty", "-m", "c")
        return git("rev-parse", "HEAD")

    commit("README")
    monkeypatch.chdir(root)
    found = Repo(root.resolve())
    found.git, found.commit = git, commit
    return found


@pytest.fixture
def tree(repo):
    return repo / "adws/adw_data/worktrees/abc"


def test_a_committed_extension_loads_from_the_clone_with_what_it_imports_uncommitted(repo, tree):
    (repo / ".pi/extensions").mkdir(parents=True)
    (repo / MARKER).write_text('import "./helper.ts"\n')
    repo.git("add", MARKER)
    repo.git("commit", "-qm", "m")
    (repo / ".pi/extensions/helper.ts").write_text("// untracked\n")
    start = repo.git("rev-parse", "HEAD")
    cfg, agent = roster(MARKER)
    sandbox.extensions_in_clone(cfg, repo, start, tree)
    assert agent.harness_engineering == [str(tree / MARKER)]


def test_the_shipped_rosters_subagents_extension_is_checked_and_anchored(repo, tree):
    start = repo.commit(SUBAGENTS, "adws/adw_data/harness_engineering/themeMap.ts")
    cfg = shipped()
    had = [a.name for a in cfg.agents if a.harness_engineering]
    assert {"planner", "scout"} <= set(had)
    sandbox.extensions_in_clone(cfg, repo, start, tree)
    for a in cfg.agents:
        if a.name in had:
            assert a.harness_engineering == [str(tree / SUBAGENTS)]
        else:
            assert a.harness_engineering == []


def test_the_shipped_roster_with_its_extension_uncommitted_stops_naming_the_agent(repo, tree):
    start = repo.commit()
    with pytest.raises(SystemExit) as stop:
        sandbox.extensions_in_clone(shipped(), repo, start, tree)
    assert "planner" in str(stop.value) and SUBAGENTS in str(stop.value)


def test_the_path_as_worktree_anchor_left_it_is_accepted(repo, tree):
    start = repo.commit(MARKER)
    cfg, agent = roster(str(repo / MARKER))
    sandbox.extensions_in_clone(cfg, repo, start, tree)
    assert agent.harness_engineering == [str(tree / MARKER)]


@pytest.mark.parametrize("how", ["untracked", "staged", "missing"])
def test_an_uncommitted_extension_stops_the_run_naming_agent_and_path(repo, tree, how):
    start = repo.commit()
    if how != "missing":
        (repo / ".pi/extensions").mkdir(parents=True)
        (repo / MARKER).write_text("// x\n")
    if how == "staged":
        repo.git("add", MARKER)
    cfg, _ = roster(MARKER)
    with pytest.raises(SystemExit) as stop:
        sandbox.extensions_in_clone(cfg, repo, start, tree)
    assert "builder" in str(stop.value) and MARKER in str(stop.value)


def test_an_extension_committed_after_the_run_started_stops_it(repo, tree):
    start = repo.commit()
    repo.git("branch", "sssf/abc", start)
    repo.git("checkout", "-q", "sssf/abc")
    repo.commit(MARKER)
    cfg, _ = roster(MARKER)
    with pytest.raises(SystemExit):
        sandbox.extensions_in_clone(cfg, repo, start, tree)
    repo.git("checkout", "-q", "main")
    repo.commit(MARKER)
    cfg, _ = roster(MARKER)
    with pytest.raises(SystemExit):
        sandbox.extensions_in_clone(cfg, repo, start, tree)


@pytest.mark.parametrize("where", ["absolute", "../outside.ts", ".pi/../../outside.ts"])
def test_an_extension_outside_the_repo_stops_the_run(repo, tree, tmp_path, where):
    (tmp_path / "outside.ts").write_text("// x\n")
    path = str(tmp_path / "outside.ts") if where == "absolute" else where
    start = repo.commit()
    cfg, _ = roster(path)
    with pytest.raises(SystemExit, match="outside the repo") as stop:
        sandbox.extensions_in_clone(cfg, repo, start, tree)
    assert "builder" in str(stop.value) and path in str(stop.value)


def test_a_directory_or_a_committed_symlink_is_not_an_extension(repo, tree):
    (repo / ".pi/extensions").mkdir(parents=True)
    (repo / ".pi/extensions/a.ts").write_text("// x\n")
    (repo / "link.ts").symlink_to("/etc/hostname")
    repo.git("add", ".pi", "link.ts")
    repo.git("commit", "-qm", "d")
    start = repo.git("rev-parse", "HEAD")
    for path in (".pi/extensions", "link.ts"):
        cfg, _ = roster(path)
        with pytest.raises(SystemExit):
            sandbox.extensions_in_clone(cfg, repo, start, tree)


def test_a_roster_without_extensions_is_untouched(repo, tree, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("git ran")
    monkeypatch.setattr(sandbox.subprocess, "run", fail)
    cfg, _ = roster()
    sandbox.extensions_in_clone(cfg, repo, "", tree)
    assert all(a.harness_engineering == [] for a in cfg.agents)
    claude = agents.load_config(str(SKILL / "templates" / "sssf.config.claude_code.yaml"))
    sandbox.extensions_in_clone(claude, repo, "", tree)
    assert all(a.coding_agent == "claude_code" for a in claude.agents)


def test_agents_sharing_one_list_each_get_their_own(repo, tree):
    start = repo.commit(MARKER)
    cfg, builder = roster()
    shared = [MARKER]
    pis = [a for a in cfg.agents if a.coding_agent == "pi"][:2]
    assert len(pis) == 2
    for a in pis:
        a.harness_engineering = shared
    sandbox.extensions_in_clone(cfg, repo, start, tree)
    for a in pis:
        assert a.harness_engineering == [str(tree / MARKER)]
    assert shared == [MARKER]


def test_a_vm_run_checks_extensions_before_the_vm_opens(repo, monkeypatch):
    start = repo.commit(MARKER)
    wt = repo / "adws/adw_data/worktrees/abc"
    repo.git("worktree", "add", "-q", "-b", "sssf/abc", str(wt), start)
    monkeypatch.chdir(wt)
    subprocess.run(["git", "config", "user.name", "t"], cwd=wt, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=wt, check=True)
    (wt / ".pi/extensions/other.ts").write_text("// o\n")
    subprocess.run(["git", "add", ".pi"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "later"], cwd=wt, check=True, capture_output=True)

    class Reached(Exception):
        pass

    calls = []

    def open_remote(*a, **k):
        calls.append(a)
        raise Reached

    monkeypatch.setattr(sandbox, "open_remote", open_remote)
    monkeypatch.setattr(sandbox, "prepare", lambda *a: None)
    monkeypatch.setattr(session.worktree, "main_checkout", lambda: repo)
    monkeypatch.setattr(session.worktree, "live_runs", lambda db: [])

    ended = []

    @contextlib.contextmanager
    def phase(params):
        # A real phase ends the run failed on any exception, so a stop must come from inside it.
        try:
            yield SimpleNamespace(log=lambda **kw: None)
        except BaseException as error:
            ended.append((params.name, type(error)))
            raise

    def run_for(cfg):
        return SimpleNamespace(cfg=cfg, adw_id="abc", phase=phase)

    place = {"branch": "sssf/abc", "base": start[:7]}
    cfg, agent = roster(MARKER)
    with pytest.raises(Reached):
        session._open_sandbox(run_for(cfg), place, "vercel", "vercel")
    assert agent.harness_engineering == [str(Path.cwd() / MARKER)]

    calls.clear()
    cfg, _ = roster(".pi/extensions/other.ts")
    ended.clear()
    with pytest.raises(SystemExit):
        session._open_sandbox(run_for(cfg), place, "vercel", "vercel")
    assert ended == [("sandbox", SystemExit)]
    cfg, _ = roster(MARKER)
    with pytest.raises(SystemExit):
        session._open_sandbox(run_for(cfg), {"branch": "sssf/abc"}, "vercel", "vercel")
    assert calls == []


# --- extensions_in_repo: the operator container mounts only the repo (decision 14) ---

def test_in_repo_extensions_pass(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    cfg, _ = roster(SUBAGENTS, str(root / "x" / "y.ts"), "uncommitted/new.ts")
    sandbox.extensions_in_repo(cfg, root)
    cfg, _ = roster()
    agent = next(a for a in cfg.agents if a.name == "builder")
    agent.harness_engineering = [SUBAGENTS]
    sandbox.extensions_in_repo(cfg, root)


@pytest.mark.parametrize("path", ["/tmp/elsewhere/x.ts", "~/.pi/agent/extensions/x.ts", "../outside.ts"])
def test_outside_extensions_stop_the_run(tmp_path, monkeypatch, path):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    cfg, _ = roster(path)
    with pytest.raises(SystemExit) as stop:
        sandbox.extensions_in_repo(cfg, root)
    assert "builder" in str(stop.value) and path in str(stop.value)


def test_a_claude_code_roster_has_nothing_to_check(tmp_path):
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.claude_code.yaml"))
    sandbox.extensions_in_repo(cfg, tmp_path)


def _ensure(monkeypatch, tmp_path, cfg, **kwargs):
    calls = []
    monkeypatch.setattr(session.worktree, "main_checkout", lambda: tmp_path)
    monkeypatch.setattr(sandbox, "extensions_in_repo", lambda c, r: calls.append(r))
    monkeypatch.setattr(sandbox, "prepare", lambda *a: None)

    def launching():
        raise AssertionError("worktree.launching entered")
    monkeypatch.setattr(session.worktree, "launching", launching)
    with contextlib.suppress(AssertionError):
        session.ensure(cfg, **kwargs)
    return calls


def test_ensure_checks_extensions_in_the_container_before_launching(monkeypatch, tmp_path):
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", "/x")
    cfg, _ = roster("/tmp/elsewhere/x.ts")
    monkeypatch.setattr(session.worktree, "main_checkout", lambda: tmp_path)
    monkeypatch.setattr(session.worktree, "launching",
                        lambda: pytest.fail("worktree.launching entered"))
    with pytest.raises(SystemExit):
        session.ensure(cfg)


def test_ensure_leaves_a_host_run_alone(monkeypatch, tmp_path):
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    assert _ensure(monkeypatch, tmp_path, shipped()) == []


def test_ensure_leaves_a_vm_run_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", "/x")
    assert _ensure(monkeypatch, tmp_path, shipped(), sandbox_name="e2b") == []

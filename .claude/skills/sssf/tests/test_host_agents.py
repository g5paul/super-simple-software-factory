"""`host_agents: false` in repo.yaml refuses every launch that would run an agent here.

Real chains' `main` run in a throwaway git repo with a faked `agents.execute` and a
faked VM (`session._open_sandbox`). The roster is the one SSSF ships.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_host_agents.py
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ADWS = SKILL / "templates" / "adws"
sys.path.insert(0, str(ADWS))

from adw_modules import agents, permissions, quality, repo_settings, session, trial, worktree
from adw_modules import sandbox as sandbox_mod
from adw_modules.tracer import Tracer

from test_adw_commit_messages import fake_execute, suite
from test_trial import attempt, fake_panel
from test_trial import repo as trial_repo      # noqa: F401  (a fixture)

REPO_YAML = "adws/adw_sssf_config/repo.yaml"
OFF = "sandbox: openshell\nhost_agents: false\n"
ALL_ADWS = ["adw_plan", "adw_build", "adw_plan_build", "adw_build_review", "adw_document", "adw_prompt",
            "adw_scout"]
FLAG_ADWS = ["adw_simple_sdlc", "adw_build_test", "adw_plan_build_test", "adw_plan_build_test_quality"]


def git(*args: str, cwd=None) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


def set_repo_yaml(root: Path, text: str) -> None:
    (root / REPO_YAML).write_text(text)


class Rig:
    def __init__(self, root: Path):
        self.root = root
        self.executed: list[str] = []
        self.vms: list[tuple[str, str | None]] = []

    def trace_counts(self) -> tuple:
        db = self.root / "adws/adw_data/sssf.db"
        counts = []
        for table in ("sessions", "processes", "events", "phases"):
            try:
                with sqlite3.connect(db) as conn:
                    counts.append(conn.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.Error:
                counts.append(0)
        data = self.root / "adws/adw_data/worktrees"
        return (tuple(counts), git("worktree", "list", cwd=self.root),
                git("branch", "--list", "sssf/*", cwd=self.root),
                sorted(p.name for p in data.iterdir()) if data.is_dir() else [])

    def launch(self, module: str, *args, cwd: Path | None = None, **kw) -> int:
        os.chdir(cwd or self.root)
        adw = importlib.import_module(module)
        sys.argv = [f"adws/{module}.py"]
        return adw.main(*args, **kw)

    def refused(self, module: str, *args, cwd: Path | None = None, mentions: str = "",
                **kw) -> str:
        before = self.trace_counts()
        n_exec, n_vm = len(self.executed), len(self.vms)
        with pytest.raises(session.HostAgentsRefused) as stop:
            self.launch(module, *args, cwd=cwd, **kw)
        message = str(stop.value)
        assert "host_agents: false" in message and REPO_YAML in message
        assert mentions in message
        assert self.trace_counts() == before
        assert (len(self.executed), len(self.vms)) == (n_exec, n_vm)
        return message


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    saved = (os.getcwd(), sys.argv, sys.excepthook,
             {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)})
    git("init", "-q", "-b", "main", cwd=root)
    (root / ".gitignore").write_text("adws/adw_data/\n")
    (root / "adws/adw_sssf_config").mkdir(parents=True)
    (root / "app.txt").write_text("one\n")
    set_repo_yaml(root, "sandbox: openshell\n")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "init", cwd=root)
    monkeypatch.chdir(root)
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    rig = Rig(root)

    def execute(run, phase, call):
        rig.executed.append(phase.params.name)
        if phase.params.owner == "planner":
            Path("specs").mkdir(exist_ok=True)
            Path(f"specs/{run.adw_id}_{len(rig.executed)}.md").write_text("spec")
        out = fake_execute(run, phase, call)
        Path(f"{phase.params.name}.txt").write_text(f"call {len(rig.executed)}")   # every call changes the tree
        return out

    monkeypatch.setattr(agents, "load_config", lambda path=None: cfg)
    monkeypatch.setattr(agents, "validate", lambda c, required: None)
    monkeypatch.setattr(agents, "execute", execute)
    monkeypatch.setattr(permissions, "check_local_commit", lambda *a, **k: None)
    monkeypatch.setattr(sandbox_mod, "prepare", lambda name, c: None)
    monkeypatch.setattr(session, "_open_sandbox",
                        lambda run, place, provider, asked: rig.vms.append((provider, asked)))
    monkeypatch.setattr(quality, "run_quality", suite(*[True] * 30))
    monkeypatch.setattr(quality, "run_tests", suite(*[True] * 30))
    yield rig
    os.chdir(saved[0])
    sys.argv, sys.excepthook = saved[1], saved[2]
    for sig, handler in saved[3].items():
        signal.signal(sig, handler)


def host_run(rig: Rig, adw_id: str = "aaaa0001") -> str:
    """A worktree run on the host, made while the setting is off its default."""
    assert rig.launch("adw_simple_sdlc", "add it", adw_id=adw_id, worktree=True, sandbox_name="none") == 0
    assert not worktree.recorded(str(rig.root / "adws/adw_data/sssf.db"), adw_id).get("sandbox")
    return adw_id


# 1 settings

def test_settings():
    assert repo_settings.RepoSettings().host_agents is True
    with pytest.raises(ValueError, match=r"host_agents.*sandbox|sandbox.*host_agents"):
        repo_settings.RepoSettings(host_agents=False, sandbox="none")
    for name in ("openshell", "vercel", "e2b"):
        assert repo_settings.RepoSettings(host_agents=False, sandbox=name).host_agents is False
    for bad in (0, "false"):
        with pytest.raises(ValueError):
            repo_settings.RepoSettings(host_agents=bad, sandbox="openshell")


def test_the_shipped_repo_yaml_loads_with_host_agents_on(tmp_path):
    target = tmp_path / REPO_YAML
    target.parent.mkdir(parents=True)
    shutil.copy(SKILL / "templates" / "repo.yaml", target)
    assert repo_settings.load(tmp_path).host_agents is True


# 2 bad pair

def test_the_bad_pair_stops_a_launch_naming_both(rig):
    set_repo_yaml(rig.root, "host_agents: false\n")
    before = rig.trace_counts()
    with pytest.raises(SystemExit) as stop:
        rig.launch("adw_plan", "x")
    assert "host_agents" in str(stop.value) and "sandbox" in str(stop.value)
    assert rig.trace_counts() == before and rig.executed == []


# 3, 4 new runs

@pytest.mark.parametrize("module", ALL_ADWS)
def test_a_no_flag_chain_refuses(rig, module):
    set_repo_yaml(rig.root, OFF)
    rig.refused(module, "x", mentions=module)


@pytest.mark.parametrize("module", FLAG_ADWS)
def test_sandbox_none_refuses(rig, module):
    set_repo_yaml(rig.root, OFF)
    rig.refused(module, "x", sandbox_name="none", mentions="--sandbox none")


def test_tickets_with_sandbox_none_refuses(rig):
    folder = Path(".scratch/summaries")
    (rig.root / folder / "issues").mkdir(parents=True)
    (rig.root / folder / "spec.md").write_text("# spec\n")
    git("add", "-f", "-A", cwd=rig.root)
    git("commit", "-qm", "feature", cwd=rig.root)
    set_repo_yaml(rig.root, OFF)
    rig.refused("adw_tickets", str(folder), sandbox_name="none", mentions="--sandbox none")


# 5 trial start, 6 trial review

def test_trial_start_refuses_whatever_the_flag(trial_repo, monkeypatch):
    (trial_repo / REPO_YAML).parent.mkdir(parents=True, exist_ok=True)
    set_repo_yaml(trial_repo, OFF)

    real = subprocess.Popen

    def no_spawn(argv, *a, **k):
        assert argv[0] != "uv", "a trial attempt was spawned"
        return real(argv, *a, **k)

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    for asked in ("none", None, "openshell"):
        with pytest.raises(trial.TrialError) as stop:
            trial.start("x", ["main", "chatgpt"], asked=asked)
        assert "host_agents: false" in str(stop.value) and REPO_YAML in str(stop.value)
    import adw_trial
    args = argparse.Namespace(action="start", prompt="x", rosters=["main", "chatgpt"], sandbox="none")
    assert adw_trial.main(args) == 1


def test_trial_review_refuses(trial_repo, monkeypatch):
    attempt("t0000001", "aaaa0001", "main", text="good\n", index=0)
    attempt("t0000001", "bbbb0001", "chatgpt", text="fine\n", index=1)
    seen = fake_panel(monkeypatch)
    (trial_repo / REPO_YAML).parent.mkdir(parents=True, exist_ok=True)
    set_repo_yaml(trial_repo, OFF)
    with pytest.raises(session.HostAgentsRefused) as stop:
        trial.review("t0000001")
    assert "host_agents: false" in str(stop.value) and REPO_YAML in str(stop.value)
    assert seen == []


# 7 resuming a host run

def test_a_host_run_does_not_resume_by_any_flag(rig):
    host_run(rig)
    set_repo_yaml(rig.root, OFF)
    for flag in (None, "none", "openshell"):
        rig.refused("adw_simple_sdlc", "again", adw_id="aaaa0001", sandbox_name=flag, mentions="aaaa0001")
    rig.refused("adw_build", "again", adw_id="aaaa0001", mentions="aaaa0001")


def test_a_plain_host_run_does_not_resume(rig):
    assert rig.launch("adw_plan", "x", adw_id="bbbb0001") == 0
    set_repo_yaml(rig.root, OFF)
    rig.refused("adw_plan", "x", adw_id="bbbb0001", mentions="bbbb0001")


# 8 a run can't turn it off

@pytest.mark.parametrize("committed", [True, False])
def test_a_worktree_copy_of_repo_yaml_changes_nothing(rig, committed):
    host_run(rig)
    set_repo_yaml(rig.root, OFF)
    tree = rig.root / "adws/adw_data/worktrees/aaaa0001"
    (tree / REPO_YAML).parent.mkdir(parents=True, exist_ok=True)
    (tree / REPO_YAML).write_text("sandbox: openshell\nhost_agents: true\n")
    if committed:
        git("add", "-f", REPO_YAML, cwd=tree)
        git("commit", "-qm", "turn it off", cwd=tree)
    rig.refused("adw_simple_sdlc", "again", adw_id="aaaa0001", mentions="aaaa0001")
    rig.refused("adw_simple_sdlc", "again", adw_id="aaaa0001", cwd=tree, mentions="aaaa0001")
    rig.refused("adw_plan", "x", cwd=tree, mentions="adw_plan")


# 9 the VM path, and 12 the default

@pytest.mark.parametrize("setting", [OFF, "sandbox: openshell\nhost_agents: true\n", "sandbox: openshell\n"])
def test_the_vm_path_is_as_before(rig, setting):
    set_repo_yaml(rig.root, setting)
    assert rig.launch("adw_simple_sdlc", "add it", adw_id="cccc0001", worktree=True) == 0
    assert rig.launch("adw_simple_sdlc", "add it", adw_id="cccc0002", worktree=True,
                      sandbox_name="openshell") == 0
    assert [v[0] for v in rig.vms] == ["openshell", "openshell"]
    db = str(rig.root / "adws/adw_data/sssf.db")
    assert worktree.recorded(db, "cccc0001")["sandbox"] == "openshell"
    assert rig.executed
    assert rig.launch("adw_build", "again", adw_id="cccc0001") == 0            # resume, no flag
    assert rig.launch("adw_build", "join", adw_id="cccc0002") == 0              # joined by a no-flag chain
    assert len(rig.vms) == 4


def test_the_default_runs_agents_on_the_host(rig):
    for setting in ("sandbox: none\n", "sandbox: none\nhost_agents: true\n"):
        set_repo_yaml(rig.root, setting)
        assert rig.launch("adw_plan", "x") == 0
        assert rig.launch("adw_simple_sdlc", "x", sandbox_name="none") == 0
    assert rig.executed and rig.vms == []


# 10 non-agent commands

def test_quality_and_the_trace_still_work(rig):
    host_run(rig)
    set_repo_yaml(rig.root, OFF)
    n = len(rig.executed)
    assert rig.launch("adw_quality", "check", adw_id="dddd0001") == 0
    assert len(rig.executed) == n
    db = rig.root / "adws/adw_data/sssf.db"
    with sqlite3.connect(db) as conn:
        assert conn.execute("select count(*) from sessions where adw_id='dddd0001'").fetchone()[0] == 1
    os.chdir(rig.root)
    cfg = agents.load_config()
    assert "aaaa0001" in worktree.discard(cfg, "aaaa0001")
    assert rig.launch("adw_simple_sdlc", "more", adw_id="ffff0001", worktree=True, sandbox_name="openshell") == 0
    os.chdir(rig.root)
    git("commit", "-aqm", "settings", cwd=rig.root)
    assert "ffff0001" in worktree.merge(cfg, "ffff0001")


@pytest.mark.skipif(not shutil.which("just") or not shutil.which("sqlite3"), reason="just or sqlite3 is missing")
def test_sessions_and_phases_recipes_still_print(tmp_path):
    repo = tmp_path / "repo"
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    set_repo_yaml(repo, OFF)
    shutil.copy(SKILL / "templates" / "justfile", repo / "justfile")
    db = repo / "adws/adw_data/sssf.db"
    db.parent.mkdir(parents=True)
    tracer = Tracer(str(db), str(repo / "adws/adw_data/events.jsonl"), data_root=str(repo / "adws/adw_data"))
    tracer.session_start("eeee0001", "t", adw_name="adw_build")
    from adw_modules.data_types import Phase, PhaseParams
    tracer.phase_upsert(Phase(phase_id="p1", adw_id="eeee0001", seq=1, status="success",
                              params=PhaseParams(name="build_phase", kind="code", owner="x", description="d")))
    for argv, needle in ((["sessions"], "eeee0001"), (["phases", "eeee0001"], "build_phase")):
        done = subprocess.run(["just", *argv], cwd=repo, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert needle in done.stdout


# 11 only quality is agentless

def test_only_quality_is_agentless():
    users = [p.name for p in ADWS.rglob("*.py") if "agentless=True" in p.read_text()]
    assert users == ["adw_quality.py"]
    assert ".call(" not in (ADWS / "adw_quality.py").read_text()

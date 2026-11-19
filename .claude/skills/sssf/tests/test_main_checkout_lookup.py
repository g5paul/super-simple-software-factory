"""The main checkout is found without git's location variables, and fails closed when git can't answer.

A VM agent commits a `.env` holding `GIT_DIR=sub/gd` plus a bare repo at `sub/gd`. A chain that
starts in the run's worktree loads that `.env`; the lookup of the main checkout must not follow it.
Every launch here is a fresh subprocess with the worktree as its working folder, the way a
re-exec or a `just` recipe starts one. The config is the one SSSF ships.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_main_checkout_lookup.py
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
ADWS = SKILL / "templates" / "adws"
sys.path.insert(0, str(ADWS))

from adw_modules import budget, utils, worktree        # noqa: E402
from adw_modules.data_types import EventRecord         # noqa: E402
from adw_modules.tracer import Tracer                  # noqa: E402

RUN = "bbbb0001"
REPO_YAML = "adws/adw_sssf_config/repo.yaml"


def git(*args: str, cwd=None) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True, cwd=cwd).stdout.strip()


class Rig:
    def __init__(self, root: Path, bin_dir: Path, marker: Path):
        self.root, self.bin, self.marker = root, bin_dir, marker
        self.wt = root / "adws/adw_data/worktrees" / RUN
        self.db = root / "adws/adw_data/sssf.db"

    def counts(self) -> list[int]:
        out = []
        for table in ("sessions", "processes", "events"):
            try:
                with sqlite3.connect(self.db) as conn:
                    out.append(conn.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.Error:
                out.append(0)
        return out

    def env(self, **extra: str) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("GIT_") and k != "SSSF_SANDBOX_SDK"}
        env["PATH"] = f"{self.bin}{os.pathsep}{env['PATH']}"
        env.update(extra)
        return env

    def launch(self, *argv: str, env: dict | None = None, code: str | None = None):
        command = [sys.executable, *(["-c", code] if code else list(argv))]
        return subprocess.run(command, cwd=self.wt, env=env or self.env(),
                              capture_output=True, text=True, timeout=120)

    def chain(self, name: str, *args: str, env: dict | None = None):
        return self.launch(f"adws/{name}.py", *args, "--config", str(self.root / "sssf.config.yaml"),
                           env=env)

    def plant(self, how: str, **variables: str) -> dict:
        """The environment a chain started in the worktree gets: the variables from a `.env` the run's
        branch carries, or from the engineer's shell. agentsh refuses to write a file named `.env`, so
        the `.env` cases are host_only."""
        if how == "dotenv":
            (self.wt / ".env").write_text("".join(f"{k}={v}\n" for k, v in variables.items()))
            return self.env()
        return self.env(**variables)

    def assert_refused(self, done) -> None:
        assert done.returncode != 0, done.stdout + done.stderr
        assert "host_agents: false" in done.stderr and REPO_YAML in done.stderr, done.stderr
        assert not self.marker.exists(), "an agent started on this machine"
        assert not (self.wt / "sub/adws").exists()
        assert not (self.wt / "adws/adw_data/sssf.db").exists()


@pytest.fixture
def rig(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    shutil.copytree(ADWS, root / "adws", ignore=shutil.ignore_patterns("adw_data", "__pycache__"))
    shutil.copy(SKILL / "templates" / "sssf.config.yaml", root / "sssf.config.yaml")
    (root / REPO_YAML).parent.mkdir(parents=True, exist_ok=True)
    (root / REPO_YAML).write_text("sandbox: vercel\nhost_agents: false\nmax_live_cost: 7\n")
    (root / ".gitignore").write_text("adws/adw_data/\n")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "init", cwd=root)

    wt = root / "adws/adw_data/worktrees" / RUN
    git("worktree", "add", "-q", "-b", f"sssf/{RUN}", str(wt), "HEAD", cwd=root)
    git("init", "-q", "--bare", "sub/gd", cwd=wt)
    for keep in ("objects/.keep", "refs/.keep"):
        (wt / "sub/gd" / keep).write_text("")
    git("add", "-f", "-A", cwd=wt)
    git("commit", "-qm", "agent work", cwd=wt)

    # The run is a VM run, as session._start records one.
    config = root / "adws/adw_data"
    tracer = Tracer(str(root / "adws/adw_data/sssf.db"), str(config / f"sessions/{RUN}/events.jsonl"),
                    data_root=str(config))
    tracer.session_start(RUN, "t", adw_name="adw_plan")
    tracer.event(EventRecord(adw_id=RUN, phase_id="", type="log", name="worktree", payload={
        "branch": f"sssf/{RUN}", "worktree": str(wt), "launch_branch": "main",
        "base": git("rev-parse", "--short", "HEAD", cwd=wt), "sandbox": "vercel"}))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    marker = tmp_path / "agent-ran"
    # Config validation asks `pi --list-models` and `claude auth`; only a real agent call writes the marker.
    models = sorted(set(re.findall(r"^\s*model:\s*(\S+)", (root / "sssf.config.yaml").read_text(), re.M)))
    rows = "\n".join(f"{m.split('/', 1)[0]} {m.split('/', 1)[1]} 200K" for m in models if "/" in m)
    for name in ("claude", "pi"):
        script = bin_dir / name
        script.write_text(f"#!/bin/sh\ncase \"$1\" in --list-models) echo 'provider model context'\n"
                          f"cat <<'ROWS'\n{rows}\nROWS\nexit 0;; auth|--version) exit 0;; esac\n"
                          f"echo {name} >> {marker}\nexit 1\n")
        script.chmod(0o755)
    # The data folder is gitignored, so the prompts exist in the checkout and the worktree alike.
    for folder in (root, wt):
        for part in ("prompt_engineering", "harness_engineering"):
            shutil.copytree(SKILL / "templates" / part, folder / "adws/adw_data" / part)
    return Rig(root, bin_dir, marker)


# ── the plant ────────────────────────────────────────────────────────────────

HOW = [pytest.param("dotenv", marks=pytest.mark.host_only), "shell"]


@pytest.mark.parametrize("how", HOW)
def test_the_plant_works(rig, how):
    env = rig.plant(how, GIT_DIR="sub/gd")
    env["PYTHONPATH"] = str(rig.wt / "adws")
    done = rig.launch(code="import adw_modules.utils, os; print(os.environ.get('GIT_DIR'))", env=env)
    assert done.stdout.strip() == "sub/gd", done.stderr
    raw = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=rig.wt,
                         env=rig.env(GIT_DIR="sub/gd"), capture_output=True, text=True)
    assert Path(raw.stdout.strip()) == (rig.wt / "sub/gd").resolve()


# ── the launches ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("how", HOW)
def test_the_bypass_refuses(rig, how):
    before = rig.counts()
    rig.assert_refused(rig.chain("adw_plan", "make it", env=rig.plant(how, GIT_DIR="sub/gd")))
    assert rig.counts() == before


@pytest.mark.parametrize("name,value", [("GIT_COMMON_DIR", "sub/gd"), ("GIT_WORK_TREE", "sub")])
def test_the_shell_export_refuses(rig, name, value):
    before = rig.counts()
    rig.assert_refused(rig.chain("adw_plan", "make it", env=rig.env(**{name: value})))
    assert rig.counts() == before


@pytest.mark.parametrize("how", HOW)
def test_the_resume_stays_a_vm_run(rig, how):
    """host_agents finds the run's record in the real trace, so the resume isn't judged a host run.
    What follows may still fail (the runner's own git call obeys GIT_DIR, out of scope), but never
    by starting an agent here or by writing under the planted folder."""
    env = rig.plant(how, GIT_DIR="sub/gd")
    env["SSSF_SANDBOX_SDK"] = "1"
    done = rig.chain("adw_build", "go", "--adw-id", RUN, env=env)
    assert "host_agents: false" not in done.stderr, done.stderr
    assert not rig.marker.exists(), done.stdout + done.stderr
    assert not (rig.wt / "sub/adws").exists()
    assert not (rig.wt / "adws/adw_data/sssf.db").exists()


@pytest.mark.parametrize("how", HOW)
def test_every_setting_comes_from_the_real_checkout(rig, how):
    code = ("from adw_modules import budget, session; "
            "print(budget._root()); print(budget.max_live_cost()); "
            "print(session.choose_sandbox(None, 'adws/adw_data/sssf.db', None))")
    env = rig.plant(how, GIT_DIR="sub/gd")
    env["PYTHONPATH"] = str(rig.wt / "adws")
    done = rig.launch(code=code, env=env)
    assert done.stdout.split() == [str(rig.root.resolve()), "7.0", "vercel"], done.stderr


@pytest.mark.parametrize("how", HOW)
def test_a_failing_git_refuses(rig, how):
    done = rig.chain("adw_plan", "make it", env=rig.plant(how, GIT_CONFIG_COUNT="x"))
    assert done.returncode != 0
    assert "main checkout" in done.stderr, done.stderr
    assert not rig.marker.exists()
    assert not (rig.wt / "adws/adw_data/sssf.db").exists()


# ── in-process ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", sorted(utils.GIT_LOCATION_VARS) if hasattr(utils, "GIT_LOCATION_VARS")
                         else ["GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE"])
def test_the_environment_is_left_alone(rig, monkeypatch, name):
    monkeypatch.chdir(rig.wt)
    monkeypatch.setenv(name, "sub/gd")
    worktree.main_checkout()
    assert os.environ[name] == "sub/gd"
    assert utils.operator_env()[name] == "sub/gd"


def test_same_answers_as_before(rig, monkeypatch, tmp_path):
    for var in [v for v in os.environ if v.startswith("GIT_")]:
        monkeypatch.delenv(var)
    root = rig.root.resolve()
    (rig.root / "adws/sub").mkdir(exist_ok=True)
    for where in (rig.root, rig.root / "adws", rig.wt):
        monkeypatch.chdir(where)
        assert worktree.main_checkout() == root
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert budget._root() == Path.cwd()

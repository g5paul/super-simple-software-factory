"""A VM run's committed files must not run on the host.

An agent in a VM commits any file outside `adws/`; those commits land in the run's worktree on the
host. Two host steps read the worktree as project config: the Python lookup (`uv python find`) and
the SDK re-exec (`uv run --with`). SSSF's own git calls there must also run no hook and no
fsmonitor. Config is the one SSSF ships (`templates/sssf.config.yaml`).

Each docstring says whether the test fails on the baseline (0e09a03) or passes on both.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_vm_files_on_host.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_host_python import host, open_box, script                       # noqa: E402,F401
from test_main_checkout_lookup import RUN, git, rig                       # noqa: E402,F401

from adw_modules import agent_pi, agents, sandbox, sandbox_remote, session, worktree  # noqa: E402

REAL_UV = shutil.which("uv")
SKILL = Path(__file__).resolve().parent.parent
MAJOR_MINOR_LINE = "import sys; print('%d.%d' % sys.version_info[:2])"


# ── H1: the Python lookup ───────────────────────────────────────────────────

def worktree_of(h):
    wt = h.repo / "adws" / "adw_data" / "worktrees" / "abc"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "other", str(wt)],
                   cwd=h.repo, check=True, capture_output=True)
    return wt


@pytest.mark.skipif(REAL_UV is None, reason="needs uv")
def test_a_committed_python_version_is_never_run(tmp_path, monkeypatch):
    """H1, resume and join (both reach Remote.open in the worktree). Fails on the baseline."""
    h = host(tmp_path, monkeypatch)
    (h.bin / "uv").unlink()                        # the real uv, found further down PATH
    wt = worktree_of(h)
    marker = tmp_path / "ran"
    tools = wt / "tools"
    tools.mkdir()
    script(tools / "python3", f"echo hit >> {marker}\necho 3.13\n")
    (wt / ".python-version").write_text("./tools/python3\n")
    box = open_box(h, monkeypatch, cwd=wt)
    for _ in ("resume", "join"):
        try:
            box.open()
        except Exception:
            pass                                    # the stand-in openshell makes no VM
    assert not marker.exists(), "uv ran the worktree's interpreter"


def test_uv_is_asked_in_the_main_checkout(tmp_path, monkeypatch):
    """H1, the call: uv's folder is the main checkout, without UV_PROJECT_ENVIRONMENT.
    Fails on the baseline."""
    h = host(tmp_path, monkeypatch, uv=None)
    script(h.bin / "uv", f'echo "$PWD ${{UV_PROJECT_ENVIRONMENT-unset}}" >> {h.tmp}/uv.log\n'
                         f'echo {h.interp}\n')
    wt = worktree_of(h)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/somewhere/.venv-abc")
    box = open_box(h, monkeypatch, cwd=wt)
    try:
        box.open()
    except Exception:
        pass
    folders = [line.split() for line in (h.tmp / "uv.log").read_text().splitlines()]
    assert folders and all(f in ([str(h.repo.resolve()), "unset"], [str(h.repo), "unset"])
                           for f in folders), folders
    assert os.environ["UV_PROJECT_ENVIRONMENT"] == "/somewhere/.venv-abc"


def installed_minors() -> list[str]:
    out = subprocess.run(["uv", "python", "list", "--only-installed", "--output-format", "json"],
                         capture_output=True, text=True).stdout
    import json
    seen: list[str] = []
    for item in json.loads(out or "[]"):
        minor = f"{item['version_parts']['major']}.{item['version_parts']['minor']}"
        if minor in seen:
            continue
        # keep only a minor that `uv python find` can answer for by name
        if subprocess.run(["uv", "python", "find", minor], capture_output=True).returncode == 0:
            seen.append(minor)
    return seen


@pytest.mark.skipif(REAL_UV is None, reason="needs uv")
@pytest.mark.parametrize("tracked", [False, True])
def test_the_python_is_the_one_the_baseline_asked(tmp_path, monkeypatch, tracked):
    """UV_PYTHON is unchanged while the two trees' tracked files match. Passes on both."""
    minors = installed_minors()
    if not minors:
        pytest.skip("no installed Python")
    h = host(tmp_path, monkeypatch)
    (h.bin / "uv").unlink()
    if tracked:
        (h.repo / ".python-version").write_text(f"{minors[-1]}\n")
        subprocess.run(["git", "add", "."], cwd=h.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "pin"], cwd=h.repo, check=True, capture_output=True)
    wt = worktree_of(h)
    found = subprocess.run(["uv", "python", "find"], cwd=wt, capture_output=True, text=True)
    if found.returncode != 0 or not found.stdout.strip():
        pytest.skip("uv finds no Python in the worktree")
    expected = subprocess.run([found.stdout.strip(), "-I", "-S", "-c", MAJOR_MINOR_LINE],
                              capture_output=True, text=True).stdout.strip()
    box = open_box(h, monkeypatch, cwd=wt)
    try:
        box.open()
    except Exception:
        pass
    assert box.python == expected


# ── H2: the SDK re-exec ─────────────────────────────────────────────────────

class Exec(Exception):
    pass


@pytest.fixture
def sdk(rig, monkeypatch):
    """The vercel SDK can't be imported; os.execvp is recorded. No config of the worktree matters."""
    calls: list = []

    def fake_exec(file, argv):
        calls.append((Path.cwd(), file, list(argv)))
        raise Exec

    monkeypatch.setitem(sys.modules, "vercel", None)
    monkeypatch.setitem(sys.modules, "vercel.sandbox", None)
    monkeypatch.setitem(sys.modules, "vercel.sandbox.sync", None)
    monkeypatch.delenv("SSSF_SANDBOX_SDK", raising=False)
    for var in [v for v in os.environ if v.startswith("GIT_")]:
        monkeypatch.delenv(var)
    monkeypatch.setattr(sandbox_remote.os, "execvp", fake_exec)
    monkeypatch.setattr(sandbox, "check_roster", lambda provider, cfg: None)
    monkeypatch.setattr(agent_pi, "vm_token", lambda *a, **k: "t")
    monkeypatch.setattr(sys, "argv", ["adws/adw_plan.py", "a prompt"])
    (rig.wt / "uv.toml").write_text("# agent\n")
    (rig.wt / ".python-version").write_text("./tools/python3\n")
    cfg = agents.load_config(str(rig.root / "sssf.config.yaml"))
    return calls, cfg


def test_resume_re_execs_before_the_worktree(rig, sdk, monkeypatch):
    """H2, resume without the flag: execvp from the launch folder, before worktree.enter.
    Fails on the baseline."""
    calls, cfg = sdk
    monkeypatch.chdir(rig.root)
    entered = []
    monkeypatch.setattr(worktree, "enter", lambda *a, **k: entered.append(1))
    with pytest.raises(Exec):
        session.ensure(cfg, RUN)
    assert entered == []
    (folder, file, argv), = calls
    assert folder == rig.root.resolve()
    assert file == "uv" and argv == ["uv", "run", "--quiet", "--with", "vercel-sandbox==0.10.0",
                                     "adws/adw_plan.py", "a prompt"]


def test_a_launch_from_inside_a_worktree_refuses(rig, sdk, monkeypatch):
    """H2: from inside the worktree the SDK fetch refuses in one line. Fails on the baseline."""
    calls, cfg = sdk
    monkeypatch.chdir(rig.wt)
    with pytest.raises(SystemExit) as stop:
        sandbox_remote.require_sdk("vercel", "vercel.sandbox.sync", "vercel-sandbox==0.10.0")
    assert calls == [] and "\n" not in str(stop.value)
    assert str(rig.wt) in str(stop.value) and str(rig.root.resolve()) in str(stop.value)


def test_git_that_cannot_answer_refuses_the_fetch(rig, sdk, monkeypatch):
    """H2: inside a checkout where git fails, the fetch refuses. Fails on the baseline."""
    calls, cfg = sdk
    monkeypatch.chdir(rig.root)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "x")
    with pytest.raises(SystemExit):
        sandbox_remote.require_sdk("vercel", "vercel.sandbox.sync", "vercel-sandbox==0.10.0")
    assert calls == []


def test_a_new_run_asks_execvp_what_it_always_did(rig, sdk, monkeypatch):
    """H2, new run with the flag from the main checkout. Passes on both."""
    calls, cfg = sdk
    monkeypatch.chdir(rig.root)
    with pytest.raises(Exec):
        session.ensure(cfg, "cccc0002", use_worktree=True, sandbox_name="vercel")
    (folder, file, argv), = calls
    assert folder == rig.root.resolve() and file == "uv"
    assert argv == ["uv", "run", "--quiet", "--with", "vercel-sandbox==0.10.0",
                    "adws/adw_plan.py", "a prompt"]


# ── git in the worktree ─────────────────────────────────────────────────────

def plant_fsmonitor(rig, monkeypatch, tmp_path) -> Path:
    marker = tmp_path / "fsmonitor-ran"
    hook = script(tmp_path / "fsm.sh", f"echo hit >> {marker}\n")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(hook))
    return marker


def test_fsmonitor_from_the_environment_never_runs(rig, monkeypatch, tmp_path):
    """GIT_CONFIG_* naming an fsmonitor: the plant works on plain git, and SSSF's calls
    write no marker. Fails on the baseline."""
    marker = plant_fsmonitor(rig, monkeypatch, tmp_path)
    (rig.wt / "new.txt").write_text("x")
    subprocess.run(["git", "status", "--porcelain"], cwd=rig.wt, capture_output=True)
    assert marker.exists(), "the plant does not work"
    marker.unlink()
    monkeypatch.chdir(rig.wt)
    worktree.dirty(rig.wt)
    worktree.uncommitted(rig.wt)
    sandbox_remote._host_git("status", "--porcelain", cwd=rig.wt)
    assert not marker.exists()


def test_a_post_merge_hook_never_runs(rig, monkeypatch, tmp_path):
    """A relative core.hooksPath with a committed post-merge hook: _host_git's merge runs
    no hook. Fails on the baseline."""
    marker = tmp_path / "hook-ran"
    hooks = rig.wt / ".githooks"
    hooks.mkdir()
    script(hooks / "post-merge", f"echo hit >> {marker}\n")
    git("config", "core.hooksPath", ".githooks", cwd=rig.root)
    git("add", "-f", ".githooks", cwd=rig.wt)
    git("commit", "-qm", "hook", cwd=rig.wt)
    (rig.wt / "f.txt").write_text("f")
    git("add", "f.txt", cwd=rig.wt)
    git("commit", "-qm", "f", cwd=rig.wt)
    tip = git("rev-parse", "HEAD", cwd=rig.wt)
    git("reset", "-q", "--hard", "HEAD~1", cwd=rig.wt)
    # the plant: a plain merge runs the hook
    git("-c", "core.hooksPath=.githooks", "merge", "-q", "--ff-only", tip, cwd=rig.wt)
    assert marker.exists(), "the plant does not work"
    marker.unlink()
    git("reset", "-q", "--hard", "HEAD~1", cwd=rig.wt)
    sandbox_remote._host_git("merge", "-q", "--ff-only", tip, cwd=rig.wt)
    assert not marker.exists()


# ── the refusal ─────────────────────────────────────────────────────────────

def test_a_checkout_git_rejects_stops_with_the_fix(rig, monkeypatch):
    """budget._root names the working folder, the checkout folder and safe.directory;
    adw_tickets prints it without a traceback. Fails on the baseline."""
    from adw_modules import budget
    for var in [v for v in os.environ if v.startswith("GIT_")]:
        monkeypatch.delenv(var)
    monkeypatch.chdir(rig.wt)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "x")
    with pytest.raises(worktree.WorktreeError) as error:
        budget._root()
    text = str(error.value)
    assert str(rig.wt) in text and str(rig.wt.resolve()) in text and "safe.directory" in text
    done = rig.chain("adw_tickets", "some-folder", env=rig.env(GIT_CONFIG_COUNT="x"))
    assert done.returncode != 0 and "Traceback" not in done.stderr, done.stderr
    assert "safe.directory" in done.stderr, done.stderr

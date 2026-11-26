"""Only the main checkout's `.env` loads, in every layout, and the lookup stops where there is
no main checkout.

Baseline (`c7b8066`): `utils` loaded the first `.env` at or above its own folder, so a
worktree's committed `.env`, or an empty one in `adws/` or `adws/adw_data/`, won over the main
checkout's; `main_checkout()` was `common_dir().parent`, wrong for a submodule, a separate git
dir and a bare repo; the template justfile used `set dotenv-load`. The config is the one SSSF
ships. Tests that write a file named `.env` are `host_only`: agentsh refuses to open one.

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

import pytest

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
sys.path.insert(0, str(TEMPLATES / "adws"))

from adw_modules import checkout, worktree  # noqa: E402

host_only = pytest.mark.host_only

PROBE = """
import json, os, sys
from pathlib import Path
from dotenv import dotenv_values
sys.path.insert(0, sys.argv[1])
from adw_modules import utils
here = Path(utils.__file__).resolve().parent
walked = next((f / ".env" for f in (here, *here.parents) if (f / ".env").is_file()), None)
base = set(dotenv_values(walked)) if walked else set()
assert base <= utils.DOTENV_KEYS, (base, utils.DOTENV_KEYS)
print(json.dumps({"keys": sorted(utils.DOTENV_KEYS),
                  "env": {k: os.environ.get(k) for k in ("SSSF_MAIN", "SSSF_PROBE")}}))
"""


def git(cwd, *args: str, env: dict | None = None, check: bool = True):
    done = subprocess.run(["git", "-c", "protocol.file.allow=always", *args], cwd=cwd,
                          capture_output=True, text=True, env=env)
    if check and done.returncode != 0:
        raise AssertionError(f"git {args}: {done.stderr}")
    return done


def init(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    return root


def install(root: Path) -> None:
    """The template's adw_modules and justfile, as install.py stamps them."""
    dest = root / "adws" / "adw_modules"
    shutil.copytree(TEMPLATES / "adws" / "adw_modules", dest, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(TEMPLATES / "justfile", root / "justfile")


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init", "--allow-empty")


def clean_env(**extra: str) -> dict[str, str]:
    drop = checkout.GIT_LOCATION_VARS | {"JUST_NO_DOTENV", "SSSF_MAIN", "SSSF_PROBE", "SSSF_AGENTSH"}
    return {**{k: v for k, v in os.environ.items() if k not in drop}, **extra}


@pytest.fixture
def probe(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text(PROBE)

    def run(copy_adws: Path, cwd: Path, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(path), str(copy_adws)], cwd=cwd,
                              capture_output=True, text=True, env=clean_env(**env))
    return run


@pytest.fixture
def hub(tmp_path):
    """A main checkout with SSSF installed and one worktree of a run."""
    main = init(tmp_path / "main")
    install(main)
    (main / ".gitignore").write_text("adws/adw_data/\n.env\n")
    commit_all(main)
    wt = main / "adws" / "adw_data" / "worktrees" / "w1"
    git(main, "worktree", "add", "-q", "-b", "sssf/w1", str(wt))
    return main, wt


# ── the layouts of main_checkout_from (no .env is written) ───────────────────

def test_plain_clone_and_linked_worktree_find_the_main_checkout(hub):
    main, wt = hub
    assert checkout.main_checkout_from(main) == main.resolve()
    assert checkout.main_checkout_from(wt / "adws" / "adw_modules") == main.resolve()


def test_outside_a_repo_is_none(tmp_path):
    assert checkout.main_checkout_from(tmp_path) is None


def test_a_submodule_is_its_own_main_checkout(tmp_path):
    inner = init(tmp_path / "inner")
    (inner / "f").write_text("x")
    commit_all(inner)
    outer = init(tmp_path / "outer")
    git(outer, "submodule", "add", "-q", str(inner), "sub")
    sub = outer / "sub"
    assert checkout.main_checkout_from(sub) == sub.resolve()


def test_a_separate_git_dir(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    gitdir = tmp_path / "elsewhere"
    git(work, "init", "-q", "-b", "main", "--separate-git-dir", str(gitdir))
    git(work, "config", "user.name", "t")
    git(work, "config", "user.email", "t@t")
    commit_all(work)
    assert checkout.main_checkout_from(work) == work.resolve()
    linked = tmp_path / "linked"
    git(work, "worktree", "add", "-q", "-b", "b2", str(linked))
    with pytest.raises(checkout.NoMainCheckout, match="separate-git-dir"):
        checkout.main_checkout_from(linked)


def test_enter_refuses_a_separate_git_dir_before_making_anything(tmp_path, monkeypatch):
    from adw_modules import agents
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q", "-b", "main", "--separate-git-dir", str(tmp_path / "g"))
    git(work, "config", "user.name", "t")
    git(work, "config", "user.email", "t@t")
    commit_all(work)
    monkeypatch.chdir(work)
    cfg = agents.load_config(str(TEMPLATES / "sssf.config.yaml"))
    with pytest.raises(worktree.WorktreeError, match="separate-git-dir"):
        worktree.enter(cfg, "aaaa0001", requested=True)
    assert not (work / cfg.defaults.data_dir / "worktrees").exists()
    assert git(work, "branch", "--list", "sssf/*").stdout.strip() == ""


def test_a_bare_repo_with_a_worktree_stops(tmp_path):
    src = init(tmp_path / "src")
    commit_all(src)
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    linked = tmp_path / "linked"
    git(bare, "worktree", "add", "-q", str(linked), "main")
    with pytest.raises(checkout.NoMainCheckout, match="bare repo"):
        checkout.main_checkout_from(linked)


def test_git_location_variables_move_nothing(hub, tmp_path, monkeypatch):
    main, wt = hub
    other = init(tmp_path / "other")
    commit_all(other)
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_COMMON_DIR", str(other / ".git"))
    assert checkout.main_checkout_from(wt) == main.resolve()


@pytest.mark.parametrize("explicit", [False, True])
def test_a_planted_bare_shape_is_not_a_repo(hub, explicit, monkeypatch):
    main, wt = hub
    mods = wt / "adws" / "adw_modules"
    (mods / "HEAD").write_text("ref: refs/heads/main\n")
    (mods / "objects").mkdir()
    (mods / "refs").mkdir()
    if explicit:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.bareRepository")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", "explicit")
    assert checkout.main_checkout_from(mods) == main.resolve()


# ── which .env loads (writes a file named .env) ──────────────────────────────

def keys_of(done: subprocess.CompletedProcess) -> dict:
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@host_only
def test_a_worktrees_own_env_is_not_loaded(hub, probe):
    """Baseline: the worktree's `.env` was found first and loaded; SSSF_PROBE reached os.environ."""
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    (wt / ".env").write_text("SSSF_PROBE=worktree\n")
    out = keys_of(probe(wt / "adws", wt))
    assert out["env"] == {"SSSF_MAIN": "main", "SSSF_PROBE": None}
    assert "SSSF_MAIN" in out["keys"] and "SSSF_PROBE" in out["keys"]   # names stay held back


@host_only
@pytest.mark.parametrize("where", ["adws", "adws/adw_data", "worktree-adws"])
def test_an_empty_env_above_the_copy_hides_no_key(hub, probe, where):
    """Baseline: an empty `.env` the walk found first made DOTENV_KEYS empty."""
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    target = (wt / "adws") if where == "worktree-adws" else main / where
    (target / ".env").write_text("")
    out = keys_of(probe(wt / "adws", wt))
    assert "SSSF_MAIN" in out["keys"] and out["env"]["SSSF_MAIN"] == "main"


@host_only
def test_outside_a_repo_the_walk_is_kept(tmp_path, probe):
    """Baseline behaviour, kept: no git repo, so the first `.env` above the copy loads."""
    install(tmp_path)
    (tmp_path / ".env").write_text("SSSF_MAIN=walk\n")
    assert keys_of(probe(tmp_path / "adws", tmp_path))["env"]["SSSF_MAIN"] == "walk"


@host_only
def test_a_nested_repo_with_its_own_env_changes_nothing(hub, probe):
    """The working folder in a nested repo doesn't choose the `.env`."""
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    nested = init(main / "nested")
    (nested / ".env").write_text("SSSF_PROBE=nested\n")
    out = keys_of(probe(main / "adws", nested))
    assert out["env"] == {"SSSF_MAIN": "main", "SSSF_PROBE": None}


@host_only
def test_the_bare_layout_stops_the_import(tmp_path, probe):
    src = init(tmp_path / "src")
    install(src)
    commit_all(src)
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    linked = tmp_path / "linked"
    git(bare, "worktree", "add", "-q", str(linked), "main")
    done = probe(linked / "adws", linked)
    assert done.returncode != 0 and "bare repo" in done.stderr


# ── the template justfile ────────────────────────────────────────────────────

needs_just = pytest.mark.skipif(shutil.which("just") is None or shutil.which("uv") is None,
                                reason="needs just and uv")
RECIPE = "\nshow:\n    @echo main=${SSSF_MAIN:-} probe=${SSSF_PROBE:-}\n"


def recipe_just(wt: Path) -> None:
    (wt / "justfile").write_text((TEMPLATES / "justfile").read_text() + RECIPE)


@needs_just
@host_only
def test_just_loads_the_main_checkouts_env_from_a_worktree(hub):
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    (wt / ".env").write_text("SSSF_PROBE=worktree\n")
    recipe_just(wt)
    done = subprocess.run(["just", "show"], cwd=wt, capture_output=True, text=True, env=clean_env())
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "main=main probe="


@needs_just
@host_only
def test_just_through_mod_sssf_from_a_subfolder(hub):
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    (wt / ".env").write_text("SSSF_PROBE=worktree\n")
    shutil.copy(TEMPLATES / "justfile", wt / "sssf.just")
    (wt / "sssf.just").write_text((wt / "sssf.just").read_text() + RECIPE)
    (wt / "justfile").write_text("mod sssf 'sssf.just'\n")
    (wt / "sub").mkdir()
    done = subprocess.run(["just", "sssf", "show"], cwd=wt / "sub", capture_output=True, text=True,
                          env=clean_env())
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "main=main probe="


@needs_just
@host_only
def test_just_stops_in_the_bare_layout(tmp_path):
    src = init(tmp_path / "src")
    install(src)
    commit_all(src)
    bare = tmp_path / "bare.git"
    git(tmp_path, "clone", "-q", "--bare", str(src), str(bare))
    linked = tmp_path / "linked"
    git(bare, "worktree", "add", "-q", str(linked), "main")
    recipe_just(linked)
    done = subprocess.run(["just", "show"], cwd=linked, capture_output=True, text=True, env=clean_env())
    assert done.returncode == 2 and "bare repo" in done.stderr


@needs_just
@host_only
def test_the_lookup_step_runs_no_file_from_the_worktree(hub):
    """Baseline: `uv run --no-project` alone ran the interpreter a `.python-version` named."""
    main, wt = hub
    (main / ".env").write_text("SSSF_MAIN=main\n")
    marker = wt / "ran"
    planted = wt / "evil"
    planted.mkdir()
    script = f"#!/bin/sh\ntouch {marker}\nexit 1\n"
    (planted / "python").write_text(script)
    (planted / "python").chmod(0o755)
    (wt / ".python-version").write_text("./evil/python\n")
    (wt / "uv.toml").write_text('python-preference = "only-managed"\n')
    # a real venv, as uv reads one: pyvenv.cfg plus the interpreters
    (wt / ".venv" / "bin").mkdir(parents=True)
    (wt / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\nversion = 3.12.3\n")
    for name in ("python", "python3"):
        (wt / ".venv" / "bin" / name).write_text(script)
        (wt / ".venv" / "bin" / name).chmod(0o755)
    line = next(l for l in (TEMPLATES / "justfile").read_text().splitlines()
                if l.startswith("set dotenv-command"))
    command = line.split(":=", 1)[1].strip().strip('"')
    assert "uv" not in command.split()      # uv would read the worktree's .venv, .python-version, uv.toml
    done = subprocess.run(command, shell=True, cwd=wt, capture_output=True, text=True, env=clean_env())
    assert done.returncode == 0, done.stderr
    assert done.stdout == "SSSF_MAIN=main\n"
    assert not marker.exists()

"""An agent's commit of a secret file is refused, on any ref and in the after-call check.

Baseline (`c7b8066`): `judge_commits`, `ref_moves` and `enforce` judged paths with
`permitted()` only, so a commit adding `.env` passed for any agent whose `writes` allowed
the path (or for any agent at all, since `.env` is in no `protected_files`). The config is
the one SSSF ships. Commits are made with `git hash-object` and `commit-tree`, so no file
named `.env` is ever written and the tests run under agentsh.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, permissions  # noqa: E402


@pytest.fixture
def cfg():
    return agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))


def git(cwd, *args: str, stdin: str | None = None, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          input=stdin, env=env).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("a = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root


def make_commit(root: Path, changes: dict[str, str | None], parent: str = "HEAD") -> str:
    """A commit on `parent` with `changes` (path -> text, None deletes), made in a scratch
    index, so no file in the work tree is written and no ref moves."""
    index = root / ".git" / "scratch-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    git(root, "read-tree", parent, env=env)
    for path, text in changes.items():
        if text is None:
            git(root, "update-index", "--force-remove", path, env=env)
        else:
            blob = git(root, "hash-object", "-w", "--stdin", stdin=text)
            git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
    tree = git(root, "write-tree", env=env)
    index.unlink()
    return git(root, "commit-tree", tree, "-p", git(root, "rev-parse", parent), "-m", "agent")


def judge(root, base, tip, cfg, **kw):
    agents_ = [a for a in cfg.agents if a.name == "builder"]
    return permissions.judge_commits(root, base, tip, cfg, agents_, **kw)


@pytest.mark.parametrize("path", [".env", "sub/.env.local", ".env.template", ".env.test"])
def test_a_commit_adding_a_secret_file_is_refused(repo, cfg, path):
    base = git(repo, "rev-parse", "HEAD")
    tip = make_commit(repo, {path: "KEY=1\n", "src/app.py": "a = 2\n"})
    refused = judge(repo, base, tip, cfg)
    assert [paths for _, paths in refused] == [[path]]


def test_an_example_file_passes(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    tip = make_commit(repo, {"src/.env.example": "KEY=\n", "src/.env.sample": "KEY=\n"})
    assert judge(repo, base, tip, cfg) == []


def test_a_rename_to_env_is_refused(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    tip = make_commit(repo, {"src/app.py": None, ".env": "a = 1\n"})
    assert [p for _, paths in judge(repo, base, tip, cfg) for p in paths] == [".env"]


def test_deleting_and_changing_a_tracked_env_are_refused(repo, cfg):
    # the engineer's own tracked .env: only an agent's commit of it is judged
    first = make_commit(repo, {".env": "A=1\n"})
    git(repo, "update-ref", "HEAD", first)
    changed = make_commit(repo, {".env": "A=2\n"})
    assert [p for _, ps in judge(repo, first, changed, cfg) for p in ps] == [".env"]
    deleted = make_commit(repo, {".env": None})
    assert [p for _, ps in judge(repo, first, deleted, cfg) for p in ps] == [".env"]


def test_a_repo_yaml_pattern_is_secret(repo, cfg):
    (repo / "adws" / "adw_sssf_config").mkdir(parents=True)
    (repo / "adws" / "adw_sssf_config" / "repo.yaml").write_text("secret_files:\n  - '*.key'\n")
    base = git(repo, "rev-parse", "HEAD")
    tip = make_commit(repo, {"src/server.key": "k\n"})
    assert [p for _, ps in judge(repo, base, tip, cfg) for p in ps] == ["src/server.key"]


def test_no_grant_lifts_the_secret_rule(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    tip = make_commit(repo, {"src/.env.local": "K=1\n"})
    assert judge(repo, base, tip, cfg, only=["src/"], wrote={"src/.env.local"},
                 session="src/") != []


def test_a_carried_secret_passes_a_full_commit_but_not_inside_only(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    carried = make_commit(repo, {".env.local": "K=1\n"})
    tip = make_commit(repo, {".env.local": "K=1\n"})
    assert judge(repo, base, tip, cfg, carried=carried) == []
    refused = judge(repo, base, tip, cfg, carried=carried, only=["src/"])
    assert [p for _, ps in refused for p in ps] == [".env.local"]
    other = make_commit(repo, {".env.local": "K=2\n"})
    assert judge(repo, base, other, cfg, carried=carried) != []


def test_ref_moves_refuses_a_secret_on_another_ref(repo, cfg):
    run = SimpleNamespace(cfg=cfg, repo_root=repo)
    agent = [a for a in cfg.agents if a.name == "builder"][0].model_copy(update={"writes": ["src/"]})
    before = permissions.head(run)
    tip = make_commit(repo, {".env": "K=1\n", "src/new.py": "x\n"})
    git(repo, "update-ref", "refs/heads/other", tip)
    report = permissions.ref_moves(run, agent, before)
    assert "secret files" in report and ".env" in report


@pytest.mark.parametrize("forced", [False, True])
def test_an_agent_commit_of_env_is_undone_and_enforce_raises(repo, cfg, forced):
    run = SimpleNamespace(cfg=cfg, repo_root=repo)
    agent = [a for a in cfg.agents if a.name == "builder"][0]
    if forced:
        (repo / ".gitignore").write_text(".env\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-qm", "ignore")
    tree, before = permissions.snapshot(run), permissions.head(run)
    base = before["sha"]
    tip = make_commit(repo, {".env": "K=1\n"})
    git(repo, "update-ref", "HEAD", tip)
    assert permissions.undo_commits(run, before)
    assert git(repo, "rev-parse", "HEAD") == base
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, agent, tree, before)
    assert "committed secret file(s) .env" in str(breach.value)


def test_a_secret_in_a_merge_the_agent_made_is_still_named(repo, cfg):
    """`git log --name-only` shows nothing for a merge unless asked with -m."""
    run = SimpleNamespace(cfg=cfg, repo_root=repo)
    agent = [a for a in cfg.agents if a.name == "builder"][0]
    tree, before = permissions.snapshot(run), permissions.head(run)
    base = before["sha"]
    side = make_commit(repo, {"src/y.py": "y\n"})
    tree_id = git(repo, "rev-parse", f"{side}^{{tree}}")
    index = repo / ".git" / "scratch-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    git(repo, "read-tree", tree_id, env=env)
    blob = git(repo, "hash-object", "-w", "--stdin", stdin="K=1\n")
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},.env", env=env)
    merged = git(repo, "write-tree", env=env)
    index.unlink()
    tip = git(repo, "commit-tree", merged, "-p", base, "-p", side, "-m", "merge")
    git(repo, "update-ref", "HEAD", tip)
    assert permissions.undo_commits(run, before)
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, agent, tree, before)
    assert "committed secret file(s)" in str(breach.value) and ".env" in str(breach.value)

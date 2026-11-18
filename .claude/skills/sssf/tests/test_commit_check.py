"""The host judges the chain's commits against the agents' `writes`.

`permissions.judge_commits` reads a range of commits with the host's own git;
`check_local_commit` runs it after `commit_all` in local mode and undoes a
refused commit; `Run.finish` turns a VM's refusal into exit code 1. The config
is the one SSSF ships, never a test-only one.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, git_helper, permissions, runner, sandbox  # noqa: E402


@pytest.fixture
def cfg():
    return agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))


def named(cfg, name: str):
    return next(a for a in cfg.agents if a.name == name)


def git(cwd, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          input=stdin).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "t")
    git(root, "config", "user.email", "t@t")
    (root / "app.py").write_text("a = 1\n")
    (root / "justfile").write_text("default:\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root


def commit(root: Path, name: str, text: str = "x\n") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-qm", f"change {name}")
    return git(root, "rev-parse", "HEAD")


def judge(root, base, cfg, who=("builder",), **kw):
    return permissions.judge_commits(root, base, git(root, "rev-parse", "HEAD"), cfg,
                                     [named(cfg, w) for w in who], **kw)


def test_a_builder_commit_passes_and_a_protected_path_is_named(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "src/new.py")
    assert judge(repo, base, cfg) == []
    bad = commit(repo, "justfile", "evil:\n")
    assert judge(repo, base, cfg) == [(bad, ["justfile"])]


def test_only_limits_a_commit_to_its_entries(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    sha = commit(repo, "specs/a.md")
    assert judge(repo, base, cfg, ("planner",), only=["specs/a.md"]) == []
    assert judge(repo, base, cfg, ("planner",), only=["specs/"]) == []
    assert judge(repo, base, cfg, ("builder",), only=["other/"]) == [(sha, ["specs/a.md"])]


def test_the_session_folder_and_wrote_paths_pass(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "data/sessions/abc/agent_map.json")
    commit(repo, ".scratch/t/1.md")
    who = ("reviewer",)
    assert judge(repo, base, cfg, who, session="data/sessions/abc")[0][1] == [".scratch/t/1.md"]
    assert judge(repo, base, cfg, who, session="data/sessions/abc", wrote=[".scratch/t/1.md"]) == []


def test_a_merge_a_root_and_a_stranger_are_refused_before_any_diff(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, "s.txt")
    git(repo, "checkout", "-q", "main")
    commit(repo, "m.txt")
    git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge")
    assert judge(repo, base, cfg)[0][1] == ["a merge"]
    git(repo, "checkout", "-q", "--orphan", "other")
    commit(repo, "o.txt")
    refused = judge(repo, base, cfg)
    assert refused and "root" in refused[0][1][0]


@pytest.mark.parametrize("spelling", [".git", ".GIT", "git~1", ".git ", ".git.", ".git::$INDEX_ALLOCATION",
                                      ".g‌it", ".gi﻿t"])
def test_a_git_directory_path_never_passes(spelling):
    assert permissions._in_git_dir(f"sub/{spelling}/config")


@pytest.mark.parametrize("name", [".github", "gitt", ".gitignore", "a/git/b", "x.git"])
def test_other_names_are_not_the_git_directory(name):
    assert not permissions._in_git_dir(name)


def test_a_git_directory_entry_in_a_commit_is_refused_for_any_agent(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "hash-object", "-w", "--stdin", stdin="[core]\n")
    inner = git(repo, "mktree", stdin=f"100644 blob {blob}\tconfig\n")
    top = git(repo, "ls-tree", "HEAD") + f"\n040000 tree {inner}\t.GIT"
    tree = git(repo, "mktree", stdin=top + "\n")
    tip = git(repo, "commit-tree", tree, "-p", base, "-m", "git dir")
    refused = permissions.judge_commits(repo, base, tip, cfg, [named(cfg, "builder")])
    assert refused == [(tip, [".GIT/config"])]


def test_carried_work_passes_byte_for_byte_and_a_different_blob_does_not(repo, cfg):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "justfile").write_text("mine:\n")                   # the engineer's edit
    carried = permissions.carried_tree(repo)
    assert carried
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "take it")
    assert judge(repo, base, cfg, carried=carried) == []
    assert judge(repo, base, cfg) != []
    (repo / "justfile").write_text("mine:\n\tevil\n")
    bad = commit(repo, "justfile", "mine:\n\tevil\n")
    assert judge(repo, base, cfg, carried=carried)[-1] == (bad, ["justfile"])


def test_carried_tree_ignores_a_forged_index(repo):
    (repo / "x.txt").write_text("x\n")
    git(repo, "add", "x.txt")
    git(repo, "rm", "-q", "--cached", "x.txt")
    tree = permissions.carried_tree(repo)
    assert "x.txt" in git(repo, "ls-tree", "-r", "--name-only", tree)
    assert not (repo / ".git" / "sssf-carried").exists()


def local_run(repo, cfg, ran=(), carried=""):
    events = []
    return SimpleNamespace(
        repo_root=repo, session_dir=repo / "data" / "sessions" / "abc", cfg=cfg, ran=list(ran),
        carried=carried, adw_id="abc",
        tracer=SimpleNamespace(event=lambda e: events.append((e.name, e.payload))), events=events)


def test_a_local_commit_of_a_protected_path_is_undone_and_the_tree_kept(repo, cfg):
    before = git(repo, "rev-parse", "HEAD")
    run = local_run(repo, cfg, [named(cfg, "builder")])
    (repo / "justfile").write_text("evil:\n")
    with pytest.raises(permissions.PermissionBreach, match="justfile"):
        git_helper.commit_all("go", run=run)
    assert git(repo, "rev-parse", "HEAD") == before
    assert (repo / "justfile").read_text() == "evil:\n"
    assert git(repo, "status", "--porcelain") == "M justfile"
    assert run.events[0][0] == "commit_refused"


def test_a_local_commit_that_passes_clears_run_ran_unless_only(repo, cfg):
    run = local_run(repo, cfg, [named(cfg, "builder"), named(cfg, "planner")])
    (repo / "specs").mkdir()
    (repo / "specs" / "p.md").write_text("plan\n")
    git_helper.commit_all("plan", ["specs/"], run=run)
    assert len(run.ran) == 2
    (repo / "app.py").write_text("a = 2\n")
    git_helper.commit_all("build", run=run)
    assert run.ran == []


def test_a_planner_only_commit_refuses_what_the_planner_may_not_write(repo, cfg):
    before = git(repo, "rev-parse", "HEAD")
    run = local_run(repo, cfg, [named(cfg, "planner")])
    (repo / "app.py").write_text("a = 3\n")
    with pytest.raises(permissions.PermissionBreach, match="app.py"):
        git_helper.commit_all("plan", ["app.py"], run=run)
    assert git(repo, "rev-parse", "HEAD") == before


def test_a_forged_index_commit_is_refused_locally(repo, cfg):
    # A path staged only in the index (the work tree shows nothing) is still in the commit.
    before = git(repo, "rev-parse", "HEAD")
    run = local_run(repo, cfg, [named(cfg, "builder")])
    blob = git(repo, "hash-object", "-w", "--stdin", stdin="evil:\n")
    git(repo, "update-index", "--cacheinfo", f"100644,{blob},justfile")
    git(repo, "update-index", "--skip-worktree", "justfile")    # `add -A` keeps the entry
    (repo / "app.py").write_text("a = 4\n")
    with pytest.raises(permissions.PermissionBreach, match="justfile"):
        git_helper.commit_all("go", run=run)
    assert git(repo, "rev-parse", "HEAD") == before


def test_finish_exits_1_when_the_vm_commits_are_refused(monkeypatch):
    told, ended = [], []
    breach = permissions.PermissionBreach("The VM's commits were refused")

    class Box:
        def close(self, stop=False):
            assert stop
            raise breach

    me = SimpleNamespace(
        phases=[SimpleNamespace(status="success", phase_id="p")], adw_id="abc", tokens=0, cost=0,
        cfg=SimpleNamespace(observability=SimpleNamespace(db="db")),
        tracer=SimpleNamespace(event=lambda e: None, session_finish=lambda a, ok: ended.append(ok)),
        console=SimpleNamespace(advise=told.append, note=lambda t: None,
                                session_finished=lambda *a: None))
    monkeypatch.setattr(sandbox, "_current", Box())
    assert runner.Run.finish(me) == 1
    assert ended == [False] and "refused" in told[0]


def test_every_commit_all_call_in_the_chains_passes_run():
    calls = []
    for path in sorted((SKILL / "templates" / "adws").glob("adw_*.py")):
        calls += [(path.name, m) for m in re.findall(r"commit_all\([^\n]*", path.read_text())]
    assert len(calls) == 6
    assert [name for name, call in calls if "run=run" not in call] == []


# --- the planner's grant ends inside an `only` commit; spellings are folded ----


def _plan_then_builder(repo, cfg):
    run = local_run(repo, cfg, [named(cfg, "planner")])
    (repo / "specs").mkdir()
    (repo / "specs" / "old.md").write_text("plan\n")
    git_helper.commit_all("plan", ["specs/"], run=run)
    return run


def test_a_builder_change_to_a_plan_committed_spec_is_refused_locally(repo, cfg):
    run = _plan_then_builder(repo, cfg)
    before = git(repo, "rev-parse", "HEAD")
    run.ran.append(named(cfg, "builder"))
    (repo / "specs" / "old.md").write_text("changed\n")
    with pytest.raises(permissions.PermissionBreach, match="specs/old.md"):
        git_helper.commit_all("build", run=run)
    assert git(repo, "rev-parse", "HEAD") == before


def test_a_builder_alone_cannot_change_a_spec(repo, cfg):
    run = local_run(repo, cfg, [named(cfg, "builder")])
    (repo / "specs").mkdir()
    (repo / "specs" / "old.md").write_text("x\n")
    with pytest.raises(permissions.PermissionBreach, match="specs/old.md"):
        git_helper.commit_all("build", run=run)


def test_the_grant_outside_only_holds_until_the_full_commit(repo, cfg):
    cfg = cfg.model_copy(deep=True)
    planner = named(cfg, "planner")
    planner.writes = ["specs/", "docs/plans/"]
    run = local_run(repo, cfg, [planner])
    (repo / "specs").mkdir()
    (repo / "specs" / "a.md").write_text("a\n")
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "x.md").write_text("x\n")
    git_helper.commit_all("plan", ["specs/"], run=run)
    git_helper.commit_all("rest", run=run)
    assert run.ran == [] and run.taken == []


def test_each_only_entry_is_judged_without_the_agents_before_its_commit(repo, cfg):
    cfg = cfg.model_copy(deep=True)
    a, b, c = (named(cfg, n).model_copy(deep=True) for n in ("planner", "planner", "planner"))
    a.name, b.name, c.name = "A", "B", "C"
    a.writes, b.writes, c.writes = ["specs/", "docs/plans/"], ["specs/", "docs/plans/"], ["docs/x/"]
    run = local_run(repo, cfg, [a])
    (repo / "specs").mkdir()
    (repo / "specs" / "s.md").write_text("s\n")
    git_helper.commit_all("one", ["specs/"], run=run)
    run.ran.append(b)
    (repo / "docs" / "plans").mkdir(parents=True)
    (repo / "docs" / "plans" / "p.md").write_text("p\n")
    git_helper.commit_all("two", ["docs/plans/"], run=run)
    run.ran.append(c)
    assert run.taken == [(a, "specs/"), (a, "docs/plans/"), (b, "docs/plans/")]
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "specs/s.md", "again\n")
    assert permissions.judge_commits(repo, base, git(repo, "rev-parse", "HEAD"), cfg, [a, b, c],
                                     taken=run.taken) == []          # B still holds specs/
    bad = commit(repo, "docs/plans/p.md", "again\n")
    assert permissions.judge_commits(repo, base, git(repo, "rev-parse", "HEAD"), cfg, [a, b, c],
                                     taken=run.taken) == [(bad, ["docs/plans/p.md"])]


def test_a_planner_that_runs_again_is_refused_until_a_full_commit_passes(repo, cfg):
    run = _plan_then_builder(repo, cfg)
    (repo / "specs" / "x_v2.md").write_text("v2\n")
    run.ran.append(named(cfg, "planner"))
    with pytest.raises(permissions.PermissionBreach, match="x_v2.md"):
        git_helper.commit_all("plan2", ["specs/"], run=run)
    (repo / "specs" / "x_v2.md").unlink()
    (repo / "app.py").write_text("a = 9\n")
    run.ran.append(named(cfg, "builder"))
    git_helper.commit_all("build", run=run)
    assert run.ran == [] and run.taken == []
    run.ran.append(named(cfg, "planner"))
    (repo / "specs" / "y.md").write_text("y\n")
    git_helper.commit_all("plan3", ["specs/"], run=run)


def test_commit_passed_copes_with_a_run_without_taken_or_ran():
    run = SimpleNamespace(ran=["a"])
    permissions.commit_passed(run, ["specs/"])
    assert run.taken == [("a", "specs/")]
    permissions.commit_passed(run, ["specs/"])
    assert run.taken == [("a", "specs/")]
    permissions.commit_passed(run, None)
    assert run.ran == [] and run.taken == []
    bare = SimpleNamespace(ran=["a"])
    permissions.commit_passed(bare, None)
    assert bare.ran == [] and not hasattr(bare, "taken")
    permissions.commit_passed(SimpleNamespace(), ["specs/"])


SPELLINGS = [
    "JUSTFILE", "ADWS/adw_x.py", ".CLAUDE/settings.json", "SPECS/old.md", "glossary.md",
    "juſtfile", ".githooKs/pre-commit", ".pı/x.json",
    "just‌file", "just­file", "justfile.", "justfile ", "adws./x.py", "adws /x.py",
    "justfile::$DATA", "adws::$INDEX_ALLOCATION/x.py", "adws\\x.py",
    "CLAUDE~1/settings.json", "PI~1/x.json", "MCP~1.JSO", "GITHOO~1/pre-commit",
    "SSSF~1.JUS", "LOCAL~1.JUS", "CL1A2B~1/settings.json", "JUSTFI~1",
]


@pytest.mark.parametrize("name", SPELLINGS)
def test_a_protected_path_in_another_spelling_is_refused_by_the_host_check(repo, cfg, name):
    base = git(repo, "rev-parse", "HEAD")
    bad = commit(repo, name)
    assert judge(repo, base, cfg) == [(bad, [name])]


@pytest.mark.parametrize("name", ["justfile", "adws/adw_x.py", ".claude/settings.json", "specs/old.md"])
def test_the_exact_name_is_refused_as_before(repo, cfg, name):
    base = git(repo, "rev-parse", "HEAD")
    bad = commit(repo, name, "y\n")
    assert judge(repo, base, cfg) == [(bad, [name])]

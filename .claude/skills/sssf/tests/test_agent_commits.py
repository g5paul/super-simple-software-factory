"""An agent that commits its own work is undone, then checked as usual.

In a project's run e257f6d5 the documenter ran `git add ... && git commit`
itself. commit_docs then found nothing to commit and failed the run. Worse,
permissions.enforce compares the working tree against HEAD, so a commit hid
the agent's changes from it entirely: a builder could have committed a
protected file and passed. permissions.undo_commits now resets the
agent's commits before enforce runs, so its changes are back in the tree.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
PROMPTS = SKILL / "templates" / "prompt_engineering"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import git_helper, permissions
from adw_modules.data_types import AgentConfig, ConfigDefaults, PromptEngineering, SSSFConfig


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def agent(name: str, writes=None) -> AgentConfig:
    return AgentConfig(name=name, writes=writes,
                       prompt_engineering=PromptEngineering(system="s.md", user="u.md"))


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    for path in ("adws/adw_modules/quality.py", "src/app.py"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("old\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    cfg = SSSFConfig(defaults=ConfigDefaults(data_dir="adws/adw_data",
                                             protected_files=["adws/adw_modules/"]))
    return SimpleNamespace(cfg=cfg, repo_root=tmp_path)


def commit(run, path: str, text: str = "new\n") -> None:
    target = run.repo_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git("add", path)
    git("commit", "-qm", f"agent wrote {path}")


def test_no_commit_undoes_nothing(run):
    before = permissions.head(run)
    (run.repo_root / "src/app.py").write_text("new\n")
    assert permissions.undo_commits(run, before) == []


def test_a_permitted_commit_is_undone_and_its_change_kept(run):
    base = git("rev-parse", "HEAD")
    tree, before = permissions.snapshot(run), permissions.head(run)
    commit(run, "app_docs/note.md")
    commit(run, "app_docs/more.md")

    undone = permissions.undo_commits(run, before)

    assert len(undone) == 2
    assert git("rev-parse", "HEAD") == base
    touched = permissions.enforce(run, None, agent("documenter", ["app_docs/"]), tree)
    assert touched == ["app_docs/more.md", "app_docs/note.md"]
    # the chain's own commit phase now has something to commit
    git_helper.commit_all("Document the change")
    assert git("log", "-1", "--format=%s") == "Document the change"


def test_a_committed_protected_file_is_caught_and_rolled_back(run):
    tree, before = permissions.snapshot(run), permissions.head(run)
    commit(run, "adws/adw_modules/quality.py")

    permissions.undo_commits(run, before)
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, agent("builder"), tree)

    assert "adws/adw_modules/quality.py" in str(breach.value)
    assert (run.repo_root / "adws/adw_modules/quality.py").read_text() == "old\n"


def test_switching_branches_stops_the_run(run):
    before = permissions.head(run)
    git("switch", "-qc", "elsewhere")
    commit(run, "src/other.py")

    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.undo_commits(run, before)

    assert "refs/heads/main" in str(breach.value) and "refs/heads/elsewhere" in str(breach.value)
    assert git("rev-parse", "--abbrev-ref", "HEAD") == "elsewhere"   # nothing was undone


def test_the_first_commit_in_an_empty_repo_is_undone(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    run = SimpleNamespace(repo_root=tmp_path)
    before = permissions.head(run)
    commit(run, "src/app.py")
    assert len(permissions.undo_commits(run, before)) == 1
    assert git("status", "--porcelain") == "?? src/"


def test_a_reset_past_the_start_stops_the_run(run):
    commit(run, "src/second.py")
    before = permissions.head(run)
    git("reset", "-q", "--hard", "HEAD~1")

    with pytest.raises(permissions.PermissionBreach, match="doesn't descend"):
        permissions.undo_commits(run, before)


@pytest.mark.parametrize("name", ["planner", "builder", "documenter", "ticket_builder"])
def test_every_writing_agent_is_told_not_to_commit(name):
    assert "Do not commit" in (PROMPTS / name / "system.md").read_text()


def test_a_git_dir_change_does_not_shield_other_changes(run, tmp_path, monkeypatch):
    """bypass-reviewer: an agent that commits a protected file and also touches
    .git/info/exclude must still have its commit undone and the file rolled
    back. The git-directory breach is reported alongside, not instead."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    tree_before, head_before = permissions.snapshot(run), permissions.head(run)
    git_before = permissions.guard_git_dir(run)
    commit(run, "adws/adw_modules/quality.py", "bad\n")
    with open(run.repo_root / ".git" / "info" / "exclude", "a") as f:
        f.write("hidden.py\n")
    git_breach = None
    try:
        permissions.restore_git_dir(run, git_before)
    except permissions.PermissionBreach as found:
        git_breach = found
    assert git_breach is not None
    permissions.undo_commits(run, head_before)
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(run, None, agent("builder"), tree_before)
    assert git("log", "--oneline").count("\n") == 0           # the agent's commit is gone
    assert (run.repo_root / "adws/adw_modules/quality.py").read_text() == "old\n"


def test_agents_run_every_check_before_raising():
    text = (SKILL / "templates" / "adws" / "adw_modules" / "agents.py").read_text()
    git_check = text.index("permissions.restore_git_dir(run, git_before)")
    assert text.index("except permissions.PermissionBreach as found:", git_check) \
        < text.index("permissions.undo_commits(run, head_before)", git_check) \
        < text.index("permissions.enforce(run, phase, agent, tree_before, head_before)", git_check) \
        < text.index("raise git_breach", git_check)


# ── refs other than HEAD ──────────────────────────────────────────────────────
# An agent with HEAD on its run branch can make a commit on another ref with
# `git commit-tree` and `git update-ref`. Its own branch and tree stay clean, so
# these tests run the guard's real sequence against the roster SSSF ships.

import os

from adw_modules import agents

R = ".claude/skills/sssf/templates/adws/adw_modules/rules.py"
OWN = "refs/heads/sssf/run"


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    git("init", "-qb", "main")
    for path, text in {R: "v0\n", "src/app.py": "old\n", "specs/note.md": "old\n",
                       ".gitignore": "adws/adw_data/sessions/\n"}.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    git("add", "-A")
    git("commit", "-qm", "base")
    git("switch", "-qc", "sssf/run")
    cfg = agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))
    return SimpleNamespace(cfg=cfg, repo_root=repo)


def roster(run, name: str) -> AgentConfig:
    return next(a for a in run.cfg.agents if a.name == name)


def plant(run, parent: str, files: dict[str, str], extra=()) -> str:
    """A commit on `parent` changing `files`, written with plumbing only."""
    index = run.repo_root.parent / "plant.index"
    index.unlink(missing_ok=True)
    env = {**os.environ, "GIT_INDEX_FILE": str(index), "GIT_NO_REPLACE_OBJECTS": "1"}

    def g(*args, input=None):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                              check=True, capture_output=True, text=True, env=env,
                              cwd=run.repo_root, input=input).stdout.strip()
    g("read-tree", parent)
    for path, text in files.items():
        g("update-index", "--add", "--cacheinfo", f"100644,{g('hash-object', '-w', '--stdin', input=text)},{path}")
    return g("commit-tree", g("write-tree"), "-p", parent,
             *[x for e in extra for x in ("-p", e)], "-m", "planted")


def hijack(run, ref="refs/heads/main", files=None, parent=None) -> str:
    sha = plant(run, parent or git("rev-parse", ref), files or {R: "evil\n"})
    git("update-ref", ref, sha)
    return sha


def go(run, who, act=lambda: None):
    tree, head = permissions.snapshot(run), permissions.head(run)
    act()
    permissions.undo_commits(run, head)
    return permissions.enforce(run, None, who, tree, head)


def stopped(run, who, act) -> str:
    with pytest.raises(permissions.PermissionBreach) as breach:
        go(run, who, act)
    return str(breach.value)


def refs(run) -> str:
    return git("for-each-ref", "--format=%(objectname) %(refname)")


def promisor(path: Path, marker: Path) -> None:
    """Make `path` a repo whose lazy fetch would run a script that writes `marker`."""
    script = path.parent / f"{path.name}-uploadpack.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    script.chmod(0o755)
    git("-C", str(path), "config", "remote.origin.url", str(path.parent / "nowhere"))
    git("-C", str(path), "config", "remote.origin.promisor", "true")
    git("-C", str(path), "config", "remote.origin.uploadpack", str(script))
    git("-C", str(path), "config", "extensions.partialClone", "origin")


@pytest.mark.parametrize("name", ["builder", "reviewer"])
def test_the_hijack_of_main_is_stopped(shipped, name):
    old = git("rev-parse", "main")
    who = roster(shipped, name)
    box = {}

    def act():
        box["new"] = hijack(shipped)
    message = stopped(shipped, who, act)
    assert "refs/heads/main" in message and old in message and box["new"] in message and R in message
    assert git("rev-parse", "main") == box["new"]
    assert git("status", "--porcelain") == ""


@pytest.mark.parametrize("kind", ["light", "annotated", "branch", "sssf"])
def test_a_commit_on_another_ref_is_stopped(shipped, kind):
    def act():
        sha = plant(shipped, git("rev-parse", "main"), {R: "evil\n"})
        if kind == "light":
            git("tag", "t", sha)
        elif kind == "annotated":
            git("tag", "-a", "-m", "m", "t", sha)
        else:
            git("update-ref", "refs/heads/other" if kind == "branch" else "refs/heads/sssf/other", sha)
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert R in message
    assert {"light": "refs/tags/t", "annotated": "refs/tags/t", "branch": "refs/heads/other",
            "sssf": "refs/heads/sssf/other"}[kind] in message


def test_a_roster_protected_path_is_stopped(shipped):
    assert ".scratch/" in shipped.cfg.defaults.protected_files
    message = stopped(shipped, roster(shipped, "builder"),
                      lambda: hijack(shipped, "refs/heads/other", {".scratch/note.md": "x\n"},
                                     git("rev-parse", "main")))
    assert ".scratch/note.md" in message


def test_a_replace_ref_created_deleted_or_moved_is_stopped(shipped):
    a, b = git("rev-parse", "main"), plant(shipped, git("rev-parse", "main"), {"src/app.py": "x\n"})
    who = roster(shipped, "reviewer")
    assert "created" in stopped(shipped, who, lambda: git("update-ref", f"refs/replace/{a}", b))
    git("update-ref", f"refs/replace/{b}", a)
    assert "deleted" in stopped(shipped, who, lambda: git("update-ref", "-d", f"refs/replace/{b}"))
    git("update-ref", f"refs/replace/{b}", a)
    c = plant(shipped, a, {"src/app.py": "y\n"})
    assert "moved" in stopped(shipped, who, lambda: git("update-ref", f"refs/replace/{b}", c))


def test_a_merge_taking_a_protected_path_from_an_old_parent_is_stopped(shipped):
    base = git("rev-parse", "main")
    git("update-ref", "refs/heads/main", plant(shipped, base, {R: "v1\n"}))
    main = git("rev-parse", "main")

    def act():
        tree = git("rev-parse", f"{base}^{{tree}}")
        merge = git("commit-tree", tree, "-p", main, "-p", base, "-m", "merge")
        git("update-ref", "refs/heads/main", merge)
    assert R in stopped(shipped, roster(shipped, "reviewer"), act)


def test_a_protected_commit_on_the_own_branch_with_a_tag_is_named(shipped):
    def act():
        commit(shipped, R, "bad\n")
        git("tag", "t", "HEAD")
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert "refs/tags/t" in message
    assert (shipped.repo_root / R).read_text() == "v0\n"
    assert git("status", "--porcelain") == ""


def test_a_tree_breach_and_a_ref_breach_come_together(shipped):
    def act():
        (shipped.repo_root / "specs/note.md").write_text("changed\n")
        hijack(shipped)
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert "specs/note.md" in message and "refs/heads/main" in message
    assert (shipped.repo_root / "specs/note.md").read_text() == "old\n"


def test_a_missing_parent_is_could_not_check_and_fetches_nothing(shipped, tmp_path):
    marker = tmp_path / "marker"
    promisor(shipped.repo_root, marker)

    def act():
        tree = git("rev-parse", "main^{tree}")
        body = (f"tree {tree}\nparent {'1' * 40}\nauthor t <t@e> 0 +0000\n"
                "committer t <t@e> 0 +0000\n\nlost\n")
        sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
                             input=body, check=True, capture_output=True, text=True).stdout.strip()
        git("update-ref", "refs/heads/other", sha)
    assert "could not compare" in stopped(shipped, roster(shipped, "builder"), act)
    assert not marker.exists()


def test_a_missing_tree_quotes_gits_error(shipped):
    def act():
        body = (f"tree {'2' * 40}\nparent {git('rev-parse', 'main')}\nauthor t <t@e> 0 +0000\n"
                "committer t <t@e> 0 +0000\n\nlost\n")
        sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
                             input=body, check=True, capture_output=True, text=True).stdout.strip()
        git("update-ref", "refs/heads/other", sha)
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert "could not compare" in message and "git said" in message


@pytest.mark.parametrize("name", ["builder", "reviewer"])
def test_an_unprotected_commit_on_main_is_not_stopped(shipped, name):
    assert go(shipped, roster(shipped, name),
              lambda: hijack(shipped, files={"src/app.py": "new\n"})) == []


def test_a_second_worktrees_unprotected_commit_is_not_stopped(shipped, tmp_path):
    def act():
        wt = tmp_path / "wt"
        git("worktree", "add", "-q", "-b", "side", str(wt))
        (wt / "src").mkdir(exist_ok=True)
        (wt / "src/side.py").write_text("x\n")
        git("-C", str(wt), "add", "src/side.py")
        git("-C", str(wt), "commit", "-qm", "side")
    assert go(shipped, roster(shipped, "reviewer"), act) == []


def test_deleted_rewound_and_old_refs_are_not_stopped(shipped):
    c0 = git("rev-parse", "main")
    c1 = hijack(shipped)                         # R changed, before the agent runs
    for name, at in (("gone", c0), ("rw", c1), ("ff", c0)):
        git("update-ref", f"refs/heads/{name}", at)

    def act():
        git("update-ref", "-d", "refs/heads/gone")
        git("update-ref", "refs/heads/rw", c0)
        git("update-ref", "refs/heads/ff", c1)
        git("update-ref", "refs/heads/fresh", c0)
    assert go(shipped, roster(shipped, "reviewer"), act) == []


def test_a_commit_on_the_own_branch_is_undone_as_before(shipped):
    assert go(shipped, roster(shipped, "builder"), lambda: commit(shipped, "src/new.py")) == ["src/new.py"]


def test_a_writes_entry_naming_the_path_unlocks_it_on_another_ref(shipped):
    who = roster(shipped, "builder").model_copy(update={"writes": ["src/", R]})
    assert go(shipped, who, lambda: hijack(shipped)) == []


def test_a_worktrees_commondir_is_never_followed(shipped, tmp_path):
    marker = tmp_path / "marker"
    other = tmp_path / "other"
    other.mkdir()
    git("-C", str(other), "init", "-qb", "main")
    promisor(other, marker)

    def act():
        wt = tmp_path / "wt"
        git("worktree", "add", "-q", "-b", "side", str(wt))
        (wt / "src").mkdir(exist_ok=True)
        (wt / "src/side.py").write_text("x\n")
        git("-C", str(wt), "add", "src/side.py")
        git("-C", str(wt), "commit", "-qm", "side")
        (shipped.repo_root / ".git/worktrees/wt/commondir").write_text(f"{other / '.git'}\n")
    assert go(shipped, roster(shipped, "reviewer"), act) == []
    assert not marker.exists()


def test_each_kind_of_path_in_one_commit_is_judged_alone(shipped):
    grant = next(g for g in permissions.always_writable(shipped.cfg) if g.endswith("sessions/"))
    session = f"{grant}x/note.md"
    assert permissions.permitted(session, roster(shipped, "reviewer"), shipped.cfg)
    message = stopped(shipped, roster(shipped, "reviewer"),
                      lambda: hijack(shipped, files={R: "evil\n", "src/app.py": "new\n", session: "n\n"}))
    assert f"changes: {R}" in message
    assert "src/app.py" not in message and session not in message


def test_a_moved_tag_leaves_staged_work_staged(shipped):
    git("tag", "t")
    (shipped.repo_root / "src/app.py").write_text("staged\n")
    git("add", "src/app.py")
    head = permissions.head(shipped)
    git("tag", "-f", "t", plant(shipped, git("rev-parse", "main"), {"src/x.py": "x\n"}))
    assert permissions.undo_commits(shipped, head) == []
    assert git("diff", "--cached", "--name-only") == "src/app.py"


# ── specs, stashes, parents, grafts and the no-fetch env ──────────────────────

def plumb(run, *args, input=None, index=None) -> str:
    env = {**os.environ, "GIT_NO_REPLACE_OBJECTS": "1"}
    if index:
        env["GIT_INDEX_FILE"] = str(index)
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True, env=env,
                          cwd=run.repo_root, input=input).stdout.strip()


def tree_of(run, files: dict[str, str]) -> str:
    index = run.repo_root.parent / "tree.index"
    index.unlink(missing_ok=True)
    for path, text in files.items():
        blob = plumb(run, "hash-object", "-w", "--stdin", input=text)
        plumb(run, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", index=index)
    return plumb(run, "write-tree", index=index)


def forge(run, tree: str, *parents: str) -> str:
    return plumb(run, "commit-tree", tree, *[x for p in parents for x in ("-p", p)], "-m", "forged")


def forge_object(kind: str, body: str) -> str:
    return subprocess.run(["git", "hash-object", "-t", kind, "-w", "--literally", "--stdin"],
                          input=body, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def eng(shipped, tmp_path):
    """The engineer's own worktree on main, apart from the run's tree."""
    wt = tmp_path / "eng"
    git("worktree", "add", "-q", str(wt), "main")
    return wt


def ecommit(wt, files: dict[str, str]) -> None:
    for path, text in files.items():
        (wt / path).parent.mkdir(parents=True, exist_ok=True)
        (wt / path).write_text(text)
        git("-C", str(wt), "add", path)
    git("-C", str(wt), "commit", "-qm", "eng")


@pytest.mark.parametrize("name", ["builder", "reviewer"])
def test_another_runs_plan_commit_under_specs_is_not_stopped(shipped, eng, name):
    def act():
        git("-C", str(eng), "switch", "-qc", "sssf/other")
        ecommit(eng, {"specs/plan.md": "plan\n"})
    assert go(shipped, roster(shipped, name), act) == []


def test_two_runs_planning_during_each_others_calls_are_not_stopped(shipped, tmp_path):
    wt_b = tmp_path / "run-b"
    git("worktree", "add", "-q", "-b", "sssf/b", str(wt_b), "main")
    run_b = SimpleNamespace(cfg=shipped.cfg, repo_root=wt_b)
    assert go(shipped, roster(shipped, "builder"),
              lambda: ecommit(wt_b, {"specs/plan-b.md": "b\n"})) == []
    assert go(run_b, roster(shipped, "builder"),
              lambda: ecommit(shipped.repo_root, {"specs/plan-a.md": "a\n"})) == []


@pytest.mark.parametrize("untracked", [False, True])
def test_the_engineers_stash_of_unprotected_files_is_not_stopped(shipped, eng, untracked):
    def act():
        (eng / "src/app.py").write_text("changed\n")
        if untracked:
            (eng / "src/new.py").write_text("n\n")
        git("-C", str(eng), "stash", *(["-u"] if untracked else []))
    assert go(shipped, roster(shipped, "builder"), act) == []
    assert "refs/stash" in refs(shipped)


def test_an_agents_own_stash_of_unprotected_files_is_not_stopped(shipped):
    def act():
        (shipped.repo_root / "src/app.py").write_text("changed\n")
        git("stash", "-u")
    assert go(shipped, roster(shipped, "reviewer"), act) == []


@pytest.mark.parametrize("untracked", [False, True])
def test_a_stash_of_a_protected_file_is_stopped(shipped, eng, untracked):
    def act():
        if untracked:
            (eng / "src/new.py").write_text("n\n")
        (eng / R).write_text("stashed\n")
        git("-C", str(eng), "stash", *(["-u"] if untracked else []))
    assert R in stopped(shipped, roster(shipped, "builder"), act)


def test_a_stash_of_an_untracked_protected_file_is_stopped(shipped, eng):
    extra = R.replace("rules.py", "extra.py")
    assert not permissions.permitted(extra, roster(shipped, "builder"), shipped.cfg)

    def act():
        (eng / extra).write_text("n\n")
        git("-C", str(eng), "stash", "-u")
    assert extra in stopped(shipped, roster(shipped, "builder"), act)


def merge_setup(eng, protected: bool) -> None:
    git("-C", str(eng), "switch", "-qc", "feat")
    ecommit(eng, {"specs/plan.md": "plan\n", "src/x.py": "x\n", **({R: "feat\n"} if protected else {})})
    git("-C", str(eng), "switch", "-q", "main")
    ecommit(eng, {"src/other.py": "o\n"})


@pytest.mark.parametrize("form", ["--no-ff", "--ff-only"])
def test_an_engineer_merge_changing_only_specs_is_not_stopped(shipped, eng, form):
    merge_setup(eng, False)
    if form == "--ff-only":
        git("-C", str(eng), "reset", "-q", "--hard", "HEAD~1")
    assert go(shipped, roster(shipped, "builder"),
              lambda: git("-C", str(eng), "merge", form, "-m", "m", "feat")) == []


def test_an_engineer_merge_changing_a_protected_file_is_stopped(shipped, eng):
    merge_setup(eng, True)
    assert R in stopped(shipped, roster(shipped, "builder"),
                        lambda: git("-C", str(eng), "merge", "--no-ff", "-m", "m", "feat"))


def moved_on(shipped) -> tuple[str, str]:
    base = git("rev-parse", "main")
    fix = plant(shipped, base, {R: "v1\n"})
    git("update-ref", "refs/heads/main", fix)
    return base, fix


def test_a_swapped_parent_merge_is_stopped(shipped):
    base, fix = moved_on(shipped)
    assert R in stopped(shipped, roster(shipped, "reviewer"), lambda: git(
        "update-ref", "refs/heads/main", forge(shipped, git("rev-parse", f"{base}^{{tree}}"), base, fix)))


def test_an_octopus_form_of_it_is_stopped(shipped):
    base, fix = moved_on(shipped)
    side = plant(shipped, base, {"src/side.py": "s\n"})
    assert R in stopped(shipped, roster(shipped, "reviewer"), lambda: git(
        "update-ref", "refs/heads/main",
        forge(shipped, git("rev-parse", f"{base}^{{tree}}"), base, side, fix)))


def test_the_runs_own_branch_as_first_parent_is_stopped(shipped):
    commit(shipped, R, "bad\n")
    own = git("rev-parse", "HEAD")
    assert R in stopped(shipped, roster(shipped, "reviewer"), lambda: git(
        "update-ref", "refs/heads/main", forge(shipped, git("rev-parse", f"{own}^{{tree}}"),
                                              own, git("rev-parse", "main"))))


@pytest.mark.parametrize("later", [False, True])
@pytest.mark.parametrize("root_first", [True, False])
def test_a_new_root_holding_only_src_as_a_parent_is_stopped(shipped, later, root_first):
    if later:
        moved_on(shipped)

    def act():
        tree = tree_of(shipped, {"src/app.py": "x\n"})
        root = forge(shipped, tree)
        main = git("rev-parse", "main")
        git("update-ref", "refs/heads/main",
            forge(shipped, tree, *((root, main) if root_first else (main, root))))
    assert "refs/heads/main" in stopped(shipped, roster(shipped, "reviewer"), act)


@pytest.mark.parametrize("third", ["inner", "first"])
def test_a_forged_stash_with_an_existing_third_parent_is_stopped(shipped, third):
    first = git("rev-parse", "main")
    main = plant(shipped, first, {R: "v1\n"})
    git("update-ref", "refs/heads/main", main)
    inner = plant(shipped, first, {R: "v2\n"})
    git("update-ref", "refs/heads/keep", inner)
    index = forge(shipped, git("rev-parse", f"{main}^{{tree}}"), main)
    stash = lambda: git("update-ref", "refs/stash", forge(
        shipped, git("rev-parse", f"{main}^{{tree}}"), main, index, first if third == "first" else inner))
    assert R in stopped(shipped, roster(shipped, "builder"), stash)


def test_a_merge_of_two_new_roots_adding_a_protected_file_is_stopped(shipped):
    def act():
        a, b = (forge(shipped, tree_of(shipped, {f"src/{n}.py": "x\n"})) for n in "ab")
        git("update-ref", "refs/heads/other", forge(shipped, tree_of(shipped, {R: "evil\n"}), a, b))
    assert R in stopped(shipped, roster(shipped, "builder"), act)


def test_a_one_parent_commit_on_a_new_root_adding_a_protected_file_is_stopped(shipped):
    def act():
        root = forge(shipped, tree_of(shipped, {"src/a.py": "x\n"}))
        git("update-ref", "refs/heads/other", forge(shipped, tree_of(shipped, {R: "evil\n"}), root))
    assert R in stopped(shipped, roster(shipped, "builder"), act)


@pytest.mark.parametrize("part", ["..", "."])
def test_a_specs_path_with_a_dot_part_is_stopped(shipped, part):
    def act():
        blob = plumb(shipped, "hash-object", "-w", "--stdin", input="x\n")
        inner = plumb(shipped, "mktree", input=f"100644 blob {blob}\tx.py\n")
        specs = plumb(shipped, "ls-tree", "main:specs") + f"\n040000 tree {inner}\t{part}"
        specs_tree = plumb(shipped, "mktree", input=specs + "\n")
        root = [ln for ln in plumb(shipped, "ls-tree", "main").split("\n") if not ln.endswith("\tspecs")]
        top = plumb(shipped, "mktree", input="\n".join([*root, f"040000 tree {specs_tree}\tspecs"]) + "\n")
        git("update-ref", "refs/heads/main", forge(shipped, top, git("rev-parse", "main")))
    assert f"specs/{part}/x.py" in stopped(shipped, roster(shipped, "builder"), act)


def test_a_failing_read_next_to_a_graft_file_says_nothing_of_grafts(shipped):
    (shipped.repo_root / ".git/info").mkdir(exist_ok=True)
    (shipped.repo_root / ".git/info/grafts").write_text(f"{git('rev-parse', 'main')}\n")

    def act():
        body = (f"tree {'2' * 40}\nparent {git('rev-parse', 'main')}\nauthor t <t@e> 0 +0000\n"
                "committer t <t@e> 0 +0000\n\nlost\n")
        git("update-ref", "refs/heads/other", forge_object("commit", body))
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert "could not compare" in message
    assert "graft" not in message.lower() and "hint:" not in message


@pytest.mark.parametrize("form", ["plain", "linked"])
def test_a_graft_hiding_the_hijack_of_main_is_stopped(shipped, tmp_path, monkeypatch, form):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    old = git("rev-parse", "main")
    box = {}

    def act():
        box["new"] = hijack(shipped)
        info = shipped.repo_root / ".git/info"
        if form == "linked":
            elsewhere = tmp_path / "info"
            elsewhere.mkdir()
            (elsewhere / "grafts").write_text(f"{old} {box['new']}\n")
            if info.is_dir():
                import shutil
                shutil.rmtree(info)
            info.symlink_to(elsewhere)
        else:
            info.mkdir(exist_ok=True)
            (info / "grafts").write_text(f"{old} {box['new']}\n")
    guard = permissions.guard_git_dir(shipped) if form == "linked" else None
    tree, head_before = permissions.snapshot(shipped), permissions.head(shipped)
    act()
    if guard:
        try:
            permissions.restore_git_dir(shipped, guard)
        except permissions.PermissionBreach:
            pass
    permissions.undo_commits(shipped, head_before)
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(shipped, None, roster(shipped, "builder"), tree, head_before)
    assert "refs/heads/main" in str(breach.value) and box["new"] in str(breach.value)


def test_a_commit_with_sixty_thousand_parents_is_read_and_stopped(shipped):
    main = git("rev-parse", "main")
    root = forge(shipped, plumb(shipped, "mktree", input=""))
    tree = git("rev-parse", f"{plant(shipped, main, {R: 'evil'})}^{{tree}}")

    def act():
        body = (f"tree {tree}\n" + f"parent {main}\n" * 59_999 + f"parent {root}\n"
                "author t <t@e> 0 +0000\ncommitter t <t@e> 0 +0000\n\nmany\n")
        git("update-ref", "refs/heads/main", forge_object("commit", body))
    message = stopped(shipped, roster(shipped, "builder"), act)
    assert "could not compare" not in message and R in message


def test_a_four_parent_commit_with_a_new_root_is_judged_against_it(shipped):
    main = git("rev-parse", "main")
    keep = [plant(shipped, main, {f"src/k{n}.py": "k\n"}) for n in (1, 2)]
    for n, sha in enumerate(keep):
        git("update-ref", f"refs/heads/keep{n}", sha)

    def act():
        root = forge(shipped, plumb(shipped, "mktree", input=""))
        git("update-ref", "refs/heads/other",
            forge(shipped, git("rev-parse", f"{main}^{{tree}}"), main, *keep, root))
    assert R in stopped(shipped, roster(shipped, "builder"), act)


def test_a_missing_tree_fetches_nothing(shipped, tmp_path):
    marker = tmp_path / "marker"
    promisor(shipped.repo_root, marker)

    def act():
        body = (f"tree {'2' * 40}\nparent {git('rev-parse', 'main')}\nauthor t <t@e> 0 +0000\n"
                "committer t <t@e> 0 +0000\n\nlost\n")
        git("update-ref", "refs/heads/other", forge_object("commit", body))
    assert "could not compare" in stopped(shipped, roster(shipped, "builder"), act)
    assert not marker.exists()


def test_a_tag_naming_a_missing_commit_fetches_nothing(shipped, tmp_path):
    marker = tmp_path / "marker"
    promisor(shipped.repo_root, marker)

    def act():
        body = f"object {'1' * 40}\ntype commit\ntag t\ntagger t <t@e> 0 +0000\n\nm\n"
        git("update-ref", "refs/tags/t", forge_object("tag", body))
    assert "could not compare" in stopped(shipped, roster(shipped, "builder"), act)
    assert not marker.exists()

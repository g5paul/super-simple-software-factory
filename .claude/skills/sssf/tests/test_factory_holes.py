"""The holes the factory-files guard left, each closed.

Every test builds its config from the starter rosters SSSF ships and runs the
guard on a temp git repo through `snapshot` and `enforce`. No agent is started.
The per-agent tests run every agent of the pi and Claude rosters whose `writes`
doesn't name the path. `.git/info/exclude` is empty in every repo.

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

from adw_modules import agents, permissions, sandbox

PI, CLAUDE = "sssf.config.yaml", "sssf.config.claude_code.yaml"
PROMPT = "adws/adw_data/prompt_engineering/reviewer/system.md"
COMMITTED = "committed\n"


def load(roster: str):
    return agents.load_config(str(SKILL / "templates" / roster))


def named(cfg, name: str):
    return next(a for a in cfg.agents if a.name == name)


def pairs(*paths: str):
    """(roster, agent) for every agent of the pi and Claude rosters whose
    `writes` names none of `paths`, so each is refused every one."""
    found = []
    for roster, tag in ((PI, "pi"), (CLAUDE, "claude")):
        cfg = load(roster)
        for agent in cfg.agents:
            if all(not permissions.permitted(p, agent, cfg) for p in paths):
                found.append(pytest.param(roster, agent.name, id=f"{tag}-{agent.name}"))
    return found


def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make(tmp_path, monkeypatch, roster: str):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    (tmp_path / ".git/info/exclude").write_text("")
    files = {
        ".gitignore": "adws/adw_data/sessions/\n__pycache__/\n",
        PROMPT: COMMITTED,
        "src/fixtures/system.md": COMMITTED,
        "src/fixtures/settings.json": "{}\n",
        ".claude/settings.json": "{}\n",
        "justfile": "line1\nline2\n",
        "docs/guide.md": "guide\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    git("add", "-A")
    git("commit", "-qm", "base")
    return SimpleNamespace(cfg=load(roster), repo_root=tmp_path)


def write(run, path: str, text: str) -> None:
    target = run.repo_root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def refused(run, agent, before) -> str:
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(run, None, agent, before)
    return str(breach.value)


def in_index(path: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", path],
                          capture_output=True).returncode == 0


# ── case 1: a rename is a delete and an add ──

@pytest.mark.parametrize("ignore", [True, False], ids=["ignored", "no-ignore"])
@pytest.mark.parametrize("path, source", [(PROMPT, "src/fixtures/system.md"),
                                          (".claude/settings.json", "src/fixtures/settings.json")])
@pytest.mark.parametrize("roster, name", pairs(PROMPT, ".claude/settings.json"))
def test_a_moved_file_is_restored(tmp_path, monkeypatch, roster, name, path, source, ignore):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    git("rm", "-q", "--cached", path)
    write(run, "src/fixtures/moved" + Path(path).suffix, (run.repo_root / source).read_text())
    git("add", "src/fixtures/moved" + Path(path).suffix)
    (run.repo_root / path).write_text("evil\n")
    if ignore:
        with open(run.repo_root / ".gitignore", "a") as handle:
            handle.write(path + "\n")
    else:
        assert permissions.snapshot(run)[path].startswith("D,?,staged-D-")
    message = refused(run, named(run.cfg, name), before)
    assert path in message
    committed = subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True).stdout
    assert (run.repo_root / path).read_text() == committed
    assert in_index(path)


# ── case 2: a non-ASCII name ──

@pytest.mark.parametrize("roster, name", pairs(".claude/skills/sssf/é.md"))
def test_a_quoted_name_is_seen_and_deleted(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    path = ".claude/skills/sssf/é.md"
    before = permissions.snapshot(run)
    write(run, path, "new\n")
    assert path in permissions.snapshot(run)
    assert path in refused(run, named(run.cfg, name), before)
    assert not (run.repo_root / path).exists()


# ── case 3: an index flag hides a change from git diff ──

@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
@pytest.mark.parametrize("path", ["justfile", PROMPT])
@pytest.mark.parametrize("roster, name", pairs("justfile", PROMPT))
def test_a_flagged_edit_is_seen_and_restored(tmp_path, monkeypatch, roster, name, flag, path):
    run = make(tmp_path, monkeypatch, roster)
    committed = (run.repo_root / path).read_text()
    before = permissions.snapshot(run)
    git("update-index", flag, path)
    (run.repo_root / path).write_text("evil\n")
    assert path in refused(run, named(run.cfg, name), before)
    assert (run.repo_root / path).read_text() == committed
    assert git("ls-files", "-v", path).startswith("H")


# ── case 4: an edit that keeps the +/- counts ──

@pytest.mark.parametrize("roster, name", pairs("justfile", ".claude/skills/sssf/new.md"))
def test_a_rewrite_of_a_dirty_file_is_seen_and_left_alone(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    write(run, "justfile", "line1\nENGINEER\n")
    write(run, ".claude/skills/sssf/new.md", "engineer's\n")
    before = permissions.snapshot(run)
    write(run, "justfile", "line1\nAGENT\n")
    write(run, ".claude/skills/sssf/new.md", "agent's\n")
    message = refused(run, named(run.cfg, name), before)
    assert "left as-is" in message
    assert (run.repo_root / "justfile").read_text() == "line1\nAGENT\n"
    assert (run.repo_root / ".claude/skills/sssf/new.md").read_text() == "agent's\n"


# ── case 5: the rollback comes from HEAD ──

@pytest.mark.parametrize("roster, name", pairs("justfile"))
def test_a_git_rm_is_restored(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    git("rm", "-q", "justfile")
    assert "justfile" in refused(run, named(run.cfg, name), before)
    assert (run.repo_root / "justfile").read_text() == "line1\nline2\n"
    assert in_index("justfile")


@pytest.mark.parametrize("roster, name", pairs("justfile"))
def test_a_dirty_file_that_is_git_rmd_is_left_alone(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    write(run, "justfile", "line1\nENGINEER\n")
    before = permissions.snapshot(run)
    git("rm", "-qf", "justfile")
    message = refused(run, named(run.cfg, name), before)
    assert "REVERTED-BY-AGENT" in message or "left as-is" in message


@pytest.mark.parametrize("roster, name", pairs("justfile"))
def test_removed_from_the_index_and_rewritten_is_restored_not_deleted(
        tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    git("rm", "-q", "--cached", "justfile")
    (run.repo_root / "justfile").write_text("evil\n")
    assert permissions.snapshot(run)["justfile"].startswith("D,?,staged-D-")
    assert "justfile" in refused(run, named(run.cfg, name), before)
    assert (run.repo_root / "justfile").read_text() == "line1\nline2\n"
    assert in_index("justfile")


@pytest.mark.parametrize("roster, name", pairs("adws/yaml.py"))
def test_a_staged_new_fixed_path_is_unstaged_and_deleted(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    write(run, "adws/yaml.py", "evil = 1\n")
    git("add", "adws/yaml.py")
    message = refused(run, named(run.cfg, name), before)
    assert "unstaged and deleted" in message
    assert not (run.repo_root / "adws/yaml.py").exists()
    assert not in_index("adws/yaml.py")


@pytest.mark.parametrize("roster, name", pairs("adws/local.cfg"))
def test_a_staged_new_ignored_path_stays_on_disk(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    with open(run.repo_root / ".gitignore", "a") as handle:
        handle.write("adws/local.cfg\n")
    git("commit", "-qam", "ignore")
    write(run, "adws/local.cfg", "mine\n")
    before = permissions.snapshot(run)
    assert "adws/local.cfg" not in before
    git("add", "-f", "adws/local.cfg")
    message = refused(run, named(run.cfg, name), before)
    assert "gitignored" in message
    assert (run.repo_root / "adws/local.cfg").read_text() == "mine\n"
    assert not in_index("adws/local.cfg")


# ── the rollback takes a path literally ──

def test_a_glob_named_file_does_not_reset_its_matches(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, ".claude/agents/a.md", "a\n")
    write(run, "app/[id]/page.tsx", "p\n")
    write(run, "app/i/page.tsx", "i\n")
    git("add", "-A")
    git("commit", "-qm", "more")
    write(run, ".claude/settings.json", "[\"engineer\"]\n")      # staged
    git("add", ".claude/settings.json")
    write(run, ".claude/agents/a.md", "engineer\n")              # unstaged
    write(run, "app/i/page.tsx", "staged\n")
    git("add", "app/i/page.tsx")
    before = permissions.snapshot(run)
    write(run, ".claude/*", "x\n")
    git("add", "--", ":(literal).claude/*")
    git("update-index", "--skip-worktree", "--", ".claude/*")
    write(run, "app/[id]/page.tsx", "agent\n")
    refused(run, named(run.cfg, "planner"), before)
    assert (run.repo_root / ".claude/agents/a.md").read_text() == "engineer\n"
    assert (run.repo_root / ".claude/settings.json").read_text() == "[\"engineer\"]\n"
    assert "app/i/page.tsx" in git("diff", "--cached", "--name-only")
    assert (run.repo_root / "app/[id]/page.tsx").read_text() == "p\n"


# ── cases 6-8: what the launch loads from the tree ──

@pytest.mark.parametrize("roster, name", pairs("local.just"))
def test_a_new_local_just_is_deleted(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    write(run, "local.just", 'set shell := ["sh", "-c"]\n')
    assert "local.just" in refused(run, named(run.cfg, name), before)
    assert not (run.repo_root / "local.just").exists()


@pytest.mark.parametrize("roster, name", pairs("adws/yaml.py", "adws/yaml/__init__.py"))
def test_a_module_beside_the_adws_is_deleted(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    write(run, "adws/yaml.py", "x = 1\n")
    write(run, "adws/yaml/__init__.py", "x = 1\n")
    message = refused(run, named(run.cfg, name), before)
    assert "adws/yaml.py" in message and "adws/yaml/__init__.py" in message
    assert not (run.repo_root / "adws/yaml.py").exists()
    assert not (run.repo_root / "adws/yaml/__init__.py").exists()


def test_the_reviewer_may_still_write_its_session():
    cfg = load(PI)
    assert permissions.permitted("adws/adw_data/sessions/x/y.md", named(cfg, "reviewer"), cfg)


HARNESS_PATHS = [".claude/agents/x.md", ".claude/hooks/x.sh", ".claude/skills/other/SKILL.md",
                 ".pi/extensions/x.ts", ".pi/settings.json", ".mcp.json"]


@pytest.mark.parametrize("roster, name", pairs(*HARNESS_PATHS))
def test_a_new_harness_file_is_refused(tmp_path, monkeypatch, roster, name):
    run = make(tmp_path, monkeypatch, roster)
    before = permissions.snapshot(run)
    for path in HARNESS_PATHS:
        write(run, path, "x\n")
    message = refused(run, named(run.cfg, name), before)
    for path in HARNESS_PATHS:
        assert path in message
        assert not (run.repo_root / path).exists()


# ── case 9: data_dir written another way ──

def with_data_dir(cfg, data_dir: str):
    return cfg.model_copy(update={"defaults": cfg.defaults.model_copy(update={"data_dir": data_dir})})


@pytest.mark.parametrize("data_dir, data", [("./data", "data"), ("data//", "data"),
                                            ("./adws/adw_data", "adws/adw_data"),
                                            ("adws//adw_data/", "adws/adw_data")])
def test_a_data_dir_written_oddly_still_protects_its_prompts(data_dir, data):
    cfg = with_data_dir(load(PI), data_dir)
    assert f"{data}/prompt_engineering/" in permissions.factory_files(cfg)
    reviewer = named(cfg, "reviewer")
    assert not permissions.permitted(f"{data}/prompt_engineering/reviewer/system.md", reviewer, cfg)
    assert permissions.permitted(f"{data}/sessions/x.md", reviewer, cfg)


# ── case 10: the db grant ──

@pytest.mark.parametrize("db", ["adws/adw_data/", "adws/adw_data/**", "adws/adw_data/*.db",
                                "adws/adw_data"])
def test_a_db_that_is_a_folder_or_glob_gets_no_grant(db):
    cfg = load(PI)
    cfg = cfg.model_copy(update={"observability": cfg.observability.model_copy(update={"db": db})})
    assert permissions.always_writable(cfg) == [f"adws/adw_data/{p}" for p in permissions.RUNTIME]
    assert not permissions.permitted(PROMPT, named(cfg, "reviewer"), cfg)


def test_a_dotted_db_path_is_granted_and_the_default_still_is():
    base = load(PI)
    cfg = base.model_copy(update={"observability": base.observability.model_copy(
        update={"db": "./adws/adw_data/sssf.db"})})
    reviewer = named(cfg, "reviewer")
    assert permissions.permitted("adws/adw_data/sssf.db", reviewer, cfg)
    assert permissions.permitted("adws/adw_data/sssf.db-wal", reviewer, cfg)
    assert not permissions.permitted(PROMPT, reviewer, cfg)
    default = named(base, "reviewer")
    assert permissions.permitted("adws/adw_data/sssf.db", default, base)


def test_a_db_pinned_absolute_under_data_root_is_granted():
    base = load(PI)
    cfg = base.model_copy(update={
        "defaults": base.defaults.model_copy(update={"data_root": "/work/repo/adws/adw_data"}),
        "observability": base.observability.model_copy(
            update={"db": "/work/repo/adws/adw_data/sssf.db"})})
    reviewer = named(cfg, "reviewer")
    assert permissions.permitted("adws/adw_data/sssf.db-wal", reviewer, cfg)
    assert not permissions.permitted(PROMPT, reviewer, cfg)
    outside = cfg.model_copy(update={"observability": cfg.observability.model_copy(
        update={"db": "/elsewhere/sssf.db"})})
    assert not permissions.permitted("adws/adw_data/sssf.db-wal", named(outside, "reviewer"), outside)


# ── normal work is still allowed ──

def test_normal_work_is_not_refused(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    cache = ["adws/adw_modules/__pycache__/x.pyc", "src/__pycache__/app.cpython-312.pyc"]

    before = permissions.snapshot(run)
    write(run, "src/app.py", "x = 1\n")
    write(run, "tests/test_app.py", "def test(): pass\n")
    for path in cache:
        write(run, path, "pyc")
    assert permissions.enforce(run, None, named(run.cfg, "builder"), before) == [
        "src/app.py", "tests/test_app.py"]

    git("add", "-A")
    git("commit", "-qm", "build")
    before = permissions.snapshot(run)
    write(run, "specs/abc_x.md", "spec\n")
    assert permissions.enforce(run, None, named(run.cfg, "planner"), before) == ["specs/abc_x.md"]

    git("add", "-A")
    git("commit", "-qm", "plan")
    before = permissions.snapshot(run)
    write(run, "docs/guide.md", "guide, edited\n")
    assert permissions.enforce(run, None, named(run.cfg, "documenter"), before) == ["docs/guide.md"]


# ── the snapshot's cost grows with what changed ──

class Counting(sandbox.Local):
    name = "counting"

    def __init__(self):
        self.hashed: list[list[str]] = []

    def run(self, command, **kwargs):
        if isinstance(command, list) and command[:3] == ["sh", "-c", permissions._CONTENT]:
            self.hashed.append(command[4:])
        return super().run(command, **kwargs)


def test_only_changed_and_untracked_paths_are_fingerprinted(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    for i in range(200):
        write(run, f"bulk/f{i}.txt", f"{i}\n")
    git("add", "-A")
    git("commit", "-qm", "bulk")
    write(run, "bulk/f1.txt", "changed\n")
    write(run, "bulk/f2.txt", "changed\n")
    write(run, "bulk/new.txt", "new\n")
    box = Counting()
    monkeypatch.setattr(sandbox, "_current", box)
    result = permissions.snapshot(run)
    assert sorted(result) == ["bulk/f1.txt", "bulk/f2.txt", "bulk/new.txt"]
    assert sorted(p for chunk in box.hashed for p in chunk) == sorted(result)


# ── a change staged only in the index ──

NEW_PATH = "src/fixtures/staged_new.txt"
STAGE_PATHS = [pytest.param("justfile", "planner", id="protected"),
               pytest.param("src/fixtures/system.md", "reviewer", id="outside-writes")]


def blob(text: str) -> str:
    done = subprocess.run(["git", "hash-object", "-w", "--stdin"], input=text,
                          check=True, capture_output=True, text=True)
    return done.stdout.strip()


def route_add_then_restore(run, path):
    original = (run.repo_root / path).read_text()
    (run.repo_root / path).write_text("evil\n")
    git("add", path)
    (run.repo_root / path).write_text(original)


def route_cacheinfo(run, path):
    git("update-index", "--cacheinfo", f"100644,{blob('evil' + chr(10))},{path}")


def route_chmod(run, path):
    git("update-index", "--chmod=+x", path)


def route_rm_cached(run, path):
    git("rm", "-q", "--cached", path)


@pytest.mark.parametrize("route", [route_add_then_restore, route_cacheinfo, route_chmod,
                                   route_rm_cached])
@pytest.mark.parametrize("path, who", STAGE_PATHS)
def test_a_change_staged_only_in_the_index_is_seen_and_put_back(
        tmp_path, monkeypatch, route, path, who):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    route(run, path)
    assert "staged-" in permissions.snapshot(run)[path]
    assert path in refused(run, named(run.cfg, who), before)
    subprocess.run(["git", "diff", "--cached", "HEAD", "--quiet", "--", path], check=True)
    subprocess.run(["git", "diff", "HEAD", "--quiet", "--", path], check=True)


@pytest.mark.parametrize("path, who", STAGE_PATHS)
def test_a_new_path_staged_with_no_file_is_unstaged(tmp_path, monkeypatch, path, who):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    git("update-index", "--add", "--cacheinfo", f"100644,{blob('evil' + chr(10))},{NEW_PATH}")
    assert "staged-A-" in permissions.snapshot(run)[NEW_PATH]
    assert NEW_PATH in refused(run, named(run.cfg, "reviewer"), before)
    assert not in_index(NEW_PATH)


@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_a_staged_blob_behind_an_index_flag_is_seen_and_put_back(tmp_path, monkeypatch, flag):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    route_cacheinfo(run, "justfile")
    git("update-index", flag, "justfile")
    git("update-index", "--add", "--cacheinfo", f"100644,{blob('evil' + chr(10))},{NEW_PATH}")
    git("update-index", flag, NEW_PATH)
    message = refused(run, named(run.cfg, "planner"), before)
    assert "justfile" in message and NEW_PATH in message
    assert git("ls-files", "-v", "justfile").startswith("H")
    assert not in_index(NEW_PATH)
    subprocess.run(["git", "diff", "--cached", "HEAD", "--quiet"], check=True)


def test_a_staged_entry_of_the_engineers_survives_a_call_that_writes_elsewhere(
        tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    route_cacheinfo(run, "justfile")
    git("update-index", "--skip-worktree", "justfile")
    staged = git("ls-files", "-s", "-v", "justfile")
    before = permissions.snapshot(run)
    assert "staged-" in before["justfile"]
    write(run, "docs/guide.md", "changed\n")
    assert permissions.enforce(run, None, named(run.cfg, "documenter"), before) == ["docs/guide.md"]
    assert git("ls-files", "-s", "-v", "justfile") == staged
    write(run, "justfile", "evil\n")
    git("update-index", "--no-skip-worktree", "justfile")
    message = refused(run, named(run.cfg, "planner"), before)
    assert "justfile" in message and "left as-is" in message


def test_a_hidden_staged_entry_that_goes_away_is_no_finding(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    route_cacheinfo(run, "justfile")
    before = permissions.snapshot(run)
    git("reset", "-q")
    assert permissions.enforce(run, None, named(run.cfg, "planner"), before) == []


def test_a_changed_staged_entry_on_a_dirty_file_is_named_and_left(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, "justfile", "dirty\n")
    before = permissions.snapshot(run)
    route_cacheinfo(run, "justfile")
    message = refused(run, named(run.cfg, "planner"), before)
    assert "left as-is" in message and "git restore --staged -- justfile" in message
    assert (run.repo_root / "justfile").read_text() == "dirty\n"


def test_staging_its_own_write_is_not_a_finding(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    write(run, "docs/guide.md", "changed\n")
    write(run, "docs/new.md", "new\n")
    git("add", "-A")
    assert permissions.enforce(run, None, named(run.cfg, "documenter"), before) == [
        "docs/guide.md", "docs/new.md"]
    assert not any("staged-" in v for v in permissions.snapshot(run).values())


def test_git_add_over_an_engineers_dirty_protected_file_is_no_finding(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, "justfile", "dirty\n")
    before = permissions.snapshot(run)
    git("add", "-A")
    assert permissions.enforce(run, None, named(run.cfg, "planner"), before) == []


def test_a_staged_symlink_and_executable_that_match_the_work_tree_are_not_recorded(
        tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    (run.repo_root / "docs/link").symlink_to("guide.md")
    write(run, "docs/run.sh", "#!/bin/sh\n")
    (run.repo_root / "docs/run.sh").chmod(0o755)
    git("add", "-A")
    snap = permissions.snapshot(run)
    assert not any("staged-" in v for v in snap.values())


def test_a_staged_symlink_with_another_target_is_recorded(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    (run.repo_root / "docs/link").symlink_to("guide.md")
    git("add", "docs/link")
    (run.repo_root / "docs/link").unlink()
    (run.repo_root / "docs/link").symlink_to("other")
    assert "staged-A-120000" in permissions.snapshot(run)["docs/link"]


def test_a_deleted_staged_blob_is_still_seen_and_put_back(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    ident = blob("evil\n")
    git("update-index", "--cacheinfo", f"100644,{ident},justfile")
    (run.repo_root / ".git/objects" / ident[:2] / ident[2:]).unlink()
    assert "justfile" in refused(run, named(run.cfg, "planner"), before)
    subprocess.run(["git", "diff", "--cached", "HEAD", "--quiet"], check=True)


def test_nothing_staged_runs_no_exec_script(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, "docs/guide.md", "changed\n")
    seen = []

    class Watching(sandbox.Local):
        name = "watching"

        def run(self, command, **kwargs):
            seen.append(command)
            return super().run(command, **kwargs)
    monkeypatch.setattr(sandbox, "_current", Watching())
    permissions.snapshot(run)
    assert not any(isinstance(c, list) and permissions._EXEC in c for c in seen)


# ── review findings: nested repo, fileMode, symlinks ──

def test_a_flagged_staged_file_in_an_untracked_nested_repo_is_never_deleted(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, "tools/a.txt", "mine\n")
    subprocess.run(["git", "init", "-q", "tools"], check=True)
    before = permissions.snapshot(run)
    git("update-index", "--add", "--cacheinfo", f"100644,{blob('evil' + chr(10))},tools/a.txt")
    git("update-index", "--skip-worktree", "tools/a.txt")
    refused(run, named(run.cfg, "planner"), before)
    assert (run.repo_root / "tools/a.txt").read_text() == "mine\n"


def test_a_kept_staged_mode_under_file_mode_false_is_no_finding(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    git("config", "core.fileMode", "false")
    git("update-index", "--chmod=+x", "docs/guide.md")
    write(run, "docs/guide.md", "edited\n")
    before = permissions.snapshot(run)
    git("add", "-A")
    assert permissions.enforce(run, None, named(run.cfg, "planner"), before) == []


def test_a_link_stored_as_a_plain_file_under_symlinks_false_is_no_finding(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    (run.repo_root / "docs/link").symlink_to("guide.md")
    git("add", "docs/link")
    git("commit", "-qm", "link")
    git("config", "core.symlinks", "false")
    (run.repo_root / "docs/link").unlink()
    write(run, "docs/link", "edited\n")
    before = permissions.snapshot(run)
    git("add", "-A")
    assert permissions.enforce(run, None, named(run.cfg, "planner"), before) == []


# ── an index SSSF rebuilds, and what it changes (guard run ea9dc12f) ──

def staged_tokens(result: dict[str, str]) -> dict[str, str]:
    return {p: permissions._staged_token(v) for p, v in result.items()
            if permissions._staged_token(v)}


def record_built(monkeypatch) -> list[str]:
    """Every index SSSF built, so a test can see its folder is gone."""
    made: list[str] = []
    real = permissions._built_index

    def wrapped(root, argv):
        path = real(root, argv)
        made.append(path)
        return path
    monkeypatch.setattr(permissions, "_built_index", wrapped)
    return made


@pytest.mark.parametrize("how", ["fresh_worktree", "read_tree", "no_cache_tree"])
def test_an_unforged_index_gives_no_false_stop_and_no_leftover_folder(tmp_path, monkeypatch, how):
    base = tmp_path / "repo"
    base.mkdir()
    run = make(base, monkeypatch, PI)
    if how == "fresh_worktree":
        git("worktree", "add", "-q", str(tmp_path / "wt"))
        run = SimpleNamespace(cfg=run.cfg, repo_root=tmp_path / "wt")
    elif how == "read_tree":
        git("read-tree", "HEAD")
    else:
        for line in git("ls-files", "-s").splitlines():
            mode, ident, _, name = line.replace("\t", " ").split(" ", 3)
            git("update-index", "--cacheinfo", f"{mode},{ident},{name}")
    made = record_built(monkeypatch)
    assert permissions.snapshot(run) == {}
    assert len(made) == 1 and not os.path.exists(os.path.dirname(made[0]))


def test_an_intent_to_add_entry_reads_as_a_staged_empty_file_and_is_stable(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    write(run, "src/new.txt", "x\n")
    git("add", "-N", "src/new.txt")
    first = permissions.snapshot(run)
    empty = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert staged_tokens(first) == {"src/new.txt": f"staged-A-100644-{empty}"}
    # A call that doesn't touch it leaves the value as it was.
    assert permissions.snapshot(run, first) == first
    # The agent's `git add -A` stages the real bytes: no token left.
    git("add", "-A")
    assert staged_tokens(permissions.snapshot(run, first)) == {}


def test_an_agents_own_intent_to_add_on_a_new_path_is_still_a_change(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    before = permissions.snapshot(run)
    write(run, "src/agent.txt", "x\n")
    git("add", "-N", "src/agent.txt")
    after = permissions.snapshot(run, before)
    assert permissions.changed_paths(before, after) == ["src/agent.txt"]
    assert after["src/agent.txt"].startswith("A")


class Failing(sandbox.Local):
    """Fails the `_INDEX` run, or the cached diff, and records every command."""
    name = "failing"

    def __init__(self, *, index=False, diff=False):
        self.index, self.diff, self.commands = index, diff, []

    def run(self, command, **kwargs):
        self.commands.append(list(command))
        if self.index and command[:3] == ["python3", "-I", "-c"] and command[3] == permissions._INDEX:
            return subprocess.CompletedProcess(command, 1, b"", b"no python3")
        if self.diff and "--cached" in command:
            return subprocess.CompletedProcess(command, 128, b"", b"fatal: broken")
        return super().run(command, **kwargs)


def test_a_failed_index_rebuild_stops_the_snapshot(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    monkeypatch.setattr(sandbox, "_current", Failing(index=True))
    with pytest.raises(permissions.GitFailed, match="index rebuild"):
        permissions.snapshot(run)


def test_a_failed_cached_diff_still_removes_the_built_folder(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    made = record_built(monkeypatch)
    monkeypatch.setattr(sandbox, "_current", Failing(diff=True))
    with pytest.raises(permissions.GitFailed):
        permissions.snapshot(run)
    assert len(made) == 1 and not os.path.exists(os.path.dirname(made[0]))


def test_only_the_cached_diff_reads_the_built_index(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    box = Failing()
    monkeypatch.setattr(sandbox, "_current", box)
    permissions.snapshot(run)
    git_runs = [c for c in box.commands if "git" in c and c[0] == "env"]
    with_index = [c for c in git_runs if any(a.startswith("GIT_INDEX_FILE=") for a in c)]
    assert len(with_index) == 1 and "--cached" in with_index[0]
    assert [c for c in git_runs if "--cached" in c] == with_index


def link_repo(tmp_path, monkeypatch):
    run = make(tmp_path, monkeypatch, PI)
    os.symlink("target.txt", tmp_path / "lnk")
    write(run, "target.txt", "t\n")
    git("add", "-A")
    git("commit", "-qm", "link")
    os.remove(tmp_path / "lnk")
    write(run, "lnk", "other\n")
    return run


def test_a_link_file_the_engineer_edited_is_not_hidden_when_symlinks_are_off(tmp_path, monkeypatch):
    run = link_repo(tmp_path, monkeypatch)
    git("config", "core.symlinks", "false")
    before = permissions.snapshot(run)
    git("add", "-A")
    assert staged_tokens(permissions.snapshot(run, before)) == {}


@pytest.mark.parametrize("setting", ["true", "unset"])
def test_a_staged_link_over_the_engineers_plain_file_is_named_when_symlinks_are_on(
        tmp_path, monkeypatch, setting):
    run = link_repo(tmp_path, monkeypatch)
    if setting == "true":
        git("config", "core.symlinks", "true")
    before = permissions.snapshot(run)
    blob = git("hash-object", "-w", "lnk")
    git("update-index", "--cacheinfo", f"120000,{blob},lnk")
    after = permissions.snapshot(run, before)
    assert staged_tokens(after).get("lnk", "").endswith(f"-120000-{blob}")

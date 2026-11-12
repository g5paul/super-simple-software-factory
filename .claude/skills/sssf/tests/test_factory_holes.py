"""The holes the factory-files guard left, each closed.

Every test builds its config from the starter rosters SSSF ships and runs the
guard on a temp git repo through `snapshot` and `enforce`. No agent is started.
The per-agent tests run every agent of the pi and Claude rosters whose `writes`
doesn't name the path. `.git/info/exclude` is empty in every repo.

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
        assert permissions.snapshot(run)[path].startswith("D,?:")
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
    assert permissions.snapshot(run)["justfile"].startswith("D,?:")
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

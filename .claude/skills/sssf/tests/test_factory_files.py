"""SSSF's own files stay off-limits to agents, whatever the roster says.

The prompts and extensions under data_dir, the skill, the recipes, the hooks,
Claude Code's settings and the ADW code are SSSF's fixed `FACTORY_FILES`. Each
is refused after every call unless the agent names it in its own `writes`.
Configs come from the three starter rosters SSSF ships.

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

from adw_modules import agents, permissions

ROSTERS = ["sssf.config.yaml", "sssf.config.claude_code.yaml", "sssf.config.chatgpt.yaml"]
FIXED_SAMPLES = [
    "adws/adw_data/prompt_engineering/reviewer/system.md",
    "adws/adw_data/harness_engineering/subagents.ts",
    ".claude/skills/sssf/cookbooks/run_adw.md",
    "justfile", "sssf.just", ".githooks/pre-push",
    ".claude/settings.json", ".claude/settings.local.json",
    "adws/adw_modules/rules.py", "adws/adw_sssf_config/sssf.config.yaml", "adws/adw_tickets.py",
]
READ_ONLY = ["reviewer", "scout", "standards_reviewer", "spec_reviewer"]


def load(roster: str):
    return agents.load_config(str(SKILL / "templates" / roster))


def cases():
    for roster in ROSTERS:
        cfg = load(roster)
        for agent in cfg.agents:
            for sample in FIXED_SAMPLES:
                yield pytest.param(roster, agent.name, sample, id=f"{roster}-{agent.name}-{sample}")


def named(cfg, name: str):
    return next(a for a in cfg.agents if a.name == name)


def with_writes(cfg, name: str, writes):
    return named(cfg, name).model_copy(update={"writes": writes})


def with_db(cfg, db: str):
    return cfg.model_copy(update={"observability": cfg.observability.model_copy(update={"db": db})})


@pytest.mark.parametrize("roster", ROSTERS)
def test_every_fixed_entry_has_a_sample(roster):
    cfg = load(roster)
    for entry in permissions.factory_files(cfg):
        assert any(permissions._matches(s, entry) for s in FIXED_SAMPLES), entry


@pytest.mark.parametrize("roster, agent, sample", cases())
def test_every_fixed_path_is_refused_to_every_agent(roster, agent, sample):
    cfg = load(roster)
    assert not permissions.permitted(sample, named(cfg, agent), cfg)


def test_naming_an_entry_unlocks_it():
    cfg = load(ROSTERS[0])
    builder = with_writes(cfg, "builder", ["justfile"])
    assert permissions.permitted("justfile", builder, cfg)
    assert not permissions.permitted("sssf.just", builder, cfg)
    skill = with_writes(cfg, "builder", [".claude/skills/sssf/cookbooks/"])
    assert permissions.permitted(".claude/skills/sssf/cookbooks/run_adw.md", skill, cfg)
    assert not permissions.permitted(".claude/skills/sssf/SKILL.md", skill, cfg)
    prompts = with_writes(cfg, "builder", ["adws/adw_data/prompt_engineering/"])
    assert permissions.permitted("adws/adw_data/prompt_engineering/reviewer/system.md", prompts, cfg)


def test_a_roster_entry_inside_a_fixed_folder_stays_refused():
    cfg = load(ROSTERS[0])
    entry = ".claude/skills/sssf/SKILL.md"
    cfg = cfg.model_copy(update={"defaults": cfg.defaults.model_copy(
        update={"protected_files": [*cfg.defaults.protected_files, entry]})})
    agent = with_writes(cfg, "builder", [".claude/skills/sssf/"])
    assert not permissions.permitted(entry, agent, cfg)
    assert permissions.permitted(".claude/skills/sssf/cookbooks/run_adw.md", agent, cfg)


@pytest.mark.parametrize("roster", ROSTERS)
def test_every_agent_may_still_write_the_runtime(roster):
    cfg = load(roster)
    for agent in cfg.agents:
        for path in ("adws/adw_data/sessions/abc/context_handoff/review.md",
                     "adws/adw_data/agent-cache/uv/x", "adws/adw_data/sssf.db-wal"):
            assert permissions.permitted(path, agent, cfg), (agent.name, path)


def test_a_db_outside_data_dir_gets_no_grant():
    cfg = with_db(load(ROSTERS[0]), "sssf.db")
    reviewer = named(cfg, "reviewer")
    assert reviewer.writes == []
    assert not permissions.permitted("sssf.db", reviewer, cfg)
    assert not permissions.permitted("sssf.db-wal", reviewer, cfg)
    assert all(g.startswith("adws/adw_data/") for g in permissions.always_writable(cfg))
    default = load(ROSTERS[0])
    assert all(g.startswith("adws/adw_data/") for g in permissions.always_writable(default))
    absolute = with_db(default, "/tmp/x/sssf.db")
    assert not any("sssf.db" in g for g in permissions.always_writable(absolute))


def test_normal_work_is_still_allowed():
    cfg = load(ROSTERS[0])
    assert permissions.permitted("specs/abc_x.md", named(cfg, "planner"), cfg)
    docs = named(cfg, "documenter")
    for path in ("README.md", "docs/guide.md", "app_docs/abc_x.md", "src/pkg/NOTES.md"):
        assert permissions.permitted(path, docs, cfg), path
    builder = named(cfg, "builder")
    for path in ("src/app.py", "tests/test_app.py"):
        assert permissions.permitted(path, builder, cfg), path


@pytest.mark.parametrize("protected", [["specs/"], []])
def test_a_roster_cannot_drop_the_fixed_list(protected):
    base = load(ROSTERS[0])
    cfg = base.model_copy(update={"defaults": base.defaults.model_copy(
        update={"protected_files": protected})})
    builder = named(cfg, "builder")
    for sample in FIXED_SAMPLES:
        assert not permissions.permitted(sample, builder, cfg), sample


def test_protected_lists_the_fixed_files_first_and_each_once():
    cfg = load(ROSTERS[0])
    shown = permissions.protected(cfg)
    assert shown[:len(permissions.factory_files(cfg))] == permissions.factory_files(cfg)
    assert len(shown) == len(set(shown))


def test_the_constants_are_tuples():
    for const in (permissions.FACTORY_DATA, permissions.FACTORY_FILES,
                  permissions.RUNTIME, permissions._DB_SIBLINGS):
        assert isinstance(const, tuple)


# ── the confirmed repro, through enforce ──

def git(*args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    (tmp_path / ".gitignore").write_text("adws/adw_data/sessions/\n")
    prompt = tmp_path / "adws/adw_data/prompt_engineering/reviewer/system.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("committed\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return SimpleNamespace(cfg=load(ROSTERS[0]), repo_root=tmp_path)


def test_a_builder_edit_to_a_prompt_is_rolled_back(tree):
    builder = named(tree.cfg, "builder")
    path = "adws/adw_data/prompt_engineering/reviewer/system.md"
    before = permissions.snapshot(tree)
    (tree.repo_root / path).write_text("hijacked\n")
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(tree, None, builder, before)
    assert path in str(breach.value)
    assert (tree.repo_root / path).read_text() == "committed\n"


def test_a_new_file_in_the_skill_is_deleted(tree):
    builder = named(tree.cfg, "builder")
    path = ".claude/skills/sssf/x.md"
    before = permissions.snapshot(tree)
    (tree.repo_root / path).parent.mkdir(parents=True)
    (tree.repo_root / path).write_text("new\n")
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(tree, None, builder, before)
    assert path in str(breach.value)
    assert not (tree.repo_root / path).exists()


def test_the_runtime_is_kept_even_when_not_gitignored(tree):
    (tree.repo_root / ".gitignore").write_text("")
    git("commit", "-qam", "unignore")
    reviewer = named(tree.cfg, "reviewer")
    path = "adws/adw_data/sessions/abc/x.md"
    before = permissions.snapshot(tree)
    (tree.repo_root / path).parent.mkdir(parents=True)
    (tree.repo_root / path).write_text("report\n")
    assert permissions.enforce(tree, None, reviewer, before) == [path]
    assert (tree.repo_root / path).exists()

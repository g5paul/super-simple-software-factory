"""The hub's feature agents stay out of the guard code SSSF ships; only guard_builder reaches it.

Every test loads the hub's own config, adws/adw_sssf_config/sssf.config.yaml, because that
file is what is under test. No test starts an agent.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "adws"))

import adw_guard_change as gc  # noqa: E402
from adw_modules import agents, permissions  # noqa: E402

SKILL = ".claude/skills/sssf/"
MODULES = SKILL + "templates/adws/adw_modules/"
TESTS = SKILL + "tests/"
FOLDER = TESTS + "fixtures/agentsh-first-real-runs/"

LISTED = [
    MODULES + "rules.py",
    MODULES + "sssf_refuse.py",
    MODULES + "sssf_rules.ts",
    MODULES + "secret_files.py",
    MODULES + "permissions.py",
    MODULES + "agent_cc.py",
    MODULES + "agent_pi.py",
    MODULES + "gates.py",
    MODULES + "sandbox_openshell.py",
    MODULES + "agentsh_guard.py",
    MODULES + "agentsh_sssf.yaml",
    SKILL + "scripts/agentsh_install.py",
    TESTS + "test_rules.py",
    TESTS + "test_rules_continuation.py",
    TESTS + "test_rules_heredoc.py",
    TESTS + "test_rules_multiline.py",
    TESTS + "test_rules_portable.py",
    TESTS + "test_rules_tab_heredoc.py",
    TESTS + "test_secret_files.py",
    TESTS + "test_git_dir_guard.py",
    TESTS + "test_factory_files.py",
    TESTS + "test_factory_holes.py",
    TESTS + "test_agent_commits.py",
    TESTS + "test_agent_cc.py",
    TESTS + "test_agent_pi.py",
    TESTS + "test_plan_gate.py",
    TESTS + "test_sandbox_openshell.py",
    TESTS + "test_agentsh_guard.py",
    TESTS + "fixtures/cc_turn1.jsonl",
    TESTS + "fixtures/cc_turn2.jsonl",
    FOLDER,
]

# A concrete path to try for each entry; the folder gets a file that exists and a new one.
PATHS = [p for p in LISTED if not p.endswith("/")] + [FOLDER + "execve-denial-event.json",
                                                       FOLDER + "brand-new.json"]

CFG = agents.load_config(str(ROOT / "adws/adw_sssf_config/sssf.config.yaml"))
BY_NAME = {a.name: a for a in CFG.agents}
OTHERS = [a.name for a in CFG.agents if a.name != "guard_builder"]


def agent(name: str):
    return BY_NAME[name]


def ok(path: str, name: str) -> bool:
    return permissions.permitted(path, agent(name), CFG)


def test_the_list_has_31_entries_and_each_exists_in_the_hub():
    assert len(LISTED) == 31 and len(set(LISTED)) == 31
    for entry in LISTED:
        assert (ROOT / entry).exists(), entry


def test_every_listed_entry_is_protected_and_the_guard_modules_are_listed():
    for entry in LISTED:
        assert entry in CFG.defaults.protected_files, entry
    for name in gc.GUARD_MODULES:
        assert f"{gc.GUARD_DIR}/{name}" in LISTED


def test_the_guard_builder_is_the_builder_plus_the_listed_entries():
    shipped = [p for p in CFG.defaults.protected_files if p.startswith(SKILL)]
    assert shipped == LISTED
    builder, guard = agent("builder"), agent("guard_builder")
    assert guard.writes == builder.writes + shipped
    assert guard.model == builder.model and guard.tools == builder.tools
    assert guard.prompt_engineering == builder.prompt_engineering
    assert guard.max_call_cost == builder.max_call_cost


@pytest.mark.parametrize("name", OTHERS)
@pytest.mark.parametrize("path", PATHS)
def test_every_agent_but_the_guard_builder_is_refused_on_a_listed_path(name, path):
    assert not ok(path, name)


@pytest.mark.parametrize("path", PATHS)
def test_the_guard_builder_may_change_every_listed_path(path):
    assert ok(path, "guard_builder")


@pytest.mark.parametrize("name", ["builder", "ticket_builder"])
@pytest.mark.parametrize("path", [
    MODULES + "worktree.py", MODULES + "agents.py", TESTS + "test_worktree.py",
    TESTS + "test_budget.py", TESTS + "conftest.py", TESTS + "fixtures/cc_turn3.jsonl",
    SKILL + "SKILL.md", "docs/sssf.md", "README.md"])
def test_a_feature_builder_still_changes_the_skill_off_the_list_and_docs(name, path):
    assert ok(path, name)


def test_the_planner_still_writes_specs_and_the_documenter_the_docs():
    assert ok("specs/x.md", "planner")
    assert ok("docs/sssf.md", "documenter")
    assert ok(SKILL + "references/config.md", "documenter")


@pytest.mark.parametrize("name", ["guard_builder", "builder"])
@pytest.mark.parametrize("path", [
    "adws/adw_guard_change.py", "adws/adw_sssf_config/sssf.config.yaml",
    "adws/adw_modules/permissions.py", "adws/tests/test_guard_paths.py",
    ".claude/agents/bypass-reviewer.md", ".claude/settings.json", "CLAUDE.md", "specs/x.md",
    "justfile", ".githooks/pre-push"])
def test_the_rest_of_the_protected_paths_stay_refused(name, path):
    assert not ok(path, name)


# ── enforce, in a temp git repo ─────────────────────────────────────────────

EDITED = [MODULES + "permissions.py", TESTS + "test_rules.py", TESTS + "fixtures/cc_turn1.jsonl",
          FOLDER + "execve-denial-event.json"]
NEW = FOLDER + "brand-new.json"
OFF = MODULES + "worktree.py"


def git(*args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   check=True, capture_output=True, text=True)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    git("init", "-qb", "main")
    for path in EDITED + [OFF]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("committed\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    return SimpleNamespace(cfg=CFG, repo_root=tmp_path)


def edit(tree, paths, text="hijacked\n"):
    for path in paths:
        (tree.repo_root / path).write_text(text)


@pytest.mark.parametrize("name", ["builder", "ticket_builder", "documenter"])
def test_enforce_rolls_back_every_listed_change(tree, name):
    before = permissions.snapshot(tree)
    edit(tree, EDITED)
    (tree.repo_root / NEW).write_text("new\n")
    builders = name != "documenter"
    if builders:
        edit(tree, [OFF])
    with pytest.raises(permissions.PermissionBreach) as breach:
        permissions.enforce(tree, None, agent(name), before)
    for path in EDITED:
        assert (tree.repo_root / path).read_text() == "committed\n", path
    assert not (tree.repo_root / NEW).exists()
    if builders:
        assert (tree.repo_root / OFF).read_text() == "hijacked\n"
        assert OFF in str(breach.value)


def test_enforce_lets_the_guard_builder_change_them_all(tree):
    before = permissions.snapshot(tree)
    edit(tree, EDITED)
    (tree.repo_root / NEW).write_text("new\n")
    assert sorted(permissions.enforce(tree, None, agent("guard_builder"), before)) \
        == sorted(EDITED + [NEW])


def test_enforce_returns_a_builder_edit_to_the_off_list_files(tree):
    before = permissions.snapshot(tree)
    off_test = TESTS + "test_worktree.py"
    (tree.repo_root / off_test).write_text("x\n")
    edit(tree, [OFF])
    assert sorted(permissions.enforce(tree, None, agent("builder"), before)) \
        == sorted([OFF, off_test])


def test_a_path_dirty_before_the_call_is_not_checked_out_over(tree):
    path = MODULES + "permissions.py"
    edit(tree, [path], "first\n")
    before = permissions.snapshot(tree)
    edit(tree, [path], "second\n")
    with pytest.raises(permissions.PermissionBreach):
        permissions.enforce(tree, None, agent("builder"), before)
    assert (tree.repo_root / path).read_text() == "second\n"

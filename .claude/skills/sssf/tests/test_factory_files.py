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
    "local.just", "adws/yaml.py", "adws/yaml/__init__.py",
    ".claude/agents/bypass-reviewer.md", ".claude/hooks/x.sh", ".claude/skills/other/SKILL.md",
    ".pi/extensions/x.ts", ".pi/settings.json", ".mcp.json",
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
    for entry, path in (("local.just", "local.just"), (".pi/", ".pi/extensions/x.ts"),
                        (".mcp.json", ".mcp.json"), ("adws/yaml.py", "adws/yaml.py"),
                        (".claude/skills/other/", ".claude/skills/other/SKILL.md")):
        agent = with_writes(cfg, "builder", [entry])
        assert permissions.permitted(path, agent, cfg), entry
    other = with_writes(cfg, "builder", [".claude/skills/other/"])
    assert not permissions.permitted(".claude/skills/sssf/SKILL.md", other, cfg)


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


# --- protected paths are matched in every spelling a checkout takes for them ---

SPELLED = [
    "JUSTFILE", "ADWS/adw_x.py", ".CLAUDE/settings.json", "SPECS/old.md", "glossary.md",
    "juſtfile", ".githooKs/pre-commit", ".pı/x.json",
    "just‌file", "just­file", "justfile.", "justfile ", "adws./x.py", "adws /x.py",
    "justfile::$DATA", "adws::$INDEX_ALLOCATION/x.py", "adws\\x.py",
    "CLAUDE~1/settings.json", "PI~1/x.json", "MCP~1.JSO", "GITHOO~1/pre-commit",
    "SSSF~1.JUS", "LOCAL~1.JUS", "CL1A2B~1/settings.json", "JUSTFI~1",
]


@pytest.mark.parametrize("path", SPELLED)
def test_every_spelling_is_refused_for_the_builder_the_documenter_and_an_open_agent(path):
    cfg = load("sssf.config.yaml")
    for agent in (named(cfg, "builder"), named(cfg, "documenter"),
                  named(cfg, "builder").model_copy(update={"writes": None})):
        assert not permissions.permitted(path, agent, cfg), (agent.name, path)


def test_nfc_and_nfd_of_a_protected_entry_are_the_same():
    cfg = load("sssf.config.yaml")
    cfg = cfg.model_copy(deep=True)
    cfg.defaults.protected_files = [*cfg.defaults.protected_files, "docs/café/"]
    builder = named(cfg, "builder")
    assert not permissions.permitted("docs/café/x.md", builder, cfg)
    assert not permissions.permitted("docs/café/x.md", builder, cfg)


@pytest.mark.parametrize("path", ["src/JUSTFILE", "justfiles", "adwsx/a.py", "docs/just~1",
                                  "src/a~1.py", "notes~", "README.md", "src/app.py"])
def test_other_names_are_not_folded_into_a_protected_one(path):
    cfg = load("sssf.config.yaml")
    assert permissions.permitted(path, named(cfg, "builder"), cfg)


def _baseline_permitted(path, agent, cfg):
    """`permitted()` as it was: protected entries matched exactly."""
    m, n = permissions._matches, permissions._names
    if any(m(path, p) for p in permissions.always_writable(cfg)):
        return True
    writes = agent.writes or []
    fixed = [p for p in permissions.factory_files(cfg) if m(path, p) or m(path + "/", p)]
    if fixed and not any(m(path, w) and n(w, g) for w in writes for g in fixed):
        return False
    guards = [p for p in cfg.defaults.protected_files if m(path, p)]
    if guards:
        return any(m(path, w) and n(w, g) for w in writes for g in guards)
    if any(m(path, w) for w in writes):
        return True
    return agent.writes is None


def test_the_head_passes_nothing_the_baseline_refuses():
    ordinary = ["src/app.py", "README.md", "docs/a.md", "tests/t.py", "specs/new.md", "notes.txt"]
    exact = ["justfile", "adws/adw_x.py", ".claude/settings.json", "specs/old.md", "GLOSSARY.md",
             ".mcp.json", "sssf.just", "local.just", ".githooks/pre-commit", "docs/projects.md"]
    for roster in ROSTERS:
        cfg = load(roster)
        agents_ = list(cfg.agents) + [
            named(cfg, "builder").model_copy(update={"writes": None}),
            named(cfg, "builder").model_copy(update={"writes": ["**", "justfile", "adws/", "specs/"]})]
        for agent in agents_:
            for path in SPELLED + exact + ordinary:
                old, new = _baseline_permitted(path, agent, cfg), permissions.permitted(path, agent, cfg)
                assert not (new and not old), (roster, agent.name, path)
                if path in exact + ordinary:
                    assert new == old, (roster, agent.name, path)


def test_a_writes_entry_for_justfile_does_not_unlock_another_case():
    cfg = load("sssf.config.yaml")
    agent = named(cfg, "builder").model_copy(update={"writes": ["justfile"]})
    assert permissions.permitted("justfile", agent, cfg)
    assert not permissions.permitted("JUSTFILE", agent, cfg)


def test_the_ignored_set_holds_the_hfs_ones():
    assert permissions._HFS_IGNORED <= permissions._IGNORED


def test_the_fold_loses_nothing_against_plain_case_folding():
    import sys
    import unicodedata
    groups: dict[str, list[str]] = {}
    for cp in range(sys.maxunicode + 1):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        c = chr(cp)
        key = unicodedata.normalize("NFD", unicodedata.normalize("NFD", c).casefold())
        groups.setdefault(key, []).append(c)
    for members in groups.values():
        folded = {permissions._fold_part(c) for c in members} - {""}
        assert len(folded) <= 1, members


@pytest.mark.parametrize("path", ["docs/x~1/b.md", "docs/*./b.md", "docs/* /b.md", "docs/x/b.md"])
def test_a_folded_match_never_unlocks_through_a_glob_folder_entry(path):
    cfg = load("sssf.config.yaml").model_copy(deep=True)
    cfg.defaults.protected_files = [*cfg.defaults.protected_files, "docs/*/", "**/b.md"]
    agent = named(cfg, "builder").model_copy(update={"writes": ["docs/*/b.md"]})
    assert not permissions.permitted(path, agent, cfg)


# --- a component no disk takes is refused before any fold ----------------------

AT_CAP = {"ascii": "a" * 255, "two-byte": "é" * 255, "four-byte": "\U0001F600" * 255}
PAST_CAP = {k: v + v[-1] for k, v in AT_CAP.items()}


def watch_fold(monkeypatch):
    """Wrap `_fold_part`; the returned list holds the length of each part it was given."""
    seen: list[int] = []
    real = permissions._fold_part

    def wrapped(part):
        seen.append(max(len(piece) for piece in part.split(":")))
        return real(part)

    monkeypatch.setattr(permissions, "_fold_part", wrapped)
    return seen


@pytest.mark.parametrize("kind", AT_CAP)
def test_too_long_counts_code_points(kind):
    assert not permissions._too_long(AT_CAP[kind])
    assert permissions._too_long(PAST_CAP[kind])


@pytest.mark.parametrize("sep", ["\\", ":"])
def test_too_long_measures_each_piece_between_separators(sep):
    assert not permissions._too_long("a" * 200 + sep + "b" * 200)
    assert permissions._too_long("a" * 10 + sep + "b" * 256)


@pytest.mark.parametrize("kind", AT_CAP)
@pytest.mark.parametrize("who", ["builder", "planner", "documenter"])
def test_a_name_at_the_cap_gets_the_answer_of_one_shorter(kind, who):
    cfg = load("sssf.config.yaml")
    agent = named(cfg, who)
    name = AT_CAP[kind]
    for top in ("src", "specs"):
        assert (permissions.permitted(f"{top}/{name}", agent, cfg)
                == permissions.permitted(f"{top}/{name[:-1]}", agent, cfg))


@pytest.mark.parametrize("kind", PAST_CAP)
def test_a_name_past_the_cap_is_refused_for_every_agent(kind):
    cfg = load("sssf.config.yaml")
    name = PAST_CAP[kind]
    everyone = [*cfg.agents, named(cfg, "builder").model_copy(update={"writes": None}),
                named(cfg, "builder").model_copy(update={"writes": ["**", "src/", f"src/{name}"]})]
    for agent in everyone:
        for path in (f"src/{name}", f"specs/{name}", f"adws/adw_data/sessions/abc/{name}"):
            assert not permissions.permitted(path, agent, cfg)


@pytest.mark.parametrize("marks", [4000, 20000, 200000])
def test_a_long_mark_name_is_refused_without_a_fold(monkeypatch, marks):
    cfg = load("sssf.config.yaml")
    seen = watch_fold(monkeypatch)
    name = "src" + "́" * (marks // 2) + "̖" * (marks // 2) + ".py"
    assert not permissions.permitted(f"src/{name}", named(cfg, "builder"), cfg)
    assert seen == []


def test_a_colon_joined_path_is_not_over_long_and_folds_in_short_runs(monkeypatch):
    cfg = load("sssf.config.yaml")
    seen = watch_fold(monkeypatch)
    path = "src/x:" + ":".join(["́" * 255] * 80)
    assert not permissions._too_long(path)
    builder = named(cfg, "builder")
    assert permissions.permitted(path, builder, cfg) == permissions.permitted("src/x", builder, cfg)
    assert seen and max(seen) <= permissions.NAME_MAX


def test_forty_components_of_marks_at_the_cap_fold_in_pieces_under_it(monkeypatch):
    cfg = load("sssf.config.yaml")
    seen = watch_fold(monkeypatch)
    part = "x" + "́" * 127 + "̖" * 127
    assert len(part) == 255
    path = "src/" + "/".join([part] * 40)
    assert not permissions._too_long(path)
    permissions.permitted(path, named(cfg, "builder"), cfg)
    assert seen and max(seen) <= permissions.NAME_MAX

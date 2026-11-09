"""`install.py --update` brings a stamped repo up to the skill's current version
without losing what the repo customized.

The installer records the hash of every file it stamps in
adws/adw_sssf_config/stamp.json. An update replaces a file only while it still
matches that record, so a file the repo changed (the roster, quality.py, a
prompt) is kept and listed. Repos stamped before the record existed get one
exception: SSSF's own code (adw_modules/ except quality.py, and the starter
adw_*.py) is replaced, because a repo is not meant to edit it.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
STAMP = Path("adws/adw_sssf_config/stamp.json")

AGENTS = Path("adws/adw_modules/agents.py")
QUALITY = Path("adws/adw_modules/quality.py")
CONFIG = Path("adws/adw_sssf_config/sssf.config.yaml")
REVIEWER = Path("adws/adw_data/prompt_engineering/reviewer/user.md")


def install(*args: str) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), *args],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp() -> dict:
    return json.loads(STAMP.read_text())


def pretend_an_older_skill_stamped(path: Path) -> None:
    """Make `path` look like an untouched copy of an OLDER template."""
    path.write_text(path.read_text() + "\n# older template\n")
    record = stamp()
    record["files"][str(path)]["sha256"] = sha(path)
    STAMP.write_text(json.dumps(record))


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    install("--coding-agent", "claude_code")
    return tmp_path


def test_install_records_every_stamped_file(repo):
    files = stamp()["files"]
    assert files[str(AGENTS)]["sha256"] == sha(AGENTS)
    assert files[str(CONFIG)]["source"] == "sssf.config.claude_code.yaml"
    assert str(STAMP) not in files


def test_update_replaces_an_untouched_file(repo):
    pretend_an_older_skill_stamped(REVIEWER)
    out = install("--update").stdout
    assert REVIEWER.read_text() == (TEMPLATES / "prompt_engineering/reviewer/user.md").read_text()
    assert f"~ {REVIEWER}" in out


def customize_quality() -> None:
    QUALITY.write_text(QUALITY.read_text().replace('_placeholder("test")', '["make", "test"]'))


def test_update_keeps_a_file_the_repo_changed(repo):
    customize_quality()
    CONFIG.write_text(CONFIG.read_text() + "\n# tuned\n")
    install("--update")
    assert '["make", "test"]' in QUALITY.read_text()
    assert CONFIG.read_text().endswith("# tuned\n")


def test_a_changed_file_is_quiet_while_the_skill_version_is_the_same(repo):
    # The repo is meant to edit quality.py. With nothing new in the skill's
    # version there is nothing to review, so it must not be flagged.
    customize_quality()
    out = install("--update").stdout
    assert f"! {QUALITY}" not in out
    assert "customized: 1" in out


def test_a_changed_file_is_flagged_when_the_skill_version_moved(repo):
    customize_quality()
    record = stamp()
    record["files"][str(QUALITY)]["sha256"] = "0" * 64      # stamped from an older template
    STAMP.write_text(json.dumps(record))
    out = install("--update").stdout
    assert f"! {QUALITY}" in out
    assert '["make", "test"]' in QUALITY.read_text()


def test_keep_records_a_reviewed_file_so_it_goes_quiet(repo):
    customize_quality()
    record = stamp()
    record["files"][str(QUALITY)]["sha256"] = "0" * 64
    STAMP.write_text(json.dumps(record))
    install("--update", "--keep", str(QUALITY))
    assert '["make", "test"]' in QUALITY.read_text()
    out = install("--update").stdout
    assert f"! {QUALITY}" not in out


def test_update_adds_a_missing_file(repo):
    AGENTS.unlink()
    out = install("--update").stdout
    assert AGENTS.read_text() == (TEMPLATES / "adws/adw_modules/agents.py").read_text()
    assert f"+ {AGENTS}" in out


def test_second_update_changes_nothing(repo):
    install("--update")
    out = install("--update").stdout
    assert "updated: 0" in out and "added: 0" in out


def test_update_records_the_new_hashes(repo):
    pretend_an_older_skill_stamped(REVIEWER)
    install("--update")
    assert stamp()["files"][str(REVIEWER)]["sha256"] == sha(REVIEWER)


def test_take_replaces_a_kept_file(repo):
    REVIEWER.write_text("my own reviewer prompt\n")
    install("--update", "--take", str(REVIEWER))
    assert REVIEWER.read_text() == (TEMPLATES / "prompt_engineering/reviewer/user.md").read_text()


# ── a repo with its own justfile and .env.sample ─────────────────────────────

def test_a_repos_own_files_are_kept_at_install_and_stay_quiet(tmp_path, monkeypatch):
    # pi-agent-observability had both before SSSF. Install skipped them but
    # never recorded them, so its first update flagged both for review.
    monkeypatch.chdir(tmp_path)
    Path("justfile").write_text("test:\n    bun test\n")
    Path(".env.sample").write_text("OBS_PORT=43190\n")
    install("--coding-agent", "claude_code")
    files = stamp()["files"]
    assert files["justfile"]["sha256"] == sha(TEMPLATES / "justfile")
    assert files[".env.sample"]["sha256"] == sha(TEMPLATES / "env.sample")
    out = install("--update").stdout
    assert "to review: 0" in out
    assert Path("justfile").read_text() == "test:\n    bun test\n"
    assert Path(".env.sample").read_text() == "OBS_PORT=43190\n"


def test_a_leftover_under_adws_stays_unrecorded_at_install(tmp_path, monkeypatch):
    # A file under adws/ that differs is SSSF's own, left by an older install,
    # so an update must still be free to replace it.
    monkeypatch.chdir(tmp_path)
    AGENTS.parent.mkdir(parents=True)
    AGENTS.write_text("# older agents.py\n")
    install("--coding-agent", "claude_code")
    assert str(AGENTS) not in stamp()["files"]



# ── a repo that loads SSSF's recipes with `mod sssf` ─────────────────────────

MODULE = Path("sssf.just")
OWN_JUSTFILE = "mod sssf 'sssf.just'\n\ntest:\n    bun test\n"


def test_a_repo_that_loads_the_module_gets_sssf_just_stamped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("justfile").write_text(OWN_JUSTFILE)
    install("--coding-agent", "claude_code")
    assert MODULE.read_text() == (TEMPLATES / "justfile").read_text()
    files = stamp()["files"]
    assert files["sssf.just"]["sha256"] == sha(TEMPLATES / "justfile")
    assert "justfile" not in files
    assert Path("justfile").read_text() == OWN_JUSTFILE


def test_a_hand_copied_sssf_just_is_brought_up_to_date(tmp_path, monkeypatch):
    # pi-agent-observability copied sssf.just by hand, and install recorded its
    # own justfile as kept. The update never touched sssf.just, so it missed
    # every later change to SSSF's recipes.
    monkeypatch.chdir(tmp_path)
    install("--coding-agent", "claude_code")
    Path("justfile").write_text(OWN_JUSTFILE)
    MODULE.write_text("# an older copy of SSSF's recipes\n")
    out = install("--update").stdout
    assert "sssf.just  (SSSF code, no stamp record yet)" in out
    assert "to review: 0" in out
    assert MODULE.read_text() == (TEMPLATES / "justfile").read_text()
    files = stamp()["files"]
    assert "sssf.just" in files and "justfile" not in files
    assert Path("justfile").read_text() == OWN_JUSTFILE
    assert "updated: 0" in install("--update").stdout

# ── repos stamped before stamp.json existed ──────────────────────────────────

@pytest.fixture
def old_repo(repo):
    """An install from before the record: older module and prompt, no stamp.json."""
    STAMP.unlink()
    AGENTS.write_text(AGENTS.read_text() + "\n# older template\n")
    REVIEWER.write_text(REVIEWER.read_text() + "\n# older template\n")
    QUALITY.write_text(QUALITY.read_text().replace('_placeholder("test")', '["make", "test"]'))
    return repo


def test_old_repo_gets_sssf_code_replaced(old_repo):
    out = install("--update").stdout
    assert AGENTS.read_text() == (TEMPLATES / "adws/adw_modules/agents.py").read_text()
    assert f"~ {AGENTS}  (SSSF code, no stamp record yet)" in out


def test_keep_ends_the_flag_for_an_unrecorded_file(old_repo):
    install("--update", "--keep", str(QUALITY))
    out = install("--update").stdout
    assert f"! {QUALITY}" not in out
    assert '["make", "test"]' in QUALITY.read_text()


def test_old_repo_keeps_quality_and_prompts(old_repo):
    out = install("--update").stdout
    assert '["make", "test"]' in QUALITY.read_text()
    assert REVIEWER.read_text().endswith("# older template\n")
    assert f"! {REVIEWER}" in out and f"! {QUALITY}" in out


def test_old_repo_gets_a_record(old_repo):
    install("--update")
    files = stamp()["files"]
    assert files[str(AGENTS)]["sha256"] == sha(AGENTS)
    # A kept file has no trusted base yet, so it stays unrecorded and is
    # listed again next time instead of being replaced silently.
    assert str(REVIEWER) not in files


def test_update_refuses_without_an_install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), "--update"],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "install" in result.stderr


# ── what install tells you to run next ──────────────────────────────────────

def test_next_steps_use_just_when_sssf_owns_the_justfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = install("--coding-agent", "claude_code").stdout
    assert "  2. just demo " in out and "mod sssf" not in out


def test_next_steps_use_just_sssf_for_the_module(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("justfile").write_text(OWN_JUSTFILE)
    out = install("--coding-agent", "claude_code").stdout
    assert "  2. just sssf demo " in out and "  3. just sssf sessions " in out


def test_next_steps_say_how_to_load_the_recipes_into_a_repos_own_justfile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("justfile").write_text("test:\n    bun test\n")
    out = install("--coding-agent", "claude_code").stdout
    assert "add  mod sssf 'sssf.just'  to it and run install.py again" in out


# ── what the update reports ─────────────────────────────────────────────────

def test_update_names_each_customized_file(repo):
    customize_quality()
    out = install("--update").stdout
    assert "customized: 1" in out
    assert f"    = {QUALITY}" in out


def test_the_skill_commit_is_recorded(repo):
    # A docs-only update changes no stamped file, so without this only a commit
    # message would say which skill a repo is on.
    import re
    version = stamp().get("skill", "")
    assert re.fullmatch(r"[0-9a-f]{7,}(\+dirty)?", version), version
    assert f"skill: {version}" in install("--update").stdout


def test_a_copy_inside_the_repo_is_not_taken_for_the_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SSSF_SOURCE", str(tmp_path / ".claude" / "skills" / "sssf"))
    install("--coding-agent", "claude_code")
    assert "skill" not in stamp()


def test_a_hand_run_without_a_source_keeps_saying_the_recorded_skill(repo, monkeypatch):
    record = stamp()
    record["skill"] = "abc1234"
    STAMP.write_text(json.dumps(record))
    monkeypatch.setenv("SSSF_SOURCE", str(repo / "nowhere"))
    out = install("--update").stdout
    assert "skill: abc1234 (as recorded" in out
    assert stamp()["skill"] == "abc1234"


def test_a_flagged_file_shows_the_skills_own_change(repo):
    # Stamped from quality.py as it was before 253e22e changed the template.
    rel = "templates/adws/adw_modules/quality.py"
    old = subprocess.run(["git", "-C", str(SKILL), "show", f"253e22e~1:./{rel}"],
                         capture_output=True, check=True).stdout
    customize_quality()
    record = stamp()
    record["files"][str(QUALITY)]["sha256"] = hashlib.sha256(old).hexdigest()
    STAMP.write_text(json.dumps(record))
    out = install("--update").stdout
    assert f"! {QUALITY}" in out
    line = next(l for l in out.splitlines() if "the skill's change" in l)
    command = line.split("to copy in: ")[1].split()
    diff = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    assert 'verdict = ("not configured"' in diff


RETIRED = Path("adws/adw_retired.py")


def record_retired(path: Path, source: str | None = None) -> None:
    record = stamp()
    record["files"][str(path)] = {"source": source or str(path), "sha256": sha(path)}
    STAMP.write_text(json.dumps(record))


def test_update_removes_unchanged_retired_code(repo):
    RETIRED.write_text("old = 1\n")
    record_retired(RETIRED)
    out = install("--update").stdout
    assert not RETIRED.exists() and str(RETIRED) not in stamp()["files"]
    assert "removed: 1" in out and f"- {RETIRED}" in out


def test_update_keeps_changed_retired_code(repo):
    RETIRED.write_text("old = 1\n")
    record_retired(RETIRED)
    RETIRED.write_text("old = 2\n")
    out = install("--update").stdout
    assert RETIRED.exists() and "left in place" in out and f"- {RETIRED}" in out


def test_update_keeps_retired_non_code(repo):
    old = Path("adws/adw_data/prompt_engineering/old/user.md")
    old.parent.mkdir(parents=True)
    old.write_text("x\n")
    record_retired(old)
    install("--update")
    assert old.exists()


def test_update_unlinks_a_retired_link_not_its_target(repo, tmp_path_factory):
    target = tmp_path_factory.mktemp("outside") / "target.py"
    target.write_text("old = 1\n")
    RETIRED.symlink_to(target)
    record_retired(RETIRED)
    install("--update")
    assert not os.path.lexists(RETIRED) and target.read_text() == "old = 1\n"


def test_update_leaves_a_retired_link_to_a_folder(repo, tmp_path_factory):
    target = tmp_path_factory.mktemp("outside")
    RETIRED.symlink_to(target, target_is_directory=True)
    record = stamp()
    record["files"][str(RETIRED)] = {"source": str(RETIRED), "sha256": "0" * 64}
    STAMP.write_text(json.dumps(record))
    install("--update")
    assert RETIRED.is_symlink() and target.exists()


def test_update_never_deletes_through_a_traversal_key_or_a_linked_parent(repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    victim = outside / "x.py"
    victim.write_text("keep\n")
    git_config = Path(".git/config")
    git_config.parent.mkdir(exist_ok=True)
    git_config.write_text("[core]\n")
    (Path("adws/adw_modules") / "evil").symlink_to(outside, target_is_directory=True)
    record = stamp()
    for key, target in (("adws/adw_modules/../../.git/config", git_config),
                        (f"adws/adw_modules/{os.path.relpath(victim, Path.cwd() / 'adws/adw_modules')}", victim),
                        ("adws/adw_modules/evil/x.py", victim)):
        record["files"][key] = {"source": "adws/gone.py", "sha256": sha(target)}
    STAMP.write_text(json.dumps(record))
    install("--update")
    assert git_config.exists() and victim.exists()


def test_retire_unlinks_through_the_open_parent_not_a_re_resolved_path(repo, tmp_path_factory, monkeypatch):
    """A parent folder swapped for a link after the check must not redirect the unlink."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sssf_install", SKILL / "scripts/install.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    outside = tmp_path_factory.mktemp("outside")
    victim = outside / "x.py"
    victim.write_text("keep\n")
    sub = Path("adws/adw_modules/sub")
    sub.mkdir()
    (sub / "x.py").write_text("keep\n")
    real_unlink = os.unlink

    def swap_then_unlink(name, *, dir_fd=None, **kw):
        sub.rename("adws/adw_modules/sub_moved")
        sub.symlink_to(outside, target_is_directory=True)
        return real_unlink(name, dir_fd=dir_fd, **kw)

    monkeypatch.setattr(mod.os, "unlink", swap_then_unlink)
    assert mod.retire(Path.cwd(), "adws/adw_modules/sub/x.py", sha(sub / "x.py")) == "removed"
    assert victim.exists() and not Path("adws/adw_modules/sub_moved/x.py").exists()

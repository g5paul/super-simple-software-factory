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

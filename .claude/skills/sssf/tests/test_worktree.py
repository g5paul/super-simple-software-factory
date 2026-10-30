"""A run on its own branch, in its own worktree, merged back on the engineer's word.

Each test drives worktree.py against a real git repo and a real trace db, the
way a chain does: enter() at the start, the run's commits in the worktree,
then merge() or discard(). Design: docs/designs/branch-per-run.md.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import worktree
from adw_modules.data_types import EventRecord, SSSFConfig
from adw_modules.tracer import Tracer


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", "-b", "main", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "T", cwd=root)
    # What install.py writes, no more: a broader ignore would hide a worktree
    # folder that leaks into the checkout's status.
    (root / ".gitignore").write_text("adws/adw_data/sessions/\nadws/adw_data/sssf.db*\n"
                                     "adws/adw_data/worktrees/\n")
    (root / "app.txt").write_text("one\n")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "start", cwd=root)
    monkeypatch.chdir(root)
    return root


def start(adw_id: str, requested: bool = True) -> tuple[SSSFConfig, dict | None, Tracer]:
    """What session.ensure does: enter, then record the worktree in the trace."""
    cfg = SSSFConfig()
    place = worktree.enter(cfg, adw_id, requested)
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_root}/sessions/{adw_id}/events.jsonl")
    tracer.session_start(adw_id, "t", adw_name="test")
    if place and not place["resumed"]:
        tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="worktree",
                                 payload={k: v for k, v in place.items() if k != "resumed"}))
    return cfg, place, tracer


def work(text: str, message: str) -> None:
    Path("app.txt").write_text(text)
    subprocess.run(["git", "commit", "-qam", message], check=True)


def finish(tracer: Tracer, adw_id: str, ok: bool = True) -> None:
    tracer.session_finish(adw_id, ok=ok)


def test_a_plain_run_stays_in_the_checkout(repo):
    cfg, place, _ = start("plain0001", requested=False)
    assert place is None
    assert Path.cwd() == repo
    assert Path(cfg.observability.db) == repo / "adws/adw_data/sssf.db"


def test_a_worktree_run_works_on_its_own_branch_and_traces_in_the_checkout(repo):
    cfg, place, _ = start("wt000001")
    path = repo / "adws/adw_data/worktrees/wt000001"
    assert Path.cwd() == path
    assert place["branch"] == "sssf/wt000001" and place["launch_branch"] == "main"
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=path) == "sssf/wt000001"
    # The trace and run folders stay in the main checkout, not the worktree.
    assert Path(cfg.observability.db) == repo / "adws/adw_data/sssf.db"
    assert Path(cfg.defaults.data_root) == repo / "adws/adw_data"
    assert not (path / "adws/adw_data/sssf.db").exists()


def test_the_launch_checkout_stays_untouched_and_dirty_is_noted(repo):
    (repo / "app.txt").write_text("my own edit\n")
    _, place, _ = start("wt000002")
    work("run's change\n", "run")
    assert place["launch_dirty"] is True
    assert (repo / "app.txt").read_text() == "my own edit\n"


def test_merge_fast_forwards_and_cleans_up(repo):
    cfg, _, tracer = start("wt000003")
    work("two\n", "run change")
    finish(tracer, "wt000003")
    os.chdir(repo)
    out = worktree.merge(cfg, "wt000003")
    assert "fast-forward" in out
    assert (repo / "app.txt").read_text() == "two\n"
    assert not (repo / "adws/adw_data/worktrees/wt000003").exists()
    assert "sssf/wt000003" not in git("branch", cwd=repo)


def test_merge_makes_a_merge_commit_when_the_branch_moved_on(repo):
    cfg, _, tracer = start("wt000004")
    Path("new.txt").write_text("from the run\n")
    subprocess.run(["git", "add", "new.txt"], check=True)
    subprocess.run(["git", "commit", "-qm", "run adds a file"], check=True)
    finish(tracer, "wt000004")
    os.chdir(repo)
    work("moved on\n", "engineer's own commit")
    out = worktree.merge(cfg, "wt000004")
    assert "merge commit" in out
    assert (repo / "new.txt").exists() and (repo / "app.txt").read_text() == "moved on\n"


def test_a_conflict_aborts_and_keeps_the_branch(repo):
    cfg, _, tracer = start("wt000005")
    work("run's line\n", "run")
    finish(tracer, "wt000005")
    os.chdir(repo)
    work("engineer's line\n", "engineer")
    with pytest.raises(worktree.WorktreeError, match="aborted"):
        worktree.merge(cfg, "wt000005")
    assert git("status", "--porcelain", cwd=repo) == ""
    assert (repo / "app.txt").read_text() == "engineer's line\n"
    assert "sssf/wt000005" in git("branch", cwd=repo)
    assert (repo / "adws/adw_data/worktrees/wt000005").is_dir()


def test_only_a_successful_run_merges(repo):
    cfg, _, tracer = start("wt000006")
    work("half done\n", "run")
    finish(tracer, "wt000006", ok=False)
    os.chdir(repo)
    with pytest.raises(worktree.WorktreeError, match="only a successful run"):
        worktree.merge(cfg, "wt000006")


def test_merge_refuses_a_dirty_checkout_or_the_wrong_branch(repo):
    cfg, _, tracer = start("wt000007")
    work("two\n", "run")
    finish(tracer, "wt000007")
    os.chdir(repo)
    (repo / "app.txt").write_text("uncommitted\n")
    with pytest.raises(worktree.WorktreeError, match="uncommitted"):
        worktree.merge(cfg, "wt000007")
    git("checkout", "--", "app.txt", cwd=repo)
    git("switch", "-q", "-c", "other", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="git switch main"):
        worktree.merge(cfg, "wt000007")


def test_discard_removes_a_failed_runs_branch_and_folder(repo):
    cfg, _, tracer = start("wt000008")
    work("broken\n", "run")
    finish(tracer, "wt000008", ok=False)
    os.chdir(repo)
    worktree.discard(cfg, "wt000008")
    assert not (repo / "adws/adw_data/worktrees/wt000008").exists()
    assert "sssf/wt000008" not in git("branch", cwd=repo)


def test_a_resume_goes_back_into_the_worktree_even_without_the_flag(repo):
    start("wt000009")
    os.chdir(repo)
    _, place, _ = start("wt000009", requested=False)
    assert place["resumed"] is True
    assert Path.cwd() == repo / "adws/adw_data/worktrees/wt000009"


def test_a_merged_run_cannot_be_resumed(repo):
    cfg, _, tracer = start("wt000010")
    work("two\n", "run")
    finish(tracer, "wt000010")
    os.chdir(repo)
    worktree.merge(cfg, "wt000010")
    with pytest.raises(worktree.WorktreeError, match="merged or discarded"):
        start("wt000010", requested=False)


def test_a_second_worktree_run_refuses_while_one_is_alive(repo):
    _, _, tracer = start("wt000011")
    tracer.process_start("wt000011", "adw", "", os.getpid(), "adw_simple_sdlc.py")
    os.chdir(repo)
    with pytest.raises(worktree.WorktreeError, match="only one runs at a time"):
        start("wt000012")


def test_a_detached_head_cannot_start_one(repo):
    git("switch", "-q", "--detach", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="detached"):
        start("wt000013")


def test_every_single_task_chain_takes_the_flag():
    for name in ("adw_simple_sdlc.py", "adw_plan_build_test.py",
                 "adw_plan_build_test_quality.py", "adw_build_test.py"):
        text = (SKILL / "templates/adws" / name).read_text()
        assert '"--worktree"' in text and "use_worktree=worktree" in text, name
    assert '"--worktree"' not in (SKILL / "templates/adws/adw_tickets.py").read_text()


def test_the_install_ignores_the_worktrees_folder():
    sys.path.insert(0, str(SKILL / "scripts"))
    import install
    assert "adws/adw_data/worktrees/" in install.GITIGNORE_ENTRIES


def test_a_live_worktree_leaves_the_checkout_clean(repo):
    start("wt000014")
    work("two\n", "run")
    assert git("status", "--porcelain", cwd=repo) == ""


def test_a_new_ignore_entry_joins_the_existing_sssf_block(tmp_path):
    """yt-summarizer's .gitignore had three `# sssf runtime` headers, one per update."""
    sys.path.insert(0, str(SKILL / "scripts"))
    import install
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("node_modules/\n\n# sssf runtime\nadws/adw_data/sessions/\n\n# mine\n*.log\n")
    install.ensure_gitignore(tmp_path, [])
    text = gitignore.read_text()
    assert text.count("# sssf runtime") == 1
    block = text.split("# sssf runtime\n")[1].split("\n\n")[0].splitlines()
    assert "adws/adw_data/worktrees/" in block and "adws/adw_data/sessions/" in block
    assert text.endswith("# mine\n*.log\n")
    install.ensure_gitignore(tmp_path, [])
    assert gitignore.read_text() == text


def test_ff_only_refuses_a_merge_commit_and_changes_nothing(repo):
    cfg, _, tracer = start("wt000015")
    work("run's line\n", "run")
    finish(tracer, "wt000015")
    os.chdir(repo)
    (repo / "other.txt").write_text("engineer\n")
    git("add", "other.txt", cwd=repo)
    git("commit", "-qm", "engineer moved on", cwd=repo)
    head = git("rev-parse", "HEAD", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="fast-forward only"):
        worktree.merge(cfg, "wt000015", ff_only=True)
    assert git("rev-parse", "HEAD", cwd=repo) == head
    assert "sssf/wt000015" in git("branch", cwd=repo)


def test_ff_only_still_fast_forwards(repo):
    cfg, _, tracer = start("wt000016")
    work("two\n", "run")
    finish(tracer, "wt000016")
    os.chdir(repo)
    assert "fast-forward" in worktree.merge(cfg, "wt000016", ff_only=True)

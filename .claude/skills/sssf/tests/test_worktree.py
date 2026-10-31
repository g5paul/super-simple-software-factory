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


def alive(adw_id: str) -> Tracer:
    """Start a worktree run and record this test process as its live chain."""
    _, _, tracer = start(adw_id)
    tracer.process_start(adw_id, "adw", "", os.getpid(), "adw_simple_sdlc.py")
    os.chdir(worktree.main_checkout())
    return tracer


def test_two_run_at_once_and_a_third_refuses_naming_both(repo):
    alive("wt000011")
    alive("wt000012")
    with pytest.raises(worktree.WorktreeError, match="wt000011, wt000012 are still going.*allows 2"):
        start("wt000014")


def test_max_worktree_runs_1_keeps_one_at_a_time(repo):
    (repo / "adws/adw_sssf_config").mkdir(parents=True)
    (repo / "adws/adw_sssf_config/repo.yaml").write_text("max_worktree_runs: 1\n")
    alive("wt000015")
    with pytest.raises(worktree.WorktreeError, match="wt000015 is still going.*allows 1"):
        start("wt000016")


def test_a_run_from_the_other_side_counts_while_its_session_is_running(repo):
    _, _, tracer = start("wt000017")
    # A pid this host can't judge: the container's, say. Its own pid here would
    # be some other process or none.
    tracer.process_start("wt000017", "adw", "", 999999, "adw_simple_sdlc.py")
    tracer.conn.execute("update processes set host='sssf-operator-repo' where adw_id='wt000017'")
    db = SSSFConfig().observability.db
    os.chdir(repo)
    assert worktree.live_runs(str(repo / db)) == ["wt000017"]
    finish(tracer, "wt000017")
    assert worktree.live_runs(str(repo / db)) == []


def test_a_trace_from_before_hosts_were_recorded_still_counts(repo):
    import sqlite3
    db = repo / SSSFConfig().observability.db
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as conn:
        conn.execute("create table processes (id integer primary key, adw_id text, kind text, "
                     "name text, pid integer, command text, started_at text, ended_at text)")
    assert worktree.live_runs(str(db)) == []
    alive("wt000022")
    with sqlite3.connect(db) as conn:
        conn.execute("update processes set host=null")
    assert worktree.live_runs(str(db)) == ["wt000022"]


def test_a_container_run_gets_its_own_environment_and_loses_it_on_discard(repo, tmp_path, monkeypatch):
    shared = tmp_path / "venvs" / "repo"
    shared.parent.mkdir()
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(shared))
    cfg, place, tracer = start("wt000018")
    assert place["environment"] == f"{shared}-wt000018"
    assert os.environ["UV_PROJECT_ENVIRONMENT"] == f"{shared}-wt000018"
    Path(place["environment"]).mkdir()
    finish(tracer, "wt000018", ok=False)
    os.chdir(repo)
    assert "and its environment" in worktree.discard(cfg, "wt000018")
    assert not Path(place["environment"]).exists()


def test_a_second_land_waits_for_the_lock_then_refuses(repo, monkeypatch):
    cfg, _, tracer = start("wt000019")
    work("two\n", "change")
    finish(tracer, "wt000019")
    os.chdir(repo)
    monkeypatch.setattr(worktree, "LOCK_SECONDS", 1)
    with worktree.locked("land", "a test's own merge"):
        with pytest.raises(worktree.WorktreeError, match="merge, rebase, or discard.*held its lock"):
            worktree.merge(cfg, "wt000019")
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo) == "main"
    assert "fast-forward" in worktree.merge(cfg, "wt000019")


def last_event(adw_id: str, name: str) -> dict | None:
    import json, sqlite3
    with sqlite3.connect(SSSFConfig().observability.db) as conn:
        row = conn.execute("select payload_json from events where adw_id=? and name=? "
                           "order by rowid desc limit 1", (adw_id, name)).fetchone()
    return json.loads(row[0]) if row else None


def two_from_one_tip(repo: Path, text_a: str, text_b: str, file_b: str = "other.txt"):
    """Two successful runs cut from the same tip; the first is merged."""
    cfg, _, ta = start("wt000020")
    work(text_a, "first run")
    finish(ta, "wt000020")
    os.chdir(repo)
    _, _, tb = start("wt000021")
    Path(file_b).write_text(text_b)
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-qm", "second run"], check=True)
    finish(tb, "wt000021")
    os.chdir(repo)
    worktree.merge(cfg, "wt000020", ff_only=True)
    return cfg


def test_ff_only_points_at_rebase_which_rechecks_then_merge_fast_forwards(repo, monkeypatch):
    checked = []
    monkeypatch.setattr(worktree, "_recheck", lambda *a: checked.append(a[3]) or True)
    cfg = two_from_one_tip(repo, "two\n", "new\n")
    with pytest.raises(worktree.WorktreeError, match="just rebase wt000021"):
        worktree.merge(cfg, "wt000021", ff_only=True)
    assert "checks passed" in worktree.rebase(cfg, "wt000021")
    assert checked == ["wt000021"]
    assert "fast-forward" in worktree.merge(cfg, "wt000021", ff_only=True)
    assert git("log", "--format=%s", cwd=repo).splitlines() == ["second run", "first run", "start"]
    rebased = last_event("wt000021", "worktree_rebased")
    assert rebased["onto"] == "main" and rebased["kept"] is True


def test_a_rebase_conflict_aborts_and_leaves_the_branch_as_it_was(repo, monkeypatch):
    monkeypatch.setattr(worktree, "_recheck", lambda *a: True)
    cfg = two_from_one_tip(repo, "two\n", "three\n", file_b="app.txt")
    before = git("rev-parse", "sssf/wt000021", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="conflict, so it was aborted"):
        worktree.rebase(cfg, "wt000021")
    assert git("rev-parse", "sssf/wt000021", cwd=repo) == before
    path = repo / "adws/adw_data/worktrees/wt000021"
    assert git("status", "--porcelain", cwd=path) == ""
    assert last_event("wt000021", "worktree_rebased") is None


def test_a_rebase_whose_checks_fail_is_put_back(repo, monkeypatch):
    monkeypatch.setattr(worktree, "_recheck", lambda *a: False)
    cfg = two_from_one_tip(repo, "two\n", "new\n")
    before = git("rev-parse", "sssf/wt000021", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="checks failed.*back at"):
        worktree.rebase(cfg, "wt000021")
    assert git("rev-parse", "sssf/wt000021", cwd=repo) == before
    assert last_event("wt000021", "worktree_rebased")["kept"] is False


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


def test_repo_yaml_sets_ff_only_and_env_overrides_it(repo, monkeypatch):
    from adw_modules import repo_settings
    monkeypatch.delenv("SSSF_MERGE_FF_ONLY", raising=False)
    assert repo_settings.merge_ff_only(repo) is False          # no file: the default
    path = repo / repo_settings.PATH
    path.parent.mkdir(parents=True)
    path.write_text((SKILL / "templates/repo.yaml").read_text())
    assert repo_settings.merge_ff_only(repo) is False          # the stamped template
    path.write_text("merge: ff_only\n")
    assert repo_settings.merge_ff_only(repo) is True
    monkeypatch.setenv("SSSF_MERGE_FF_ONLY", "0")
    assert repo_settings.merge_ff_only(repo) is False          # one machine opts out
    path.write_text("merge: fast_forward\n")
    with pytest.raises(Exception):
        repo_settings.load(repo)                               # a typo fails loudly


def test_the_install_stamps_repo_yaml():
    sys.path.insert(0, str(SKILL / "scripts"))
    import install
    dests = [str(d) for _, d in install.targets(Path("/r"), SKILL / "templates/sssf.config.yaml")]
    assert "/r/adws/adw_sssf_config/repo.yaml" in dests


def landed(adw_id: str) -> list[tuple[str, dict]]:
    import json, sqlite3
    with sqlite3.connect(SSSFConfig().observability.db) as conn:
        rows = conn.execute("select name, payload_json from events where adw_id=? and name in "
                            "('worktree_merged', 'worktree_discarded') order by rowid", (adw_id,))
        return [(name, json.loads(payload)) for name, payload in rows]


def test_a_merge_is_recorded_in_the_trace(repo):
    cfg, _, tracer = start("wt000017")
    work("two\n", "run")
    finish(tracer, "wt000017")
    os.chdir(repo)
    worktree.merge(cfg, "wt000017")
    [(name, payload)] = landed("wt000017")
    assert name == "worktree_merged"
    assert payload == {"into": "main", "how": "fast-forward",
                       "head": git("rev-parse", "--short", "HEAD", cwd=repo)}


def test_a_discard_is_recorded_and_a_refused_merge_is_not(repo):
    cfg, _, tracer = start("wt000018")
    work("broken\n", "run")
    finish(tracer, "wt000018", ok=False)
    os.chdir(repo)
    with pytest.raises(worktree.WorktreeError):
        worktree.merge(cfg, "wt000018")
    assert landed("wt000018") == []
    worktree.discard(cfg, "wt000018")
    assert landed("wt000018") == [("worktree_discarded", {})]


def test_setup_runs_each_command_in_order_and_stops_at_the_first_failure(tmp_path):
    logs = tmp_path / "logs"
    assert worktree.run_setup(["echo one > a.txt", "cd sub 2>/dev/null || mkdir sub",
                               "cat a.txt > sub/b.txt"], tmp_path, 10, logs) == ""
    assert (tmp_path / "sub/b.txt").read_text() == "one\n"
    why = worktree.run_setup(["true", "echo broken >&2; exit 3", "touch never"], tmp_path, 10, logs)
    assert "`echo broken >&2; exit 3` exited 3" in why and "broken" in why
    assert not (tmp_path / "never").exists()
    assert "exit: 3" in (logs / "02.log").read_text()


def test_a_setup_command_over_its_time_fails(tmp_path):
    why = worktree.run_setup(["echo started; sleep 5"], tmp_path, 1, tmp_path / "logs")
    assert "exited 124" in why and "Timed out after 1s" in why and "started" in why


def test_a_rebase_reruns_setup_and_a_failed_setup_puts_the_branch_back(repo, monkeypatch):
    monkeypatch.setattr(worktree, "_recheck", lambda *a: True)
    cfg = two_from_one_tip(repo, "two\n", "new\n")
    (repo / "adws/adw_sssf_config").mkdir(parents=True, exist_ok=True)
    settings = repo / "adws/adw_sssf_config/repo.yaml"
    settings.write_text("worktree_setup:\n  - exit 7\n")
    before = git("rev-parse", "sssf/wt000021", cwd=repo)
    with pytest.raises(worktree.WorktreeError, match="worktree_setup failed.*back at.*exited 7"):
        worktree.rebase(cfg, "wt000021")
    assert git("rev-parse", "sssf/wt000021", cwd=repo) == before
    settings.write_text("worktree_setup:\n  - touch set_up.marker\n")
    assert "checks passed" in worktree.rebase(cfg, "wt000021")
    assert (repo / "adws/adw_data/worktrees/wt000021/set_up.marker").exists()

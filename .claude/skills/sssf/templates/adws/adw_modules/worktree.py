"""A run on its own branch, in its own worktree. Opt-in with `--worktree`.

Without it, a chain works and commits in the checkout it was launched from, as
it always has. With it, the run gets `sssf/<adw_id>`, cut from the tip of the
launch branch, checked out at `<data_dir>/worktrees/<adw_id>`. Its work reaches
the launch branch only when the engineer runs `just merge <adw_id>`.

The split is: SSSF itself (the chain's code, the roster, the prompts, and the
trace) stays in the launch checkout, and the work (every agent, check, and
commit) happens in the worktree. `enter()` makes that true by anchoring the
config's paths to the launch checkout and then moving the process into the
worktree, so every git call, quality block, and agent below it follows the
worktree with no flag of its own.

The worktree lives inside the repo, under the gitignored data dir, so a
container session that mounts only the repo can still reach it.

More than one can run at once, up to `max_worktree_runs` in repo.yaml. A
lock in the git dir keeps two launches from both passing that count, and
another keeps two merges, rebases, or discards from meeting in the checkout.

Design: docs/designs/branch-per-run.md and parallel-worktree-runs.md in the
SSSF repo.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import repo_settings
from .data_types import EventRecord, SSSFConfig
from .tracer import Tracer
from .utils import new_id

BRANCH_PREFIX = "sssf/"
LOCK_SECONDS = 30
LOCKED = {"launch": "launch", "land": "merge, rebase, or discard"}


class WorktreeError(RuntimeError):
    """A worktree run that cannot start, resume, merge, or be discarded as asked."""


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def branch_for(adw_id: str) -> str:
    return f"{BRANCH_PREFIX}{adw_id}"


def path_for(root: Path, cfg: SSSFConfig, adw_id: str) -> Path:
    return root / cfg.defaults.data_dir / "worktrees" / adw_id


def main_checkout() -> Path:
    """The repo's main checkout, even when called from inside a worktree."""
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    return common.parent.resolve()


def anchor(cfg: SSSFConfig, root: Path) -> None:
    """Pin every path SSSF reads or writes to the launch checkout.

    The trace db, run folders, prompts, and Pi extensions are relative paths,
    which follow the process's working folder. A worktree run moves that
    folder, so they become absolute first. `data_dir` itself stays relative:
    permissions and budget use it as a pathspec inside whatever tree they check.
    """
    def pin(value: str) -> str:
        return value if not value or Path(value).is_absolute() else str(root / value)

    cfg.defaults.data_root = pin(cfg.defaults.data_dir)
    cfg.observability.db = pin(cfg.observability.db)
    for agent in cfg.agents:
        agent.prompt_engineering.system = pin(agent.prompt_engineering.system)
        agent.prompt_engineering.user = pin(agent.prompt_engineering.user)
        agent.harness_engineering = [pin(p) if (root / p).exists() else p
                                     for p in agent.harness_engineering]


@contextlib.contextmanager
def locked(name: str, doing: str):
    """Hold `<git dir>/sssf-<name>.lock` while the block runs.

    `launch` covers a start, from the count of live runs to the trace rows
    that make this one count. `land` covers merge, rebase, and discard, which
    all move refs and worktrees in the main checkout. The lock is a file in
    the shared git dir, so a host and a container session see the same one.
    A second caller waits LOCK_SECONDS, then refuses.
    """
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip())
    with (common / f"sssf-{name}.lock").open("a+") as handle:
        deadline = time.monotonic() + LOCK_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise WorktreeError(
                        f"another {LOCKED[name]} in this repo held its lock for {LOCK_SECONDS}s, so "
                        f"{doing} did not start. Nothing was changed; try again when it is done."
                    ) from None
                time.sleep(0.5)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextlib.contextmanager
def launching():
    """The launch lock, or nothing outside a git repo, where no worktree runs."""
    if git("rev-parse", "--git-dir", check=False).returncode != 0:
        yield
        return
    with locked("launch", "this run"):
        yield


def _query(db: str, sql: str, *params) -> list[tuple]:
    if not Path(db).is_file():
        return []
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def recorded(db: str, adw_id: str) -> dict | None:
    """The `worktree` event this run's start wrote, or None for a plain run."""
    rows = _query(db, "select payload_json from events where adw_id=? and name='worktree' "
                      "order by rowid desc limit 1", adw_id)
    return json.loads(rows[0][0]) if rows else None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def live_runs(db: str, exclude: str = "") -> list[str]:
    """adw_ids of worktree runs whose chain process is still alive.

    A pid means something only on the host that recorded it: the container
    and the machine it runs on share this trace but not their pids. A run
    from the other side counts as alive while its session says `running`.
    A row from before hosts were recorded is taken to be from this one.
    """
    here = socket.gethostname()
    rows = _query(db, "select distinct p.adw_id, p.pid, p.host, s.status from processes p "
                      "join events e on e.adw_id = p.adw_id and e.name = 'worktree' "
                      "left join sessions s on s.adw_id = p.adw_id "
                      "where p.kind = 'adw' and p.ended_at is null and p.adw_id != ?", exclude)
    return sorted({adw_id for adw_id, pid, host, status in rows
                   if (_alive(pid) if pid and (host or here) == here else status == "running")})


def dirty(root: Path) -> bool:
    return bool(git("status", "--porcelain", cwd=root).stdout.strip())


def enter(cfg: SSSFConfig, adw_id: str, requested: bool) -> dict | None:
    """Anchor SSSF to the launch checkout, then start or resume the run's worktree.

    Returns what the trace should record about the worktree, or None for a plain
    run. A run that already has a worktree resumes in it whether or not
    `--worktree` was passed again, so a resume can never land in the wrong tree.
    """
    inside_repo = git("rev-parse", "--git-dir", check=False).returncode == 0
    root = main_checkout() if inside_repo else Path.cwd().resolve()
    anchor(cfg, root)
    if not inside_repo:
        if requested:
            raise WorktreeError("--worktree needs a git repository")
        return None

    db = cfg.observability.db
    path = path_for(root, cfg, adw_id)
    record = recorded(db, adw_id)
    if path.is_dir() and record:
        os.chdir(path)
        _own_environment(record.get("environment"))
        return {**record, "resumed": True}
    if record:
        raise WorktreeError(
            f"run {adw_id} worked in {record['worktree']}, which is gone: it was merged or "
            "discarded. Start a new run instead of resuming this one.")
    if not requested:
        return None

    os.chdir(root)
    launch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if launch == "HEAD":
        raise WorktreeError("--worktree needs a branch to merge back into; HEAD is detached. "
                            "Check out a branch first.")
    cap = repo_settings.max_worktree_runs(root)
    others = live_runs(db, exclude=adw_id)
    if len(others) >= cap:
        raise WorktreeError(
            f"worktree run{'s' if len(others) > 1 else ''} {', '.join(others)} "
            f"{'are' if len(others) > 1 else 'is'} still going in this repo, and "
            f"max_worktree_runs in {repo_settings.PATH} allows {cap} at once. Wait for one, "
            "or stop it with `just kill <adw_id>`.")
    git("worktree", "prune")
    branch = branch_for(adw_id)
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise WorktreeError(f"branch {branch} already exists. Merge or delete it, or use "
                            "another adw_id.")
    git("worktree", "add", "-b", branch, str(path), "HEAD")
    os.chdir(path)
    place = {"branch": branch, "worktree": str(path), "launch_branch": launch,
             "base": git("rev-parse", "--short", "HEAD").stdout.strip(),
             "launch_dirty": dirty(root), "resumed": False}
    shared = os.environ.get("UV_PROJECT_ENVIRONMENT", "")
    if shared:
        place["environment"] = f"{shared.rstrip('/')}-{adw_id}"
        _own_environment(place["environment"])
    return place


def _own_environment(environment: str | None) -> None:
    """Point uv at this run's own project environment.

    The container sets UV_PROJECT_ENVIRONMENT to one path per repo, so two
    worktree runs would sync the same environment from their own lockfiles,
    each undoing the other. A worktree run gets that path with its adw_id on
    the end, which every agent and quality block inherits. On the host the
    variable is unset and each worktree builds its own .venv.
    """
    if environment:
        os.environ["UV_PROJECT_ENVIRONMENT"] = environment


def _log(cfg: SSSFConfig, adw_id: str, name: str, payload: dict) -> None:
    """Record how a run's branch ended, so the dashboard can tell a merged or
    discarded branch from one still waiting."""
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_root}/sessions/{adw_id}/events.jsonl")
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name=name, payload=payload))


def _status(db: str, adw_id: str) -> str | None:
    rows = _query(db, "select status from sessions where adw_id=?", adw_id)
    return rows[0][0] if rows else None


def merge(cfg: SSSFConfig, adw_id: str, ff_only: bool = False) -> str:
    with locked("land", f"the merge of {adw_id}"):
        return _merge(cfg, adw_id, ff_only)


def _merge(cfg: SSSFConfig, adw_id: str, ff_only: bool) -> str:
    """Merge a successful run's branch into its launch branch, then clean up.

    Fast-forward when it can, a merge commit when the launch branch moved on,
    never a rebase. A conflict is aborted, so the checkout is left as it was,
    and the run's branch and worktree stay for a hand merge. With `ff_only`,
    for a repo that keeps a linear history, a launch branch that moved on is
    refused before anything changes.
    """
    root = main_checkout()
    anchor(cfg, root)
    db = cfg.observability.db
    record = recorded(db, adw_id)
    if not record:
        raise WorktreeError(f"run {adw_id} did not run in a worktree; there is nothing to merge.")
    branch, path, launch = record["branch"], Path(record["worktree"]), record["launch_branch"]
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root,
           check=False).returncode != 0:
        raise WorktreeError(f"branch {branch} is gone: run {adw_id} was merged or discarded.")
    status = _status(db, adw_id)
    if status != "success":
        raise WorktreeError(
            f"run {adw_id} is {status}, and only a successful run merges through this recipe. "
            f"To take its work anyway, merge {branch} yourself with git.")
    current = git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip()
    if current != launch:
        raise WorktreeError(f"run {adw_id} merges into {launch}, but {current} is checked out. "
                            f"Run `git switch {launch}` first.")
    if dirty(root):
        raise WorktreeError("the checkout has uncommitted changes. Commit or stash them first: "
                            "a merge that has to abort can't promise to put them back.")
    fast_forward = git("merge-base", "--is-ancestor", "HEAD", branch, cwd=root,
                       check=False).returncode == 0
    if ff_only and not fast_forward:
        raise WorktreeError(
            f"{launch} has moved on since run {adw_id} started, so {branch} can't fast-forward "
            f"it, and this merge is fast-forward only. Nothing was changed. To land it, "
            f"`just rebase {adw_id}` moves it onto {launch} and reruns the checks, then "
            f"`just merge {adw_id}` again.")
    result = git("merge", "--ff", "--no-edit", branch, cwd=root, check=False)
    if result.returncode != 0:
        if git("rev-parse", "-q", "--verify", "MERGE_HEAD", cwd=root, check=False).returncode == 0:
            git("merge", "--abort", cwd=root, check=False)
        raise WorktreeError(
            f"merging {branch} into {launch} failed, so it was aborted and the checkout is as "
            f"it was:\n{(result.stdout + result.stderr).strip()}\n"
            f"The branch and its worktree ({path}) are kept. To merge by hand: "
            f"`git merge {branch}`, resolve, commit, then `just discard {adw_id}`.")
    after = git("rev-parse", "--short", "HEAD", cwd=root).stdout.strip()
    how = "fast-forward" if fast_forward else "merge commit"
    left = _remove(root, path, branch, record.get("environment"), force_branch=False)
    _log(cfg, adw_id, "worktree_merged", {"into": launch, "how": how, "head": after})
    return (f"merged {branch} into {launch} ({how}), now at {after}; removed its worktree "
            f"and branch{left}")


def discard(cfg: SSSFConfig, adw_id: str) -> str:
    """Remove a run's worktree and branch, whatever state they are in."""
    with locked("land", f"the discard of {adw_id}"):
        return _discard(cfg, adw_id)


def _discard(cfg: SSSFConfig, adw_id: str) -> str:
    root = main_checkout()
    anchor(cfg, root)
    db = cfg.observability.db
    record = recorded(db, adw_id)
    if not record:
        raise WorktreeError(f"run {adw_id} did not run in a worktree; there is nothing to discard.")
    if adw_id in live_runs(db):
        raise WorktreeError(f"run {adw_id} is still going. Stop it with `just kill {adw_id}` first.")
    left = _remove(root, Path(record["worktree"]), record["branch"], record.get("environment"),
                   force_branch=True)
    _log(cfg, adw_id, "worktree_discarded", {})
    return f"discarded {record['branch']} and its worktree{left}"


def rebase(cfg: SSSFConfig, adw_id: str) -> str:
    """Move a successful run's branch onto its launch branch's tip, then recheck it.

    For a repo whose merges are fast-forward only, where the second of two runs
    cut from the same tip can never land as it is. Only the engineer starts
    this: `just merge` never rebases. A conflict aborts and leaves the branch
    as it was. So do checks that fail on the rebased code, so the branch tip
    is always code that passed. The lock is held through the checks, so no
    merge can land the rebased tip before they pass.
    """
    with locked("land", f"the rebase of {adw_id}"):
        return _rebase(cfg, adw_id)


def _rebase(cfg: SSSFConfig, adw_id: str) -> str:
    root = main_checkout()
    anchor(cfg, root)
    db = cfg.observability.db
    record = recorded(db, adw_id)
    if not record:
        raise WorktreeError(f"run {adw_id} did not run in a worktree; there is nothing to rebase.")
    branch, path, launch = record["branch"], Path(record["worktree"]), record["launch_branch"]
    if not path.is_dir():
        raise WorktreeError(f"run {adw_id}'s worktree is gone: it was merged or discarded.")
    status = _status(db, adw_id)
    if status != "success":
        raise WorktreeError(f"run {adw_id} is {status}, and only a successful run is rebased "
                            "through this recipe.")
    if dirty(path):
        raise WorktreeError(f"{path} has uncommitted changes. Commit or remove them first.")
    old = git("rev-parse", "--short", "HEAD", cwd=path).stdout.strip()
    if git("merge-base", "--is-ancestor", launch, "HEAD", cwd=path, check=False).returncode == 0:
        return f"{branch} is already on top of {launch}; nothing to rebase. `just merge {adw_id}` lands it."
    result = git("rebase", launch, cwd=path, check=False)
    if result.returncode != 0:
        git("rebase", "--abort", cwd=path, check=False)
        raise WorktreeError(
            f"rebasing {branch} onto {launch} hit a conflict, so it was aborted and the branch "
            f"is still at {old}:\n{(result.stdout + result.stderr).strip()}\n"
            f"To land it, merge it by hand, or `just discard {adw_id}` and run the request again.")
    new = git("rev-parse", "--short", "HEAD", cwd=path).stdout.strip()
    checks = new_id(8)
    passed = _recheck(root, path, record, adw_id, checks)
    payload = {"onto": launch, "old": old, "new": new, "checks": checks, "passed": passed}
    if not passed:
        git("reset", "--hard", old, cwd=path)
        _log(cfg, adw_id, "worktree_rebased", {**payload, "kept": False})
        raise WorktreeError(
            f"{branch} rebased cleanly onto {launch}, but the checks failed on the result "
            f"(run {checks}), so the branch is back at {old}. Nothing else changed.")
    _log(cfg, adw_id, "worktree_rebased", {**payload, "kept": True})
    return (f"rebased {branch} onto {launch}, {old} to {new}, and the checks passed (run "
            f"{checks}). `just merge {adw_id}` lands it.")


def _recheck(root: Path, path: Path, record: dict, adw_id: str, checks: str) -> bool:
    """Run the quality chain in the run's worktree, as its own traced run."""
    env = os.environ.copy()
    if record.get("environment"):
        env["UV_PROJECT_ENVIRONMENT"] = record["environment"]
    done = subprocess.run(
        ["uv", "run", str(root / "adws" / "adw_quality.py"),
         f"recheck {adw_id} after `just rebase`", "--adw-id", checks],
        cwd=path, env=env, stdout=sys.stderr)
    return done.returncode == 0


def _remove(root: Path, path: Path, branch: str, environment: str | None,
            force_branch: bool) -> str:
    """Remove the run's worktree, branch, and environment. Returns a note when
    the environment is on the other side of the container, where this process
    can't reach it, or "" when everything is gone."""
    if path.is_dir():
        # --force: a finished run's worktree holds a .venv and other ignored or
        # untracked build output. Its commits are already on the branch.
        git("worktree", "remove", "--force", str(path), cwd=root)
    git("worktree", "prune", cwd=root)
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root,
           check=False).returncode == 0:
        git("branch", "-D" if force_branch else "-d", branch, cwd=root)
    if not environment:
        return ""
    if Path(environment).is_dir():
        shutil.rmtree(environment, ignore_errors=True)
        return " and its environment"
    if Path(environment).parent.is_dir():
        return ""   # never built: the run synced nothing
    return (f"; its environment {environment} is in the container, so remove it from there "
            f"with `rm -rf {environment}`")

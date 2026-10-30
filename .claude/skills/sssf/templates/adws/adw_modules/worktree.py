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

Design: docs/designs/branch-per-run.md in the SSSF repo.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from pathlib import Path

from .data_types import SSSFConfig

BRANCH_PREFIX = "sssf/"


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
    """adw_ids of worktree runs whose chain process is still alive."""
    rows = _query(db, "select distinct p.adw_id, p.pid from processes p "
                      "join events e on e.adw_id = p.adw_id and e.name = 'worktree' "
                      "where p.kind = 'adw' and p.ended_at is null and p.adw_id != ?", exclude)
    return sorted({adw_id for adw_id, pid in rows if pid and _alive(pid)})


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
    others = live_runs(db, exclude=adw_id)
    if others:
        raise WorktreeError(
            f"worktree run {', '.join(others)} is still going in this repo, and only one runs "
            "at a time for now. Wait for it, or stop it with `just kill <adw_id>`.")
    git("worktree", "prune")
    branch = branch_for(adw_id)
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
        raise WorktreeError(f"branch {branch} already exists. Merge or delete it, or use "
                            "another adw_id.")
    git("worktree", "add", "-b", branch, str(path), "HEAD")
    os.chdir(path)
    return {"branch": branch, "worktree": str(path), "launch_branch": launch,
            "base": git("rev-parse", "--short", "HEAD").stdout.strip(),
            "launch_dirty": dirty(root), "resumed": False}


def _status(db: str, adw_id: str) -> str | None:
    rows = _query(db, "select status from sessions where adw_id=?", adw_id)
    return rows[0][0] if rows else None


def merge(cfg: SSSFConfig, adw_id: str, ff_only: bool = False) -> str:
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
            f"it, and this merge is fast-forward only. Nothing was changed. To land it, rebase "
            f"the branch yourself: `git -C {path} rebase {launch}`, check the result, then "
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
    _remove(root, path, branch, force_branch=False)
    return f"merged {branch} into {launch} ({how}), now at {after}; removed its worktree and branch"


def discard(cfg: SSSFConfig, adw_id: str) -> str:
    """Remove a run's worktree and branch, whatever state they are in."""
    root = main_checkout()
    anchor(cfg, root)
    db = cfg.observability.db
    record = recorded(db, adw_id)
    if not record:
        raise WorktreeError(f"run {adw_id} did not run in a worktree; there is nothing to discard.")
    if adw_id in live_runs(db):
        raise WorktreeError(f"run {adw_id} is still going. Stop it with `just kill {adw_id}` first.")
    _remove(root, Path(record["worktree"]), record["branch"], force_branch=True)
    return f"discarded {record['branch']} and its worktree"


def _remove(root: Path, path: Path, branch: str, force_branch: bool) -> None:
    if path.is_dir():
        # --force: a finished run's worktree holds a .venv and other ignored or
        # untracked build output. Its commits are already on the branch.
        git("worktree", "remove", "--force", str(path), cwd=root)
    git("worktree", "prune", cwd=root)
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root,
           check=False).returncode == 0:
        git("branch", "-D" if force_branch else "-d", branch, cwd=root)

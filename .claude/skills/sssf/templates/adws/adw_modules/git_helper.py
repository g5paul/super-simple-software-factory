"""Low-level git operations for code phases. All low-level logic lives in adw_modules."""

from __future__ import annotations

import subprocess
from pathlib import Path


# What `just sssf-update` writes. It is SSSF's work, not the project's, so a
# reviewer or documenter shown the project's change never sees it.
SSSF_PATHS = ("adws/", ".claude/skills/sssf/")


def _outside(exclude) -> list[str]:
    """A pathspec that leaves out `exclude`, or nothing when it is empty."""
    return ["--", ".", *(f":(exclude){p}" for p in exclude)] if exclude else []


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def create_branch(name: str) -> str:
    _git("checkout", "-b", name)
    return name


def is_repo() -> bool:
    result = subprocess.run(["git", "rev-parse", "--git-dir"],
                            capture_output=True, text=True)
    return result.returncode == 0


def repo_root() -> Path:
    """Absolute root of the codebase — where agents are spawned to work.

    The git toplevel when there is one, else the process cwd (ADWs run fine in a
    non-git dir; only a commit phase requires a repo). Always absolute, so it is
    safe to hand to a subprocess regardless of where the ADW was launched from.
    """
    if is_repo():
        return Path(_git("rev-parse", "--show-toplevel")).resolve()
    return Path.cwd().resolve()


def commit_all(message: str) -> str:
    """Stage the working tree and commit it. Returns the new short sha."""
    if not is_repo():
        raise RuntimeError(
            "not a git repository — a commit phase needs one. Run `git init` in the "
            "repo root (and make a first commit) before running an ADW that commits.")
    _git("add", "-A")
    if not _git("status", "--porcelain"):
        raise RuntimeError("nothing to commit — the preceding phases changed no files")
    _git("commit", "-m", message)
    return _git("rev-parse", "--short", "HEAD")


def feature_commits(feature: str) -> list[tuple[str, str]]:
    """(sha, step) for every commit on HEAD carrying `SSSF-Feature: <feature>`,
    oldest first. `step` is its `SSSF-Step` trailer, or "". adw_tickets writes
    both trailers, so a rerun can find where the feature started and whether
    it was finished."""
    out = _git("log", "--reverse",
               "--format=%H%x09%(trailers:key=SSSF-Feature,valueonly,separator=%x2C)"
               "%x09%(trailers:key=SSSF-Step,valueonly,separator=%x2C)")
    found = []
    for line in out.splitlines():
        sha, name, step = (line.split("\t") + ["", ""])[:3]
        if name.strip() == feature:
            found.append((sha, step.strip()))
    return found


def log_oneline(base: str, exclude: tuple[str, ...] = ()) -> str:
    """The commits after `base`, one line each, oldest first. A commit that
    touches only `exclude` paths is left out."""
    return _git("log", "--reverse", "--oneline", f"{base}..HEAD", *_outside(exclude))


def work_in_progress(exclude: str = "") -> list[str]:
    """`git status --porcelain` lines, leaving out `exclude` (the run's own data dir)."""
    pathspec = ["--", ".", f":!{exclude}"] if exclude else []
    out = _git("status", "--porcelain", *pathspec)
    return [line for line in out.splitlines() if line]


def tracked(path: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", path],
                          capture_output=True).returncode == 0


def ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", path],
                          capture_output=True).returncode == 0


def changed_files() -> list[str]:
    out = _git("status", "--porcelain")
    return [line[3:] for line in out.splitlines() if line]


# ── diff plumbing (composed into a ChangeSet by documentation.py) ────────────

def ref_exists(ref: str) -> bool:
    """True when `ref` resolves to a commit. Never raises — this is a question."""
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            capture_output=True, text=True)
    return result.returncode == 0


def rev(ref: str = "HEAD") -> str:
    return _git("rev-parse", ref)


def short_sha(ref: str = "HEAD") -> str:
    return _git("rev-parse", "--short", ref)


def merge_base(ref: str, other: str = "HEAD") -> str:
    """The commit where `ref` and `other` diverged — the honest base of a branch.

    On the base branch itself this returns HEAD, which makes the diff exactly
    "what is not committed yet". Off it, the diff is the whole branch plus the
    working tree. One command covers both cases, so no ADW has to branch on it.
    """
    return _git("merge-base", ref, other)


def is_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def untracked_files(exclude: tuple[str, ...] = ()) -> list[str]:
    out = _git("ls-files", "--others", "--exclude-standard", *_outside(exclude))
    return [line for line in out.splitlines() if line]


def diff_files(base: str, exclude: tuple[str, ...] = ()) -> list[str]:
    """Tracked files that differ between `base` and the working tree."""
    out = _git("diff", "--name-only", base, *_outside(exclude))
    return [line for line in out.splitlines() if line]


def diff_stat(base: str, exclude: tuple[str, ...] = ()) -> str:
    return _git("diff", "--stat", base, *_outside(exclude))


def diff_counts(base: str, exclude: tuple[str, ...] = ()) -> tuple[int, int]:
    """(insertions, deletions) across the diff. Binary files count as neither."""
    insertions = deletions = 0
    for line in _git("diff", "--numstat", base, *_outside(exclude)).splitlines():
        added, removed, *_ = line.split("\t")
        if added.isdigit():
            insertions += int(added)
        if removed.isdigit():
            deletions += int(removed)
    return insertions, deletions


def diff_text(base: str, exclude: tuple[str, ...] = ()) -> str:
    return _git("diff", base, *_outside(exclude))

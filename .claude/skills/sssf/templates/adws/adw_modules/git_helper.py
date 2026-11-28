"""Low-level git operations for code phases. All low-level logic lives in adw_modules."""

from __future__ import annotations

from pathlib import Path

from . import sandbox


# What `just sssf-update` writes. It is SSSF's work, not the project's, so a
# reviewer or documenter shown the project's change never sees it. The ticket
# review shows the feature's own changes to these paths in a separate section.
SSSF_PATHS = ("adws/", ".claude/skills/sssf/")


def _outside(exclude) -> list[str]:
    """A pathspec that leaves out `exclude`, or nothing when it is empty."""
    return ["--", ".", *(f":(exclude){p}" for p in exclude)] if exclude else []


def _git(*args: str) -> str:
    result = sandbox.current().run(["git", "-c", "core.fsmonitor=", *args])
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD")


def create_branch(name: str) -> str:
    _git("checkout", "-b", name)
    return name


def is_repo() -> bool:
    result = sandbox.current().run(["git", "-c", "core.fsmonitor=", "rev-parse", "--git-dir"])
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


def blob_id(text: str) -> str:
    """The blob id git gives `text`, hashed on the host (never in the VM). Nothing is
    written and no filter runs."""
    import subprocess
    return subprocess.run(["git", "-c", "core.fsmonitor=", "hash-object", "--stdin"],
                          input=text.encode(), capture_output=True, check=True).stdout.decode().strip()


def commit_all(message: str, only: list[str] | None = None, *, run=None,
               wrote: dict[str, str] | None = None) -> str:
    """Stage the working tree, or just the `only` paths, and commit it. Returns
    the new short sha. With `only`, other changes stay in the tree for a later
    commit. With `only`, the commit holds those paths and nothing else that is
    staged (`commit --only`), and what is staged elsewhere stays staged.
    A rename inside `only` is committed as both halves, the delete and the add
    (`--no-renames`).

    The commit is judged against the agents' `writes` (permissions.judge_commits).
    In a VM the sandbox's `commit` brings the commit back and judges it before the
    host branch moves. From the chain's first verify round until it calls
    `sandbox.verify_done()`, the commit must hold the last green round's tree,
    except the `wrote` paths, or it is refused (before a first green, always). Locally, with a `run`, a refused commit is undone (the tree
    is kept) and PermissionBreach is raised. `wrote` maps each path the chain itself
    wrote into this commit to the blob it wrote; the path passes only with that blob
    (a list of paths is a TypeError). With `run=None` nothing is checked. A passing `only`
    commit ends the agents' grant inside its entries until the next full commit."""
    if not is_repo():
        raise RuntimeError(
            "not a git repository — a commit phase needs one. Run `git init` in the "
            "repo root (and make a first commit) before running an ADW that commits.")
    from . import permissions
    wrote = permissions.wrote_map(wrote)
    scope = ["--", *only] if only else []
    _git("add", "-A", *scope)
    # --no-renames: a rename is listed as both its halves, so a plan commit that
    # holds `git mv a b` also holds the delete of `a`, never only the add of `b`.
    staged = [n for n in _git("diff", "--cached", "--no-renames", "--name-only", "-z",
                              *scope).split("\0") if n]
    if not staged:
        raise RuntimeError("nothing to commit — the preceding phases changed no files")
    # `commit --only` refuses a pathspec git knows nothing about (an empty or
    # fully ignored folder), so it gets the staged files, not the raw `only`.
    argv = ["commit", "-m", message, *(["--only", "--", *staged] if only else [])]
    box = sandbox.current()
    if box is sandbox.LOCAL:
        try:
            before = _git("rev-parse", "--verify", "-q", "HEAD")
        except RuntimeError:
            before = ""                 # no commit yet: this one is the root, nothing to judge it against
        _git(*argv)
        if run is not None and before:
            from . import permissions
            permissions.check_local_commit(run, before, only=only, wrote=wrote)
    elif hasattr(box, "commit"):
        result = box.commit(["git", "-c", "core.fsmonitor=", *argv], only=only, wrote=wrote)
        if result.returncode != 0:
            raise RuntimeError(f"git {' '.join(argv)} failed: {result.stderr.strip()}")
    else:
        _git(*argv)
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
    return sandbox.current().run(["git", "-c", "core.fsmonitor=", "ls-files", "--error-unmatch", path]).returncode == 0


def ignored(path: str) -> bool:
    return sandbox.current().run(["git", "-c", "core.fsmonitor=", "check-ignore", "-q", path]).returncode == 0


def changed_files() -> list[str]:
    out = _git("status", "--porcelain")
    return [line[3:] for line in out.splitlines() if line]


# ── diff plumbing (composed into a ChangeSet by documentation.py) ────────────

def ref_exists(ref: str) -> bool:
    """True when `ref` resolves to a commit. Never raises — this is a question."""
    result = sandbox.current().run(["git", "-c", "core.fsmonitor=", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return result.returncode == 0


def rev(ref: str = "HEAD") -> str:
    return _git("rev-parse", ref)


def changed_in(sha: str, paths: tuple[str, ...]) -> tuple[str, list[str]]:
    """(subject, files) a commit changed under `paths`. `-m --first-parent`
    makes a merge commit list everything it changed against its first parent;
    without them git shows a combined diff that leaves files out."""
    out = _git("show", "-m", "--first-parent", "--format=%s", "--name-only", sha, "--", *paths)
    lines = out.splitlines()
    return (lines[0] if lines else ""), [ln for ln in lines[1:] if ln.strip()]


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

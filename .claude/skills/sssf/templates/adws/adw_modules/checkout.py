"""Find the repo's main checkout, and so the one `.env` SSSF loads. Standard library only.

One lookup for `utils.py`, `worktree.py` and the template justfile's
`set dotenv-command`, which runs this file as a script (`python -I checkout.py --dotenv`)
before any recipe, with no python-dotenv installed.

Git's location variables are removed from every git call, and the walk for a `.git`
entry is plain path checks, so neither a `.env` nor a planted bare-repo shape
(`HEAD`, `objects/`, `refs/`) can point the lookup at another repo.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# The variables that tell git where a repo is, as `git help git` (ENVIRONMENT) and
# `git rev-parse --local-env-vars` list them. A `.env` a run committed or the engineer's
# shell can set any of them. GIT_CONFIG* stays out on purpose: no config key moves the
# common dir, and a container may need safe.directory from one of them for git to answer.
GIT_LOCATION_VARS = frozenset({
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_IMPLICIT_WORK_TREE", "GIT_PREFIX", "GIT_SHALLOW_FILE",
    "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE", "GIT_NO_REPLACE_OBJECTS",
})


def git_location_env() -> dict[str, str]:
    """A copy of the environment without git's location variables, for the calls that
    find the main checkout or the repo root. os.environ itself is never changed."""
    return {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS}


class NoMainCheckout(RuntimeError):
    """The repo has no main checkout SSSF can find."""


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              env=git_location_env())
    except OSError as failed:
        raise NoMainCheckout(f"git could not start: {failed}") from failed


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "no message")


def _config(common: Path, *flags: str) -> Optional[str]:
    """One value from the common dir's own config file; None when unset."""
    done = _git(["config", "--file", str(common / "config"), *flags], common)
    if done.returncode == 1:
        return None
    if done.returncode != 0:
        raise NoMainCheckout(f"git could not read {common / 'config'}: {_first_line(done.stderr)}")
    return done.stdout.strip()


def _is_worktree_set(common: Path) -> Optional[str]:
    return _config(common, "--get", "core.worktree")


def _is_bare(common: Path) -> bool:
    return _config(common, "--type=bool", "--get", "core.bare") == "true"


def from_common(common: Path) -> Path:
    """The main checkout, from the common dir alone. Reads the common dir's own config
    file, never the environment."""
    common = Path(common)
    set_to = _is_worktree_set(common)
    if set_to:
        return (common / set_to).resolve()          # a submodule: <super>/.git/modules/<name>
    if _is_bare(common):
        raise NoMainCheckout(
            f"This is a bare repo with linked worktrees ({common}). It has no main checkout, "
            "so SSSF has no .env, repo.yaml or trace folder to use. Clone it normally and run "
            "SSSF from the clone.")
    if common.name == ".git":
        return common.parent.resolve()
    raise NoMainCheckout(
        f"This is a linked worktree of a repo whose git dir ({common}) is outside its checkout "
        "(`git init --separate-git-dir`). Git doesn't record where that checkout is, so SSSF "
        "can't find the main checkout from here. Run SSSF from the main checkout, without "
        "`--worktree`.")


def main_checkout_from(start: Path) -> Optional[Path]:
    """The main checkout of the repo `start` is in, or None when it is in no git repo.
    Raises NoMainCheckout where the repo has none to find."""
    here = Path(start).resolve()
    top = next((f for f in (here, *here.parents) if (f / ".git").exists()), None)
    if top is None:
        return None
    done = _git(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"], top)
    if done.returncode != 0:
        raise NoMainCheckout(
            f"git could not tell where the main checkout of the repo at {top} is: "
            f"{_first_line(done.stderr)}. If git calls the folder's ownership dubious, run "
            f"`git config --global --add safe.directory {top}`. Otherwise fix the git "
            "environment (a GIT_CONFIG_* variable, or a config file git can't read).")
    lines = done.stdout.splitlines()
    if len(lines) != 2:
        raise NoMainCheckout(f"git gave an answer SSSF can't read for {top}")
    common, git_dir = Path(lines[0]).resolve(), Path(lines[1]).resolve()
    if git_dir == common and _is_worktree_set(common) is None and not _is_bare(common):
        shown = _git(["rev-parse", "--show-toplevel"], top)
        if shown.returncode != 0:
            raise NoMainCheckout(f"git could not tell where the main checkout of the repo at {top} is: {_first_line(shown.stderr)}")
        return Path(shown.stdout.strip()).resolve()
    return from_common(common)


def _walk_dotenv(start: Path) -> Optional[Path]:
    here = start.resolve()
    return next((f / ".env" for f in (here, *here.parents) if (f / ".env").is_file()), None)


def _dotenv_main(argv: list[str]) -> int:
    if argv != ["--dotenv"]:
        sys.stderr.write("usage: checkout.py --dotenv\n")
        return 2
    try:
        main = main_checkout_from(Path(__file__).resolve().parent)
        env = (_walk_dotenv(Path.cwd()) if main is None else main / ".env")
        if env is not None and env.is_file():
            sys.stdout.flush()
            sys.stdout.buffer.write(env.read_bytes())
            sys.stdout.buffer.flush()
    except NoMainCheckout as stop:
        sys.stderr.write(f"SSSF: {stop}\n")
        return 2
    except OSError as failed:
        sys.stderr.write(f"SSSF: can't read .env: {failed}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(_dotenv_main(sys.argv[1:]))

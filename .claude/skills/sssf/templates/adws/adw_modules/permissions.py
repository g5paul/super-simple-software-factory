"""What an agent may CHANGE, enforced in code after the fact.

`tools:` is a capability list, not a sandbox, and two holes make it
unenforceable on its own:

  * `bash` runs anything. A builder handed bash to run a test suite can also
    run `git checkout adws/` — which is not hypothetical: one did, discarding
    uncommitted changes to the very quality check it was about to be judged by.
  * `write` reaches any path, not just the one report file an agent was given
    it for. A reviewer configured with "no edit, so it cannot quietly fix"
    could still rewrite the code it was reviewing.

So permission is verified the way every other claim in this system is —
after the fact, against the repo itself. `snapshot()` fingerprints the working
tree's change-set before an agent runs; `enforce()` compares it afterwards and
fails the phase if the agent touched anything outside its allowlist.

Comparing change-sets, rather than watching for writes, is what catches the
`git checkout` case: a path that was modified before the agent ran and is clean
afterwards has been reverted, and a reversion is a modification. Appearing,
disappearing, and changing all count.

The change-set is read without renames (a rename is a delete and an add), with
-z (names are never quoted), with the paths an index flag hides from `git diff`
(skip-worktree, assume-unchanged), and with a content hash for each changed,
untracked or flagged path, so a rewrite of a file that was already dirty
registers. Gitignored paths are never seen.

A breach is NOT a gate violation. Gates are for work an agent can be asked to
redo; a breach cannot be corrected by re-prompting, because the write already
happened. It aborts the phase and names every offending path.

An agent that commits would hide its changes from that comparison, since
both fingerprints are taken against HEAD. `head()` records where HEAD was
before the agent ran, and `undo_commits()` resets any commits it made, so
their changes are back in the working tree, unstaged, for `enforce()` to check
and roll back, and for the chain's own commit phase to commit. An agent that moved HEAD anywhere else
(another branch, a rebase, a reset) stops the run instead.

An agent can also move a ref other than HEAD: `git commit-tree` and
`git update-ref refs/heads/main` leave its own branch and tree clean.
`head()` therefore records every ref too, and `enforce()` lists them again
afterwards through `ref_moves()`. A ref other than the run's own branch that
gained commits changing a path the agent is barred from, or any change under
`refs/replace/`, stops the run. The check only reads: nothing is moved back,
deleted or fetched, and no hook, filter or external diff runs. It can't tell
the agent's commit from the engineer's.

Two keys drive it, both in sssf.config.yaml:
    defaults.protected_files   paths no agent may touch unless it names them itself
SSSF's fixed `FACTORY_FILES` are checked on top of `protected_files`, and a
roster can't drop them.
    agents[].writes      None = unrestricted · [] = read-only · [...] = only these
"""

from __future__ import annotations

import re
from pathlib import Path

from . import sandbox
from .data_types import AgentConfig, SSSFConfig


class PermissionBreach(RuntimeError):
    """An agent modified a path it was not permitted to modify."""


# SSSF's own git here (snapshots, the commit undo, the rollback) never runs an
# fsmonitor, and its writes never run a hook: neither is needed, and either
# could be one the agent planted. On the command line, which no config file or
# GIT_CONFIG_PARAMETERS overrides.
_SAFE = ["-c", "core.fsmonitor="]     # empty: off on every git; "false" runs `false` before 2.36
_NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]

def _gitargv(args: list[str], *, writes: bool = False, literal: bool = True) -> list[str]:
    # --literal-pathspecs: a path an agent made, such as `.claude/*` or
    # `app/[id]/page.tsx`, names that file only, never every file it matches.
    # check-ignore takes paths, not pathspecs, and refuses the option.
    return ["git", *_SAFE, *(["--literal-pathspecs"] if literal else []),
            *(_NO_HOOKS if writes else []), *args]


def _git(args: list[str], cwd) -> str:
    result = sandbox.current().run(_gitargv(args), cwd=cwd, text=False)
    if result.returncode != 0:
        return ""
    out = result.stdout
    return out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else out


def _fields(text: str) -> list[str]:
    """NUL-separated output as fields, without the trailing empty one."""
    fields = text.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    return fields


# Prints one NUL-terminated field per path argument: L and the link target for
# a symlink, F and the blob hash for a regular file (no filter or textconv
# runs), D for a directory (a gitlink, a nested repo), - for a missing path.
_CONTENT = """for p in "$@"; do
  if [ -L "$p" ]; then printf "L%s\\0" "$(readlink "$p")"
  elif [ -f "$p" ]; then printf "F%s\\0" "$(git -c core.fsmonitor= hash-object --no-filters -- "$p" 2>/dev/null)"
  elif [ -d "$p" ]; then printf "D\\0"
  else printf -- "-\\0"; fi
done
"""
_CHUNK = 256


def snapshot(run) -> dict[str, str]:
    """Fingerprint every path the working tree currently differs on.

    Each value is `kind:content`. The kind is git's status for the path
    (M, A, D, T), `?` for untracked, and `flag-<tag>` for an index flag
    (skip-worktree, assume-unchanged) that hides a change from `git diff`;
    several are joined with commas, for example `D,?` for a file taken out of
    the index and written again. The content is the file's blob hash, so any
    rewrite of an already-dirty or untracked file registers.

    Renames show as a delete and an add. Names are never quoted (-z). Flagged
    paths show by content. Gitignored paths never appear. The session runtime
    is normally gitignored, but `always_writable` grants it anyway, so a
    missing gitignore line is safe. Only changed, untracked and flagged paths
    are hashed.
    """
    root = run.repo_root
    kinds: dict[str, str] = {}

    def add(path: str, kind: str) -> None:
        kinds[path] = f"{kinds[path]},{kind}" if path in kinds else kind

    diff = _fields(_git(["diff", "HEAD", "--no-renames", "--no-ext-diff", "--no-textconv",
                         "--name-status", "-z"], root))
    for status, path in zip(diff[::2], diff[1::2]):
        add(path, status)
    for path in _fields(_git(["ls-files", "--others", "--exclude-standard", "-z"], root)):
        add(path, "?")
    for record in _fields(_git(["ls-files", "-v", "-z"], root)):
        tag, _, path = record.partition(" ")
        if tag and tag != "H" and path:
            add(path, f"flag-{tag}")

    fingerprints: dict[str, str] = {}
    paths = list(kinds)
    for i in range(0, len(paths), _CHUNK):
        chunk = paths[i:i + _CHUNK]
        contents: list[str] | None = None
        try:
            done = sandbox.current().run(["sh", "-c", _CONTENT, "sh", *chunk], cwd=root, text=False)
            out = done.stdout
            out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
            contents = _fields(out)
        except Exception:
            contents = None
        if contents is None or len(contents) != len(chunk):
            # A broken sandbox, not something the agent controls. If this fails
            # both before and after the call, a rewrite of an already-dirty or
            # untracked file in the chunk looks the same on both sides and isn't
            # seen. Appearing, vanishing and a status change still are.
            contents = ["?"] * len(chunk)
        for path, content in zip(chunk, contents):
            fingerprints[path] = f"{kinds[path]}:{content}"
    return fingerprints


REFS_UNREAD = "(refs unread)"
REF_COMMITS_SHOWN = 20
REF_PATHS_SHOWN = 10
# Where plan phases commit specs/<adw_id>_<slug>.md on their own sssf/* branch.
# ref_moves() doesn't judge a regular .md file directly in it on other refs, or
# every plan commit would stop every other live run. Anything else under it
# counts. .scratch/ is not here: adw_tickets hands a ticket under it straight
# to ticket_builder.
REF_SPECS = "specs/"
# Never a spec file: a harness loads these as instructions when an agent reads
# the folder, and planners read specs/. Compared lowercased, for a
# case-insensitive disk.
REF_SPEC_NOT = ("claude.md", "claude.local.md", "agents.md")
# Every git command of the ref check starts with this: no transport for a child
# fetch (empty GIT_ALLOW_PROTOCOL refuses all), no lazy fetch, no fsmonitor, no
# commit-graph, no replace objects, no grafts (--no-replace-objects doesn't turn
# off info/grafts; the advice flag keeps git's grafts hint off stderr, which it
# prints whenever the graft file opens) and a fixed locale.
_REF_READ = ("env", "GIT_ALLOW_PROTOCOL=", "GIT_NO_LAZY_FETCH=1", "GIT_GRAFT_FILE=/dev/null",
             "LC_ALL=C", "git", "-c", "core.fsmonitor=", "-c", "core.commitGraph=false",
             "-c", "advice.graftFileDeprecated=false", "--no-replace-objects")
# Per commit, a `:<old mode> <new mode> <old sha> <new sha> <status>` field and a
# NUL-terminated path for each change, then one empty field. A path is never
# empty, so the empty field ends a commit's list. The words are, per
# commit, `<commit> [<parent> [<parent>]] .`. A group with no parent is read as
# always: -m lists a merge against each parent git finds, --root a root against
# nothing. A group with parents is diffed against each pair, which ref_moves()
# does only for a commit of at most three parents with a stash's untracked root
# (not the first parent, a new commit, a real root) left out. So a
# call names at most 256 commits and two parents each, 256 * (3 * 65 + 2) =
# about 50 KB at worst, whatever a commit holds. --raw reads no blob; it gives
# the modes, so a link or an executable file isn't taken for a spec file.
# diff-tree, not log: log honours log.showSignature and can run gpg.program.
_COMMIT_PATHS = """g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false --no-replace-objects diff-tree -r --raw -z --no-renames --no-ext-diff --no-textconv "$@"; }
while [ $# -gt 0 ]; do
  c=$1; shift
  if [ "$1" = . ]; then
    g -m --root --no-commit-id "$c" || exit 1
  fi
  while [ $# -gt 0 ] && [ "$1" != . ]; do
    g "$1" "$c" || exit 1
    shift
  done
  [ $# -gt 0 ] || exit 1
  shift
  printf '\\0'
done
"""
# One character per commit argument: r for a real root (the raw object's first
# line is the tree and the second isn't a parent), p for a commit with a parent,
# ? for an object that can't be read as a commit. rev-list --parents honours
# .git/shallow and shows a commit listed there without its parents, so only this
# tells a root from one that is disguised as one.
_COMMIT_ROOTS = """g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false --no-replace-objects "$@"; }
for c in "$@"; do
  g cat-file commit "$c" 2>/dev/null | {
    IFS= read -r t; IFS= read -r p
    case $t in
      "tree "*) case $p in "parent "*) printf p ;; *) printf r ;; esac ;;
      *) printf '?' ;;
    esac
  }
done
"""


def _head(run) -> dict[str, str]:
    return {"sha": _git(["rev-parse", "-q", "--verify", "HEAD"], run.repo_root).strip(),
            "branch": _git(["symbolic-ref", "-q", "HEAD"], run.repo_root).strip()}


def _refs(run) -> str:
    """Every ref as `<sha> <name>` lines, or REFS_UNREAD when git can't list them."""
    try:
        done = sandbox.current().run([*_REF_READ, "for-each-ref", "--format=%(objectname) %(refname)"],
                                     cwd=run.repo_root, text=False)
    except Exception:
        return REFS_UNREAD
    if done.returncode != 0:
        return REFS_UNREAD
    out = done.stdout
    return out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")


def head(run) -> dict[str, str]:
    """Where HEAD is (its commit and the branch it is on, empty when detached)
    and every ref in the repo, as `git for-each-ref` lists them.

    The refs are what `ref_moves()` compares after the call. Nothing here
    moves, opens or fetches anything.
    """
    return {**_head(run), "refs": _refs(run)}


def undo_commits(run, before: dict[str, str]) -> list[str]:
    """Undo commits an agent made itself, keeping their changes unstaged in the tree.

    Returns the short shas undone, newest first, or [] when HEAD did not move.
    Only HEAD's sha and branch are compared, never the recorded refs: a moved
    tag or another branch is `ref_moves()`'s, and sending it into a reset would
    unstage the engineer's staged work. Unstaged matters: a reset keeps the changes in the tree, and _roll_back
    restores a clean path from HEAD. Raises PermissionBreach when HEAD
    moved somewhere a reset can't safely
    bring back: onto another branch, or to a commit that doesn't descend from
    where it was.
    """
    after = _head(run)
    if (after["sha"], after["branch"]) == (before["sha"], before["branch"]):
        return []
    if after["branch"] != before["branch"]:
        raise PermissionBreach(
            f"The agent switched HEAD from {before['branch'] or 'a detached HEAD'} to "
            f"{after['branch'] or 'a detached HEAD'}. Nothing was undone; switch back by hand.")
    if before["sha"]:
        descends = sandbox.current().run(
            _gitargv(["merge-base", "--is-ancestor", before["sha"], after["sha"]]),
            cwd=run.repo_root).returncode == 0
        if not descends:
            raise PermissionBreach(
                f"The agent moved HEAD from {before['sha'][:7]} to {after['sha'][:7]}, "
                "which doesn't descend from it (a reset or rebase). Nothing was undone.")
        shas = _git(["rev-list", "--abbrev-commit", f"{before['sha']}..{after['sha']}"],
                    run.repo_root).split()
        undo = _gitargv(["reset", "-q", before["sha"]], writes=True)
    else:
        shas = _git(["rev-list", "--abbrev-commit", after["sha"]], run.repo_root).split()
        undo = _gitargv(["update-ref", "-d", "HEAD"], writes=True)
    failed = sandbox.current().run(undo, cwd=run.repo_root).returncode != 0
    if not failed and not before["sha"]:
        failed = sandbox.current().run(_gitargv(["rm", "-rq", "--cached", "."], writes=True),
                                       cwd=run.repo_root).returncode != 0
    if failed:
        raise PermissionBreach(f"The agent made commits {', '.join(shas)}, and undoing them failed.")
    return shas


# ── the git directory and git config ─────────────────────────────────────────
# `git status` never shows the git directory, so snapshot() cannot see an
# agent write there. Some of it runs code: a hook in hooks/ runs on the
# chain's own commit, and a core.fsmonitor, core.pager or filter line in
# config runs on ordinary git commands. A line in info/exclude hides a new
# file from snapshot(), and commondir points git at another directory
# altogether. The user's git config is just as live: `git config --global
# core.hooksPath` points every commit at the agent's hooks. So these paths
# are packed before each agent, as a tar stream held in this process where
# the agent can't edit it, and unpacked over the live paths after it if they
# changed. A change stops the run. A change to a shared config file
# outside the repo also stops it. The breach lists the changed keys, since the
# change may be the engineer's own. No agent has a reason to write them: SSSF
# does the committing.
#
# It catches mistakes and casual attempts. An agent runs as the same user as
# SSSF, so a determined one could still get past it, for example by killing
# the chain before the check runs. --sandbox and --container isolate.

# sssf-private-names lists the projects the pre-push hook refuses to name, so
# an agent that empties or deletes it turns that check off for the engineer's
# next push. The file is optional, and one the agent creates counts as a
# change too.
#
# info/refs is left out: git gc and repack rewrite it, and it runs nothing.
GIT_WATCHED = ("hooks", "info/exclude", "info/attributes", "info/sparse-checkout",
               "info/grafts", "config", "config.worktree", "commondir",
               "sssf-private-names")

# Packs the watched paths that exist, relative to /. With "h", follows
# symlinks, so a write through a linked ~/.gitconfig shows; that pack is only
# compared, never unpacked, and a dangling link in it is not an error. It
# still stops at the cap, and one that reaches the cap can't be checked.
_PACK = """h=$1; shift
n=$#
for p in "$@"; do
  if [ -e "$p" ] || [ -L "$p" ]; then set -- "$@" "${p#/}"; fi
done
shift $n
[ $# = 0 ] && exit 0
cd / || exit 1
if [ "$h" = h ]; then
  tar -chf - "$@" 2>/dev/null | head -c $((PACK_CAP + 1)) | base64
else
  { tar -cf - "$@" || echo "tar failed" >&2; } | head -c $((PACK_CAP + 1)) | base64
fi
"""

# A symlink to / in hooks would have the follow pack read the whole disk.
# Each pack stops at this size. A pack of either kind that is over it can't be
# checked: before the agent, that stops the run before the agent runs; after
# it, it counts as a change.
PACK_CAP = 16 * 1024 * 1024
# git's own words when a directory isn't in any repo, in the C locale.
NOT_A_REPO = "fatal: not a git repository"

# Puts back the changed paths, and only those. Each is a physical path with no
# symlink in it, checked just before this runs, so rm -rf and tar act where
# the path was when the agent started. Links are never written. The exit code
# is not trusted; the restore compares again afterwards.
_UNPACK = """f=$1; shift
for p in "$@"; do rm -rf "$p" || failed=1; done
for p in "$@"; do
  base64 -d < "$f" | tar -xpf - -C / "${p#/}" 2>/dev/null
done
rm -f "$f"
exit ${failed:-0}
"""


# Prints each path with its parent resolved physically, NUL-separated, or an
# empty field where the parent doesn't exist yet or can't be entered. It prints
# `?` where cd -P enters the parent but pwd -P can't name it (a physical path
# over the system limit under a directory that can't be read; dash's pwd -P
# exits 0 then, so the output must start with /). Callers treat any field not
# starting with / as not resolving. The restore writes only under "", never
# under `?`, since nothing can check where the links on the way to `?` now
# point. The x sentinel keeps a trailing newline in a directory's name, so `d`
# and `d\n` stay different.
_REAL = """for p in "$@"; do
  d=${p%/*}; b=${p##*/}
  if [ "$b" = .. ] || [ "$b" = . ]; then d=$p; b=; fi
  if r=$(cd -P "${d:-/}" 2>/dev/null && { pwd -P 2>/dev/null && echo x || :; }); then
    r=${r%x}; r=${r%?}
    case $r in
      /*) if [ -n "$b" ]; then r="${r%/}/$b"; fi; printf "%s\\0" "$r" ;;
      *) printf "?\\0" ;;
    esac
  else
    printf "\\0"
  fi
done
"""


# Prints, NUL-separated, a triple for every prefix of each path as written,
# the kernel's way: the prefix, the directory it resolves to (cd -P; empty if
# it isn't a directory that can be entered, or whose physical path pwd -P
# can't name), and L if it is a symlink, else -.
# Empty components are skipped; . and .. are kept, as the kernel takes them.
_WHERE = """for p in "$@"; do
  rest=${p#/}; q=
  while [ -n "$rest" ]; do
    c=${rest%%/*}
    case $rest in */*) rest=${rest#*/};; *) rest=;; esac
    [ -z "$c" ] && continue
    q="$q/$c"
    r=$(cd -P "$q" 2>/dev/null && pwd -P 2>/dev/null && echo x) || r=
    if [ -n "$r" ]; then r=${r%x}; r=${r%?}; fi
    case $r in /*) ;; *) r= ;; esac
    if [ -L "$q" ]; then l=L; else l=-; fi
    printf "%s\\0%s\\0%s\\0" "$q" "$r" "$l"
  done
done
"""


def _where(walked: list[str]) -> dict[str, list[str]] | None:
    """Where each prefix of `walked` resolves: {prefix: [dir or "", "L" or "-"]},
    or None if the output can't be read."""
    if not walked:
        return {}
    fields = _sh(_WHERE, *walked).stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 3:
        return None
    found: dict[str, list[str]] = {}
    for i in range(0, len(fields), 3):
        found.setdefault(fields[i], [fields[i + 1], fields[i + 2]])
    return found


def _moved(guard: dict, *, gone: bool = True) -> list[str] | None:
    """The recorded prefixes that resolved before the agent and now resolve
    somewhere else or nowhere, or None if they could not be walked again.
    With gone=False, only those the restore must not write under: ones that
    resolve somewhere else, or that resolve nowhere and were or are a symlink.
    """
    then = guard.get("where", {})
    if not then:
        return []
    now = _where(guard.get("walked", []))
    if now is None:
        return None
    none = ["", "-"]
    # A prefix that did not resolve before is never compared; the path packs
    # cover it. One that resolved before and now resolves anywhere else, or
    # nowhere (missing, a regular file, unsearchable, a dangling link), has
    # moved. With gone=False, a real directory that now resolves nowhere and
    # isn't a link is left out: when the restore puts back a watched file under
    # it, tar remakes it as a real directory, so writing there is as safe as
    # at the baseline.
    return [q for q, (r, l) in then.items()
            if r and now.get(q, none)[0] != r
            and (gone or now.get(q, none)[0] or l == "L" or now.get(q, none)[1] == "L")]


def _sh(script: str, *args: str, timeout: float | None = None):
    """Run a guard script. Output is decoded leniently: a Latin-1 name in a
    path must not stop the run."""
    import subprocess
    done = sandbox.current().run(["sh", "-c", script, "sh", *args], text=False,
                                    timeout=timeout)
    out, err = done.stdout, done.stderr
    if isinstance(out, bytes):
        out = out.decode("utf-8", "surrogateescape")
    if isinstance(err, bytes):
        err = err.decode("utf-8", "surrogateescape")
    return subprocess.CompletedProcess(done.args, done.returncode, out, err)


def _watched(run) -> tuple[list[str], list[str], dict[str, str]]:
    """Every path the guard covers.

    The repo's git directories (the shared one and a worktree's own) and a
    worktree's `.git` file. Every config file git loads here, by `git config
    --show-origin`, and every file an include names, even an empty one. The
    directory `core.hooksPath` points at. And the global config's usual
    homes, even before they exist. Never the credential store: copying it
    would copy tokens.

    The third value maps a resolved path to "config" or "lines" for each
    config, ignore or attributes file whose physical path is outside the
    repo's git directories and outside the work tree at `run.repo_root`
    (the run's own, in a worktree run): files shared with everything else
    the engineer runs. A file in the work tree is the agent's to edit, so
    it isn't one, however its path was written.

    The first value is every watched path as written plus its normalized form;
    `guard_git_dir` records where each of their prefixes resolves.

    A watched path with `..` that doesn't fully resolve, because a part of it
    doesn't exist yet, can't be entered, or can't be named by `pwd -P`, stops the run with
    `PermissionBreach`, since the guard can't know which file git will read.

    A failed `rev-parse` in a repo (the root has a `.git`), or one that fails
    with any error but not-a-repo, raises `PermissionBreach` with git's error.
    The empty result means only "not a git repo". A `rev-parse --git-path hooks`
    that fails, for example because `core.hooksPath` resolves to a path longer
    than the system limit, raises `PermissionBreach` with git's error, since no
    hooks would be watched.
    """
    root = str(run.repo_root).rstrip("/")
    found = sandbox.current().run(
        ["env", "LC_ALL=C", *_gitargv(["rev-parse", "--path-format=absolute",
                                       "--git-common-dir", "--git-dir"])],
        cwd=root, text=False)
    out, err = found.stdout, found.stderr
    out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
    err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
    dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    if found.returncode != 0 or not dirs:
        dot_git = _sh('[ -e "$1" ] || [ -L "$1" ]', f"{root}/.git").returncode == 0
        if not dot_git and err.lstrip().startswith(NOT_A_REPO):
            return [], [], {}
        raise PermissionBreach(_unreadable(err, found.returncode))
    paths = [f"{d}/{name}" for d in dirs for name in GIT_WATCHED]
    kind: dict[str, str] = {}
    named: dict[str, tuple[str, str]] = {}

    def mark(path: str, what: str) -> None:
        if kind.get(path) != "config":      # "config" wins over "lines"
            kind[path] = what
    logical = [f"{root}/.git"]            # a symlinked .git is a link on the way
    if sandbox.current().is_file(f"{root}/.git"):
        paths.append(f"{root}/.git")
    home = _sh('printf "%s\\0%s" "$HOME" "${XDG_CONFIG_HOME:-$HOME/.config}"').stdout.split("\0")
    home = home if len(home) == 2 and home[0].startswith("/") else None

    def absolute(path: str, beside: str) -> str:
        if path.startswith("~/") and home:
            return f"{home[0]}/{path[2:]}"
        return path if path.startswith("/") else f"{beside.rsplit('/', 1)[0]}/{path}"

    listed = sandbox.current().run(_gitargv(["config", "--list", "--show-origin", "-z"]),
                                   cwd=root, text=False).stdout
    if isinstance(listed, bytes):     # a Latin-1 name in a config must not stop the run
        listed = listed.decode("utf-8", "surrogateescape")
    fields = listed.split("\0")
    for origin, entry in zip(fields[::2], fields[1::2]):
        if not origin.startswith("file:"):
            continue
        source = absolute(origin[5:], f"{root}/x")
        paths.append(source)
        mark(source, "config")
        key, _, value = entry.partition("\n")
        if key.lower() in ("core.excludesfile", "core.attributesfile"):
            paths.append(absolute(value, source))
            named.setdefault(paths[-1], (key, source))
            mark(paths[-1], "lines")
        if key.lower() == "include.path" or (key.lower().startswith("includeif.")
                                             and key.lower().endswith(".path")):
            paths.append(absolute(value, source))
            named.setdefault(paths[-1], (key, source))
            mark(paths[-1], "config")
    named_hooks = sandbox.current().run(
        _gitargv(["rev-parse", "--path-format=absolute", "--git-path", "hooks"]),
        cwd=root, text=False)
    if named_hooks.returncode != 0:
        err = named_hooks.stderr
        err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
        raise PermissionBreach(_no_hooks_dir(err, named_hooks.returncode))
    hooks = named_hooks.stdout
    hooks = (hooks.decode("utf-8", "surrogateescape") if isinstance(hooks, bytes)
             else (hooks or "")).strip()
    if hooks.startswith("/"):
        paths.append(hooks)
    if home:
        paths += [f"{home[0]}/.gitconfig", f"{home[1]}/git/config",
                  f"{home[1]}/git/attributes", f"{home[1]}/git/ignore"]
        mark(f"{home[0]}/.gitconfig", "config")
        mark(f"{home[1]}/git/config", "config")
        mark(f"{home[1]}/git/attributes", "lines")
        mark(f"{home[1]}/git/ignore", "lines")
    # The physical path, as git resolves it: tar renames a member with ".." in
    # its path, and won't extract under a parent that is an absolute symlink,
    # so the restore would delete such a file and fail to put it back. Each
    # parent is resolved with cd -P where it exists, lexically where it doesn't,
    # except that a path with `..` that doesn't resolve refuses the run.
    import posixpath
    hooks_value = _git(["config", "core.hooksPath"], root).strip()
    if hooks_value:
        logical.append(absolute(hooks_value, f"{root}/x"))
    paths = [p for p in dict.fromkeys(paths)
             if p.startswith("/") and p.rsplit("/", 1)[-1] != "credentials" and "\n" not in p]
    written = [p for p in dict.fromkeys(logical + paths) if p.startswith("/")]
    logical = ["/" + posixpath.normpath(p).lstrip("/") for p in written]
    walked = list(dict.fromkeys(written + logical))
    real = _sh(_REAL, *paths).stdout.split("\0")
    if len(real) != len(paths) + 1:
        raise PermissionBreach("Could not resolve the git paths to watch, so the agent did not run.")
    # A path with ".." that cd -P can't resolve, or whose parent pwd -P can't
    # name (the "?" field), would take the lexical fallback
    # below, but the kernel resolves ".." physically, maybe through a link, once
    # the missing part exists, so git can read a file the guard doesn't watch.
    loose = [p for p, r in zip(paths, real) if not r.startswith("/") and ".." in p.split("/")]
    if loose:
        def which(p: str) -> str:
            key, source = named.get(p, ("", ""))
            return (f"  - {_shown_key(key)} in {source}: {p}" if key else f"  - {p}")
        raise PermissionBreach(
            "A git path SSSF watches has `..` after a directory that doesn't exist yet, "
            "that can't be entered, or whose real path can't be read. Once that changes, git resolves `..` through it and "
            "can read a different file from the one SSSF watches, so the agent did not run, "
            "and no run will until the path is fixed:\n"
            + "\n".join(which(p) for p in loose) + "\n\n"
            "Write the path without `..`, or create the missing directory first, then run again.")
    resolved = []
    kinds: dict[str, str] = {}
    tree = _sh(_REAL, f"{root}/.").stdout.split("\0")[0]
    tree = tree if tree.startswith("/") else "/" + posixpath.normpath(root).lstrip("/")
    tree = tree.rstrip("/") or "/"
    outside = [d.rstrip("/") for d in dirs] + [tree]

    def within(path: str) -> bool:
        return any(path == d or path.startswith(d + "/") for d in outside)
    for p, r in zip(paths, real):
        r = r if r.startswith("/") else "/" + posixpath.normpath(p).lstrip("/")
        if r == "/" or posixpath.dirname(r) == r:
            raise PermissionBreach(f"A git path resolves to the filesystem root ({p}), "
                                   "which the git guard can't save, so the agent did not run. "
                                   "Check core.hooksPath and include.path.")
        resolved.append(r)
        if p in kind and not within(r):
            if kinds.get(r) != "config":
                kinds[r] = kind[p]
    resolved = list(dict.fromkeys(resolved))
    kinds = {r: k for r, k in kinds.items() if r in resolved}
    return walked, resolved, kinds


def _pack(paths: list[str], *, follow: bool) -> str | None:
    done = _sh(f"PACK_CAP={PACK_CAP}\n" + _PACK, "h" if follow else "-", *paths)
    if done.returncode != 0 or "tar failed" in done.stderr:
        return None
    if not follow and len(done.stdout) * 3 // 4 > PACK_CAP + 3:
        return None
    if follow:
        import base64
        if len(base64.b64decode(done.stdout)) > PACK_CAP:    # cut at the cap
            return None
    return done.stdout


def _members(packed: str) -> dict[str, tuple]:
    """Each member's type, mode, link target and bytes. Not its mtime or owner."""
    import base64, io, tarfile
    found = {}
    if not packed:
        return found
    try:
        with tarfile.open(fileobj=io.BytesIO(base64.b64decode(packed)), mode="r:") as tar:
            for m in tar:
                data = tar.extractfile(m).read() if m.isfile() else b""
                found[m.name.rstrip("/")] = (m.type, m.mode, m.linkname, data)
    except (tarfile.TarError, OSError):
        found["(cut off at the cap)"] = (len(found),)    # a cut pack compares by what it read
    return found


def _differs(guard: dict) -> list[str] | None:
    """The watched paths that differ from the saved packs, plus TOO_LARGE if the
    follow pack is now over the cap, or None if the plain pack failed."""
    plain, followed = _pack(guard["paths"], follow=False), _pack(guard["paths"], follow=True)
    if plain is None:
        return None
    moved = _moved(guard)
    if moved is None:
        return None
    # A follow pack over the cap is never compared; only the plain packs are.
    now = (_members(plain),) + (() if followed is None else (_members(followed),))
    then = (_members(guard["plain"]), _members(guard["followed"]))

    def under(members: dict, path: str) -> dict:
        rel = path.lstrip("/")
        return {k: v for k, v in members.items() if k == rel or k.startswith(rel + "/")}

    # Resolving every prefix (_moved) fully covers a parent swapped under a
    # link with a relative target. A directory on the way that no longer
    # resolves is reported too.
    found = moved + [p for p in guard["paths"] if p not in moved
                     and any(under(a, p) != under(b, p) for a, b in zip(now, then))]
    return found + ([TOO_LARGE] if followed is None else [])


def guard_git_dir(run) -> dict | None:
    """Pack the watched paths, before an agent runs.
    None only when the repo root has no .git and git says it isn't a repo; a
    repo whose config git can't read, or whose hooks directory git can't name,
    raises PermissionBreach."""
    walked, paths, kinds = _watched(run)
    if not paths:
        return None
    plain = _pack(paths, follow=False)
    if plain is None:
        raise PermissionBreach("Could not save the git directory and git config "
                               "before the agent ran, so it did not run.")
    followed = _pack(paths, follow=True)
    if followed is None:
        raise PermissionBreach(
            "The git paths SSSF watches are too large to check: read through their "
            f"symlinks they come to more than {PACK_CAP / 1048576:g} MB, or could not be "
            "read, so the agent did not run, and no run will until that changes. A "
            "symlink among them, such as .git/hooks, .git/sssf-private-names, a linked "
            "~/.gitconfig or an included config file, points at a large file or tree. "
            "Point it at something smaller, or replace it with the file it stands for, "
            "then run again.")
    where = _where(walked)
    if where is None:
        raise PermissionBreach("Could not resolve the git paths to watch, so the agent did not run.")
    return {"walked": walked, "where": where, "paths": paths, "plain": plain,
            "followed": followed, "kinds": kinds, "done": False}


def git_dir_changed(guard: dict | None) -> bool:
    """Whether the agent has changed a watched path so far. Runs no git."""
    if not guard or guard["done"]:
        return False
    try:
        changed = _differs(guard) != []
    except Exception:
        guard["seen"] = True
        raise
    if changed:
        guard["seen"] = True        # sticks: a change undone before the restore still counts
    return changed


SEEN = "(a watched path, changed while the agent ran and changed back since)"
TOO_LARGE = ("(the watched paths, read through their symlinks, now too large to "
             "check: a link among them points at a large file or tree)")


# A git config file outside the repo, meaning both its git directory and its
# work tree, is shared with everything else the engineer runs, so a change there may be theirs, made in another
# terminal while the agent ran. The guard can't tell, so it still puts the file
# back and stops the run, but it names the keys that changed, never a value,
# so the engineer can make their own change again. Git reads the keys, under a
# time limit; Python never parses the file.
KEYS_PER_FILE = 10
KEYS_IN_ALL = 30
KEYS_SECONDS = 10
KEY_WIDTH = 120
SHARED = ("(outside the repo and shared with everything you run, so the change "
          "may have come from outside the agent)")
UNLISTED = f"(its keys could not be listed: git could not read a version of it as config within {KEYS_SECONDS} s)"

# Lists the entries in one version of a watched config file, "key\nvalue"
# NUL-separated: the saved version from the pack file $1, or the live one when
# $1 is "-". Nothing is written. A watchdog kills git after $3 seconds; its own
# output goes to /dev/null, so it doesn't hold the pipe open. Git loads no
# global or system config (the agent may have written ~/.gitconfig: a trace2
# line there would make this git write files), and follows no include.
_KEYS = """f=$1; rel=${2#/}; t=$3
cd / || exit 1
{ if [ "$f" = - ]; then tar -cf - "$rel"; else base64 -d < "$f"; fi; } 2>/dev/null |
  tar -xOf - "$rel" 2>/dev/null |
  GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 git -c core.fsmonitor= config --file - --no-includes --list -z &
pid=$!
( sleep "$t"; kill "$pid" ) >/dev/null 2>&1 &
w=$!
wait "$pid"; s=$?
kill "$w" 2>/dev/null
exit $s
"""


def _entries(source: str, path: str):
    """Counter of (key, value) git reads in one version of `path`, or None if
    git couldn't read it as config in time."""
    import subprocess
    from collections import Counter
    try:
        done = _sh(_KEYS, source, path, str(KEYS_SECONDS), timeout=KEYS_SECONDS + 5)
    except subprocess.TimeoutExpired:
        return None
    if done.returncode != 0:
        return None
    return Counter(tuple(e.partition("\n")[::2]) for e in done.stdout.split("\0") if e)


SAID_LINES = 20
SAID_WIDTH = 300
SAID_CUT = "[…]"


def _said_line(line: str) -> str:
    """One line of git's error as a breach quotes it: non-printables as ?, and
    a line over SAID_WIDTH characters cut in the middle, so the path git names
    first and the reason it gives last both survive."""
    line = "".join(c if c.isprintable() else "?" for c in line)
    if len(line) <= SAID_WIDTH:
        return line
    room = SAID_WIDTH - len(SAID_CUT)
    head = room - room // 2
    return line[:head] + SAID_CUT + line[len(line) - room // 2:]


def _said(err: str, code: int) -> str:
    """git's error as a breach quotes it: at most SAID_LINES lines of at most
    SAID_WIDTH characters, non-printables as ?. A longer line keeps its start
    and its end with SAID_CUT between them."""
    lines = [l for l in err.strip().splitlines() if l.strip()][:SAID_LINES]
    return "\n".join("  " + _said_line(l) for l in lines) or f"  (git printed no error; exit code {code})"


def _unreadable(err: str, code: int) -> str:
    """The breach for a repo whose git config git can't read."""
    return ("git couldn't read the repo's git config, so SSSF can't tell which git "
            "files to watch. The agent did not run, and no run will until git can "
            "read it. git said:\n" + _said(err, code) + "\n\n"
            "Fix the config file or include path git names above, for example a "
            "symlink loop or a directory git can't enter, then run again.")


def _no_hooks_dir(err: str, code: int) -> str:
    """The breach for a repo where git can't name its hooks directory."""
    return ("git couldn't name the hooks directory, so SSSF can't watch the hooks "
            "git will run. The agent did not run, and no run will until git can "
            "name it. git said:\n" + _said(err, code) + "\n\n"
            "Point core.hooksPath at a shorter path, one whose real path, with its "
            "symlinks resolved, is under the system's path length limit, then run again.")


def _shown_key(key: str) -> str:
    """A key as the breach shows it: URL userinfo masked, non-printables as ?,
    at most KEY_WIDTH characters."""
    scheme, sep, rest = key.partition("://")
    if sep and "@" in rest:
        key = f"{scheme}://***@{rest.rpartition('@')[2]}"
    key = "".join(c if c.isprintable() else "?" for c in key)
    return key if len(key) <= KEY_WIDTH else key[:KEY_WIDTH - 1] + "…"


def _shared(guard: dict, paths: list[str], held: str) -> dict[str, list[str]]:
    """Detail lines for each shared file in `paths`, which are about to be put
    back from the pack file `held`. Keys only, never a value or a line."""
    import tarfile
    from collections import Counter
    kinds = guard.get("kinds", {})
    shared = [p for p in paths if p in kinds]
    if not shared:
        return {}
    then = _members(guard["plain"])
    fresh = _pack(shared, follow=False)
    if fresh is None:
        return {p: [UNLISTED] for p in shared}
    now = _members(fresh)
    budget = KEYS_IN_ALL
    found: dict[str, list[str]] = {}
    for p in shared:
        rel = p.lstrip("/")
        b, a = then.get(rel), now.get(rel)
        if any(m is not None and m[0] not in (tarfile.REGTYPE, tarfile.AREGTYPE) for m in (b, a)):
            found[p] = ["(not a regular file before or after, so nothing in it is listed)"]
        elif kinds[p] == "lines":
            old = Counter(b[3].splitlines()) if b else Counter()
            new = Counter(a[3].splitlines()) if a else Counter()
            found[p] = [f"{sum((new - old).values())} line(s) added, {sum((old - new).values())} removed"]
        else:
            old = Counter() if b is None else _entries(held, p)
            new = Counter() if a is None else _entries("-", p)
            if old is None or new is None:
                found[p] = [UNLISTED]
                continue
            added = {k for k, _ in new - old}
            removed = {k for k, _ in old - new}
            had = {k for k, _ in old}
            has = {k for k, _ in new}
            marks = sorted([("~" if k in removed or k in had else "+", k) for k in added]
                           + [("~" if k in has else "-", k) for k in removed - added],
                           key=lambda m: m[1])
            if not marks:
                found[p] = ["(no key added, removed or given a new value: a comment, layout or order change)"]
                continue
            shown = marks[:max(0, min(KEYS_PER_FILE, budget))]
            budget -= len(shown)
            found[p] = [f"{m} {_shown_key(k)}" for m, k in shown]
            if len(marks) > len(shown):
                found[p].append(f"({len(marks) - len(shown)} more key(s) left out)")
    return found


def restore_git_dir(run, guard: dict | None, *, breach: bool = True) -> list[str]:
    """Put the watched paths back if the agent changed them.

    Returns the paths that changed. With `breach`, a change raises
    PermissionBreach after the restore; without it, as on an agent that
    already failed, it only restores, and raises only if that failed. For a
    shared config file outside the repo the breach lists the keys that
    changed, and never a value.
    """
    if not guard or guard["done"]:
        return []
    guard["done"] = True
    changed = _differs(guard)
    if changed is None:
        changed = list(dict.fromkeys((_moved(guard) or []) + list(guard["paths"])))   # could not compare
    if not changed and guard.get("seen"):
        changed = [SEEN]
    restored = True
    # Narrow on purpose: a change that went through a symlink is reported and
    # stops the run, but nothing is written through or to a link. Writing
    # there can land in a folder the agent chose. Only physical paths that
    # still resolve to themselves are put back, and nothing whose parent no
    # longer resolves is put back under a prefix that moved.
    paths = [p for p in guard["paths"] if p in changed]
    if paths:
        now = _sh(_REAL, *paths).stdout.split("\0")[:len(paths)]
        moved = _moved(guard, gone=False)
        where = guard.get("where", {})
        # A prefix that moved roots the paths under it, by the prefix as written
        # and, unless it ends in . or .., by where it resolved before: $HOME/d/..
        # resolved to $HOME, which hasn't moved. Only a path whose parent no
        # longer resolves ("") is skipped there: tar would write through a
        # dangling link. A path whose _REAL is unchanged is safe, as today.
        # A real directory that now resolves nowhere is reported (through
        # _differs) but isn't a root: tar remakes it as a real directory, and
        # the check after the restore sees whether it resolves to the same
        # place again.
        roots = [] if moved is None else [
            s for q in moved
            for s in ([q] + ([where[q][0]] if q.rsplit("/", 1)[-1] not in (".", "..") else []))]

        def under(p: str) -> bool:
            return moved is None or any(p.startswith(s.rstrip("/") + "/") for s in roots)
        paths = [p for p, r in zip(paths, now) if r == p or (r == "" and not under(p))]
    sentinel = changed == [SEEN]
    if changed and not paths and not sentinel:
        restored = False
    details: dict[str, list[str]] = {}
    if paths:
        box = sandbox.current()
        made = box.run(["mktemp"])
        held = made.stdout.strip()
        if made.returncode != 0 or not held.startswith("/"):
            # TMPDIR made unwritable: fall back to the git directory as it was
            # saved, and only if it still resolves to itself, never a folder
            # the agent swapped in.
            git_dir = next((p.rsplit("/", 1)[0] for p in guard["paths"]
                            if p.endswith("/config") and p.rsplit("/", 1)[0].endswith(".git")), "")
            ok = git_dir and _sh(_REAL, git_dir + "/x").stdout.split("\0")[0] == git_dir + "/x"
            made = box.run(["mktemp", git_dir + "/sssf.XXXXXX"]) if ok else made
            held = made.stdout.strip() if ok else ""
        if made.returncode == 0 and held.startswith("/"):
            box.write_text(held, guard["plain"])
            try:
                details = _shared(guard, paths, held)
            except Exception:      # naming keys must never stop the put-back
                details = {p: [UNLISTED] for p in paths if p in guard.get("kinds", {})}
            _sh(_UNPACK, held, *paths)
        left = _differs(guard)
        restored = left == []
        details = {} if left is None else {p: d for p, d in details.items() if p not in left}
        changed += [p for p in (left or []) if p not in changed]
    root = str(run.repo_root).rstrip("/") + "/"
    shown = [p[len(root):] if p.startswith(root) else p for p in changed]
    if changed and (breach or not restored):
        outcome = ("changed back since" if sentinel else
                   "put back as they were" if restored else
                   "NOT all put back. A change through a symlink is reported, never "
                   "written back; a path SSSF couldn't read or write is left as it is. "
                   "Check these by hand before the next git command in this repo")
        lead = ("The agent changed the git directory or the git config, which no agent may "
                "write: hooks and config there run code on later git commands. ")
        if not details:
            raise PermissionBreach(
                lead + f"{len(changed)} path(s), {outcome}:\n" + "\n".join(f"  - {p}" for p in shown))
        if all(p in details for p in changed):
            lead = ("A git config or ignore file outside the repo changed while the agent ran, "
                    "and no agent may write one: config there runs code on later git commands. ")
        lines = []
        for p, s in zip(changed, shown):
            lines.append(f"  - {s}" + (f" {SHARED}" if p in details else ""))
            lines += [f"      {d}" for d in details.get(p, [])]
        raise PermissionBreach(
            lead + f"{len(changed)} path(s), {outcome}:\n" + "\n".join(lines) + "\n\n"
            "Files marked as shared are used by everything else you run, so another terminal "
            "or session, such as `gh auth setup-git`, may have made the change, not the agent. "
            "SSSF can't tell which, so it put them back and stopped the run either way: an "
            "agent's line there would run on your next git command. Keys are marked + added, "
            "- removed or ~ given a new value; no value or line is ever shown. If a change is "
            "yours, make it again with the command you used.")
    return shown


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Every path whose state differs — appeared, vanished, or was rewritten."""
    return sorted({p for p in set(before) | set(after)
                   if before.get(p) != after.get(p)})


def _glob(pattern: str) -> re.Pattern:
    """Translate a pattern, with `*` stopping at a path separator.

    fnmatch would let `*` cross `/`, which quietly widens every pattern:
    `adws/adw_*.py` would match `adws/adw_data/sessions/x/y.py` as well as the
    ADW scripts it means. `**` is the way to say "cross directories".
    """
    out, i = [], 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(char))
            i += 1
    return re.compile("".join(out))


def _matches(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):                      # directory prefix
        return path.startswith(pattern)
    if "*" in pattern or "?" in pattern:
        return _glob(pattern).fullmatch(path) is not None
    return path == pattern


# SSSF's own files, protected whatever the roster says. Only an agent's own
# `writes`, naming one, unlocks it. FACTORY_DATA is relative to data_dir.
#   .claude/    the skill, agents, hooks and settings Claude Code loads
#   .pi/        pi's extensions and settings
#   .mcp.json   the MCP servers both harnesses start
#   justfile, sssf.just, local.just
#               the recipes and the local.just the justfile imports
#   .githooks/  the hooks
#   adws/       the ADW code, and any module beside it that would shadow an import
# The runtime under data_dir stays writable: always_writable is checked first.
# FACTORY_DATA stays for a data_dir outside adws/.
FACTORY_DATA = ("prompt_engineering/", "harness_engineering/")
FACTORY_FILES = (".claude/", ".pi/", ".mcp.json", "justfile", "sssf.just", "local.just",
                 ".githooks/", "adws/")
# The runtime under data_dir: the agents' own (sessions, tool caches) and
# SSSF's worktrees. The trace db is added from cfg.observability.db when it
# is a file path strictly inside data_dir.
RUNTIME = ("sessions/", "agent-cache/", "worktrees/")
_DB_SIBLINGS = ("", "-wal", "-shm", "-journal")


def _clean(path: str) -> str:
    """A config path as a repo-relative path: no leading ./, no doubled or
    trailing slash. "" for the repo root. Absolute paths stay absolute."""
    import posixpath
    path = path.strip()
    if not path:
        return ""
    clean = posixpath.normpath(path)
    if clean == ".":
        return ""
    if path.startswith("//"):        # normpath keeps a double leading slash
        clean = "/" + clean.lstrip("/")
    return clean


def _joined(data: str, p: str) -> str:
    return f"{data}/{p}" if data else p


def factory_files(cfg: SSSFConfig) -> list[str]:
    """SSSF's fixed list of protected paths, with data_dir filled in."""
    data = _clean(cfg.defaults.data_dir)
    return [_joined(data, p) for p in FACTORY_DATA] + list(FACTORY_FILES)


def protected(cfg: SSSFConfig) -> list[str]:
    """The fixed list, then the roster's `protected_files`, each entry once.

    For DISPLAY only (prompts, messages, the trace). Never use it to decide.
    """
    return list(dict.fromkeys(factory_files(cfg) + list(cfg.defaults.protected_files)))


def always_writable(cfg: SSSFConfig) -> list[str]:
    """The runtime EVERY agent must be able to write.

    The session runtime and the tool caches are always writable.
    `context_handoff/` is the one place agents hand work to each other, and an
    agent's own prompts, raw_output.jsonl, and envelope.json land beside it.
    Scout writes its findings there, the reviewer its review, the planner its
    plan — a read-only agent is read-only with respect to the REPO, never with
    respect to its own report.

    SSSF's worktrees and trace db are granted too, inside `data_dir` only, so a
    missing gitignore line can't get them deleted by a rollback. The rest of
    `data_dir`, the prompts and the extensions, is factory and protected.
    """
    data = _clean(cfg.defaults.data_dir)
    grants = [_joined(data, p) for p in RUNTIME]
    raw = cfg.observability.db
    db = _clean(raw)
    # A run pins the db absolute, under data_root (data_dir in the launch
    # checkout). Read it back as the data_dir-relative path a snapshot shows,
    # so its WAL, which changes on every call, isn't refused where the
    # db isn't gitignored. Only a db under data_root: nothing else is read back.
    root = _clean(cfg.defaults.data_root)
    if db.startswith("/") and root.startswith("/") and db.startswith(root + "/"):
        db = _joined(data, db[len(root) + 1:])
    # A file path strictly inside data_dir, so the grant stays within the
    # baseline's (all of data_dir): no directory, no glob. With data_dir at the
    # repo root, any relative path without `..`. Never on a FACTORY_DATA path.
    # Not checked against all of factory_files: `adws/` matches the default db,
    # which must stay granted. A worktree run pins db absolute, outside the
    # worktree; an absolute path never matches a snapshot path.
    if data:
        inside = db.startswith(data + "/") and db != data
    else:
        inside = bool(db) and not db.startswith("/") and ".." not in db.split("/")
    if (inside and not raw.endswith("/") and not any(c in raw for c in "*?[")
            and not any(_matches(db, _joined(data, p)) for p in FACTORY_DATA)):
        grants += [db + s for s in _DB_SIBLINGS]
    return grants


def _names(entry: str, protected: str) -> bool:
    """True when a `writes` entry names a protected path: the same pattern, or a
    path inside a protected directory. A broad glob such as `**/*.md` matches
    files there, but it does not name them, so it does not unlock them."""
    return entry == protected or (protected.endswith("/") and entry.startswith(protected))


def permitted(path: str, agent: AgentConfig, cfg: SSSFConfig) -> bool:
    """Session runtime first, then what is protected, then the agent's own list."""
    if any(_matches(path, p) for p in always_writable(cfg)):
        return True
    writes = agent.writes or []
    # SSSF's own files, whatever the roster says. A separate check: it can only
    # refuse, so the roster check below still decides everything it did before.
    # `path + "/"` so a bare `.pi` or `.githooks` (a link, a file) is inside its folder.
    fixed = [p for p in factory_files(cfg) if _matches(path, p) or _matches(path + "/", p)]
    if fixed and not any(_matches(path, w) and _names(w, g) for w in writes for g in fixed):
        return False
    guards = [p for p in cfg.defaults.protected_files if _matches(path, p)]
    if guards:
        # Naming a protected path is what unlocks it. The documenter's `**/*.md`
        # reaches every Markdown file, but must not reach a spec on record.
        return any(_matches(path, w) and _names(w, g) for w in writes for g in guards)
    if any(_matches(path, w) for w in writes):
        return True
    return agent.writes is None          # None = unrestricted, [] = no repo writes


def _roll_back(run, path: str, before: dict[str, str], after: dict[str, str]) -> str:
    """Undo one unauthorized change. Returns a word describing what happened.

    Only changes the agent INTRODUCED are undone. A path that was already dirty,
    untracked or flagged when the agent started is left exactly as it is: the
    operator had uncommitted work there, and discarding it to tidy up would be
    the same harm this module exists to prevent, committed by the cleanup
    instead of the agent.

    A path untracked alone (kind `?`) is deleted. A staged-new path is
    unstaged, then deleted only if git says it isn't ignored. Anything in HEAD
    (including `D,?`, a file taken out of the index and written again) has its
    index flags cleared and is restored from HEAD, index and tree both.
    """
    if path in before:
        # Already dirty beforehand. If it is gone from the diff now, the agent
        # reverted an engineer's uncommitted work and the content is not ours
        # to reconstruct — say so loudly rather than pretend it was handled.
        return "REVERTED-BY-AGENT (uncommitted work lost, cannot restore)" \
            if path not in after else \
            "left as-is (it was already changed, untracked or flagged before the call, so it may hold your work)"
    kind = after.get(path, "").partition(":")[0]
    parts = kind.split(",")
    box = sandbox.current()

    def delete() -> str:
        try:
            box.remove(Path(run.repo_root) / path)
            return "deleted"
        except OSError as error:
            return f"could not delete ({error})"
    if kind == "?":
        return delete()
    if parts[0] == "A" and not any(p.startswith("flag-") for p in parts):
        if box.run(_gitargv(["rm", "-q", "--cached", "--force", "--", path], writes=True),
                   cwd=run.repo_root).returncode != 0:
            return "could not unstage"
        ignored = box.run(_gitargv(["check-ignore", "-q", "--no-index", "--", path], literal=False),
                          cwd=run.repo_root).returncode
        if ignored == 1:
            done = delete()
            return "unstaged and deleted" if done == "deleted" else f"unstaged; {done}"
        return ("unstaged; left on disk because it is gitignored and may be yours, "
                "so check it by hand")
    # The path may no longer be in the index, so their status is ignored. One
    # flag per call: git applies only the last of two given together.
    for flag in ("--no-skip-worktree", "--no-assume-unchanged"):
        box.run(_gitargv(["update-index", "-q", flag, "--", path], writes=True),
                cwd=run.repo_root)
    result = box.run(_gitargv(["checkout", "HEAD", "--", path], writes=True), cwd=run.repo_root)
    return "rolled back" if result.returncode == 0 else "could not roll back"


def ref_moves(run, agent: AgentConfig, before: dict[str, str] | None) -> str:
    """The breach text for refs other than the run's own branch that gained
    commits changing a path `agent` is barred from, or "" when there is none.

    Reads only. Compares `before["refs"]`, recorded by `head()`, with the refs
    now. Barred means protected and not unlocked by the agent's own `writes`:
    `permitted` is False for it, and still False for the same agent with
    `writes=None`. Replace refs count whatever they point at. Any failure to
    compare is itself reported, never passed; this function never raises.

    A spec file (a regular `.md` directly in `REF_SPECS`, not dotted, not in
    `REF_SPEC_NOT`) isn't judged; a `specs` entry in place of the folder is
    judged as the folder. A new commit is read as on `main` (`diff-tree -m
    --root`: every parent, a root by its tree), except a commit of at most
    three parents that has a stash's untracked-files root among them: a parent
    that is not the first, is itself new and is a real root, told from its raw
    object since `rev-list` honours `.git/shallow`. That commit is diffed
    against its other parents, and the root is read by its own tree. So a call
    names at most 256 commits and two parents each. Grafts are off for every
    read.
    """
    def cannot(err: str = "") -> str:
        said = f" git said:\n{_said(err, 0)}\n" if err.strip() else " "
        return ("SSSF could not compare the repo's refs before and after "
                f"{agent.name} ran.{said}No ref was moved and no fetch was tried. "
                "Check `git for-each-ref` by hand before the next run.")

    def git(*args: str):
        done = sandbox.current().run([*_REF_READ, *args], cwd=run.repo_root, text=False)
        out, err = done.stdout, done.stderr
        out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
        err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
        return done.returncode, out, err

    def parse(text: str) -> dict[str, str] | None:
        found: dict[str, str] = {}
        for line in text.split("\n"):
            if not line:
                continue
            sha, _, name = line.partition(" ")
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha) or not name:
                return None
            found[name] = sha
        return found

    try:
        then_text = (before or {}).get("refs")
        if then_text is None:
            return ""
        now_text = _refs(run)
        if then_text == now_text:
            return ""
        if REFS_UNREAD in (then_text, now_text):
            return cannot()
        then, now = parse(then_text), parse(now_text)
        if then is None or now is None:
            return cannot("a ref line was not `<sha> <name>`")
        own = before.get("branch", "")
        replaced = {}
        for name in sorted(set(then) | set(now)):
            if name.startswith("refs/replace/") and then.get(name) != now.get(name):
                replaced[name] = ("created" if name not in then
                                  else "deleted" if name not in now else "moved")
        tips = {n: s for n, s in now.items() if n != own and then.get(n) != s}
        offenders: dict[str, list[str]] = {}
        old = sorted(set(then.values()))
        if tips:
            code, out, err = git("rev-list", "--parents", *sorted(set(tips.values())), "--not", *old)
            if code != 0:
                return cannot(err)
            commits, parents = [], {}
            for line in out.split("\n"):
                if not line:
                    continue
                words = line.split(" ")
                if not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", w) for w in words):
                    return cannot("a commit line was not `<sha> [<parent>...]`")
                commits.append(words[0])
                parents[words[0]] = words[1:]
            open_ = agent.model_copy(update={"writes": None})

            def run_script(script: str, words: list[str]):
                done = sandbox.current().run(["sh", "-c", script, "sh", *words],
                                             cwd=run.repo_root, text=False)
                text, err = done.stdout, done.stderr
                text = text.decode("utf-8", "surrogateescape") if isinstance(text, bytes) else (text or "")
                err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
                return done.returncode, text, err

            # Candidates for a stash's untracked-files root: a later parent of a
            # commit with at most three parents, itself new, listed without
            # parents. rev-list honours .git/shallow, so each is read raw.
            candidates = sorted({p for c in commits if len(parents[c]) <= 3
                                 for p in parents[c][1:] if p in parents and not parents[p]})
            roots: set[str] = set()
            for i in range(0, len(candidates), _CHUNK):
                chunk = candidates[i:i + _CHUNK]
                code, text, err = run_script(_COMMIT_ROOTS, chunk)
                if code != 0:
                    return cannot(err)
                if len(text) != len(chunk) or any(ch not in "rp?" for ch in text):
                    return cannot("a commit's raw object could not be classified")
                roots.update(p for p, ch in zip(chunk, text) if ch == "r")

            def named(c: str) -> list[str]:
                # A stash's untracked-files root: not the first parent, a new
                # commit, a real root. Only for a commit with at most three
                # parents, so no more than two shas are ever named. Empty: git
                # reads the parents itself.
                ps = parents[c]
                kept = [p for i, p in enumerate(ps) if i == 0 or p not in roots]
                return kept if len(ps) <= 3 and len(kept) < len(ps) else []

            def judged(path: str, old_mode: str, new_mode: str) -> str | None:
                # None for a spec file: a regular .md directly in specs/, not
                # dotted, not an instruction file. `specs` itself is judged as
                # the folder, so a link in its place doesn't pass.
                if path == REF_SPECS.rstrip("/"):
                    return REF_SPECS
                name = path[len(REF_SPECS):]
                if (path.startswith(REF_SPECS) and name and "/" not in name
                        and name.endswith(".md") and not name.startswith(".")
                        and name.lower() not in REF_SPEC_NOT
                        and old_mode in ("100644", "000000") and new_mode in ("100644", "000000")):
                    return None
                return path

            for i in range(0, len(commits), _CHUNK):
                chunk = commits[i:i + _CHUNK]
                words = [w for c in chunk for w in (c, *named(c), ".")]
                code, text, err = run_script(_COMMIT_PATHS, words)
                if code != 0:
                    return cannot(err)
                fields = _fields(text)
                if fields.count("") != len(chunk):
                    return cannot()
                lists: list[list[tuple[str, str]]] = []
                current: list[tuple[str, str]] = []
                at = 0
                while at < len(fields):
                    if fields[at] == "":
                        lists.append(current)
                        current = []
                        at += 1
                        continue
                    meta = re.fullmatch(r":([0-7]{6}) ([0-7]{6}) [0-9a-f]+ [0-9a-f]+ [A-Z]", fields[at])
                    if not meta or at + 1 >= len(fields) or fields[at + 1] == "":
                        return cannot("a raw diff record was not `:<modes> <shas> <status>` and a path")
                    shown = fields[at + 1]
                    path = judged(shown, meta.group(1), meta.group(2))
                    if path is not None:
                        current.append((path, shown))
                    at += 2
                for sha, records in zip(chunk, lists):
                    barred = sorted({shown for path, shown in records
                                     if not permitted(path, agent, run.cfg)
                                     and not permitted(path, open_, run.cfg)})
                    if barred:
                        offenders[sha] = barred
        if not offenders and not replaced:
            return ""

        lines = [f"Refs other than the run's own branch gained commits that change protected "
                 f"paths while {agent.name} ran, or a replace ref changed. Nothing was moved "
                 "back, deleted or rewritten:"]
        for name in sorted(tips):
            code, out, _err = git("rev-list", tips[name], "--not", *old)
            mine = [c for c in out.split() if c in offenders] if code == 0 else []
            if code == 0 and not mine:
                continue
            lines.append(f"  - {_said_line(name)}: {then.get(name, '(new)')} -> {tips[name]}"
                         + ("" if code == 0 else " (could not list its commits)"))
            for sha in mine[:REF_COMMITS_SHOWN]:
                paths = [_said_line(p) for p in offenders[sha]]
                more = len(paths) - REF_PATHS_SHOWN
                lines.append(f"      {sha} changes: {', '.join(paths[:REF_PATHS_SHOWN])}"
                             + (f" (+{more} more)" if more > 0 else ""))
            if len(mine) > REF_COMMITS_SHOWN:
                lines.append(f"      (+{len(mine) - REF_COMMITS_SHOWN} more commits)")
        for name, how in replaced.items():
            ends = " -> ".join(f"{label}{sha}" for label, sha in
                               (("", then.get(name)), ("", now.get(name))) if sha)
            lines.append(f"  - {_said_line(name)}: {how}" + (f" ({ends})" if ends else ""))
        lines.append("SSSF can't tell who moved a ref. Your own commit, merge, pull, fetch or "
                     "stash that changes a protected path during an agent call stops the run "
                     "the same way. If it was yours, nothing needs undoing; run again. If not, "
                     "`git show --stat <commit>` shows it, and `git update-ref <ref> <old> <new>` "
                     "moves a branch back.")
        return "\n".join(lines)
    except Exception as failed:
        return cannot(repr(failed))


def enforce(run, phase, agent: AgentConfig, before: dict[str, str],
            head_before: dict[str, str] | None = None) -> list[str]:
    """Compare the tree against `before`; undo and raise if the agent overstepped.

    Returns the paths it legitimately changed, so the trace records what an
    agent actually touched rather than only what it claimed in its envelope.

    Detection alone would leave the repo holding the unauthorized change while
    reporting a failure, so anything the agent introduced outside its allowlist
    is rolled back before the phase dies. What it cannot undo, it names.

    With `head_before` (from `head()`), the refs are compared too, after the
    tree work and its rollbacks: a ref other than the run's own branch that
    gained commits changing a protected path stops the run, and its report is
    added to a tree breach if there is one. Refs are only reported, never moved.
    Without it, only the tree is checked.
    """
    after = snapshot(run)
    touched = changed_paths(before, after)
    breaches = [p for p in touched if not permitted(p, agent, run.cfg)]
    outcomes = {p: _roll_back(run, p, before, after) for p in breaches}
    moved = ref_moves(run, agent, head_before) if head_before else ""
    if not breaches and not moved:
        return touched

    # The run stops here, so the agent's permitted changes are never verified,
    # reviewed, or committed. Name them, so the engineer knows what is sitting
    # uncommitted in the tree and has to be finished or discarded by hand.
    kept = [p for p in touched if p not in outcomes]
    if not breaches:
        message = moved
    else:
        scope = ("read-only" if agent.writes == []
                 else f"limited to {agent.writes}" if agent.writes
                 else f"barred from {protected(run.cfg)}")
        detail = "\n".join(f"  - {p} — {outcome}" for p, outcome in outcomes.items())
        message = f"{agent.name} is {scope} but modified {len(breaches)} path(s):\n{detail}"
    if kept:
        message += ("\nIts other changes are left uncommitted and unreviewed:\n"
                    + "\n".join(f"  - {p}" for p in kept))
    if breaches and moved:
        message += "\n" + moved
    raise PermissionBreach(message)

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

A breach is NOT a gate violation. Gates are for work an agent can be asked to
redo; a breach cannot be corrected by re-prompting, because the write already
happened. It aborts the phase and names every offending path.

An agent that commits would hide its changes from that comparison, since
both fingerprints are taken against HEAD. `head()` records where HEAD was
before the agent ran, and `undo_commits()` resets any commits it made, so
their changes are back in the working tree, unstaged, for `enforce()` to check
and roll back, and for the chain's own commit phase to commit. An agent that moved HEAD anywhere else
(another branch, a rebase, a reset) stops the run instead.

Two keys drive it, both in sssf.config.yaml:
    defaults.protected_files   paths no agent may touch unless it names them itself
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

def _gitargv(args: list[str], *, writes: bool = False) -> list[str]:
    return ["git", *_SAFE, *(_NO_HOOKS if writes else []), *args]


def _git(args: list[str], cwd) -> str:
    result = sandbox.current().run(_gitargv(args), cwd=cwd, text=False)
    if result.returncode != 0:
        return ""
    out = result.stdout
    return out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else out


def snapshot(run) -> dict[str, str]:
    """Fingerprint every path the working tree currently differs on.

    Tracked files carry their numstat counts, so an edit to an already-dirty
    file still registers as a change. Untracked files are listed by name.
    Gitignored paths never appear, which is why the session runtime under
    `data_dir` — where handoff files legitimately land — needs no special case.
    """
    fingerprints: dict[str, str] = {}
    for line in _git(["diff", "HEAD", "--numstat"], run.repo_root).splitlines():
        fields = line.split("\t")
        if len(fields) >= 3:
            path = fields[-1].strip()
            fingerprints[path] = f"{fields[0]},{fields[1]}"
    for path in _git(["ls-files", "--others", "--exclude-standard"],
                     run.repo_root).splitlines():
        if path.strip():
            fingerprints[path.strip()] = "untracked"
    return fingerprints


def head(run) -> dict[str, str]:
    """Where HEAD is: its commit and the branch it is on (empty when detached)."""
    return {"sha": _git(["rev-parse", "-q", "--verify", "HEAD"], run.repo_root).strip(),
            "branch": _git(["symbolic-ref", "-q", "HEAD"], run.repo_root).strip()}


def undo_commits(run, before: dict[str, str]) -> list[str]:
    """Undo commits an agent made itself, keeping their changes unstaged in the tree.

    Returns the short shas undone, newest first, or [] when HEAD did not move.
    Unstaged matters: _roll_back restores a path from the index, so a staged
    change would be "restored" to itself. Raises PermissionBreach when HEAD
    moved somewhere a reset can't safely
    bring back: onto another branch, or to a commit that doesn't descend from
    where it was.
    """
    after = head(run)
    if after == before:
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
# empty field where the parent doesn't exist yet.
_REAL = """for p in "$@"; do
  d=${p%/*}; b=${p##*/}
  if [ "$b" = .. ] || [ "$b" = . ]; then d=$p; b=; fi
  if r=$(cd -P "${d:-/}" 2>/dev/null && pwd -P); then
    if [ -n "$b" ]; then r="${r%/}/$b"; fi
    printf "%s\\0" "$r"
  else
    printf "\\0"
  fi
done
"""


# Prints, NUL-separated, every symlink each path passes through: the path
# itself and each parent. The guard keeps these links too, so an agent that
# points one somewhere else is seen, and the restore puts it back first.
_LINKS = """for p in "$@"; do
  q=$p
  while [ -n "$q" ] && [ "$q" != / ]; do
    if [ -L "$q" ]; then printf "%s\\0" "$q"; fi
    q=${q%/*}
  done
done
"""


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

    A watched path with `..` that doesn't fully resolve, because a part of it
    doesn't exist yet or can't be entered, stops the run with
    `PermissionBreach`, since the guard can't know which file git will read.
    """
    root = str(run.repo_root).rstrip("/")
    out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"], root)
    dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    if not dirs:
        return [], [], {}
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
    hooks = _git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], root).strip()
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
    logical = ["/" + posixpath.normpath(p).lstrip("/")
               for p in dict.fromkeys(logical + paths) if p.startswith("/")]
    links = [q for q in _sh(_LINKS, *logical).stdout.split("\0") if q]
    real = _sh(_REAL, *paths).stdout.split("\0")
    if len(real) != len(paths) + 1:
        raise PermissionBreach("Could not resolve the git paths to watch, so the agent did not run.")
    # A path with ".." that cd -P can't resolve would take the lexical fallback
    # below, but the kernel resolves ".." physically, maybe through a link, once
    # the missing part exists, so git can read a file the guard doesn't watch.
    loose = [p for p, r in zip(paths, real) if not r.startswith("/") and ".." in p.split("/")]
    if loose:
        def which(p: str) -> str:
            key, source = named.get(p, ("", ""))
            return (f"  - {_shown_key(key)} in {source}: {p}" if key else f"  - {p}")
        raise PermissionBreach(
            "A git path SSSF watches has `..` after a directory that doesn't exist yet, "
            "or that can't be entered. Once that changes, git resolves `..` through it and "
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
    links = [q for q in dict.fromkeys(links) if q not in resolved and q != "/"]
    return links, resolved, kinds


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
    every = guard.get("links", []) + guard["paths"]
    plain, followed = _pack(every, follow=False), _pack(guard["paths"], follow=True)
    if plain is None:
        return None
    # A follow pack over the cap is never compared; only the plain packs are.
    now = (_members(plain),) + (() if followed is None else (_members(followed),))
    then = (_members(guard["plain"]), _members(guard["followed"]))

    def under(members: dict, path: str) -> dict:
        rel = path.lstrip("/")
        return {k: v for k, v in members.items() if k == rel or k.startswith(rel + "/")}

    def exact(members: dict, path: str) -> object:
        return members.get(path.lstrip("/"))   # a link: the link itself, never what it holds

    links = guard.get("links", [])
    moved = [q for q in links if exact(now[0], q) != exact(then[0], q)]
    # A parent swapped under a link with a relative target keeps the link's
    # text the same; where the link sits physically shows it.
    if links and _sh(_REAL, *links).stdout != guard.get("parents", ""):
        before = guard.get("parents", "").split("\0")
        after = _sh(_REAL, *links).stdout.split("\0")
        moved += [q for q, b, a in zip(links, before, after) if b != a and q not in moved]
    found = moved + [p for p in guard["paths"]
                     if any(under(a, p) != under(b, p) for a, b in zip(now, then))]
    return found + ([TOO_LARGE] if followed is None else [])


def guard_git_dir(run) -> dict | None:
    """Pack the watched paths, before an agent runs. None outside a git repo."""
    links, paths, kinds = _watched(run)
    if not paths:
        return None
    plain = _pack(links + paths, follow=False)
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
    return {"links": links, "paths": paths, "plain": plain,
            "followed": followed,
            "parents": _sh(_REAL, *links).stdout, "kinds": kinds, "done": False}


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
        changed = guard.get("links", []) + list(guard["paths"])   # could not compare
    if not changed and guard.get("seen"):
        changed = [SEEN]
    restored = True
    # Narrow on purpose: a change that went through a symlink is reported and
    # stops the run, but nothing is written through or to a link. Writing
    # there can land in a folder the agent chose. Only physical paths that
    # still resolve to themselves are put back.
    paths = [p for p in guard["paths"] if p in changed]
    if paths:
        now = _sh(_REAL, *paths).stdout.split("\0")[:len(paths)]
        paths = [p for p, r in zip(paths, now) if r in ("", p)]
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


def always_writable(cfg: SSSFConfig) -> list[str]:
    """The session runtime, which EVERY agent must be able to write.

    `context_handoff/` is the one place agents hand work to each other, and an
    agent's own prompts, raw_output.jsonl, and envelope.json land beside it.
    Scout writes its findings there, the reviewer its review, the planner its
    plan — a read-only agent is read-only with respect to the REPO, never with
    respect to its own report.

    This is granted from `data_dir` rather than left to .gitignore. The runtime
    is normally ignored, so it never even appears in a snapshot — but an agent's
    ability to record its work must not hang on a gitignore entry that someone
    can delete or that a changed `data_dir` can outgrow.
    """
    return [cfg.defaults.data_dir.rstrip("/") + "/"]


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

    Only changes the agent INTRODUCED are undone. A path that was already dirty
    when the agent started is left exactly as it is: the operator had
    uncommitted work there, and discarding it to tidy up would be the same harm
    this module exists to prevent, committed by the cleanup instead of the agent.
    """
    if path in before:
        # Already dirty beforehand. If it is gone from the diff now, the agent
        # reverted an engineer's uncommitted work and the content is not ours
        # to reconstruct — say so loudly rather than pretend it was handled.
        return "REVERTED-BY-AGENT (uncommitted work lost, cannot restore)" \
            if path not in after else "left as-is (was already modified)"
    if after.get(path) == "untracked":
        try:
            sandbox.current().remove(Path(run.repo_root) / path)
            return "deleted"
        except OSError as error:
            return f"could not delete ({error})"
    result = sandbox.current().run(_gitargv(["checkout", "--", path], writes=True), cwd=run.repo_root)
    return "rolled back" if result.returncode == 0 else "could not roll back"


def enforce(run, phase, agent: AgentConfig, before: dict[str, str]) -> list[str]:
    """Compare the tree against `before`; undo and raise if the agent overstepped.

    Returns the paths it legitimately changed, so the trace records what an
    agent actually touched rather than only what it claimed in its envelope.

    Detection alone would leave the repo holding the unauthorized change while
    reporting a failure, so anything the agent introduced outside its allowlist
    is rolled back before the phase dies. What it cannot undo, it names.
    """
    after = snapshot(run)
    touched = changed_paths(before, after)
    breaches = [p for p in touched if not permitted(p, agent, run.cfg)]
    if not breaches:
        return touched

    outcomes = {p: _roll_back(run, p, before, after) for p in breaches}
    scope = ("read-only" if agent.writes == []
             else f"limited to {agent.writes}" if agent.writes
             else f"barred from {run.cfg.defaults.protected_files}")
    detail = "\n".join(f"  - {p} — {outcome}" for p, outcome in outcomes.items())
    message = f"{agent.name} is {scope} but modified {len(breaches)} path(s):\n{detail}"
    # The run stops here, so the agent's permitted changes are never verified,
    # reviewed, or committed. Name them, so the engineer knows what is sitting
    # uncommitted in the tree and has to be finished or discarded by hand.
    kept = [p for p in touched if p not in outcomes]
    if kept:
        message += ("\nIts other changes are left uncommitted and unreviewed:\n"
                    + "\n".join(f"  - {p}" for p in kept))
    raise PermissionBreach(message)

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
registers. Gitignored paths are never seen. The index is read against HEAD as
well: staged content the work tree doesn't show counts as a change and is put back
to HEAD's.

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
deleted or fetched, and no hook, filter, fsmonitor, pager or external diff runs, in
the repo or in a submodule (the snapshot, undo and rollback git work the same way). It can't tell
the agent's commit from the engineer's.

The chain's own commits are judged too. `check_local_commit()` runs after
`commit_all` in local mode, and `Remote.commits_out` runs `judge_commits()` on
what a VM run brings back before the host branch moves. Each commit is diffed
against its parent by the host's own git (no replace ref, graft, hook or
external diff), and a path passes when an agent that ran may write it, or it is
under the session folder, or the chain wrote it with the blob it hashed (`wrote`), or it is exactly
what was already in the tree when the run began (`carried_tree`). A merge, a
root, or a head that doesn't descend from the last sync is refused outright. An
`only` commit that passes ends the grant of the agents that ran before it, inside
its entries, until the next full commit passes (`run.taken`); an agent that runs
again does not get it back. A protected path is matched in every spelling a
case-insensitive checkout takes for it (see `_folds_to`). A
refused local commit is undone with the tree kept; a refused VM bundle lands
nothing. A path with a `.git` component never passes, and neither does one with a
component over 255 code points.

Two keys drive it, both in sssf.config.yaml:
    defaults.protected_files   paths no agent may touch unless it names them itself
SSSF's fixed `FACTORY_FILES` are checked on top of `protected_files`, and a
roster can't drop them.
    agents[].writes      None = unrestricted · [] = read-only · [...] = only these
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Mapping
from pathlib import Path

from . import sandbox
from .data_types import AgentConfig, EventRecord, SSSFConfig


class PermissionBreach(RuntimeError):
    """An agent modified a path it was not permitted to modify.

    `enforce` sets `left` (the breached paths it didn't put back; `None` when no
    rollback list came with the breach) and `refs_left` (a ref still moved)."""

    def __init__(self, message: str = "", *, left: list[str] | None = None,
                 refs_left: bool = False):
        super().__init__(message)
        self.left = left
        self.refs_left = refs_left


class GitFailed(PermissionBreach):
    """SSSF's own git (or its file-hash loop) failed, so it can't tell what changed.
    Not a named-paths breach: nothing was rolled back, and the tree may still
    hold the agent's writes."""


# SSSF's own git here (snapshots, the commit undo, the rollback) runs no program
# the agent could have planted, on every command, reads and writes alike:
#   * no hook (core.hooksPath=/dev/null), no fsmonitor (empty: "false" runs
#     `false` before git 2.36), no pager, no external diff or textconv;
#   * no filter driver: `_filters_off` names every filter in the repo's config,
#     and each is turned off by --config-env. No git child starts in a submodule
#     (`diff HEAD --ignore-submodules=dirty`), so a
#     submodule's config is never read and never an argument. An edit inside a
#     submodule is seen by hashing its files with `python3` (`_DIGEST`), which
#     reads no git config;
#   * no grafts, no replace refs, no lazy fetch (GIT_ALLOW_PROTOCOL= is the lock
#     on git 2.43; GIT_NO_LAZY_FETCH arrived later);
#   * no recursion into a submodule (submodule.recurse=false).
# On the command line and in the environment, which no config file overrides.
# Never read by these commands, so left alone: core.editor, sequence.editor (no
# commit, rebase, tag or merge runs); gpg.* and log.showSignature (no commit,
# tag, log or verify); merge.<driver>.driver, merge.tool (checkout and a mixed
# reset merge no content); alias.* (SSSF names builtins); gc.auto and
# maintenance.auto (only commit, merge, fetch, rebase, am start them);
# core.alternateRefsCommand, uploadpack.packObjectsHook (fetch and server only);
# fetch.recurseSubmodules, push.recurseSubmodules, submodule.<name>.update (no
# fetch, push or `submodule update` runs).
_SAFE = ["-c", "core.fsmonitor="]     # empty: off on every git; "false" runs `false` before 2.36
_NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]
_GIT_ENV = ("env", "GIT_ALLOW_PROTOCOL=", "GIT_NO_LAZY_FETCH=1", "GIT_GRAFT_FILE=/dev/null",
            "LC_ALL=C")


def _gitargv(args: list[str], *, off=(), literal: bool = True, hooks: bool = False,
             index: str = "") -> list[str]:
    # --literal-pathspecs: a path an agent made, such as `.claude/*` or
    # `app/[id]/page.tsx`, names that file only, never every file it matches.
    # check-ignore takes paths, not pathspecs, and refuses the option.
    # hooks=True is `_watched`'s own reads of the engineer's config before the
    # agent runs: they read no object, so they take only the locale, as they
    # always have (`env LC_ALL=C git ...`; tests pin that shape).
    env = (("env", "LC_ALL=C") if hooks else
           (*_GIT_ENV, "GIT_PAGER=cat", "SSSF_GIT_EMPTY=", "SSSF_GIT_FALSE=false"))
    if index:
        # The one command that reads an index SSSF built itself (`_INDEX`).
        env = (*env, f"GIT_INDEX_FILE={index}")
    return [*env, "git", "--no-pager", "--no-replace-objects", *_SAFE,
            *([] if hooks else _NO_HOOKS),
            "-c", "advice.graftFileDeprecated=false", "-c", "submodule.recurse=false",
            *off, *(["--literal-pathspecs"] if literal else []), *args]


def _try_git(args: list[str], cwd, off=(), hooks: bool = False, literal: bool = True,
             index: str = ""):
    """Run SSSF's own git and return `(code, out, err)`, decoded.

    A command that can't be started (an `OSError` such as E2BIG, or anything
    else the sandbox raises) gives `code=None` and the exception's text as `err`.
    `KeyboardInterrupt` and `SystemExit` pass through.
    """
    try:
        done = sandbox.current().run(
            _gitargv(args, off=off, hooks=hooks, literal=literal, index=index),
            cwd=cwd, text=False)
    except Exception as error:
        return None, "", str(error)

    def text(value) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", "surrogateescape")
        return value or ""
    return done.returncode, text(done.stdout), text(done.stderr)


GIT_ARGS_SHOWN = 120


def _shown(args: list[str]) -> str:
    """The git arguments as a breach names them: no --config-env list, control
    characters made visible, cut to a fixed length (a path can be the agent's)."""
    line = _said_line(" ".join(args))
    return line if len(line) <= GIT_ARGS_SHOWN else line[:GIT_ARGS_SHOWN - len(SAID_CUT)] + SAID_CUT


def _git(args: list[str], cwd, off=(), hooks: bool = False, answers=(), literal: bool = True,
         index: str = "") -> str:
    """stdout of SSSF's own git on exit 0, "" when the exit code is in `answers`
    (an exit code that is git's answer, such as 1 for "not set"), else a breach.

    A failing git is never an empty answer: SSSF couldn't tell what changed.
    """
    code, out, err = _try_git(args, cwd, off=off, hooks=hooks, literal=literal, index=index)
    if code == 0:
        return out
    if code is not None and code in answers:
        return ""
    raise _git_failed(args, code, err)


def _git_failed(args: list[str], code, err: str) -> PermissionBreach:
    said = f"couldn't start: {_said_line(err)}" if code is None else _said(err, code).strip()
    return GitFailed(
        f"SSSF's own git command failed: git {_shown(args)} ({said}). SSSF can't tell what "
        "changed in the tree, so it stopped. The tree may hold writes no check saw; fix what "
        "git reports, then check git status.")


def _no_commit() -> PermissionBreach:
    return GitFailed(
        "HEAD has no commit (a new repo with no commit, or an orphan or unborn branch), so SSSF "
        "has no HEAD to snapshot the tree against and can't guard an agent's writes. SSSF runs "
        "no agent there. Make a first commit, with `git commit --allow-empty -m init` or a real "
        "one (or switch to a branch that has commits), then run again.")


def _filters_off(cwd) -> list[str]:
    """Arguments that turn off every filter driver git could run, in the repo at
    `cwd`.

    SSSF's git starts no child inside a submodule (`diff HEAD` is given
    --ignore-submodules=dirty; edits there are seen by `_DIGEST`'s file reads),
    so no submodule's config is read, and none of its names becomes an argument:
    a name in a config the agent wrote can't make the argument list agent-sized.
    Only the one config `git config --list` reads at the repo root is listed,
    the empty filter name included (git runs `[filter ""]` too).

    Reads key names only, through git (`config --list --name-only -z`); no
    config value is scanned. A git that can't list them raises PermissionBreach.
    """
    names: list[str] = []
    code, out, err = _try_git(["config", "--list", "--name-only", "-z"], cwd, literal=False)
    if code != 0:
        said = f"couldn't start: {_said_line(err)}" if code is None else _said(err, code)
        raise GitFailed(
            f"SSSF couldn't list git's filter drivers (git config --list --name-only: {said}), so it ran "
            "no further git command. The tree may still hold the agent's writes.")
    for key in _fields(out):
        if key.startswith("filter.") and key.rsplit(".", 1)[-1] in ("clean", "smudge", "process", "required"):
            name = key[len("filter."):key.rindex(".")]
            if name not in names:
                names.append(name)
    off: list[str] = []
    for name in names:
        off += [f"--config-env=filter.{name}.clean=SSSF_GIT_EMPTY",
                f"--config-env=filter.{name}.smudge=SSSF_GIT_EMPTY",
                f"--config-env=filter.{name}.process=SSSF_GIT_EMPTY",
                f"--config-env=filter.{name}.required=SSSF_GIT_FALSE"]
    return off


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
  elif [ -f "$p" ]; then printf "F%s\\0" "$(env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.hooksPath=/dev/null -c submodule.recurse=false hash-object --no-filters -- "$p" 2>/dev/null)"
  elif [ -d "$p" ]; then printf "D\\0"
  else printf -- "-\\0"; fi
done
"""
_CHUNK = 256

# Prints one NUL-terminated field per path argument: `x` for a regular file (not a
# link) whose owner execute character in `ls -ld` is x or s, `-` for anything else.
# Read for a staged mode only, never from the index's own stat data.
_EXEC = """for p in "$@"; do
  if [ -f "$p" ] && [ ! -L "$p" ]; then
    case "$(LC_ALL=C ls -ld -- "$p")" in
      -??[xs]*) printf "x\\0" ;;
      *) printf -- "-\\0" ;;
    esac
  else printf -- "-\\0"; fi
done
"""


def _ask(script: str, root, paths: list[str]) -> list[str]:
    """One NUL-terminated answer per path from `script`, run in chunks through the
    current sandbox. A command that fails, or prints the wrong number of fields,
    raises GitFailed: a loop that fails on both sides would read as no change."""
    found: list[str] = []
    for i in range(0, len(paths), _CHUNK):
        chunk = paths[i:i + _CHUNK]
        try:
            done = sandbox.current().run(["sh", "-c", script, "sh", *chunk], cwd=root, text=False)
            out = done.stdout
            out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
            answers = _fields(out)
            failure = (f"it exited {done.returncode}" if done.returncode != 0
                       else "it printed the wrong number of fields" if len(answers) != len(chunk)
                       else "")
        except Exception as error:
            failure = f"couldn't start: {_said_line(str(error))}"
        if failure:
            # One file's hash-object failing is not this: that file is `F` with an
            # empty hash, an answer.
            raise GitFailed(
                f"SSSF's own file-hash command failed ({failure}). SSSF can't tell what changed "
                "in the tree, so it stopped. The tree may hold writes no check saw; fix what "
                "the shell reports, then check git status.")
        found.extend(answers)
    return found


def _blob_id(data: bytes, width: int) -> str:
    """Git's blob id of `data`: sha1 for a 40-character id, sha256 for 64."""
    import hashlib
    h = hashlib.sha256() if width == 64 else hashlib.sha1()
    h.update(b"blob %d\0" % len(data) + data)
    return h.hexdigest()


def _staged_hidden(path: str, rec: tuple[str, str, str, str], c: str, first: dict,
                   flagged: bool, executable: str, kept: str = "",
                   symlinks: bool = True) -> bool:
    """True when `git add <path>` on the current work tree would NOT stage the
    entry the index holds. `rec` is (status, old mode, new mode, new id) of the
    `diff --cached HEAD` record; `c` the path's `_CONTENT` answer; `first` the
    `diff HEAD` records' (new mode, new id); `executable` the `_EXEC` answer.
    `symlinks` is `core.symlinks`: only when it is false does `git add` keep a
    plain file's bytes under a link mode HEAD already holds."""
    status, old, mode, ident = rec
    if status == "D":
        return c != "-"
    if status not in ("A", "M", "T"):
        return True
    if mode == "160000":
        return flagged or first.get(path) != ("160000", ident)
    if mode == "120000":
        # Only under core.symlinks=false does `git add` of a link HEAD already
        # holds store the plain file's bytes under the link mode.
        if not symlinks and old == "120000" and c == "F" + ident:
            return False
        return not (c.startswith("L") and
                    _blob_id(c[1:].encode("utf-8", "surrogateescape"), len(ident)) == ident)
    if mode in ("100644", "100755"):
        if c != "F" + ident:
            return True
        # core.fileMode=false: `git add` keeps the index mode, so a mode the
        # engineer already staged before the call (`kept`) is not the agent's.
        return not (mode == old or mode == kept
                    or (mode == "100755") == (executable == "x"))
    return True


def _staged_token(value: str) -> str:
    """The `staged-…` kind of a snapshot value, or an empty string."""
    for kind in value.partition(":")[0].split(","):
        if kind.startswith("staged-"):
            return kind
    return ""


def _without_staged(value: str) -> str | None:
    """The value without its `staged-…` kind; None when no kind is left."""
    kind, _, content = value.partition(":")
    parts = [k for k in kind.split(",") if not k.startswith("staged-")]
    return f"{','.join(parts)}:{content}" if parts else None


# Run as `python3 -I -c`, where the work tree is: for each gitlink path argument,
# prints two NUL-terminated fields, a sha256 in hex of every file under it and
# the first path under it (relative to it) it couldn't read, empty when it read
# everything. A regular file counts by its relative path, its owner execute bit
# and its bytes; a symlink by its path and target, never followed; anything else
# that isn't a folder by its path and type, never opened. `.git` entries at any
# depth are skipped. Reads no ignore rule and runs no git, so no submodule config
# is read. An OSError is remembered and the walk goes on.
_DIGEST = r"""import hashlib, os, stat, sys
D = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
out = sys.stdout.buffer
for top in sys.argv[1:]:
    h = hashlib.sha256()
    bad = []
    def note(rel):
        if not bad:
            bad.append(rel)
    def rec(dfd, rel):
        try:
            names = sorted(os.listdir(dfd))
        except OSError:
            note(rel or ".")
            return
        for n in names:
            if n == ".git":
                continue
            r = rel + "/" + n if rel else n
            p = os.fsencode(r)
            try:
                m = os.stat(n, dir_fd=dfd, follow_symlinks=False).st_mode
            except OSError:
                note(r)
                continue
            if stat.S_ISDIR(m):
                try:
                    sub = os.open(n, D, dir_fd=dfd)
                except OSError:
                    note(r)
                    continue
                try:
                    rec(sub, r)
                except (OSError, RecursionError):
                    note(r)
                finally:
                    os.close(sub)
            elif stat.S_ISLNK(m):
                try:
                    t = os.fsencode(os.readlink(n, dir_fd=dfd))
                except OSError:
                    note(r)
                    continue
                h.update(b"L\0" + p + b"\0" + t + b"\0")
            elif stat.S_ISREG(m):
                try:
                    f = os.open(n, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
                except OSError:
                    note(r)
                    continue
                try:
                    st = os.fstat(f)
                    if not stat.S_ISREG(st.st_mode):
                        h.update(b"O\0" + p + b"\0")
                        continue
                    fh = hashlib.sha256()
                    while True:
                        b = os.read(f, 1 << 20)
                        if not b:
                            break
                        fh.update(b)
                except OSError:
                    note(r)
                    continue
                finally:
                    os.close(f)
                x = b"x" if st.st_mode & stat.S_IXUSR else b"-"
                h.update(b"F\0" + p + b"\0" + x + fh.hexdigest().encode() + b"\0")
            else:
                h.update(b"O\0" + p + b"\0" + str(stat.S_IFMT(m)).encode() + b"\0")
    try:
        top_fd = os.open(top, D)
    except OSError:
        note(".")
    else:
        try:
            rec(top_fd, "")
        except Exception:
            note(".")
        finally:
            os.close(top_fd)
    out.write(h.hexdigest().encode() + b"\0" + (os.fsencode(bad[0]) if bad else b"") + b"\0")
out.flush()
"""


def _failed_digest(failure: str) -> GitFailed:
    return GitFailed(
        f"SSSF's submodule digest command failed ({failure}). SSSF hashes the files under each "
        "submodule with `python3`, run where the work tree is, and can't tell what changed under "
        "a submodule without it, so it stopped. Make `python3` available there (in the sandbox "
        "image, for a sandbox run), then run again. The tree may hold writes no check saw; "
        "check git status.")


def _digests(root, paths: list[str]) -> dict[str, tuple[str, str]]:
    """`path -> (digest, first unread path)` for each gitlink folder in `paths`.

    The only way the digest runs: a `python3 -I -c` command through the current
    sandbox, so it reads the files where the work tree is. `-I` keeps the repo
    root (the `cwd`), where the agent could leave a `hashlib.py`, off the import
    path. A command that can't start, exits non-zero or prints the wrong number
    of fields raises GitFailed: a digest that can't run on either side would read
    as no change."""
    found: dict[str, tuple[str, str]] = {}
    for i in range(0, len(paths), _CHUNK):
        chunk = paths[i:i + _CHUNK]
        try:
            done = sandbox.current().run(["python3", "-I", "-c", _DIGEST, *chunk],
                                         cwd=root, text=False)
            out = done.stdout
            out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
            fields = out.split("\0")
            if fields and fields[-1] == "":
                fields.pop()
            failure = (f"it exited {done.returncode}" if done.returncode != 0
                       else "it printed the wrong number of fields"
                       if len(fields) != 2 * len(chunk) else "")
        except Exception as error:
            failure = f"couldn't start: {_said_line(str(error))}"
        if failure:
            raise _failed_digest(failure)
        for path, digest, unread in zip(chunk, fields[::2], fields[1::2]):
            found[path] = (digest, unread)
    return found


# Builds a version 2 index from `ls-files -s -z`'s own records (its argv is the
# arguments) and prints its path. Nothing of `.git/index` but those records is
# used: not a flag, not stat data, not the cache-tree, which the agent can forge.
# The TREE extension is computed here from the entries, so git reads the trees of
# only the folders that really differ from HEAD; a folder holding an unmerged
# entry, and its ancestors, are written invalid. No object is read.
_INDEX = r"""import hashlib, os, struct, subprocess, sys, tempfile
argv = sys.argv[1:]
prefix = argv[:argv.index("ls-files")]


def run(a):
    try:
        return subprocess.check_output(a, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        sys.exit(1)


info = run(prefix + ["rev-parse", "--show-toplevel", "--absolute-git-dir",
                     "--show-object-format"]).decode("utf-8", "surrogateescape").split("\n")
if len(info) < 3:
    sys.exit(1)
hasher = hashlib.sha256 if info[2].strip() == "sha256" else hashlib.sha1
size = hasher().digest_size
records = run(argv).split(b"\0")
if records and records[-1] == b"":
    records.pop()


class Node:
    def __init__(self):
        self.files, self.dirs, self.count, self.bad = [], {}, 0, False


root = Node()
body = []
for record in records:
    meta, _, name = record.partition(b"\t")
    mode, ident, stage = meta.split(b" ")
    raw = bytes.fromhex(ident.decode())
    if len(raw) != size or not name:
        sys.exit(1)
    stage = int(stage)
    fixed = struct.pack(">10I", 0, 0, 0, 0, 0, 0, int(mode, 8), 0, 0, 0) + raw
    fixed += struct.pack(">H", stage << 12 | min(len(name), 0xFFF)) + name
    body.append(fixed + b"\0" * (8 - len(fixed) % 8))
    parts = name.split(b"/")
    node = root
    if stage:
        root.bad = True
    else:
        root.count += 1
    for part in parts[:-1]:
        node = node.dirs.setdefault(part, Node())
        if stage:
            node.bad = True
        else:
            node.count += 1
    if not stage:
        node.files.append((parts[-1], mode.lstrip(b"0"), raw))


def tree_id(node):
    items = [(n, m, r) for n, m, r in node.files]
    items += [(n, b"40000", node.dirs[n].ident) for n in node.dirs]
    items.sort(key=lambda i: i[0] + (b"/" if i[1] == b"40000" else b""))
    data = b"".join(m + b" " + n + b"\0" + r for n, m, r in items)
    return hasher(b"tree %d\0" % len(data) + data).digest()


def finish(top):
    # No recursion: an agent can stage a path thousands of folders deep.
    order, stack = [], [top]
    while stack:
        node = stack.pop()
        order.append(node)
        stack.extend(node.dirs.values())
    for node in reversed(order):
        node.ident = tree_id(node)


def extension(top):
    out, stack = [], [(top, b"")]
    while stack:
        node, name = stack.pop()
        out.append(name + b"\0")
        if node.bad:
            out.append(b"-1 %d\n" % len(node.dirs))
        else:
            out.append(b"%d %d\n" % (node.count, len(node.dirs)) + node.ident)
        stack.extend((node.dirs[c], c) for c in sorted(node.dirs, reverse=True))
    return b"".join(out)


finish(root)
payload = extension(root)
data = b"DIRC" + struct.pack(">II", 2, len(records)) + b"".join(body)
data += b"TREE" + struct.pack(">I", len(payload)) + payload
data += hasher(data).digest()


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


for where in (None, "/tmp", "/var/tmp"):
    try:
        made = tempfile.mkdtemp(dir=where)
    except OSError:
        continue
    if inside(made, info[0]) or inside(made, info[1]):
        os.rmdir(made)
        continue
    with open(os.path.join(made, "index"), "wb") as out:
        out.write(data)
    sys.stdout.write(os.path.join(made, "index"))
    sys.exit(0)
sys.exit(1)
"""


def _failed_index(failure: str) -> GitFailed:
    return GitFailed(
        f"SSSF's index rebuild failed ({failure}). SSSF compares the index with HEAD through an "
        "index it builds from the entries with `python3`, run where the work tree is, and can't "
        "tell what is staged without it, so it stopped. Make `python3` available there (in the "
        "sandbox image, for a sandbox run) and a temp folder outside the work tree, then run "
        "again. The tree may hold writes no check saw; check git status.")


def _built_index(root, argv: list[str]) -> str:
    """The path of an index SSSF built from `argv`'s `ls-files -s` records.

    A `python3 -I -c` command through the current sandbox, so the file is made
    where git runs. A command that can't start, exits non-zero or prints a path
    that isn't absolute raises GitFailed."""
    try:
        done = sandbox.current().run(["python3", "-I", "-c", _INDEX, *argv], cwd=root, text=False)
        out = done.stdout
        out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
        failure = (f"it exited {done.returncode}" if done.returncode != 0
                   else "it printed no absolute path" if not out.startswith("/") else "")
    except Exception as error:
        failure = f"couldn't start: {_said_line(str(error))}"
    if failure:
        raise _failed_index(failure)
    return out


def _drop_index(root, path: str) -> None:
    """Remove the folder a built index is in; best effort."""
    try:
        sandbox.current().run(["rm", "-rf", "--", os.path.dirname(path)], cwd=root, text=False)
    except Exception:
        pass




def snapshot(run, before: dict[str, str] | None = None) -> dict[str, str]:
    """Fingerprint every path the working tree currently differs on.

    Each value is `kind:content`. The kind is git's status for the path
    (M, A, D, T), `?` for untracked, and `flag-<tag>` for an index flag
    (skip-worktree, assume-unchanged) that hides a change from `git diff`;
    several are joined with commas, for example `D,?` for a file taken out of
    the index and written again. The content is the file's blob hash, so any
    rewrite of an already-dirty or untracked file registers.

    The index is compared with HEAD too (`diff --cached HEAD`). A staged entry
    that `git add <path>` on the current work tree would not make (other bytes,
    other mode, a path taken out of the index while the file is on disk, a gitlink
    the submodule's HEAD doesn't match) adds the kind `staged-<status>-<mode>-<id>`,
    before `sub`. SSSF hashes the file itself, so no index flag or stat field
    decides it. An entry the work tree shows adds nothing: values are as without it.
    The cached diff reads an index SSSF rebuilds from `ls-files -s` (`_INDEX`),
    with a cache-tree it computes, so a forged cache-tree or stat field hides no
    entry and git reads the tree of only a folder that really differs. An
    intent-to-add entry (`git add -N`) reads as a staged empty file there.

    Renames show as a delete and an add. Names are never quoted (-z). Flagged
    paths show by content. Gitignored paths never appear. The session runtime
    is normally gitignored, but `always_writable` grants it anyway, so a
    missing gitignore line is safe. Only changed, untracked and flagged paths
    are hashed.

    Every gitlink is listed, with kind `sub` last (`M,sub` when moved). The list
    comes from the index and from the old modes of the diff against HEAD; the
    snapshot reads no tree the diff didn't. Its content is `D@<digest>` of every
    file under it, tracked or not, taken by `python3` where the work tree is
    (`_digests`); a file it couldn't read gives `D@unread:<nonce>:<path>`, never
    equal to another snapshot's. No git runs inside a submodule. `python3`
    starts only when a gitlink that is a folder was there before the call (the
    `before` snapshot, when given), or for the before snapshot itself.
    """
    root = run.repo_root
    kinds: dict[str, str] = {}
    # Listed here, every time: the list must come from the index this snapshot's
    # commands read, never from before a command that changed it.
    off = _filters_off(root)

    def add(path: str, kind: str) -> None:
        kinds[path] = f"{kinds[path]},{kind}" if path in kinds else kind

    # No commit (exit 1, nothing printed) is a refusal: there is no HEAD to
    # snapshot the tree against. Any other failure stops the snapshot too.
    # --ignore-submodules=dirty: git compares a gitlink with the submodule's HEAD
    # in-process and starts no `git status` child inside the submodule, so a
    # submodule's config (the agent can write it) never runs. A moved gitlink
    # still shows, `ignore = all` notwithstanding. An edit inside the submodule's
    # own work tree is seen by reading its files (`_DIGEST`), with no git there
    # (see references/config.md).
    if not _git(["rev-parse", "-q", "--verify", "HEAD"], root, off, answers=(1,)).strip():
        raise _no_commit()
    # --raw --no-abbrev: each record is `:<old mode> <new mode> <old id> <new id>
    # <status>`, then the path. It reads the objects `--name-status` read, and the
    # old mode names a gitlink that was in HEAD.
    diff = _fields(_git(["diff", "HEAD", "--ignore-submodules=dirty", "--no-renames",
                         "--no-ext-diff", "--no-textconv", "--raw", "--no-abbrev", "-z"],
                        root, off))
    head_gitlinks: list[str] = []
    first: dict[str, tuple[str, str]] = {}      # path -> (new mode, new id) of `diff HEAD`
    bad_diff = len(diff) % 2 != 0
    for record, path in zip(diff[::2], diff[1::2]):
        words = record.split()
        if len(words) != 5 or not record.startswith(":"):
            bad_diff = True
            break
        add(path, words[4])
        first[path] = (words[1], words[3])
        if words[0] == ":160000" and path not in head_gitlinks:
            head_gitlinks.append(path)
    # The entries as the index holds them: path, mode, id and stage, whatever its
    # cache-tree or stat data say. They feed the gitlink list and the index SSSF
    # rebuilds for the cached diff.
    ls_args = ["ls-files", "--no-recurse-submodules", "-s", "-z"]
    entries = _fields(_git(ls_args, root, off))
    # The index against HEAD, parsed the same way: what is staged, whatever the
    # work tree shows. It reads an index SSSF built from the entries above, with a
    # cache-tree SSSF computed, so a forged cache-tree or stat field hides nothing.
    built = _built_index(root, _gitargv(ls_args, off=off))
    try:
        cached = _fields(_git(["diff", "--cached", "HEAD", "--ignore-submodules=dirty",
                               "--no-renames", "--no-ext-diff", "--no-textconv", "--raw",
                               "--no-abbrev", "-z"], root, off, index=built))
    finally:
        _drop_index(root, built)
    staged: dict[str, tuple[str, str, str, str]] = {}
    bad_diff = bad_diff or len(cached) % 2 != 0
    for record, path in zip(cached[::2], cached[1::2]):
        words = record.split()
        if len(words) != 5 or not record.startswith(":"):
            bad_diff = True
            break
        staged[path] = (words[4], words[0][1:], words[1], words[3])
    if bad_diff:
        raise GitFailed(
            "SSSF's own git diff printed a record it couldn't read. SSSF can't tell what "
            "changed in the tree, so it stopped. The tree may hold writes no check saw; "
            "check git status.")
    for path in _fields(_git(["ls-files", "--no-recurse-submodules", "--others",
                              "--exclude-standard", "-z"], root, off)):
        add(path, "?")
    for record in _fields(_git(["ls-files", "--no-recurse-submodules", "-v", "-z"], root, off)):
        tag, _, path = record.partition(" ")
        if tag and tag != "H" and path:
            add(path, f"flag-{tag}")

    # Every gitlink (a 160000 entry in the index, or the old mode of the diff
    # against HEAD) is in every snapshot, so a change under it compares like any
    # other path. The snapshot reads no tree the diff didn't. A gitlink only in
    # HEAD is in the diff, as D or T. Added last, so its kind ends in `sub`.
    gitlinks: list[str] = []
    for record in entries:
        meta, _, path = record.partition("\t")
        if meta.startswith("160000 ") and path and path not in gitlinks:
            gitlinks.append(path)
    for path in head_gitlinks:
        if path not in gitlinks:
            gitlinks.append(path)
    for path in gitlinks:
        add(path, "sub")

    # A staged path is hashed with the rest, so the index is compared with the
    # bytes SSSF takes itself, never with git's own "unchanged" answer.
    paths = list(kinds) + [p for p in staged if p not in kinds]
    contents: dict[str, str] = dict(zip(paths, _ask(_CONTENT, root, paths)))
    # A gitlink that is a folder gets the digest of the files under it. Any other
    # content (missing, a file, a link) stays as it is. With `before`, only a
    # gitlink that was there before the call is digested: a new one is a changed
    # path whatever is in it, and needs no python3.
    folders = [p for p in gitlinks if contents[p] == "D"
               and (before is None or "sub" in before.get(p, "").partition(":")[0].split(","))]
    if folders:
        nonce = secrets.token_hex(8)
        for path, (digest, unread) in _digests(root, folders).items():
            contents[path] = (f"D@unread:{nonce}:{unread}" if unread else f"D@{digest}")
    # A staged entry `git add` on the work tree wouldn't make is hidden: one
    # `staged-<status>-<mode>-<id>` kind, before `sub`. The disk execute bit is read
    # only for a content match whose staged mode differs from HEAD's.
    need = [p for p, (status, old, mode, ident) in staged.items()
            if status in ("A", "M", "T") and mode in ("100644", "100755") and mode != old
            and contents[p] == "F" + ident]
    execs = dict(zip(need, _ask(_EXEC, root, need))) if need else {}
    # core.symlinks (git's default is true), read only when a staged link is judged.
    symlinks = True
    if any(rec[2] == "120000" for rec in staged.values()):
        symlinks = _git(["config", "--type=bool", "--get", "core.symlinks"], root, off,
                        answers=(1,), literal=False).strip() != "false"
    for path, rec in staged.items():
        flagged = any(k.startswith("flag-") for k in kinds.get(path, "").split(","))
        was = _staged_token((before or {}).get(path, "")).split("-")
        kept = was[2] if len(was) == 4 else ""
        if _staged_hidden(path, rec, contents[path], first, flagged, execs.get(path, ""), kept,
                          symlinks):
            parts = kinds[path].split(",") if path in kinds else []
            parts.insert(len(parts) - 1 if parts and parts[-1] == "sub" else len(parts),
                         f"staged-{rec[0]}-{rec[2]}-{rec[3]}")
            kinds[path] = ",".join(parts)
    return {path: f"{kind}:{contents[path]}" for path, kind in kinds.items()}


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
             "-c", "advice.graftFileDeprecated=false", "-c", "submodule.recurse=false",
             "--no-replace-objects")
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
_COMMIT_PATHS = """g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false -c submodule.recurse=false --no-replace-objects diff-tree -r --raw -z --no-renames --no-ext-diff --no-textconv "$@"; }
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
_COMMIT_ROOTS = """g() { env GIT_ALLOW_PROTOCOL= GIT_NO_LAZY_FETCH=1 GIT_GRAFT_FILE=/dev/null LC_ALL=C git -c core.fsmonitor= -c core.commitGraph=false -c advice.graftFileDeprecated=false -c submodule.recurse=false --no-replace-objects "$@"; }
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


COMMITS_MAX = 256
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_ZERO = re.compile(r"0+\Z")


def _host_read(repo, args: list[str], env: dict | None = None) -> bytes:
    """stdout of one read-only git in `repo`, run on the host with `subprocess`
    (never through `sandbox.current()`, which is the VM). A failure is GitFailed."""
    argv = [*_REF_READ, *args]
    try:
        done = subprocess.run(argv, cwd=repo, capture_output=True,
                              env={**os.environ, **(env or {})})
    except Exception as error:
        raise GitFailed(f"SSSF's commit check couldn't start git {_shown(args)}: "
                        f"{_said_line(str(error))}") from error
    if done.returncode != 0:
        err = done.stderr.decode("utf-8", "surrogateescape")
        raise GitFailed(f"SSSF's commit check: git {_shown(args)} failed "
                        f"({_said_line(err) or done.returncode}).")
    return done.stdout


def carried_tree(repo) -> str:
    """The tree `git add -A` would commit in `repo` right now, built in a fresh
    index of SSSF's own (never `.git/index`, whose cache-tree an earlier process
    could have forged), or "" when any step fails."""
    folder = tempfile.mkdtemp(prefix="sssf-carried-")
    try:
        env = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        tail = ["-c", "core.hooksPath=/dev/null"]
        for step in (["read-tree", "HEAD"], ["add", "-A"], ["write-tree"]):
            out = _host_read(repo, [*tail, *step], env)
        return out.decode("utf-8", "surrogateescape").strip()
    except Exception:
        return ""
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _commit_parents(repo, sha: str) -> list[str]:
    raw = _host_read(repo, ["cat-file", "commit", sha]).decode("utf-8", "surrogateescape")
    parents = []
    for line in raw.split("\n"):
        if not line:
            break
        if line.startswith("parent "):
            parents.append(line[len("parent "):].strip())
    return parents


def _diff_paths(repo, parent: str, commit: str) -> list[tuple[str, str, str, str]]:
    """`(path, status, new mode, new sha)` for every change from `parent` to `commit`."""
    raw = _host_read(repo, ["diff-tree", "-r", "--raw", "-z", "--no-renames", "--no-ext-diff",
                            "--no-textconv", parent, commit]).decode("utf-8", "surrogateescape")
    fields = raw.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise GitFailed("SSSF's commit check couldn't read git's diff-tree output.")
    found = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        words = meta.lstrip(":").split(" ")
        if len(words) != 5 or not path:
            raise GitFailed("SSSF's commit check couldn't read git's diff-tree output.")
        found.append((path, words[4][:1], words[1], words[3]))
    return found


def _secret_patterns(repo) -> list[str]:
    """repo.yaml's `secret_files`, read from the main checkout of `repo`, the way
    agents.py reads them. A repo.yaml that can't be read raises GitFailed: the
    commit check can't tell what is secret, so it refuses."""
    from . import checkout, repo_settings
    try:
        main = checkout.main_checkout_from(Path(repo)) or Path(repo)
        return list(repo_settings.load(main).secret_files)
    except Exception as failed:
        raise GitFailed(f"SSSF's commit check couldn't read repo.yaml's secret_files: {failed}")


def _secret(path: str, patterns) -> bool:
    """Whether `path` names a secret file: .env and its kin, or a `secret_files` pattern.
    Exact names only, as secret_files reads them."""
    from . import secret_files
    name = path.rsplit("/", 1)[-1]
    return secret_files.is_default(name) or secret_files._matches(name, path, list(patterns))


def _in_carried(repo, carried: str, path: str, status: str, mode: str, sha: str) -> bool:
    out = _host_read(repo, ["--literal-pathspecs", "ls-tree", "-z", carried, "--", path])
    entry = out.decode("utf-8", "surrogateescape").split("\0")[0]
    if not entry:
        return status == "D"
    head, _, name = entry.partition("\t")
    words = head.split(" ")
    return name == path and status != "D" and len(words) == 3 and (words[0], words[2]) == (mode, sha)


def _inside(path: str, entries) -> bool:
    """True when `path` is an entry, or under an entry that ends in `/`. Literal."""
    return any(path == e or (e.endswith("/") and path.startswith(e)) for e in entries)


def wrote_blob(wrote, path: str, status: str, mode: str, sha: str) -> bool:
    """True when the chain wrote `path` and the commit holds exactly that blob there,
    as a regular file. `wrote` maps a path to the blob id the chain hashed."""
    return status != "D" and mode in ("100644", "100755") and bool(sha) and wrote.get(path) == sha


def wrote_map(wrote) -> dict[str, str]:
    """`wrote` as a dict from path to blob id. None or empty is `{}`; a list of paths
    binds nothing and is a TypeError."""
    if not wrote:
        return {}
    if isinstance(wrote, Mapping):
        return dict(wrote)
    raise TypeError("wrote maps each path the chain wrote to its blob id; "
                    "a list of paths binds nothing")


def commit_passed(run, only) -> None:
    """Record that a commit passed. A full commit (`only` None) clears `run.ran`
    and `run.taken`. An `only` commit ends each agent's grant inside its entries:
    `(agent, entry)` pairs go into `run.taken`, until the next full commit."""
    if not hasattr(run, "ran"):
        return
    if only is None:
        run.ran.clear()
        if hasattr(run, "taken"):
            run.taken.clear()
        return
    if not hasattr(run, "taken"):
        run.taken = []
    for a in list(run.ran):
        for e in only:
            if (a, e) not in run.taken:
                run.taken.append((a, e))


def judge_commits(repo, base: str, tip: str, cfg: SSSFConfig, agents, *, only=None,
                  session: str = "", wrote=None, carried: str = "",
                  taken=()) -> list[tuple[str, list[str]]]:
    """Judge every commit from `base` (exclusive) to `tip`, by host git alone.
    A `wrote` path (path -> blob id) passes only when the commit holds that blob there.

    Returns `(commit, refused paths or one shape reason)` pairs, `[]` when all pass.
    A git failure raises GitFailed, which callers treat as a refusal. A shape
    refusal (a merge, a root, more than 256 commits, a tip that doesn't descend
    from `base`) comes before any diff is read."""
    wrote = wrote_map(wrote)
    for sha in (base, tip):
        if not _SHA.match(sha or ""):
            return [(tip or "?", ["not a commit id"])]
    walked: list[str] = []
    sha = tip
    while sha != base:
        if len(walked) >= COMMITS_MAX:
            return [(sha, [f"more than {COMMITS_MAX} commits"])]
        parents = _commit_parents(repo, sha)
        if len(parents) > 1:
            return [(sha, ["a merge"])]
        if not parents:
            return [(sha, ["a root: the head does not descend from the last sync"])]
        walked.append(sha)
        sha = parents[0]
    walked.reverse()
    refused: list[tuple[str, list[str]]] = []
    patterns = _secret_patterns(repo) if walked else []
    for commit in walked:
        parent = _commit_parents(repo, commit)[0]
        bad = []
        for path, status, mode, new in _diff_paths(repo, parent, commit):
            ok = not _in_git_dir(path) and not _too_long(path)
            if ok and _secret(path, patterns):
                # No agent's grant lifts this. Only a path the local checkout already
                # held, byte for byte (`carried`), is excused from the secret rule.
                ok = bool(carried and _in_carried(repo, carried, path, status, mode, new))
            if ok and only is not None:
                ok = _inside(path, only)
            if ok:
                ok = (any(permitted(path, a, cfg)
                          and not _inside(path, [e for t, e in taken if t == a])
                          for a in agents)
                      or bool(session and (path == session.rstrip("/")
                                           or path.startswith(session.rstrip("/") + "/")))
                      or wrote_blob(wrote, path, status, mode, new)
                      or bool(carried and _in_carried(repo, carried, path, status, mode, new)))
            if not ok:
                bad.append(path)
        if bad:
            refused.append((commit, bad))
    return refused


def refusal_text(refused: list[tuple[str, list[str]]]) -> str:
    lines = []
    for sha, paths in refused[:REF_COMMITS_SHOWN]:
        for path in paths[:REF_PATHS_SHOWN]:
            lines.append(f"  - {_said_line(f'{sha[:12]} {path}')}")
        if len(paths) > REF_PATHS_SHOWN:
            lines.append(f"  - {sha[:12]}: and {len(paths) - REF_PATHS_SHOWN} more")
    if len(refused) > REF_COMMITS_SHOWN:
        lines.append(f"  - and {len(refused) - REF_COMMITS_SHOWN} more commit(s)")
    return "\n".join(lines)


def check_local_commit(run, before: str, *, only=None, wrote=None) -> None:
    """Judge the commit(s) the chain just made in the local tree, `before`..HEAD.
    A `wrote` path (path -> blob id) passes only with that blob; a list is a TypeError.

    On a refusal the branch goes back to `before` with the tree kept, and a
    PermissionBreach says so. On a pass with `only` None, `run.ran` and `run.taken`
    are cleared; with `only`, each agent that ran has its grant inside those
    entries ended (`run.taken`) until the next full commit passes."""
    wrote = wrote_map(wrote)
    tip = ""
    try:
        tip = _git(["rev-parse", "HEAD"], run.repo_root).strip()
        folder = ""
        try:
            folder = Path(run.session_dir).resolve().relative_to(Path(run.repo_root).resolve()).as_posix()
        except ValueError:
            pass
        refused = judge_commits(run.repo_root, before, tip, run.cfg, getattr(run, "ran", []),
                                only=only, session=folder, wrote=wrote,
                                carried=getattr(run, "carried", ""),
                                taken=list(getattr(run, "taken", ())))
        text = refusal_text(refused)
    except GitFailed as error:
        refused, text = [(tip or "?", ["unreadable"])], str(error)
    if not refused:
        commit_passed(run, only)
        return
    reset = ""
    try:
        _git(["update-ref", "HEAD", before, tip], run.repo_root)
        code, _, _ = _try_git(["reset", "-q", "--no-recurse-submodules", before], run.repo_root,
                              _filters_off(run.repo_root))
        if code != 0:
            reset = " The index reset failed: run `git reset` to rebuild it."
    except GitFailed as error:
        reset = f" Putting the branch back failed too ({_said_line(str(error))}); HEAD is at {tip[:12]}."
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id="", type="error",
                                 name="commit_refused",
                                 payload={"before": before, "head": tip,
                                          "refused": [[c, p] for c, p in refused]}))
    raise PermissionBreach(
        "A commit the chain just made holds paths no agent that ran may write, so the branch is "
        f"back at {before[:12]} and the tree is kept as it was:\n{text}{reset}")


def _head(run, off=()) -> dict[str, str]:
    # Exit 1 is git's answer: a detached HEAD, or (after the call) a branch ref the
    # agent deleted. Both read as an empty value, so undo_commits can stop on a
    # deleted ref instead of failing here. Anything else fails.
    return {"sha": _git(["rev-parse", "-q", "--verify", "HEAD"], run.repo_root, off,
                        answers=(1,)).strip(),
            "branch": _git(["symbolic-ref", "-q", "HEAD"], run.repo_root, off,
                           answers=(1,)).strip()}


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
    where it was. A branch ref the agent deleted reads as an empty sha, and
    `merge-base` fails on it, so GitFailed stops the run with nothing undone.
    snapshot() refuses a repo with no commit, so `before` always holds a sha.
    """
    off = _filters_off(run.repo_root)   # once: the index these commands read
    after = _head(run, off)             # read once, before any reset
    if (after["sha"], after["branch"]) == (before["sha"], before["branch"]):
        return []
    if after["branch"] != before["branch"]:
        raise PermissionBreach(
            f"The agent switched HEAD from {before['branch'] or 'a detached HEAD'} to "
            f"{after['branch'] or 'a detached HEAD'}. Nothing was undone; switch back by hand.")
    # Exit 1 is the answer "doesn't descend"; any other non-zero is a failure.
    # An empty sha (the agent deleted the branch ref) makes this exit 128, so the
    # run stops with GitFailed before any reset.
    ask = ["merge-base", "--is-ancestor", before["sha"], after["sha"]]
    code, _, err = _try_git(ask, run.repo_root, off)
    if code not in (0, 1):
        raise _git_failed(ask, code, err)
    descends = code == 0
    if not descends:
        raise PermissionBreach(
            f"The agent moved HEAD from {before['sha'][:7]} to {after['sha'][:7]}, "
            "which doesn't descend from it (a reset or rebase). Nothing was undone.")
    shas = _git(["rev-list", "--abbrev-commit", f"{before['sha']}..{after['sha']}"],
                run.repo_root, off).split()
    # Read before the reset: the secret files these commits changed. enforce(), which gets
    # this same dict, adds them to its breach, so a commit of one is never silent.
    # The undo itself never depends on this: a repo.yaml that can't be read leaves the
    # default names, and a log that can't be read records nothing.
    code, named, _ = _try_git(["log", "--format=", "--name-only", "-z", "--no-renames", "-m",
                               f"{before['sha']}..{after['sha']}"], run.repo_root, off,
                              literal=False)
    try:
        patterns = _secret_patterns(run.repo_root)
    except GitFailed:
        patterns = []
    before["secret_committed"] = "\0".join(
        sorted({p for p in (named if code == 0 else "").split("\0") if p and _secret(p, patterns)}))
    undo = ["reset", "-q", "--no-recurse-submodules", before["sha"]]
    code, _, err = _try_git(undo, run.repo_root, off)
    if code != 0:
        said = f"couldn't start: {_said_line(err)}" if code is None else _said(err, code).strip()
        raise PermissionBreach(
            f"The agent made commits {', '.join(shas)}, and undoing them failed "
            f"(git {_shown(undo)}: {said}).")
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
# Which of these git reads as config, and which not (GIT_NOT_CONFIG), decides
# whether a path left changed stops all git (config) or only the run (other).
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
    """The first three values of `_watched_all`."""
    return _watched_all(run)[:3]


# Watched names git doesn't read as config: hooks are off on every command, the
# ignore, attributes and sparse-checkout files start no program, info/grafts is
# ignored through GIT_GRAFT_FILE, and git never reads sssf-private-names.
GIT_NOT_CONFIG = ("hooks", "info/exclude", "info/attributes", "info/sparse-checkout",
                  "info/grafts", "sssf-private-names")


def _watched_all(run) -> tuple[list[str], list[str], dict[str, str], list[str], list[str]]:
    """Every path the guard covers, and which of them git reads as config.

    The last two values are the resolved config paths and the config walked
    forms (see `guard_git_dir`).

    Every path the guard covers.

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
    # These git commands run before the agent, on the engineer's config, and
    # name the hooks folder to watch, so they pass hooks=True: under
    # core.hooksPath=/dev/null the real folder would go unwatched. None writes
    # the index or a ref, so no hook can fire.
    found = sandbox.current().run(
        _gitargv(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"],
                 hooks=True),
        cwd=root, text=False)
    out, err = found.stdout, found.stderr
    out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
    err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
    dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    if found.returncode != 0 or not dirs:
        dot_git = _sh('[ -e "$1" ] || [ -L "$1" ]', f"{root}/.git").returncode == 0
        if not dot_git and err.lstrip().startswith(NOT_A_REPO):
            return [], [], {}, [], []
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

    listed = sandbox.current().run(_gitargv(["config", "--list", "--show-origin", "-z"], hooks=True),
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
        _gitargv(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], hooks=True),
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
    # Exit 1 is git's answer "not set"; any other non-zero is a failure.
    hooks_value = _git(["config", "core.hooksPath"], root, hooks=True, answers=(1,)).strip()
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
    # Which paths git reads as config. A path is "other" when it is
    # <dir>/<name> for a GIT_NOT_CONFIG name, the hooks folder git names, or
    # marked "lines", and isn't marked "config". Config wins when a path is in
    # both groups.
    other_written = {f"{d}/{name}" for d in dirs for name in GIT_NOT_CONFIG}
    if hooks.startswith("/"):
        other_written.add(hooks)
    other_written |= {p for p, k in kind.items() if k == "lines"}
    config_written = [p for p in paths if not (p in other_written and kind.get(p) != "config")]
    config_real = list(dict.fromkeys(r for p, r in zip(paths, resolved) if p in config_written))
    config_walked = list(dict.fromkeys(
        [f"{root}/.git"] + config_written
        + ["/" + posixpath.normpath(p).lstrip("/") for p in [f"{root}/.git", *config_written]]))
    resolved = list(dict.fromkeys(resolved))
    kinds = {r: k for r, k in kinds.items() if r in resolved}
    return walked, resolved, kinds, config_real, config_walked


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
    # Resolving every prefix (_moved) fully covers a parent swapped under a
    # link with a relative target. A directory on the way that no longer
    # resolves is reported too.
    found = moved + [p for p in guard["paths"] if p not in moved
                     and p in _members_differ(guard, plain, followed)]
    return found + ([TOO_LARGE] if followed is None else [])


def _members_differ(guard: dict, plain: str, followed: str | None) -> list[str]:
    """The paths of `guard` whose members, in the packs taken now, differ from the
    saved ones. Shared by `_differs` and `_config_reads_same`, so the two compare
    the same way. A follow pack over the cap is never compared; only the plain
    packs are."""
    now = (_members(plain),) + (() if followed is None else (_members(followed),))
    then = (_members(guard["plain"]), _members(guard["followed"]))

    def under(members: dict, path: str) -> dict:
        rel = path.lstrip("/")
        return {k: v for k, v in members.items() if k == rel or k.startswith(rel + "/")}

    return [p for p in guard["paths"]
            if any(under(a, p) != under(b, p) for a, b in zip(now, then))]


def _config_reads_same(run, guard: dict) -> bool:
    """Whether git reads the same config bytes through every config path as it
    did before the agent ran, even if a folder on the way moved and was linked
    back. False when a pack failed or is over the cap, when the prefixes can't be
    walked, when one now resolves nowhere, or when one now resolves into the work
    tree (outside `.git`), where a later agent may write."""
    config = {**guard, **guard["config"]}
    plain, followed = _pack(config["paths"], follow=False), _pack(config["paths"], follow=True)
    if plain is None or followed is None:
        return False
    if _members_differ(config, plain, followed):
        return False
    moved = _moved(config)
    if moved is None:
        return False
    if not moved:
        return True
    now = _where(config.get("walked", []))
    real = _sh(_REAL, str(run.repo_root)).stdout.split("\0")[0]
    if now is None or not real:
        return False
    for q in moved:
        there = now.get(q, ["", "-"])[0]
        if not there:
            return False
        if (there == real or there.startswith(real + "/")) and not (
                there == real + "/.git" or there.startswith(real + "/.git/")):
            return False
    return True


def guard_git_dir(run) -> dict | None:
    """Pack the watched paths, before an agent runs.
    None only when the repo root has no .git and git says it isn't a repo; a
    repo whose config git can't read, or whose hooks directory git can't name,
    raises PermissionBreach."""
    walked, paths, kinds, config_paths, config_walked = _watched_all(run)
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
    # One call (a test pins that): every prefix of the config forms is also a
    # prefix of `walked`, so the config copy shares the same table.
    where = _where(list(dict.fromkeys(walked + config_walked)))
    if where is not None:
        prefixes = {"/".join(w.split("/")[:i]) for w in config_walked
                    for i in range(2, len(w.split("/")) + 1)}
        config_where = {k: v for k, v in where.items() if k in prefixes}
    else:
        config_where = {}
    if where is None:
        raise PermissionBreach("Could not resolve the git paths to watch, so the agent did not run.")
    # "config": the paths git reads as config, compared alone when the whole set
    # can't be put back (see restore_git_dir).
    return {"walked": walked, "where": where, "paths": paths, "plain": plain,
            "followed": followed, "kinds": kinds, "done": False,
            "config": {"paths": config_paths, "walked": config_walked, "where": config_where}}


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
    except (subprocess.TimeoutExpired, OSError):
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
    changed, and never a value. After a call, `guard["restored"]` is True only
    when git reads the same config bytes through every config path, even if a
    folder on the way moved and was linked back (a hooks
    folder or an ignore file left changed doesn't count: SSSF's git runs no
    hook); a call that crashed or was interrupted leaves it unset.
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
    # Read by agents.execute: no git runs unless this is True. Only a file git
    # reads as config must be back. Anything else left changed (hooks/, info/)
    # is rolled back around, since SSSF's git runs no hook and ignores grafts;
    # the breach below still names it. On the config paths alone, by the bytes
    # git reads: a folder moved and linked back with the same bytes counts as put
    # back, though the breach below still names the moved path.
    try:
        guard["restored"] = restored or _config_reads_same(run, guard)
    except Exception:
        guard["restored"] = False
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


# SSSF's own files (matched in any spelling a checkout takes for them),
# protected whatever the roster says. Only an agent's own
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
    """Session runtime first, then what is protected, then the agent's own list.
    Protected paths are matched in any case and in the spellings a case-insensitive
    checkout takes for them; the always-writable and `writes` matches stay exact.
    A path with a component no disk takes (over NAME_MAX) is refused for every
    agent, before any fold."""
    if _too_long(path):
        return False
    if any(_matches(path, p) for p in always_writable(cfg)):
        return True
    writes = agent.writes or []
    # SSSF's own files, whatever the roster says. A separate check: it can only
    # refuse, so the roster check below still decides everything it did before.
    # `path + "/"` so a bare `.pi` or `.githooks` (a link, a file) is inside its folder.
    # Also in any spelling a case-insensitive checkout takes for them (_folds_to);
    # only the refusing side folds, so no path passes that passed before.
    # A folded match only marks the path protected; a `writes` entry unlocks it
    # through the entries the path matches exactly, as before the fold.
    exact_fixed = [p for p in factory_files(cfg) if _matches(path, p) or _matches(path + "/", p)]
    folded_fixed = [p for p in factory_files(cfg) if _folds_to(path, p, bare=True)]
    if (exact_fixed or folded_fixed) and not any(
            _matches(path, w) and _names(w, g) for w in writes for g in exact_fixed):
        return False
    exact = [p for p in cfg.defaults.protected_files if _matches(path, p)]
    folded = [p for p in cfg.defaults.protected_files if _folds_to(path, p)]
    if exact or folded:
        # Naming a protected path is what unlocks it. The documenter's `**/*.md`
        # reaches every Markdown file, but must not reach a spec on record.
        return any(_matches(path, w) and _names(w, g) for w in writes for g in exact)
    if any(_matches(path, w) for w in writes):
        return True
    return agent.writes is None          # None = unrestricted, [] = no repo writes


GIT_DIR_KEPT = "unstaged; nothing on disk touched, since the path is inside the git directory"
_HFS_IGNORED = {*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF}

# Unicode's Default_Ignorable_Code_Point (from memory, not re-read): a Linux
# casefold folder may compare names with these dropped. A superset of _HFS_IGNORED.
_IGNORED = {0x00AD, 0x034F, 0x061C, *range(0x115F, 0x1161), *range(0x17B4, 0x17B6),
            *range(0x180B, 0x1810), *range(0x200B, 0x2010), *range(0x202A, 0x202F),
            *range(0x2060, 0x2070), 0x3164, *range(0xFE00, 0xFE10), 0xFEFF, 0xFFA0,
            *range(0xFFF0, 0xFFF9), *range(0x1BCA0, 0x1BCA4), *range(0x1D173, 0x1D17B),
            *range(0xE0000, 0xE1000)}
# The shape of an NTFS 8.3 alias (`CLAUDE~1`, `NOTES~1.TXT`, the hash form), on a folded name.
_SHORT = re.compile(r"[^.~]{1,6}~[0-9]+(?:\.[^.]{0,3})?")


# The longest name a disk takes, in code points: Linux 255 bytes (NAME_MAX in
# limits.h); APFS 255 characters, HFS+ and NTFS 255 UTF-16 units (not re-read).
NAME_MAX = 255


def _too_long(path: str) -> bool:
    """True when any piece of `path` between `/`, `\\` and `:` is over NAME_MAX
    code points. `\\` separates on Windows and `:` starts an NTFS stream."""
    return any(len(piece) > NAME_MAX for piece in re.split(r"[/\\:]", path))


def _fold_part(part: str) -> str:
    """In a part `permitted()` passes here, no run between `:` is over NAME_MAX
    code points, so the cost is linear in the part's length.

    One path component in the form every case-insensitive spelling shares:
    NFD, ignorable code points dropped, upper then casefold, NFD again, cut at
    the first `:` (an NTFS stream), trailing dots and spaces dropped."""
    text = unicodedata.normalize("NFD", part)
    text = "".join(c for c in text if ord(c) not in _IGNORED)
    text = unicodedata.normalize("NFD", text.upper().casefold())
    return text.split(":", 1)[0].rstrip(". ")


def _fold(path: str) -> list[str]:
    """The folded components of `path`; `\\` is a separator, empty results go."""
    return [f for f in (_fold_part(p) for p in path.replace("\\", "/").split("/")) if f]


def _folds_to(path: str, entry: str, *, bare: bool = False) -> bool:
    """True when `path` is `entry`, or inside it, in any spelling a case-insensitive
    checkout takes for it. Only the refusing side uses this. A glob entry that is
    not a folder is matched on the folded strings; otherwise component by component,
    where a component shaped like an 8.3 alias counts as any entry component.
    `bare` takes a path with as many components as a folder entry as inside it."""
    want = _fold(entry)
    have = _fold(path)
    if not want:
        return False
    if ("*" in entry or "?" in entry) and not entry.endswith("/"):
        return _glob("/".join(want)).fullmatch("/".join(have)) is not None
    if entry.endswith("/"):
        if len(have) < len(want) + (0 if bare else 1):
            return False
    elif len(have) != len(want):
        return False
    return all(h == w or _SHORT.fullmatch(h) is not None for h, w in zip(have, want))


def _in_git_dir(path: str) -> bool:
    """True when any `/` component of `path` is one git treats as `.git`: any case,
    with HFS-ignored code points dropped, and NTFS's `:stream`, trailing dots and
    spaces, and `git~1` short name."""
    for part in path.split("/"):
        name = "".join(c for c in part.lower() if ord(c) not in _HFS_IGNORED)
        for form in (name, name.split(":", 1)[0].rstrip(". ")):
            if form in (".git", "git~1"):
                return True
    return False


def _is_git_dir_here(run, path: str, off=()) -> bool:
    """True when `path` really names a file in a git directory on this disk: a
    component exactly `.git`, or `.git` in any case when core.ignorecase is true.
    The broad `_in_git_dir` also matches `git~1`, `.git.` and the like, which are
    ordinary names here, so the rollback must still delete them. It only refuses
    in the commit check."""
    parts = path.split("/")
    if ".git" in parts:
        return True
    if not any(p.lower() == ".git" for p in parts):
        return False
    code, out, _ = _try_git(["config", "--type=bool", "core.ignorecase"], run.repo_root, off)
    return code == 0 and out.strip() == "true"


IN_SUBMODULE = ("SSSF can't roll back inside a submodule, so the files under it may hold the "
                "agent's writes.")


def _roll_back_gitlink(run, path: str, before: dict[str, str], after: dict[str, str], off=()) -> str:
    """The outcome for a breach on a gitlink path.

    Clean before the call (kind exactly `sub`): the superproject's index entry is
    put back from HEAD, which touches no file under the submodule. Any other
    kind before: no git runs, since the path may hold the engineer's work."""
    shown = shlex.quote(path)
    gitfile = shlex.quote(path + "/.git")
    check = (f"Before you run git there, check that `{gitfile}` still points into this repo's "
             f"git directory: `cat {gitfile}` should print one `gitdir:` line, ending in "
             "`modules/<name>` under that directory (normally `.git/modules/<name>`, with the "
             "submodule's name from `.gitmodules`). If it prints anything else, or `.git` is "
             "now a folder, git there would run a config the agent chose, so put the line "
             f"back first. Then check with `git -C {shown} status` and `git -C {shown} diff`")
    was = before.get(path, "").partition(":")[0]
    unread = ""
    content = after.get(path, "").partition(":")[2]
    if "unread:" in content:
        # D@unread:<nonce>:<relative path>
        where = content.split(":", 2)[2] if content.count(":") >= 2 else ""
        unread = f" SSSF couldn't read {where or '.'} under it, so it counts as changed."
    if path in before and was != "sub":
        return (f"left as-is (it was already changed before the call, so it may hold your work). "
                f"{IN_SUBMODULE} {check}.{unread}")
    failed = ""
    if path in before:
        for flag in ("--no-skip-worktree", "--no-assume-unchanged"):
            _try_git(["update-index", "-q", flag, "--", path], run.repo_root, off)
        code, _, _ = _try_git(["checkout", "--no-recurse-submodules", "HEAD", "--", path],
                              run.repo_root, off)
        if code != 0:
            failed = "could not reset its index entry; "
    return f"{failed}NOT rolled back: {IN_SUBMODULE} {check}.{unread}"


def _roll_back(run, path: str, before: dict[str, str], after: dict[str, str], off=()) -> str:
    """Undo one unauthorized change. Returns a word describing what happened.

    Only changes the agent INTRODUCED are undone. A path that was already dirty,
    untracked or flagged when the agent started is left exactly as it is: the
    operator had uncommitted work there, and discarding it to tidy up would be
    the same harm this module exists to prevent, committed by the cleanup
    instead of the agent.

    A path with a component that is exactly `.git` (any case when core.ignorecase
    is true; `_is_git_dir_here`) is only unstaged: nothing on
    disk is deleted or checked out, since it is inside the git directory.
    A path untracked alone (kind `?`) is deleted. A staged-new path (`A`, or a
    hidden `staged-A-…` entry, flags cleared first) is
    unstaged, then deleted only if git says it isn't ignored; with nothing on
    disk it is only unstaged. A path that was already changed before the call is
    left as-is, with a note when its staged entry changed. Anything in HEAD
    (including `D,?`, a file taken out of the index and written again) has its
    index flags cleared and is restored from HEAD, index and tree both.

    A gitlink (kind `sub`) is never "rolled back": SSSF runs no git inside a
    submodule, so what the agent wrote under it stays, and the outcome says so.
    """
    # A gitlink the agent made (not in `before`) is a new staged path: unstaged
    # below, as before, with nothing under it touched.
    if path in before and "sub" in (before[path].partition(":")[0].split(",")
                                    + after.get(path, "").partition(":")[0].split(",")):
        return _roll_back_gitlink(run, path, before, after, off)
    if path in before:
        # Already dirty beforehand. If it is gone from the diff now, the agent
        # reverted an engineer's uncommitted work and the content is not ours
        # to reconstruct — say so loudly rather than pretend it was handled.
        if path not in after:
            return "REVERTED-BY-AGENT (uncommitted work lost, cannot restore)"
        said = ("left as-is (it was already changed, untracked or flagged before the call, "
                "so it may hold your work)")
        if _staged_token(after[path]) != _staged_token(before[path]):
            said += (" Its staged entry changed during the call: check "
                     f"`git diff --cached -- {shlex.quote(path)}`; `git restore --staged -- "
                     f"{shlex.quote(path)}` drops what is staged.")
        return said
    kind, _, content = after.get(path, "").partition(":")
    parts = kind.split(",")
    flagged = any(p.startswith("flag-") for p in parts)
    staged_new = any(p.startswith("staged-A-") for p in parts)
    box = sandbox.current()

    in_git = _is_git_dir_here(run, path, off)

    def delete() -> str:
        if in_git:
            return "left on disk (inside the git directory)"
        try:
            box.remove(Path(run.repo_root) / path)
            return "deleted"
        except OSError as error:
            return f"could not delete ({error})"
    if in_git:
        # A path git reads as inside the git directory is the engineer's file there
        # (`.git/config`, a hook), never the agent's to have SSSF delete or check out.
        for flag in ("--no-skip-worktree", "--no-assume-unchanged"):
            _try_git(["update-index", "-q", flag, "--", path], run.repo_root, off)
        code, _, _ = _try_git(["rm", "-q", "--cached", "--force", "--", path], run.repo_root, off)
        if code != 0:
            code, _, _ = _try_git(["update-index", "--force-remove", "--", path],
                                  run.repo_root, off)
        return GIT_DIR_KEPT if code == 0 else "could not unstage"
    if kind == "?":
        return delete()
    if staged_new or (parts[0] == "A" and not flagged):
        if flagged:
            # `git rm --cached` exits 1 on a skip-worktree entry.
            for flag in ("--no-skip-worktree", "--no-assume-unchanged"):
                _try_git(["update-index", "-q", flag, "--", path], run.repo_root, off)
        code, _, err = _try_git(["rm", "-q", "--cached", "--force", "--", path], run.repo_root, off)
        if code != 0:
            return "could not unstage" if code is not None else f"could not unstage ({_said_line(err)})"
        if staged_new and content == "-":
            return "unstaged"
        if staged_new and any(b.endswith("/") and path.startswith(b) for b in before):
            # Inside a folder that was untracked before the call (a nested repo):
            # the file may be the engineer's, so it is never deleted.
            return ("unstaged; left on disk because its folder was untracked before the "
                    "call, so it may be yours; check it by hand")
        ignored, _, err = _try_git(["check-ignore", "-q", "--no-index", "--", path],
                                   run.repo_root, off, literal=False)
        if ignored not in (0, 1):
            # Not an answer: reading it as "is gitignored" would keep the file.
            said = _said_line(err) if ignored is None else _said(err, ignored).strip()
            return ("unstaged; couldn't ask git whether it is gitignored "
                    f"({said}), so left on disk; check it by hand")
        if ignored == 1:
            done = delete()
            return "unstaged and deleted" if done == "deleted" else f"unstaged; {done}"
        return ("unstaged; left on disk because it is gitignored and may be yours, "
                "so check it by hand")
    # The path may no longer be in the index, so their status is ignored. One
    # flag per call: git applies only the last of two given together.
    for flag in ("--no-skip-worktree", "--no-assume-unchanged"):
        code, _, err = _try_git(["update-index", "-q", flag, "--", path], run.repo_root, off)
        if code is None:
            return f"could not roll back ({_said_line(err)})"
    code, _, err = _try_git(["checkout", "--no-recurse-submodules", "HEAD", "--", path],
                            run.repo_root, off)
    if code is None:
        return f"could not roll back ({_said_line(err)})"
    return "rolled back" if code == 0 else "could not roll back"


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
            patterns = _secret_patterns(run.repo_root)

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
                                     if _secret(path, patterns)
                                     or (not permitted(path, agent, run.cfg)
                                         and not permitted(path, open_, run.cfg))})
                    if barred:
                        offenders[sha] = barred
        if not offenders and not replaced:
            return ""

        lines = [f"Refs other than the run's own branch gained commits that change protected "
                 f"paths or secret files while {agent.name} ran, or a replace ref changed. Nothing was moved "
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
    after = snapshot(run, before)
    # A hidden staged entry that only went away is no finding: nothing is hidden
    # any more and the work-tree check judges the rest.
    compare: dict[str, str] = {}
    for p, v in before.items():
        if _staged_token(v) and not _staged_token(after.get(p, "")):
            v = _without_staged(v)
            if v is None:
                continue
        compare[p] = v
    touched = changed_paths(compare, after)
    breaches = [p for p in touched if not permitted(p, agent, run.cfg)]
    # Listed after the snapshot, from the index the rollback reads, only when
    # there is something to roll back.
    off = _filters_off(run.repo_root) if breaches else []
    outcomes = {p: _roll_back(run, p, before, after, off) for p in breaches}
    moved = ref_moves(run, agent, head_before) if head_before else ""
    # Secret files the agent's own commits changed, recorded by undo_commits() before it
    # reset them. The commits are undone and their changes are in the tree.
    secret = sorted(p for p in (head_before or {}).get("secret_committed", "").split("\0") if p)
    if secret:
        listed = ", ".join(_said_line(p) for p in secret[:REF_PATHS_SHOWN])
        more = len(secret) - REF_PATHS_SHOWN
        committed = (f"{agent.name} committed secret file(s) {listed}"
                     + (f" (+{more} more)" if more > 0 else "")
                     + "; the commits were undone and the changes are in the tree, uncommitted.")
    else:
        committed = ""
    if not breaches and not moved and not committed:
        return touched

    # The run stops here, so the agent's permitted changes are never verified,
    # reviewed, or committed. Name them, so the engineer knows what is sitting
    # uncommitted in the tree and has to be finished or discarded by hand.
    kept = [p for p in touched if p not in outcomes]
    if not breaches:
        message = "\n".join(m for m in (moved, committed) if m)
    else:
        scope = ("read-only" if agent.writes == []
                 else f"limited to {agent.writes}" if agent.writes
                 else f"barred from {protected(run.cfg)}")
        detail = "\n".join(f"  - {p} — {outcome}" for p, outcome in outcomes.items())
        message = f"{agent.name} is {scope} but modified {len(breaches)} path(s):\n{detail}"
        if off and any(o == "rolled back" or o.startswith("left as-is") for o in outcomes.values()):
            message += (
                "\nSSSF's git ran no filter program, so a rolled-back file under a filter holds "
                "what git stores for it: delete it and run `git checkout HEAD -- <path>` to get "
                "the filtered form. A file under a filter that git hadn't recorded as unchanged "
                "before the call counts as already changed and was left; check it with "
                "`git diff -- <path>` (see config.md).")
    if kept:
        message += ("\nIts other changes are left uncommitted and unreviewed:\n"
                    + "\n".join(f"  - {p}" for p in kept))
    if breaches:
        message += "".join("\n" + m for m in (moved, committed) if m)
    raise PermissionBreach(
        message, left=[p for p, o in outcomes.items()
                       if o not in ("rolled back", "deleted", "unstaged and deleted", "unstaged",
                                    GIT_DIR_KEPT)],
        refs_left=bool(moved))

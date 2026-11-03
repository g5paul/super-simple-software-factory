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


def _git(args: list[str], cwd) -> str:
    result = sandbox.current().run(["git", *args], cwd=cwd)
    return result.stdout if result.returncode == 0 else ""


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
            ["git", "merge-base", "--is-ancestor", before["sha"], after["sha"]],
            cwd=run.repo_root).returncode == 0
        if not descends:
            raise PermissionBreach(
                f"The agent moved HEAD from {before['sha'][:7]} to {after['sha'][:7]}, "
                "which doesn't descend from it (a reset or rebase). Nothing was undone.")
        shas = _git(["rev-list", "--abbrev-commit", f"{before['sha']}..{after['sha']}"],
                    run.repo_root).split()
        undo = ["git", "reset", "-q", before["sha"]]
    else:
        shas = _git(["rev-list", "--abbrev-commit", after["sha"]], run.repo_root).split()
        undo = ["git", "update-ref", "-d", "HEAD"]
    failed = sandbox.current().run(undo, cwd=run.repo_root).returncode != 0
    if not failed and not before["sha"]:
        failed = sandbox.current().run(["git", "rm", "-rq", "--cached", "."],
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
# changed. A change stops the run. No agent has a reason to write them: SSSF
# does the committing.
#
# It catches mistakes and casual attempts. An agent runs as the same user as
# SSSF, so a determined one could still get past it, for example by killing
# the chain before the check runs. --sandbox and --container isolate.

# info/refs is left out: git gc and repack rewrite it, and it runs nothing.
GIT_WATCHED = ("hooks", "info/exclude", "info/attributes", "info/sparse-checkout",
               "info/grafts", "config", "config.worktree", "commondir")

# Packs the watched paths that exist, relative to /. With "h", follows
# symlinks, so a write through a linked ~/.gitconfig shows; that pack is only
# compared, never unpacked, and a dangling link in it is not an error.
_PACK = """h=$1; shift
n=$#
for p in "$@"; do
  if [ -e "$p" ] || [ -L "$p" ]; then set -- "$@" "${p#/}"; fi
done
shift $n
[ $# = 0 ] && exit 0
cd / || exit 1
if [ "$h" = h ]; then
  tar -chf - "$@" 2>/dev/null | head -c $PACK_CAP | base64
else
  { tar -cf - "$@" || echo "tar failed" >&2; } | head -c $((PACK_CAP + 1)) | base64
fi
"""

# A symlink to / in hooks would have the follow pack read the whole disk.
# Each pack stops at this size; a plain pack over it can't be saved whole, so
# the agent doesn't run, and a follow pack is compared as far as it goes.
PACK_CAP = 16 * 1024 * 1024

# Removes every watched path, carrying on past a failure, then unpacks the
# saved stream from the file SSSF just wrote, and deletes that file.
_UNPACK = """f=$1; shift
for p in "$@"; do rm -rf "$p" || failed=1; done
if [ -s "$f" ]; then base64 -d < "$f" | tar -xpf - -C / || failed=1; fi
rm -f "$f"
exit ${failed:-0}
"""


def _sh(script: str, *args: str):
    return sandbox.current().run(["sh", "-c", script, "sh", *args])


def _watched(run) -> list[str]:
    """Every path the guard covers.

    The repo's git directories (the shared one and a worktree's own) and a
    worktree's `.git` file. Every config file git loads here, by `git config
    --show-origin`, and every file an include names, even an empty one. The
    directory `core.hooksPath` points at. And the global config's usual
    homes, even before they exist. Never the credential store: copying it
    would copy tokens.
    """
    root = str(run.repo_root).rstrip("/")
    out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"], root)
    dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    if not dirs:
        return []
    paths = [f"{d}/{name}" for d in dirs for name in GIT_WATCHED]
    if sandbox.current().is_file(f"{root}/.git"):
        paths.append(f"{root}/.git")
    home = _sh('printf "%s\\0%s" "$HOME" "${XDG_CONFIG_HOME:-$HOME/.config}"').stdout.split("\0")
    home = home if len(home) == 2 and home[0].startswith("/") else None

    def absolute(path: str, beside: str) -> str:
        if path.startswith("~/") and home:
            return f"{home[0]}/{path[2:]}"
        return path if path.startswith("/") else f"{beside.rsplit('/', 1)[0]}/{path}"

    listed = sandbox.current().run(["git", "config", "--list", "--show-origin", "-z"],
                                   cwd=root, text=False).stdout
    if isinstance(listed, bytes):     # a Latin-1 name in a config must not stop the run
        listed = listed.decode("utf-8", "surrogateescape")
    fields = listed.split("\0")
    for origin, entry in zip(fields[::2], fields[1::2]):
        if not origin.startswith("file:"):
            continue
        source = absolute(origin[5:], f"{root}/x")
        paths.append(source)
        key, _, value = entry.partition("\n")
        if key.lower() == "core.excludesfile":
            paths.append(absolute(value, source))
        if key.lower() == "include.path" or (key.lower().startswith("includeif.")
                                             and key.lower().endswith(".path")):
            paths.append(absolute(value, source))
    hooks = _git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], root).strip()
    if hooks.startswith("/"):
        paths.append(hooks)
    if home:
        paths += [f"{home[0]}/.gitconfig", f"{home[1]}/git/config",
                  f"{home[1]}/git/attributes", f"{home[1]}/git/ignore"]
    return [p for p in dict.fromkeys(paths)
            if p.startswith("/") and p.rsplit("/", 1)[-1] != "credentials" and "\n" not in p]


def _pack(paths: list[str], *, follow: bool) -> str | None:
    done = _sh(f"PACK_CAP={PACK_CAP}\n" + _PACK, "h" if follow else "-", *paths)
    if done.returncode != 0 or "tar failed" in done.stderr:
        return None
    if not follow and len(done.stdout) * 3 // 4 > PACK_CAP + 3:
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
    """The watched paths that differ from the saved packs, or None if packing failed."""
    plain, followed = _pack(guard["paths"], follow=False), _pack(guard["paths"], follow=True)
    if plain is None:
        return None
    now = (_members(plain), _members(followed or ""))
    then = (_members(guard["plain"]), _members(guard["followed"]))

    def under(members: dict, path: str) -> dict:
        rel = path.lstrip("/")
        return {k: v for k, v in members.items() if k == rel or k.startswith(rel + "/")}

    return [p for p in guard["paths"]
            if any(under(a, p) != under(b, p) for a, b in zip(now, then))]


def guard_git_dir(run) -> dict | None:
    """Pack the watched paths, before an agent runs. None outside a git repo."""
    paths = _watched(run)
    if not paths:
        return None
    plain = _pack(paths, follow=False)
    if plain is None:
        raise PermissionBreach("Could not save the git directory and git config "
                               "before the agent ran, so it did not run.")
    return {"paths": paths, "plain": plain, "followed": _pack(paths, follow=True) or "",
            "done": False}


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


def restore_git_dir(run, guard: dict | None, *, breach: bool = True) -> list[str]:
    """Put the watched paths back if the agent changed them.

    Returns the paths that changed. With `breach`, a change raises
    PermissionBreach after the restore; without it, as on an agent that
    already failed, it only restores, and raises only if that failed.
    """
    if not guard or guard["done"]:
        return []
    guard["done"] = True
    changed = _differs(guard)
    if changed is None:
        changed = list(guard["paths"])        # could not compare: put everything back
    if not changed and guard.get("seen"):
        changed = ["(a watched path, changed while the agent ran and changed back since)"]
    restored = True
    if changed:
        box = sandbox.current()
        made = box.run(["mktemp"])
        held = made.stdout.strip()
        if made.returncode != 0 or not held.startswith("/"):
            made = box.run(["mktemp", _git(["rev-parse", "--path-format=absolute", "--git-dir"],
                                           run.repo_root).strip() + "/sssf.XXXXXX"])
            held = made.stdout.strip()
        if made.returncode == 0 and held.startswith("/"):
            box.write_text(held, guard["plain"])
            _sh(_UNPACK, held, *guard["paths"])
        restored = _differs(guard) == []
    root = str(run.repo_root).rstrip("/") + "/"
    shown = [p[len(root):] if p.startswith(root) else p for p in changed]
    if changed and (breach or not restored):
        outcome = ("put back as they were" if restored else
                   "NOT all put back. Check them by hand before the next git command in "
                   "this repo; a path changed through a symlink is only reported")
        raise PermissionBreach(
            "The agent changed the git directory or the git config, which no agent may "
            "write: hooks and config there run code on later git commands. "
            f"{len(changed)} path(s), {outcome}:\n" + "\n".join(f"  - {p}" for p in shown))
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
    result = sandbox.current().run(["git", "checkout", "--", path], cwd=run.repo_root)
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

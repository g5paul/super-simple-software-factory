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
# file from snapshot(). The user's global git config is just as live:
# `git config --global core.hooksPath` points every commit at the agent's
# hooks. So these paths are copied aside before each agent and put back after
# it if they changed, and a change stops the run. No agent has a reason to
# write them: SSSF does the committing.

GIT_WATCHED = ("hooks", "info", "config", "config.worktree")

_SAVE = """b=$(mktemp -d) || exit 1
i=0
for p in "$@"; do
  mkdir -p "$b/$i" || exit 1
  if [ -e "$p" ] || [ -L "$p" ]; then cp -a "$p" "$b/$i/" || exit 1; fi
  i=$((i+1))
done
echo "$b"
"""

# Modes and link targets from ls, contents compared byte for byte with the
# copy (cmp through diff -r), so a crafted file with a matching checksum
# still counts as changed. Prints each watched path that differs.
_DIFFERS = """b=$1; shift
i=0
for p in "$@"; do
  c="$b/$i/${p##*/}"
  if [ -e "$p" ] || [ -L "$p" ]; then live=1; else live=0; fi
  if [ -e "$c" ] || [ -L "$c" ]; then kept=1; else kept=0; fi
  if [ $live != $kept ]; then echo "$p"
  elif [ $live = 1 ]; then
    if ! diff -r "$c" "$p" >/dev/null 2>&1; then echo "$p"
    elif [ "$(cd "${c%/*}" && find "${p##*/}" -exec ls -ldn {} + | awk '{print $1, $NF}')" != \
           "$(cd "${p%/*}" && find "${p##*/}" -exec ls -ldn {} + | awk '{print $1, $NF}')" ]; then echo "$p"
    fi
  fi
  i=$((i+1))
done
"""

_RESTORE = """b=$1; shift
i=0
for p in "$@"; do
  c="$b/$i/${p##*/}"
  rm -rf "$p" || exit 1
  if [ -e "$c" ] || [ -L "$c" ]; then cp -a "$c" "${p%/*}/" || exit 1; fi
  i=$((i+1))
done
"""


def _sh(script: str, *args: str):
    return sandbox.current().run(["sh", "-c", script, "sh", *args])


def _watched(run) -> list[str]:
    """Every path the guard covers: the repo's git directories (the shared one
    and a worktree's own), and the user's global git config."""
    out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"],
               run.repo_root)
    dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
    if not dirs:
        return []
    home = _sh('printf "%s\\n%s\\n" "$HOME" "${XDG_CONFIG_HOME:-$HOME/.config}"').stdout.split("\n")
    paths = [f"{d}/{name}" for d in dirs for name in GIT_WATCHED]
    if len(home) >= 2 and home[0].startswith("/"):
        paths += [f"{home[0]}/.gitconfig", f"{home[1]}/git"]
    return paths


def guard_git_dir(run) -> dict | None:
    """Copy the watched paths aside, before an agent runs. None outside a git repo."""
    paths = _watched(run)
    if not paths:
        return None
    saved = _sh(_SAVE, *paths)
    if saved.returncode != 0 or not saved.stdout.strip():
        raise PermissionBreach("Could not copy the git directory and git config aside "
                               f"before the agent ran: {saved.stderr.strip()}")
    return {"paths": paths, "backup": saved.stdout.strip(), "done": False}


def _differs(guard: dict) -> list[str] | None:
    done = _sh(_DIFFERS, guard["backup"], *guard["paths"])
    return done.stdout.split() if done.returncode == 0 else None


def restore_git_dir(run, guard: dict | None, *, breach: bool = True) -> list[str]:
    """Put the watched paths back if the agent changed them.

    Returns the paths that changed. With `breach`, a change raises
    PermissionBreach after the restore; without it, as on an agent that
    already failed, it only restores. Either way the copy is removed once
    the paths match it again.
    """
    if not guard or guard["done"]:
        return []
    guard["done"] = True
    changed = _differs(guard)
    if changed is None:
        changed = list(guard["paths"])        # could not compare: put everything back
    restored = True
    if changed:
        done = _sh(_RESTORE, guard["backup"], *guard["paths"])
        restored = done.returncode == 0 and _differs(guard) == []
    if restored:
        sandbox.current().run(["rm", "-rf", guard["backup"]])
    root = str(run.repo_root).rstrip("/") + "/"
    shown = [p[len(root):] if p.startswith(root) else p for p in changed]
    if changed and breach:
        outcome = ("put back as they were" if restored else
                   f"NOT put back; the originals are in {guard['backup']}")
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

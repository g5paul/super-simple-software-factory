# Catch an agent that moves a ref other than its own HEAD

open.md P1 row "An agent can rewrite another branch without the after-call check seeing it". The design is the brief's; this spec makes it exact.

This is the second version. The spec review changed one thing: the module-level `protected_from` is gone, and `ref_moves` decides "barred" with two calls to `permitted`. The rest is as the first version had it.

## Read this first: a cost the brief did not list

The hub's `protected_files` lists `specs/`, and every run's chain commits its plan to `specs/` on its own `sssf/<id>` branch. Under this design that commit is "a new commit on another ref that changes a protected path", so **any run whose agent is mid-call when another run commits its plan (or any other protected path) is stopped**. With two runs going at once this is likely, not rare, because agent calls take most of a run's wall time. The design cannot tell that commit from an agent's hijack of `specs/` on another branch, so this spec does not exempt it. The engineer should decide before merging whether that cost is acceptable (see `## Costs`). The builder builds the design as written.

## Threat model

The agent runs as the engineer's own user with a shell. With HEAD on its run branch it can write any object and move any ref: `git commit-tree` plus `git update-ref refs/heads/main`, a tag, another run's `sssf/*` branch, `refs/replace/*`. Its own branch and tree come back clean, so `undo_commits` and `enforce` see nothing. It can also write `.git/worktrees/<id>/commondir` and `gitdir` of other worktrees, the commit-graph, replace refs and loose objects, to make a later git read lie or run its code.

What this guard can't stop at its level: it runs after the call, as the same user. An agent can kill the chain first, move a ref after the check, or leave a background process that does. It cannot tell the agent's commit from the engineer's. It lists refs with `git for-each-ref`, so it never sees another worktree's `HEAD` file or per-worktree refs, nor pseudo-refs outside `refs/`.

## Baseline

`main` at `ea53cdf`. Every finding is judged against it. There, `permissions.head()` records HEAD's sha and branch only, and the brief's command passes for every agent.

## Bar

Must:

- Stop the brief's command for every agent, `writes: []` included. `main` keeps the hijack commit, and the breach names `refs/heads/main`, its old and new sha, the commit and the path.
- Stop the same on a tag (light or annotated), another branch and an `sssf/*` branch. Stop on any `refs/replace/` ref created, moved or deleted.
- Not stop for: commits on the run's own branch (undone as today); new commits on other refs that change only paths this agent is not barred from; deleted refs; rewinds; fast-forwards and new refs at commits that already existed.

Must never, where the baseline does not:

- Move, delete or create a ref, or delete or overwrite an engineer commit or file. The check only reads.
- Run git with a `cwd` outside `run.repo_root`, or read another checkout's files.
- Run a hook, filter, textconv, external diff or fetch from its reads.
- Turn a baseline refusal into a pass. A switched or rewound HEAD is refused by `undo_commits` exactly as today.
- Change an ADW script (`adws/adw_*.py`, shipped or hub), or add module-level mutable state.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: the check.
- `.claude/skills/sssf/templates/adws/adw_modules/agents.py`: one argument and its comment.
- `.claude/skills/sssf/tests/test_agent_commits.py`: the tests.
- `.claude/skills/sssf/references/config.md`: document the check and its costs.
- `docs/open.md`: rewrite the P1 row, add the rows named below.

## Design, exact

All of it in `permissions.py`. The new module-level names are these eight and no others: `REFS_UNREAD`, `REF_COMMITS_SHOWN`, `REF_PATHS_SHOWN`, `_REF_READ`, `_COMMIT_PATHS`, `_head`, `_refs`, `ref_moves`. All go in the **first build**; a fix round that adds one stops the run. The constants are immutable (str, int, tuple). `permitted` is not changed and not copied.

### Constants

- `REFS_UNREAD = "(refs unread)"`: what `head()` records when git can't list refs.
- `REF_COMMITS_SHOWN = 20`, `REF_PATHS_SHOWN = 10`: caps on what a breach lists; the rest is counted as `(+N more)`.
- `_REF_READ`, a tuple: `("env", "GIT_ALLOW_PROTOCOL=", "GIT_NO_LAZY_FETCH=1", "LC_ALL=C", "git", "-c", "core.fsmonitor=", "-c", "core.commitGraph=false", "--no-replace-objects")`. Every git command of this check starts with it. `GIT_ALLOW_PROTOCOL=` (empty) refuses every transport for a child `git fetch`, over any config; `GIT_NO_LAZY_FETCH` is for gits that have it.
- `_COMMIT_PATHS`, a shell script. For each sha in `"$@"` it runs the `_REF_READ` git (written out in the script, same env and options) with `diff-tree -r -m --root --no-commit-id --name-only -z --no-renames --no-ext-diff --no-textconv "$c"`, exits 1 if that fails, then prints one extra NUL. A path is never empty, so an empty field ends a commit's list. `-m` lists a merge against each parent, `--root` a root commit against nothing. `--name-only` compares tree entries and reads no blob. Keep `diff-tree`: `git log` honours `log.showSignature` and can run `gpg.program`.

### `head(run)`

Split today's body into `_head(run)` (sha and branch, unchanged). `head(run)` returns `{**_head(run), "refs": _refs(run)}`. The return type stays `dict[str, str]`.

`_refs(run) -> str` runs `[*_REF_READ, "for-each-ref", "--format=%(objectname) %(refname)"]` with `cwd=run.repo_root`, `text=False`, through `sandbox.current().run`. Exit 0: stdout decoded with `surrogateescape`. Anything else: `REFS_UNREAD`. This format opens no object. Loose and packed refs both appear.

### `undo_commits(run, before)`

Uses `_head(run)` for `after` and compares sha and branch only: `if (after["sha"], after["branch"]) == (before["sha"], before["branch"]): return []`. Nothing else changes. Without this, a moved tag would send it into `git reset`, which unstages the engineer's staged work.

### Which paths are barred

Decided inside `ref_moves`, with names local to it. `open_ = agent.model_copy(update={"writes": None})`. A path `p` is barred when:

```
not permitted(p, agent, run.cfg) and not permitted(p, open_, run.cfg)
```

With `writes=None`, `permitted` is False only for a path on the fixed list or in `protected_files`, and True for an `always_writable` one. So the second call says "protected", and the first says "this agent's own `writes` doesn't unlock it". `writes: []` alone never bars a path here: an ordinary path is permitted for `open_`.

### `ref_moves(run, agent, before) -> str`

Returns the breach text, or `""`. It never raises: any exception inside becomes the could-not-check text. Steps:

1. `then = before.get("refs")`. `None` (a caller that recorded no refs): return `""`.
2. `now = _refs(run)`. `then == now`: return `""`. This is the usual path, one git call. It covers both sides unread (not a repo, as `head` treats it today).
3. Exactly one side is `REFS_UNREAD`: return the could-not-check text.
4. Parse each side to `{refname: sha}`: per non-empty line, `sha, _, name = line.partition(" ")`. A sha that is not 40 or 64 lowercase hex, or an empty name: could-not-check. Only validated shas ever reach a git command line, never a ref name.
5. `own = before["branch"]` (`""` when detached).
6. `replaced`: every name under `refs/replace/` whose value differs or that exists on one side only, with `created`, `moved` or `deleted`.
7. `tips`: `{name: sha}` for names in `now`, other than `own`, whose sha differs from `then` or that are new. Deleted refs are not tips.
8. If `tips`: new commits = `[*_REF_READ, "rev-list", *sorted(set(tips.values())), "--not", *sorted(set(then.values()))]`, `cwd=run.repo_root`. Non-zero exit (a missing or unreadable object, an argument list too long): could-not-check. A rewind, a fast-forward to old commits or a new ref at an old commit gives no commits. A tip that is a tree or blob gives none.
9. Paths per commit: `_COMMIT_PATHS` over the commits in chunks of `_CHUNK` (it exists already, at `permissions.py:101`), `cwd=run.repo_root`, `text=False`. Non-zero exit, or a count of empty-field terminators that differs from the chunk's length: could-not-check.
10. `offenders`: commits with at least one barred path, with those paths sorted.
11. No `offenders` and no `replaced`: return `""`.
12. Only now, per tip in name order: `rev-list <sha> --not <then shas>`, intersect with `offenders`. A ref with a non-empty intersection is named. If that call fails, the ref is named with `(could not list its commits)`.

The breach text holds, with non-printable characters shown as `?` as `_said_line` does:

- a lead: refs other than the run's own branch gained commits that change protected paths while `<agent.name>` ran; nothing was moved back, deleted or rewritten;
- per named ref: `  - <refname>: <old full sha or (new)> -> <new full sha>`, then per commit `      <full sha> changes: <paths>`, capped by the two constants;
- per replace ref: `  - <refname>: created|moved|deleted` with its old and new full sha where each exists;
- a close: SSSF can't tell who moved a ref; the engineer's own commit, merge, pull, fetch or stash that changes a protected path during an agent call stops the run the same way; if it was theirs, nothing needs undoing and they run again; if not, `git show --stat <commit>` shows it and `git update-ref <ref> <old> <new>` moves a branch back.

The could-not-check text says SSSF could not compare the refs before and after the call, quotes git's error through `_said` where there is one, says no ref was moved and no fetch was tried, and tells the engineer to check `git for-each-ref` by hand. It stops the run.

### `enforce(run, phase, agent, before, head_before=None)`

Tree work is unchanged and comes first, rollbacks included. Then `moved = ref_moves(run, agent, head_before) if head_before else ""`.

- No tree breach, no `moved`: return `touched`, as today.
- No tree breach, `moved`: raise `PermissionBreach(moved)` plus the "left uncommitted and unreviewed" lines for `touched`, in the wording the tree breach uses.
- Tree breach: today's message, then `moved` appended on a new line if any.

A caller that passes no `head_before` gets today's behaviour exactly.

### `agents.py`

Line 400 becomes `permissions.enforce(run, phase, agent, tree_before, head_before)`. Add to the comment above the `try` that the ref check runs inside `enforce`, after the git-directory restore and the commit undo. No other change. When `undo_commits` itself raises (a switched or rewound HEAD), `enforce` is not reached and no ref report is made, as at baseline.

### Docstrings

Update the module docstring (a paragraph on refs, after the one on commits), and the docstrings of `head`, `undo_commits` and `enforce`.

## Tests

In `test_agent_commits.py`, with a second fixture that builds its config from the shipped `sssf.config.yaml` under `.claude/skills/sssf/templates/`, the way `test_factory_holes.py`'s `load`, `named` and `make` do. Agents come from that roster: its builder and a `writes: []` reviewer. The sequence in every test is `tree = snapshot(run)`, `head = permissions.head(run)`, the agent's git commands by `subprocess`, `undo_commits(run, head)`, `enforce(run, None, agent, tree, head)`. No real agent starts. `R` is `.claude/skills/sssf/templates/adws/adw_modules/rules.py`, committed in the base.

Stopped (each fails on `main`, where `enforce` takes no fifth argument):

1. The brief's command, for the builder and for the reviewer. The message has `refs/heads/main`, the old sha, the new sha and `R`. After the check `main` still points at the hijack commit, the tree is clean and no other ref moved.
2. A light tag, an annotated tag, another branch and an `sssf/other` branch, each given a new commit that changes `R`.
3. A path from the roster's `protected_files` rather than the fixed list, on another branch.
4. A `refs/replace/` ref created; one that existed before the call deleted; one moved.
5. A merge commit on `main` with parents (`main`, an old commit) whose tree takes `R` from the old parent.
6. A protected commit on the run's own branch with a tag put on it: the commit is undone, the tree rolled back, and the tag named.
7. A tree breach and a ref breach together: one `PermissionBreach` with both, the tree rolled back.
8. The run's own repo given a promisor remote with an `uploadpack` script, and another branch moved to a commit written with `git hash-object -t commit -w --literally` whose parent doesn't exist: stopped as could-not-check, and the script's marker file does not exist.

Not stopped (`enforce` returns):

9. A commit on `main` changing only `src/app.py`, made by `commit-tree` and `update-ref` while HEAD is on the run branch, standing in for the engineer. For the builder and for the `writes: []` reviewer.
10. A second worktree (`git worktree add`) committing an unprotected path on its own branch.
11. A branch deleted; a branch rewound; a branch fast-forwarded to a commit that existed before; a new branch at an existing commit.
12. A commit on the run's own branch: undone as today, its path returned by `enforce`.
13. An agent whose `writes` names `R` (the builder with `model_copy(update={"writes": [..., R]})`): a commit changing `R` on another branch.
14. A second worktree whose `commondir` is rewritten to point at a repo with a promisor remote and an `uploadpack` script that touches a marker file, with that worktree's branch moved to a new unprotected commit: no stop, and the marker file does not exist.

Also:

15. The three kinds of path in one commit. A commit on another branch, for the `writes: []` reviewer, changes `R`, `src/app.py` and a session runtime path under `always_writable` (added with `git add -f`). The run is stopped, and the breach lists `R` for that commit and neither of the other two paths.
16. `undo_commits` with a tag moved and HEAD untouched returns `[]` and leaves a staged file staged.
17. Every existing test in the file passes unchanged, including those that call `enforce` with four arguments.

## Costs

`config.md` states these, in these terms:

- **Concurrent runs stop each other on protected paths the chain commits.** See the top of this spec. In the hub that is every plan commit under `specs/`, and any commit under `adws/`, `.claude/`, `docs/adr/`, `GLOSSARY.md`, `CLAUDE.md` or `docs/projects.md` on any ref. The stopped run's breach names the other run's branch and commit, and tells the engineer nothing needs undoing.
- **The engineer's own protected edits.** A commit, merge, pull, fetch (it moves `refs/remotes/*`) or stash (`refs/stash`) that brings in a commit changing a protected path during an agent call stops that run. Merging a guard run while another run's agent works is one. A true merge commit is judged against both parents, so it stops the run when either side changed a protected path since the fork, even if those commits were old. How often: whenever the engineer touches protected paths with a run in flight; the fix is to run again.
- **Concurrent guard runs.** Their commits change guard code, so they stop a feature run whose agent is working.
- **Size.** One `for-each-ref` before and after each call. More git only when a ref changed: one `rev-list`, and one `diff-tree` per new commit. A repo with more refs than fit on one command line stops with could-not-check when a ref moves.

## Out of scope

Old holes this change leaves, for a reviewer to file as such:

- **Unprotected-path commits on other branches.** An agent can still land commits changing only unprotected paths on any other ref, outside review. A `writes: []` reviewer could put product code on `main` that way. New open.md row.
- Another worktree's `HEAD` file and per-worktree refs, the launch checkout's `.git/HEAD` in a worktree run, and pseudo-refs outside `refs/`. New open.md row.
- A ref created or moved to a tree or a blob: it carries no commit, so nothing is judged.
- No ref report when `undo_commits` stops the run for a switched or rewound HEAD.
- `--no-replace-objects` for the rest of the guard's git.
- Gitignored files, a write or ref move landing after the check, an agent that kills the chain first, an object file whose content doesn't match its name.
- A package standing in for a listed module, the guard files off the protected list, `adw_prompt.py --agent guard_builder`.
- agentsh's policy.

## Docs

- `.claude/skills/sssf/references/config.md`: beside the text on an agent's own commit (near lines 175 to 230), add what is recorded, what stops a run, what doesn't, that nothing is moved back, and the `## Costs` list.
- `docs/open.md`: replace the P1 row at line 36 with the two new rows above and one for the concurrent-run cost.
- No CHANGELOG exists. There is no `app_docs/` folder, so no older write-up goes stale.

## Verification

The builder runs from its copy of the skill outside the repo, with the copy command the build brief gives:

```
pytest tests/test_agent_commits.py tests/test_factory_holes.py tests/test_git_dir_guard.py -q
pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py
```

Both exit 0. Never run `tests/test_export.py` or `tests/test_install_hook.py` under agentsh: they push, and three refusals stop the call. The chain's verify phase runs the whole suite.

By hand, in a throwaway repo: run the brief's command between `head()` and `enforce()` and read the breach; confirm `git rev-parse main` is the hijack commit before and after the check.

What the planner observed on this machine's git 2.43, in a throwaway repo: `rev-list <tips> --not <shas>` and the `diff-tree` line above give the expected commits and paths for a merge and a root commit; a tree or blob tip exits 0 with no commits; in a repo with a promisor remote and an `uploadpack` script, a commit with a missing parent made `rev-list` and `diff-tree` exit 128 without the script running, with and without `GIT_ALLOW_PROTOCOL=`. Not observed: that the empty `GIT_ALLOW_PROTOCOL` is what blocks a fetch, since none was attempted either way. Tests 8 and 14 pin the outcome.

## For the engineer

- `adws/adw_modules/permissions.py` and `adws/adw_modules/agents.py` are the hub's running copies and are protected. Until they take this change the way the hub takes shipped modules, the hub's own runs are unchecked.
- Decide on the concurrent-run cost at the top before merging.
- `docs/projects.md`, `.claude/agents/bypass-reviewer.md` and `adws/adw_data/prompt_engineering/bypass_reviewer/system.md` mention the after-call check; update them if they describe this hole as open.

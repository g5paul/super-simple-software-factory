# Stop the ref check stopping runs for SSSF's own records and a stash

This is open.md's three P1 rows on the ref check merged in 06f6886: "The after-call ref check stops a run when another run commits a protected path", "`git stash -u` during any agent call stops the run", and "Two gaps in the ref check's own reads". No SSSF update goes to the projects until they're fixed. Run it with `--worktree` and `--base main`, on the hub's own config. `guard_builder` writes the shipped guard code and its tests, so this run needs no one-run config.

## The problem

`permissions.ref_moves` lists the commits that became reachable during an agent call on refs other than the run's own branch, and runs `diff-tree -r -m --root` on each. It stops the run if any listed path is barred to the agent. Two things make it stop runs it shouldn't:

- **Records.** The shipped roster protects `specs/` and `.scratch/`, and every chain's plan phase commits its spec under `specs/` on its own `sssf/*` branch. So in every project, another run's plan commit stops a running builder or reviewer. `just trial` starts its attempts at once, so they stop each other. An engineer merge of a finished run does the same.
- **A stash.** `git stash -u` makes a commit whose third parent holds the untracked files and is a root commit. With `-m`, the stash commit is diffed against that parent too, which lists every tracked file. So any `git stash -u` during a call stops the run and names protected files nobody touched. Git 2.43 makes that parent even with nothing untracked. SSSF's own `just merge` error says "Commit or stash them first".

And two gaps in the check's reads, both found by the `bypass-reviewer` subagent on run ae5ebd82:

- **Grafts.** `--no-replace-objects` doesn't turn off `info/grafts`. An agent that replaces `.git/info` with a symlink keeps a graft live past the git-directory restore, and the graft hides the hijack from `ref_moves`. The run still stops, for `.git/info`, but the breach doesn't name `main`, so after the engineer fixes `.git/info`, `main` keeps the hijack.
- **The no-fetch env isn't pinned.** Removing `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` from `_REF_READ` and `_COMMIT_PATHS` leaves every test green. Test 8's missing object is a parent, and git never fetches a missing parent; it does fetch a missing tree.

## The design

This brief settles the design. Spend the spec on making it exact.

1. **Records don't count on other refs.** In `ref_moves` only, a path under `specs/` or `.scratch/` is never barred. The run's own branch is unchanged: `enforce` and `undo_commits` judge it as today, so a builder still can't write `specs/` in its own tree. Name the two folders in one constant in the first build, with a comment on why.
2. **Each new commit is judged against its first parent**, or against nothing for a root commit, instead of against every parent. That covers the stash: the stash commit is judged against `HEAD`, its index commit against `HEAD`, and its untracked commit, a root, by its own tree, which holds only the untracked files. A merge judged against its first parent still lists everything the merge brings into that line, including a second parent's changes. The spec says what `-m` caught that this doesn't, and checks that each such case is already one of open.md's filed ref-move holes (re-parenting on an existing commit, a fast-forward or rewind to one, a symbolic ref). If any isn't, say so and keep `-m` for commits that aren't stash-shaped instead.
3. **No grafts.** `_REF_READ` and `_COMMIT_PATHS` set `GIT_GRAFT_FILE=/dev/null`, beside the env they set today.

Decided by the engineer on 2026-11-13, so not open here: exempting live runs' `sssf/*` branches was turned down, since `just merge` would land a hijack of one; barring only paths every agent is barred from was turned down, since in the hub `guard_builder` may write the guard code and a reviewer could then put guard code on `main`.

## Rules from the last two guard runs

- **All git runs in the run's own repo**, never with a `cwd` inside another worktree or checkout.
- **Reads can't run agent code or fetch.** Every object read keeps the env and flags `_REF_READ` has, plus the graft line.
- **Constants go in the build.** A fix round that adds any module-level name stops the run, even a constant.

## The bar

The baseline is `main`.

- Everything `main` stops still stops: the `commit-tree` plus `update-ref` hijack of `main`, a tag, another branch or an `sssf/*` branch with a new commit changing a protected path outside `specs/` and `.scratch/`, any `refs/replace/` change, for every agent including `writes: []` reviewers. The breach still names the ref, both shas, the commits and the paths.
- The hijack of `main` behind a symlinked `.git/info` with a live graft: the breach names `refs/heads/main`.
- No stop for: another run's plan commit under `specs/`; a commit under `.scratch/` on another ref; an engineer merge, true or fast-forward, of a finished run whose protected changes are only under `specs/` and `.scratch/`; `git stash` and `git stash -u` of unprotected files, by the engineer or an agent; two runs started at once, each committing its plan, as `just trial` does.
- Still a stop: a stash of a protected file outside `specs/` and `.scratch/`, tracked or untracked.
- No engineer commit, branch or file is deleted or overwritten, and no ref is moved.
- No change to the run's own branch: `enforce` and `undo_commits` behave as on `main`.
- No change to any ADW script, and no new module-level mutable state.

## Costs the spec states

- An agent can put a spec or a `.scratch/` file on another ref, `main` included, without a stop. A spec on `main` runs nothing, but a later run's planner may read it.
- What first-parent judging gives up, from design point 2.

## Testing

In `.claude/skills/sssf/tests/test_agent_commits.py`, through `permissions.head`, `undo_commits` and `enforce` in throwaway git repos, as its ref tests do:

- another worktree's plan commit under `specs/` on its own branch during a builder's call and a reviewer's call: no stop. Fails on `main`, passes after;
- two runs in two worktrees, each committing a plan while the other's builder runs: no stop for either;
- the engineer's `git stash -u` with an unprotected change and with nothing untracked: no stop. Fails on `main`;
- a stash of a protected file outside `specs/` and `.scratch/`, tracked and untracked: stopped;
- the true merge into `main` of a branch whose only protected changes are under `specs/`: no stop; and of a branch carrying a protected change outside them: stopped;
- the hijack behind a symlinked `.git/info` with a live graft: the breach names `refs/heads/main`. Fails on `main`;
- a commit whose tree is missing, with a promisor remote whose `uploadpack` is a script that writes a marker: the marker never appears, and the test fails when the no-fetch env is removed from `_REF_READ` or `_COMMIT_PATHS`;
- every existing ref test still passes.

The tests never start a real agent.

## Where to edit

Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`, never under the launch checkout above it. agentsh refuses writes outside the worktree, so an `EACCES` on a path outside it means a wrong path, not a missing permission.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. That holds for the builder, the bypass reviewer and any harness an agent writes. `tests/test_export.py` and `tests/test_install_hook.py` push to bare repos, so the builder leaves them out of its own runs: `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py`. The chain's verify phase runs as code, outside agentsh, and runs both. To give a bare repo a commit, as an upstream for a fetch case, fetch into it: `git -C <bare> fetch -q <work> main:main`.

## Out of scope

- The ref-move holes open.md files: re-parenting, fast-forward or rewind to an existing commit, symbolic refs, the two-call form, pseudo-refs outside `refs/`, another worktree's `HEAD`, and an agent's `writes` applying on every ref.
- The hub's own `adws/adw_modules/`, which doesn't have the ref check yet.
- The engineer's own commits to other protected paths during an agent call, such as `CLAUDE.md` or `adws/`, which still stop the run.
- `uv.toml` and the other unprotected-path rows.

# Stop the ref check stopping runs for SSSF's own specs and a stash

This is open.md's three P1 rows on the ref check merged in 06f6886: "The after-call ref check stops a run when another run commits a protected path", "`git stash -u` during any agent call stops the run", and "Two gaps in the ref check's own reads". No SSSF update goes to the projects until they're fixed. Run it with `--worktree` and `--base main`, on the hub's own config. `guard_builder` writes the shipped guard code and its tests, so this run needs no one-run config.

## The problem

`permissions.ref_moves` lists the commits that became reachable during an agent call on refs other than the run's own branch, and runs `diff-tree -r -m --root` on each. It stops the run if any listed path is barred to the agent. Two things make it stop runs it shouldn't:

- **Specs.** The shipped roster protects `specs/`, and every chain's plan phase commits its spec under `specs/` on its own `sssf/*` branch. So in every project, another run's plan commit stops a running builder or reviewer. `just trial` starts its attempts at once, so they stop each other. An engineer merge of a finished run does the same.
- **A stash.** `git stash -u` makes a commit whose third parent holds the untracked files and is a root commit. With `-m`, the stash commit is diffed against that parent too, which lists every tracked file. So any `git stash -u` during a call stops the run and names protected files nobody touched. Git 2.43 makes that parent even with nothing untracked. SSSF's own `just merge` error says "Commit or stash them first".

And two gaps in the check's reads, both found by the `bypass-reviewer` subagent on run ae5ebd82:

- **Grafts.** `--no-replace-objects` doesn't turn off `info/grafts`. An agent that replaces `.git/info` with a symlink keeps a graft live past the git-directory restore, and the graft hides the hijack from `ref_moves`. The run still stops, for `.git/info`, but the breach doesn't name `main`, so after the engineer fixes `.git/info`, `main` keeps the hijack.
- **The no-fetch env isn't pinned.** Removing `GIT_ALLOW_PROTOCOL=` and `GIT_NO_LAZY_FETCH=1` from `_REF_READ` and `_COMMIT_PATHS` leaves every test green. Test 8's missing object is a parent, and git never fetches a missing parent; it does fetch a missing tree.

## The design

This brief settles the design. Spend the spec on making it exact.

1. **Specs don't count on other refs.** In `ref_moves` only, a path under `specs/` is never barred, as long as no part of the path is `..`, `.` or empty. The run's own branch is unchanged: `enforce` and `undo_commits` judge it as today, so a builder still can't write `specs/` in its own tree. Name the folder in one constant in the first build, with a comment on why. `.scratch/` is not exempt: `adw_tickets.py` hands each ticket under `.scratch/` straight to `ticket_builder`, with no planner between, so a ticket planted on `main` would be built.
2. **Each new commit is judged against every parent, as on `main`, except a parent that is a root commit.** A root commit is still judged by its own tree. That covers a real stash: `git stash -u`'s untracked parent is a root, so the stash commit is no longer diffed against it, and the root itself lists only the untracked files. A merge, an octopus merge and a forged stash whose untracked parent isn't a root are still diffed against every parent.
3. **No grafts.** `_REF_READ` and `_COMMIT_PATHS` set `GIT_GRAFT_FILE=/dev/null` and `-c advice.graftFileDeprecated=false`, beside the env and flags they set today, so a breach never opens with git's grafts hint.

Decided by the engineer on 2026-11-13, so not open here: exempting live runs' `sssf/*` branches was turned down, since `just merge` would land a hijack of one; barring only paths every agent is barred from was turned down, since in the hub `guard_builder` may write the guard code and a reviewer could then put guard code on `main`; exempting `.scratch/` was turned down for the ticket reason above.

### Why not first-parent judging

Run 92434a0e judged each commit against its first parent only, and the `bypass-reviewer` subagent found it let through what `main` stops. An agent makes a merge whose first parent is `main~1`, the commit before a guard fix, and whose second parent is `main`, with `main~1`'s tree, and points `main` at it. Against its first parent it changes nothing; against `main` it reverts the fix. It descends from `main`, so it is a fast-forward that a plain push or `pull --ff-only` accepts. The same shape with an octopus merge, the run's own branch, or a new root holding only `src/` as first parent lands unreviewed guard code or strips the factory files. A forged stash whose untracked parent is an existing commit, not a root, restored a deleted protected file on `git stash pop` the same way. Judging every parent but a root keeps all of these stopped.

## Rules from the last two guard runs

- **All git runs in the run's own repo**, never with a `cwd` inside another worktree or checkout.
- **Reads can't run agent code or fetch.** Every object read keeps the env and flags `_REF_READ` has, plus the graft line.
- **Constants go in the build.** A fix round that adds any module-level name stops the run, even a constant.

## The bar

The baseline is `main`.

- Everything `main` stops still stops: the `commit-tree` plus `update-ref` hijack of `main`, a tag, another branch or an `sssf/*` branch with a new commit changing a protected path outside `specs/`, every shape in "Why not first-parent judging", a path under `specs/` with a `..` or `.` part, any `refs/replace/` change, for every agent including `writes: []` reviewers. The breach still names the ref, both shas, the commits and the paths.
- The hijack of `main` behind a symlinked `.git/info` with a live graft: the breach names `refs/heads/main`.
- No stop for: another run's plan commit under `specs/`; an engineer merge, true or fast-forward, of a finished run whose protected changes are only under `specs/`; `git stash` and `git stash -u` of unprotected files, by the engineer or an agent; two runs started at once, each committing its plan, as `just trial` does.
- Still a stop: a stash of a protected file outside `specs/`, tracked or untracked; a commit under `.scratch/` on another ref.
- No engineer commit, branch or file is deleted or overwritten, and no ref is moved.
- No change to the run's own branch: `enforce` and `undo_commits` behave as on `main`.
- No change to any ADW script, and no new module-level mutable state.

## Costs the spec states

- An agent can put a spec on another ref, `main` included, without a stop. A spec on `main` runs nothing, but a later run's planner may read it.
- A ticket run's commits under `.scratch/` still stop other runs' builders and reviewers while they work.
- What skipping a root parent gives up, if anything: say whether any shape lands protected content through a root parent that the root's own tree and the other parents don't show.

## Testing

In `.claude/skills/sssf/tests/test_agent_commits.py`, through `permissions.head`, `undo_commits` and `enforce` in throwaway git repos, as its ref tests do:

- another worktree's plan commit under `specs/` on its own branch during a builder's call and a reviewer's call: no stop. Fails on `main`, passes after;
- two runs in two worktrees, each committing a plan while the other's builder runs: no stop for either;
- the engineer's `git stash -u` with an unprotected change and with nothing untracked: no stop. Fails on `main`;
- a stash of a protected file outside `specs/`, tracked and untracked: stopped;
- the true merge into `main` of a branch whose only protected changes are under `specs/`: no stop; and of a branch carrying a protected change outside them: stopped;
- each shape in "Why not first-parent judging": the swapped-parent merge reverting a guard fix, the octopus form, the run's own branch as first parent, a new root with only `src/` as first parent, and the forged stash: stopped, on `main` and after;
- a tree entry `specs/../<guard file>` and `specs/./<guard file>` on `main`: stopped; a `.scratch/` commit on another ref: stopped;
- a breach from a read that fails carries no grafts hint;
- the hijack behind a symlinked `.git/info` with a live graft: the breach names `refs/heads/main`. Fails on `main`;
- a commit whose tree is missing, with a promisor remote whose `uploadpack` is a script that writes a marker: the marker never appears, and the test fails when the no-fetch env is removed from `_REF_READ` or `_COMMIT_PATHS`;
- every existing ref test still passes.

The tests never start a real agent.

## Where to edit

Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`, never under the launch checkout above it. agentsh refuses writes outside the worktree, so an `EACCES` on a path outside it means a wrong path, not a missing permission.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. That holds for the builder, the bypass reviewer and any harness an agent writes. `tests/test_export.py` and `tests/test_install_hook.py` push to bare repos, so the builder leaves them out of its own runs: `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py`. The chain's verify phase runs as code, outside agentsh, and runs both.

Under agentsh, run only the tests this change touches: `pytest tests/test_agent_commits.py -q`, and any other file whose tests you changed. Leave the whole suite to the chain's verify phase. This goes for the builder, its fix rounds and the bypass reviewer. After each agent call SSSF reads every agentsh event of the call into memory, and run 92434a0e's first build ran the whole suite under ptrace several times: the read grew to 8.6 GB, timed out, and failed the build with its code done.

To give a bare repo a commit, as an upstream for a fetch case, fetch into it: `git -C <bare> fetch -q <work> main:main`.

## Out of scope

- The ref-move holes open.md files: re-parenting, fast-forward or rewind to an existing commit, symbolic refs, the two-call form, pseudo-refs outside `refs/`, another worktree's `HEAD`, and an agent's `writes` applying on every ref.
- The hub's own `adws/adw_modules/`, which doesn't have the ref check yet.
- The engineer's own commits to other protected paths during an agent call, such as `CLAUDE.md` or `adws/`, which still stop the run.
- `uv.toml` and the other unprotected-path rows.

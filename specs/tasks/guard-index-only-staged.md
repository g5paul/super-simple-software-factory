# See an agent's change staged only in the index

The `bypass-reviewer` subagent found this on 2026-11-17, reviewing guard run 5c0b61a4, and rated it High. It's an old hole, on `main` before and after that merge. Run it with `--worktree` and `--base main`, on the hub's own config. `git_helper.py` isn't on CLAUDE.md's guard list, but its commit is where the hole lands, so the change goes through this chain.

## The hole

After an agent call, `permissions.snapshot` (`templates/adws/adw_modules/permissions.py`) lists what changed with `git diff HEAD --raw`, which compares `HEAD` with the work tree, plus untracked files and index flags (`ls-files -v`). It never compares the index with `HEAD`. So an agent can stage content the work tree doesn't show:
- write its version of a protected file, `git add` it, then write the original bytes back;
- `git update-index --cacheinfo 100644,<blob>,<path>` with a blob it made with `git hash-object -w`;
- `git update-index --chmod=+x <path>`, or `git rm --cached <path>`.

The work tree matches `HEAD`, so the check sees nothing and `enforce` rolls nothing back. If the agent commits, `undo_commits`' mixed reset puts the index back. If it doesn't, the staged content stays, and:
- **The plan commit takes it.** `git_helper.commit_all(message, only)` runs `git add -A -- <only>` and then a plain `git commit`, which commits the whole index. `adw_simple_sdlc.py` calls it with the planner's `writes`, so the planner's staged content for a protected file lands in SSSF's own plan commit.
- **A later full commit hides it in history.** The other chains call `commit_all(message)`, whose `add -A` restages the work tree's bytes, so the agent's blob is in the branch's history but not its final tree.
- **The engineer's own `git commit` takes it.**

## The change

1. **The check sees the index.** The snapshot also compares the index with `HEAD`, for example with `git diff --cached HEAD --raw`, and records each staged path's index mode and blob id in its content, so a change to what's staged reads as a change like any other. A path whose staged entry changed during the call is attributed to the agent and handled by `enforce` as a work-tree change is: rolled back when it's outside the agent's `writes` or protected, with the index entry put back to `HEAD`'s, or left as-is and named when it was already changed or staged before the call.
   - **What counts.** The hole is content in the index that the work tree doesn't show. An agent staging its own allowed writes, or staging the work tree's current bytes of a file it may leave, isn't a finding. Say in the spec how the snapshot tells the two apart, and why that doesn't open a new route, for instance an agent that stages its bytes, then rewrites the work tree.
   - **The engineer's staged work.** A path the engineer staged before the call is in the before snapshot, so a call that doesn't touch it never names it and never resets it.
   - **Index flags.** A staged blob behind `--skip-worktree` or `--assume-unchanged` is seen, and after the rollback the index entry is `HEAD`'s, with the flags the engineer had before.
   - **No new reads.** The new command reads no git object `main`'s snapshot doesn't, or the spec says why it must. `test_a_deleted_tree_object_of_an_untouched_folder_still_rolls_back` and its failed-call sibling still pass. It runs with the snapshot's existing settings: filters, hooks and programs off, fail closed on a git failure.
2. **The plan commit commits only its paths.** `commit_all` with `only` commits only those paths, whatever else is staged: for example `git add -A -- <only>`, then `git commit --only -- <only>` or an equivalent. A commit with nothing in those paths still raises "nothing to commit". `commit_all` with no `only` keeps its `add -A`, which already restages the work tree.
3. **Docs.** `config.md`'s "Write permissions" says the check compares the index with `HEAD` as well as the work tree. The known limit for a staged change inside a submodule stays, since that index is the submodule's own. `docs/open.md`'s P1 row on this hole is removed.

## The bar

- **Each route is closed,** on the success and the failure path, with no commit by the agent: the three staging routes above, plus `git rm --cached` and `--chmod=+x`, on a protected path and on a path outside the agent's `writes`. The run stops naming the path, and afterwards the index entry and the work tree match `HEAD`.
- **The plan commit.** A test runs `commit_all(message, ["specs/"])` with a protected file staged and a spec written: the commit holds only `specs/`, and the protected file's staged content isn't in it.
- **No false stop.** A builder that stages its own writes with `git add`, or runs `git add -A` over the engineer's dirty files without changing their bytes, passes as on `main`. A call that commits and so has its commit undone behaves as on `main`.
- **The engineer's staged work is untouched** by a call that doesn't touch it, on both paths.
- **Flags.** A staged blob behind `--skip-worktree` and behind `--assume-unchanged` is seen and rolled back, and the existing flag tests in `test_factory_holes.py` pass.
- **Nothing else changes.** With nothing staged during the call, the tree, commits, trace events and exception are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. Mark any test that starts a command past agentsh's argument limits, 128 KiB per argument or 2 MiB in all, `host_only`, and never run those in an agent's run.

## Out of scope

- A staged change inside a submodule, the decided limit in `config.md`.
- The index reset when an agent's commit is undone, which loses the engineer's partly staged version; `config.md` names it.
- The other P2 rows on `permissions.py`, and the gitignore row.

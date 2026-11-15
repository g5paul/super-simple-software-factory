# Roll back a failed agent call's writes

The `bypass-reviewer` subagent confirmed this on 2026-11-15 and rated it High. It's an old hole, on `main` since the rollback was written. Run it with `--worktree` and `--base main`, on the hub's own config. `agents.py` isn't on CLAUDE.md's guard list, but the code at fault is the one place `permissions.py`'s rollback is called, so the change goes through this chain.

## The hole

In `agents.execute` (`templates/adws/adw_modules/agents.py`), the `try` at line 331 takes `tree_before` and `head_before`. After the agent's last send, the success path:
- restores the git directory;
- undoes the agent's commits, with `permissions.undo_commits`;
- rolls back every write outside the agent's `writes` and every write to a protected path, with `permissions.enforce` at line 406.

Any exception before line 406 goes to `except BaseException` at line 442. That restores only the git directory and re-raises. So when a call fails, the agent's commits stay, and so do its writes to protected paths and outside its `writes` stay in the tree.

The agent can make a call fail whenever it likes:
- answer with no valid JSON three times (`RuntimeError`);
- fail a gate after its retries (`GateFailure`);
- run a forbidden command, or pile up refusals (`RuleBreach`);
- go quiet past `max_silence_minutes` (`SilentStop`);
- spend into a cost cap;
- crash its own harness;
- write a `repo.yaml` that no longer loads, then force a retry, so that `budget.check_live` raises.

The subagent planted `adws/adw_modules/permissions.py` and `justfile` and failed the call by three of these routes. The files survived on base and head alike. agentsh doesn't stop it: the policy's workspace allows writes to `adws/`, `.claude/` and the justfile.

What the leftovers can do:
- **Local run:** the next `uv run adws/adw_*.py` imports the planted module in the chain's own process, which nothing sandboxes. That's code running as the engineer, outside agentsh. A planted recipe runs on the next `just`, and a `.claude/settings.json` hook in the next Claude Code session.
- **Resume:** the next call's `tree_before` counts the planted files as the engineer's own dirty work, which `enforce` leaves alone. Then the chain's `git add -A` commits them, and `just merge` lands them. The subagent reproduced this.
- **Worktree run:** they stay in the worktree. `just merge` refuses, and its message at `worktree.py:415` suggests `git -C <path> add -A && git -C <path> commit`, which would commit them.
- **No word:** no stop message says protected paths were left changed.

`config.md` ("Write permissions") promises the opposite: after the agent's last send, SSSF rolls back every unauthorized change and fails the phase.

## The change

1. **The failure path rolls back what the success path does.** Whenever `tree_before` was taken, an exception reaching `except BaseException` does three things, in this order:
   1. restores the git directory, as today;
   2. undoes the agent's commits back to `head_before`;
   3. rolls back every write `enforce` would have rolled back: protected paths and writes outside the agent's `writes`.
   
   It does this for every exception class, `KeyboardInterrupt` and `SystemExit` included. Then it re-raises the original exception.
2. **The original error survives.** A rollback that finds unauthorized writes doesn't replace the original exception with a `PermissionBreach`. The trace gets an `error` event naming each path put back and each commit undone, and the console note says so in one line.
   If the rollback itself fails, the engineer has to hear it. Chain the rollback's error to the original, and say in the console that the tree may still hold the agent's writes. A failed rollback never passes silently.
3. **Writes the agent was allowed stay.** A failed builder's own edits inside its `writes` stay in the tree, as today, so the engineer can see what it did. Only what `enforce` would reject is rolled back.
4. **No baseline, no guessing.** If the exception comes before `tree_before` is taken, nothing is rolled back, as today.
5. **`just rebase` loads the settings before it rebases.** In `worktree._rebase`, `repo_settings.load(root)`, at about line 511, runs after `git rebase` has moved the branch, at line 498. So a `repo.yaml` that no longer loads leaves the branch rebased with no recheck. Load the settings first, so a failed load changes nothing.
6. **The merge message.** The message at `worktree.py:415` keeps its advice to commit by hand, but says first to check `git -C <path> status` for anything the run didn't make.
7. **Docs.** `config.md`'s "Write permissions" says the rollback runs when an agent call fails too, and names the case it can't cover: a chain process killed outright, which runs no Python. `GLOSSARY.md` stays as it is.

## The bar

- **Every route is closed.** For each route above, and for `KeyboardInterrupt`, a test plants a write to a protected path, a write outside `writes`, and a commit, then makes the call fail that way. After `execute` raises:
  - the protected file and the outside write are back as they were;
  - the commit is undone;
  - the raised exception is the original's type.
- **The resume case is closed.** A test runs a failing call that plants a protected file, then a second call that succeeds and commits through the chain's commit step. The planted file isn't in the commit, and it isn't in the tree.
- **The success path is unchanged.** Every existing test of `execute` and `permissions` passes untouched. A call that succeeds, or that ends in a `PermissionBreach` from `enforce`, gives the same tree, commits, trace events and exception as on `main`.
- **The engineer's own work is untouched.** A file the engineer changed before the call is the same after a failed call as before it.
- **A failed rollback is loud.** A test makes the rollback raise, and checks that the original exception still reaches the caller and that the console and trace say the tree may hold the agent's writes.
- **Rebase.** A test with a `repo.yaml` that doesn't load shows `just rebase` refusing with the branch where it was.

## Running the tests

Run the test files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. The build brief says so too.

## Out of scope

- A chain process killed by a signal Python can't catch. Note it in `config.md`, as change 7 says.
- What `enforce` counts as unauthorized, and `protected_files`.
- The agentsh policy's workspace paths.

# Roll back a failed agent call's writes, second run

This finishes `specs/tasks/guard-failed-call-rollback.md`. Read that brief for the hole and the bar, which carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

Guard run 29bf0a6a built the first brief and stopped on 2026-11-16 at round 2, because round 1's fix added a mechanism. Its spec is `specs/29bf0a6a_failed-call-rollback_v2.md` on branch `sssf/29bf0a6a`. Its report is `adws/adw_data/sessions/29bf0a6a/context_handoff/guard_report.md`.

## The engineer accepted the mechanism

The code is uncommitted in `adws/adw_data/worktrees/29bf0a6a`. In `agents.execute`'s failure path, `git_dir_failed` records that the git directory couldn't be put back. With it set, `_roll_back_failed` runs no git and reports that the tree may still hold the agent's writes. The engineer accepted that on 2026-11-16: when git's config can't be trusted, running no git and saying so is the only safe answer. The planner keeps it, and the reviewer doesn't flag it again as a new mechanism.

Start from that code. `git -C adws/adw_data/worktrees/29bf0a6a diff`, plus its untracked `tests/test_failed_call_rollback.py`, is the starting point. `main` has moved since its base only by records outside the code. Read the round reports before relying on any of it.

## What this run adds

1. **Close the Ctrl-C race.** This is 29bf0a6a's first harm. Suppose a `KeyboardInterrupt` lands inside the success path's git-directory check, after `restore_git_dir` marked its guard done, or just after it raised its breach. The failure path's restore then returns `[]` without raising, so `git_dir_failed` stays `None`, and the rollback runs git under a filter the agent planted. On `main` the failure path ran no git there.
   - Make the failure path treat a success-path check that had started but not settled like a failed restore: run no git, and report.
   - Narrow the existing condition, for example by recording that the check started next to `settled`. Don't add a third mode.
2. **The success path runs no git after a failed restore either.** This is 29bf0a6a's first old hole. When the success path's `restore_git_dir` can't put the git directory back, `undo_commits` and `enforce` still run git under the agent's config. That covers a planted filter in `.git/config` or a linked `~/.gitconfig`, plus `.gitattributes` and `chmod a-w .git`, and the agent's code then runs in the chain process.
   - Give that case the same answer as the failure path: no git, a `PermissionBreach` saying the git directory couldn't be put back and nothing was rolled back, and the console line saying the tree may hold the agent's writes.
   - A restore that found tampering and did put it back still goes on to the undo and rollback, as today.
3. **Name the shared limits.** `config.md`'s "Write permissions" names the limits that the success and failure paths share, which live in `permissions.py`:
   - a file the engineer had gitignored is deleted if the agent un-ignores it;
   - undoing an agent's commit resets the index, so a partly staged file loses its staged version;
   - a corrupted `.git/index` makes git report no change, so planted files stay.

## The bar

- 29bf0a6a's spec's bar holds, including each failure route's test and the resume case.
- **The race is closed.** A test interrupts the call at each point inside the success path's git-directory check. With a filter planted in the git config, the filter never runs, and the console says the tree may hold the agent's writes.
- **The success path is closed.** A test plants a filter and makes `restore_git_dir` fail on a call that reports success. The filter never runs, `execute` raises `PermissionBreach`, and its message says nothing was rolled back.
- **Nothing else changes on the success path.** When the git directory is clean, or was put back, the tree, commits, trace events and exception are as on `main`.

## Running the tests

Run the test files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- `permissions.py`'s rollback rules: the gitignored-file deletion, the index reset and the corrupted index. They're listed in change 3 and get their own row in `docs/open.md`.
- Commits the agent makes on another branch.
- A chain process killed by a signal Python can't catch.

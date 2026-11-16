# Roll back a failed agent call's writes, third run

This finishes `specs/tasks/guard-failed-call-rollback.md` and `specs/tasks/guard-failed-call-rollback-2.md`. Read both briefs; their bars carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

Guard run d339590e built the second brief and passed on 2026-11-16, as 101cc8b on branch `sssf/d339590e`. Its spec is `specs/d339590e_rollback-no-git-unrestored_v2.md` on that branch. The `bypass-reviewer` subagent then found a bypass, so the engineer didn't merge it. Start from 101cc8b's code: `git diff main...sssf/d339590e` is the starting point, and `main` has moved since only by records outside the code.

The subagent's probes are in `adws/adw_data/sessions/d339590e/bypass_probes/`. `common.py` is their fixture, and `PROBE_SKILL` names the unpacked skill to run them against. Reuse them as the starting point for this run's tests.

## What stays

The engineer accepted `git_dir_failed` and its report-only mode on 2026-11-16, and that holds. When git would read a config the agent may have changed, SSSF runs no git and says the tree may still hold the agent's writes. The reviewer doesn't flag that as a new mechanism. What this run changes is when it applies.

## The bypass

The agent writes protected files, such as `adws/adw_modules/permissions.py` and `justfile`, adds a git hook and runs `chmod 0555 .git/hooks`. Then it reports success.
- **On `main`**, `restore_git_dir` can't put `hooks/` back, but `undo_commits` and `enforce` still run. They're safe, because SSSF's git commands run with `core.hooksPath=/dev/null` (`permissions.py`, `_NO_HOOKS`), so no planted hook fires. The writes are rolled back and the run stops.
- **On 101cc8b**, `_git_dir_back` is false, so the success path (`agents.py` about line 402) runs no git. The run stops with the planted files and the agent's commit in place. On a resume the next call counts them as the engineer's dirty work, and `git add -A` commits them. The subagent reproduced this (`test_q2.py`, `test_q2b.py`).

## The change

1. **Run no git only when git would read what the agent left.** Split the watched paths in two.
   - **Config paths:** `.git/config`, a global or system config reached through a link, and any other watched file git reads as config, which the planner names from `guard_git_dir`. If one of these can't be put back, keep 101cc8b's answer: no git, a `PermissionBreach` on the success path, and the report on the failure path.
   - **Everything else:** `hooks/`, `info/` and any other watched path git doesn't read as config. If one of these can't be put back, roll back as `main` did, with every git command under `_NO_HOOKS`, then stop the run with a `PermissionBreach` naming the paths left changed. On the failure path, the original exception still wins, as 101cc8b has it.
   
   The planner shows for each watched path which group it's in and why. Narrow `guard["restored"]` or `_git_dir_back` to make the split. Don't add a third mode. If git reads anything in the second group as config, say so in the spec and move it to the first.
2. **`_roll_back_failed` never raises.** Its docstring says so, but its reporting branch is unguarded: the `failed_call_rollback` event, `permissions.protected`, `console.note` and `console.advise`. If one of those raises, the original exception is replaced (`test_q3.py`).
   - Guard the branch so the caller always gets the original exception, with a reporting failure chained to it.
   - A `KeyboardInterrupt` or `SystemExit` during reporting still lets the original through.
3. **A broken trace doesn't skip the rollback.** An agent may write under `sessions/`, so it can turn `events.jsonl` into a directory, and then every trace event raises (`test_q3b.py`, `test_q3c.py`).
   - On the success path, a raising `agent_committed` event turns into "Could not undo…" and `enforce` is skipped, with `settled = True`.
   - On the failure path, the raising `git_dir_restored` event escapes before `_roll_back_failed`.
   
   On both paths a trace write that fails is reported, on the console if the trace can't take it, and the undo and rollback still run. A trace event that fails after a restore that succeeded doesn't count as a failed restore.

## The bar

- Both earlier briefs' bars hold, including every failure route, the resume case, the Ctrl-C points and the planted filter.
- **The bypass is closed.** With `hooks/` or `info/` locked and protected writes and a commit made, on the success and the failure path: the writes are rolled back, the commit is undone, no planted hook runs, and the run stops naming the paths left changed. A resume then commits none of the planted files.
- **A config the agent planted still runs nothing.** With a filter in `.git/config` or a linked `~/.gitconfig` that can't be put back, the filter never runs, on either path.
- **The original error survives reporting.** A test makes each reporting call in `_roll_back_failed` raise, including a `KeyboardInterrupt`, and the caller gets the original exception's type.
- **A broken trace doesn't skip the rollback.** With `events.jsonl` turned into a directory, on both paths, the protected writes are rolled back and the commit is undone.
- **Nothing else changes.** When the git directory is clean, or was put back, the tree, commits, trace events and exception are as on `main`.

## Running the tests

Run the test files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- `permissions.py`'s rollback rules: the gitignored-file deletion, the index reset and the corrupted index, which are in `docs/open.md`'s P2 row. Also undoing the engineer's own edits made during the call, which the success path already does. `config.md` should name that next to the other limits.
- A check that refuses to resume after a "may still hold" stop. That would be a new mechanism, and the engineer hasn't asked for one.
- A planted hook running on the resumed commit, after the next call takes the locked hooks as its baseline. It's an old hole on both versions; note it in the spec's old holes.
- Commits the agent makes on another branch, and a chain process killed outright.

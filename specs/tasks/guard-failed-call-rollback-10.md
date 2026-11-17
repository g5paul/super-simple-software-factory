# Roll back a failed agent call's writes, tenth run

This finishes `specs/tasks/guard-failed-call-rollback-9.md`. Read it and the briefs it names. Their bars and the engineer's decisions carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 3d1ef579 got to

Guard run 3d1ef579 built `-9` and passed the chain on 2026-11-17 after one review round, as 6fa3b47 on branch `sssf/3d1ef579`. Its spec is `specs/3d1ef579_drop-same-bytes-skip_v2.md` on that branch, and its report is `adws/adw_data/sessions/3d1ef579/context_handoff/guard_report.md`. Start from that code: `git diff main...sssf/3d1ef579` is the starting point. `main` has moved since only by this brief.

The `bypass-reviewer` subagent then found a bypass, and the engineer's session reproduced it, so the engineer didn't merge. Its probes are in `adws/adw_data/sessions/3d1ef579/bypass_probes/`: `probes/harness.py` runs one scenario from `probes/scenarios.py` through the real `agents.execute` of an exported version, and `probes/run.sh <scenario>...` runs base and head. Reuse them as this run's test cases.

It found what holds, too. A failed call is put back, the commit undone and the protected writes rolled back. A second interrupt during the success path's rollback still finishes it. A failed call that wrote nothing leaves the engineer's untracked and dirty files alone. No planted program ran. Keep all of that.

## The findings to fix

1. **B1, Medium: one deleted git object stops the whole rollback.** Scenario `missing_tree_object`. The engineer's `HEAD` has an untouched folder whose tree object is loose. The agent writes `adws/adw_modules/permissions.py`, then deletes that tree object from `.git/objects`.
   - `main` rolls the write back.
   - The head stops with `GitFailed` on `git ls-tree -r -z HEAD` and rolls nothing back, so the guard's own code keeps the agent's text.
   - The cause is the gitlink listing in `snapshot`: `ls-tree -r HEAD` reads every tree in `HEAD`, and `main`'s `diff HEAD` doesn't need them all. `.git/objects` isn't a watched path.
2. **F2, Low: no `python3` and a new gitlink.** Scenario `new_gitlink_no_python`. In a repo with no submodule before the call, the agent writes a protected file, makes a nested repo with a commit and stages it with `git add`. Where `python3` can't start, the digest fails and the write stays; `main` rolls it back.
3. **F1, Low to Medium: a false "rolled back" note on a failed call.** Scenarios `fail_undeletable`, `fail_revert_engineer` and `fail_ref_only`. `_roll_back_failed` prints "SSSF rolled back its unauthorized writes" whenever it found anything, while the advice under it says a file couldn't be deleted, the engineer's work was reverted, or a commit stays on another branch. The advice is right; the note isn't. It breaks `config.md`'s promise that the console says when the tree may still hold the agent's writes.

## The change

1. **The snapshot reads no git object `main` doesn't.** List gitlinks from what the snapshot already reads: the index, with `ls-files -s` (mode `160000`), and the diff against `HEAD`, read with `--raw` so the old mode shows a gitlink in `HEAD` that left the index. Drop `ls-tree -r HEAD`. The spec says, for each git command the snapshot, undo and rollback run, that it reads no object `main`'s same step doesn't, or why it must.
2. **Digest only a gitlink that was there before the call.** A gitlink new in the after snapshot is already an added path, and it rolls back as on `main`, so it needs no digest and no `python3`. A gitlink that was there before still needs both, as `-8` decided.
3. **The failed-call note matches the outcome.** It says SSSF rolled back the unauthorized writes only when every path named was rolled back and every commit undone. Otherwise it says which it couldn't put back and that the tree may still hold the agent's writes, in the same words as the success path's stop. Narrow the existing note; don't add a mode.

## The engineer's decision

On 2026-11-17 the engineer chose to document the staged change inside a submodule as a known limit, not fix it. The agent writes a file in a submodule, stages it with `git -C <path> add`, and puts the file back; `git -C <path> rm --cached` and a staged `chmod +x` do the same. `main` stops the call, though it can't roll it back either. The head passes it, and the engineer's next commit in the submodule takes the agent's content. Seeing it would mean running git in the submodule, which `-5` ruled out, or parsing git's index in Python, which is a new mechanism.

- It's a decided difference from `main`, and a reviewer files a matching finding as meeting the bar.
- `config.md`'s known limits name it, next to the submodule `.git` file entry, and say how to check: first that `<path>/.git` points at `.git/modules/<name>`, then `git -C <path> diff --cached`.

## The bar

- `-9`'s bar holds, with its decided exceptions.
- **B1 is closed.** `missing_tree_object` rolls the write back as on `main`, on the success path. A test covers it. On the failure path, `fail_missing_tree_object` keeps the write on both versions; the head says so, as it does now.
- **F2 is closed.** `new_gitlink_no_python` rolls the write back as on `main`, and no `python3` starts.
- **F1 is closed.** In each of the three scenarios, the failed-call note doesn't say "rolled back" and does say the tree may still hold the agent's writes. A failed call where everything was rolled back still says so.
- **The docs.** A test checks `config.md` names the staged change in a submodule and `git -C <path> diff --cached`.
- **Nothing else changes.** With no submodule, no moved config, no filter and git objects intact, the tree, commits, trace events and exception are as on 6fa3b47.

## Running the tests

As in `-5`, including its section on tests that agentsh refuses: run the touched files with `-m 'not pushes and not host_only'`, mark any test that starts a command past agentsh's argument limits `host_only`, and never run those in an agent's run.

## Out of scope

- `-9`'s out-of-scope list.
- The old holes the reviewer listed on both versions: an agent that moves `HEAD`, which has its own P2 row, and the `tests_pass(cmd)` gate running before the permission check.
- Shadowing `git` or `python3` in a venv first on `PATH`, which the chain's report named. Note it in `config.md`'s known limits in one line.

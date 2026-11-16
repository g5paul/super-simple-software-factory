# Roll back a failed agent call's writes, sixth run

This finishes `specs/tasks/guard-failed-call-rollback-5.md`. Read it and the briefs it names. Their bars and the engineer's decisions carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 6ef2f3f0 got to

Guard run 6ef2f3f0 built `-5` from run 0ece22a5's code and stopped on 2026-11-16 at round 3, because round 2's fix added a mechanism. Its spec is `specs/6ef2f3f0_git-fail-closed.md` on branch `sssf/6ef2f3f0`. Its code is uncommitted in `adws/adw_data/worktrees/6ef2f3f0`, and its report is `adws/adw_data/sessions/6ef2f3f0/context_handoff/guard_report.md`. The round reviews are `bypass_review_1.md` to `_3.md` next to it, with their probes in the folders of the same names.

The build itself held. Round 1 found no bypass: 0c741413's long-name case is closed at both sizes, a corrupted index stops the run, and no submodule marker runs. Start from that code: `git -C adws/adw_data/worktrees/6ef2f3f0 diff`, plus its untracked `tests/test_failed_call_rollback.py`. Read the three round reviews before relying on it.

Each round's harm was in a repo with no commit:
1. The build's `undo_commits` emptied the index with `rm -rq --cached --force .`, and `enforce` then deleted the engineer's staged files, which the snapshot never recorded.
2. Round 1's fix recorded each staged path as kind `?`, so an agent's `git add -f` of the engineer's gitignored file got that file deleted.
3. Round 2's fix gave `_roll_back`'s `?` route a no-commit mode with two git queries per path. That's the mechanism, and its pathspec match on a folder keeps an agent file that replaces a staged folder.

## The engineer picked: no agent runs in a repo with no commit

On 2026-11-16 the engineer chose to refuse the case instead of guarding it. A repo with no commit has no `HEAD` to snapshot against, and `-5` already says a repo SSSF can't snapshot stops the call before the agent runs.

1. **Refuse before the agent.** In the baseline, before the agent's first send, a repo where `rev-parse -q --verify HEAD` finds no commit stops the call with an error that says SSSF needs a first commit to guard an agent's writes, and how to make one (`git commit --allow-empty -m init` or a real first commit). Nothing runs and nothing is rolled back.
2. **Drop the no-commit code.** With no agent running there, `snapshot`, `undo_commits`, `enforce` and the restore need no branch for a repo with no commit. Remove the ones `main` and the build have, including round 1's snapshot branch, round 2's `_roll_back` mode and the build's `rm -rq --cached --force .`, unless the spec shows one is still reached. An agent that deletes `HEAD`'s ref or the whole history during the call is still reached; say in the spec what happens then, and it must stop the run, never pass.
3. **Find the callers.** The planner lists where SSSF calls the guarded `execute` and says what each does in a repo with no commit today. That covers `adw_*.py`, the install, and anything that runs an agent before the first commit. If one would now fail where it worked, name it in the spec as a decided difference, with the message the engineer sees.
4. **Docs.** `config.md`'s "Write permissions" says SSSF runs no agent in a repo with no commit, and why, in one or two lines.

## The bar

- `-5`'s bar holds, with its decided exceptions, except where this brief removes the no-commit case.
- **The refusal.** A test in a repo with no commit shows the call stops before the agent runs, with the message naming the first commit. No agent process starts and the tree is unchanged.
- **The rounds' cases are moot.** Each of the three rounds' no-commit probes either refuses before the agent or is shown unreachable. Round 3's folder-to-file case and round 2's `git add -f` case each have a test.
- **History removed during the call.** A test where the agent deletes the branch ref, or makes `HEAD` unborn with `git checkout --orphan`, stops the run on both paths.
- **Nothing else changes.** In a repo with a commit, the tree, commits, trace events and exception are as on the build's head, 6ef2f3f0's round 1 code.

## Running the tests

As in `-5`, including its section on tests that agentsh refuses: run the touched files with `-m 'not pushes and not host_only'`, mark any test that starts a command past agentsh's argument limits `host_only`, and never run those in an agent's run.

## Out of scope

- `-5`'s out-of-scope list.
- The `.gitignore` sort-order old hole round 3 found, now item (4) in `docs/open.md`'s P2 Medium row on `permissions.py`'s rollback.

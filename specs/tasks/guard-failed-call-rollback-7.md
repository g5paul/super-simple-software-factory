# Roll back a failed agent call's writes, seventh run

This finishes `specs/tasks/guard-failed-call-rollback-6.md`. Read it and the briefs it names. Their bars and the engineer's decisions carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run eafe12cf got to

Guard run eafe12cf built `-6` and stopped on 2026-11-16 after three rounds with no bypass and no harm. Its spec is `specs/eafe12cf_refuse-no-commit-repo.md`, uncommitted in `adws/adw_data/worktrees/eafe12cf` along with its code, and its report is `adws/adw_data/sessions/eafe12cf/context_handoff/guard_report.md`. Round 3's head was 77d5631. Start from that code and spec: `git -C adws/adw_data/worktrees/eafe12cf diff`, plus its untracked files, is the starting point. Read the round reviews next to the report before relying on it.

Two findings were left, and both are about the spec, not the code:
1. **False positive.** The refusal checks whether `HEAD` has a commit, so it also refuses a repo with commits whose `HEAD` is on an unborn branch: `git checkout --orphan`, `HEAD` naming a missing branch, and `git worktree add --orphan`. The spec decides the refusal only for a repo with no commit.
2. **Broken promise.** The spec's "The message the engineer sees" still quotes round 1's text, not the message `_no_commit` prints.

## The engineer accepted the unborn-branch refusal

On 2026-11-16 the engineer accepted refusing an agent call whenever `HEAD` has no commit, an unborn or orphan branch in a repo with history included. It's the same missing `HEAD` `-6` refused, and allowing it would bring back the harms of 6ef2f3f0's rounds 1 to 3.

1. **The spec.** Widen its decided difference to name the three cases above, and quote the message `_no_commit` prints now. A reviewer files a finding that matches it as meeting the bar.
2. **The code.** No change is asked for. Keep eafe12cf's code as it is, unless a finding in this run needs a fix.
3. **The tests.** Each of the three unborn-branch cases has a test showing the call stops before the agent runs, with that message and the tree unchanged. Add any the build lacks.

## The bar

- `-6`'s bar holds, with the decided difference widened as above.
- **Nothing else changes.** In a repo whose `HEAD` has a commit, the tree, commits, trace events and exception are as on eafe12cf's round 3 head.

## Running the tests

As in `-5`, including its section on tests that agentsh refuses: run the touched files with `-m 'not pushes and not host_only'`, mark any test that starts a command past agentsh's argument limits `host_only`, and never run those in an agent's run.

## Out of scope

- `-6`'s out-of-scope list.
- An agent that moves `HEAD` during a call keeps its writes, as on `main`. That's its own P2 row in `docs/open.md`.

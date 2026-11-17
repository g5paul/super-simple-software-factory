# Roll back a failed agent call's writes, ninth run

This finishes `specs/tasks/guard-failed-call-rollback-8.md`. Read it and the briefs it names. Their bars and the engineer's decisions carry over, except where this brief changes them. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 69c6a2f7 got to

Guard run 69c6a2f7 built `-8` and stopped on 2026-11-17 at round 3, because round 2's fix added a mechanism. Its spec is `specs/69c6a2f7_submodule-digest-config-bytes_v2.md` on branch `sssf/69c6a2f7`. Its code is uncommitted in `adws/adw_data/worktrees/69c6a2f7`, and its report is `adws/adw_data/sessions/69c6a2f7/context_handoff/guard_report.md`, with the round reviews and their probes next to it. Start from that code: `git -C adws/adw_data/worktrees/69c6a2f7 diff`, plus its untracked files.

`-8`'s changes 1 and 2 held in round 1, and stay:
- **The submodule digest (change 1).** An edit, a same-size rewrite, `chmod +x`, a commit inside the submodule, and a submodule folder replaced or moved and linked back all stop the run, with the truthful `NOT rolled back` message. A deep folder chain no longer crashes it (round 1's fix).
- **Config bytes (change 2).** The moved-folder cases roll back as on `main`, and a config whose bytes changed still runs no git.

Everything that blocked came from change 3, the fix for the false loss report:
- **Round 1:** skipping a path that left the diff with the same bytes trusts the index, and the agent can forge it. With the engineer's uncommitted edit, the agent rewrites the index entry's stat to match the file. `main` stops with `REVERTED-BY-AGENT`; the head passed. On the next call, any stat change made SSSF's own rollback overwrite the engineer's work with `HEAD`.
- **Round 2:** limiting the skip to filtered paths only narrowed it, and needed `check-attr --source`, which git before 2.40 lacks.
- **Round 3:** a temporary index built with `mktemp` and `rm -rf` was the mechanism that stopped the run. The reviewer's conclusion: SSSF can't tell a forged index from a real `git status` refresh without running the filter, and running no filter is the point of this work.

## The engineer's decisions

On 2026-11-17 the engineer chose:

1. **Drop `-8`'s change 3 and accept the false positive.** Take out the same-bytes skip in `enforce`, `_filtered`, the temporary index and anything else that only change 3 needed. A path in `before` that has left the diff is `REVERTED-BY-AGENT` again, as on d6aa79a and `main`. That brings back the false positive: in a repo with a byte-rewriting filter and a racy checkout, an agent that only runs `git status` stops the run with that message, though nothing was lost. It's a decided exception to the bar, and a reviewer files a matching finding as meeting it. When the engineer chose, no repo on this machine had such a filter.
   - `config.md` says so in a line, next to the other filtered-repo exceptions: the stop can name a file as `REVERTED-BY-AGENT` when the agent only refreshed the index, and how to check with `git diff` and `git status`.
2. **The stop message sends no one into the agent's git directory.** The submodule stop tells the engineer to run `git -C <path> status`. The agent can rewrite the submodule's `.git` file to `gitdir: <a folder it controls>`, and that command then runs the agent's git config. The message says first to check that `<path>/.git` points where it should (`cat <path>/.git`, against `.git/modules/<name>`), and only then to look with git. It runs no git itself.
3. **Name two old holes in `config.md`'s known limits.** Both pass on `main` and the head:
   - an agent that rewrites a submodule's `.git` file to point at another git directory;
   - a file under a nested `.git` inside a submodule, such as `vendor/lib/x/.git/hooks/post-checkout`.

## The bar

- `-8`'s bar holds, except its false-positive line, which decision 1 replaces.
- **Change 3 is gone.** Round 1's forged-index scenario and its two-call harm, under no filter, a defined filter and an undefined one, stop at the first call with `REVERTED-BY-AGENT` and the engineer's file intact, as on `main`. A test pins the filtered `git status` case as the decided exception.
- **No new mechanism.** The diff from d6aa79a adds nothing to `enforce`'s rules but the submodule digest and the config-bytes check that `-8` decided. No temporary index, `mktemp` or `rm` is left.
- **The message.** A test checks the submodule stop names `<path>/.git` before any git command, and SSSF runs no git in the submodule.
- **The docs.** A test checks `config.md` names the filtered `REVERTED-BY-AGENT` case and both old holes.
- **Nothing else changes.** With no submodule, no moved config and no filter, the tree, commits, trace events and exception are as on d6aa79a.

## Running the tests

As in `-5`, including its section on tests that agentsh refuses: run the touched files with `-m 'not pushes and not host_only'`, mark any test that starts a command past agentsh's argument limits `host_only`, and never run those in an agent's run.

## Out of scope

- `-8`'s out-of-scope list.

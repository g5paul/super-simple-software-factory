# Roll back a failed agent call's writes, eighth run

This finishes `specs/tasks/guard-failed-call-rollback-7.md`. Read it and the briefs it names. Their bars and the engineer's decisions carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 424a6411 got to

Guard run 424a6411 built `-7` and passed the chain on 2026-11-16, as d6aa79a on branch `sssf/424a6411`. Its spec is `specs/424a6411_refuse-unborn-head.md` on that branch, and its report is `adws/adw_data/sessions/424a6411/context_handoff/guard_report.md`. Start from that code: `git diff main...sssf/424a6411` is the starting point. `main` has moved since only by a51e85f, which makes a sandbox command with a timeout run in its own process group, and by records outside the code.

The `bypass-reviewer` subagent then found a bypass, and the engineer's session reproduced it, so the engineer didn't merge. Its probes are in `adws/adw_data/sessions/424a6411/bypass_probes/`: `probes/harness.py` runs one scenario from `probes/scenarios.py` on an exported version (`probes/run.sh <scenario>...` runs base and head). Reuse them as this run's test cases.

It found what holds, too. The failure path rolls back twelve kinds of agent change that `main` left. A corrupted index stops the run. No filter, hook or fsmonitor runs, with ten odd filter names. The no-commit refusal works. Keep all of that.

## The findings to fix

1. **B1, High: an edit inside a submodule's work tree passes.** Scenario `submodule_edit_reviewer`, and its siblings with a staged edit inside the submodule. The agent writes `vendor/lib/file.txt` in a submodule.
   - `main`'s `diff HEAD` shows `M vendor/lib` and the run stops.
   - The head's `diff HEAD --ignore-submodules=dirty` (`permissions.py`, `snapshot`) doesn't see it, and the call passes.
   - Where a submodule sits under a protected path, such as a skill vendored at `.claude/skills/ext`, a builder can rewrite a `SKILL.md` that later agents load.
   - `main`'s stop says `vendor/lib — rolled back`, which is false: the rollback doesn't go into the submodule, and the file keeps the agent's text there too.
2. **B2, Medium: a moved config folder with the same bytes stops all rollback.** The agent commits protected writes, then runs `mv ~/.config ~/.config2 && ln -s .config2 ~/.config`. The same works with a `~/.gitconfig` include's folder, and in a linked worktree with `<main>/.git/worktrees`.
   - `main` names the path as not put back, then undoes the commit, rolls the writes back and stops.
   - The head treats it as a config that couldn't be put back (`permissions.py`, `guard["restored"] = restored or _differs(...) == []`; `agents.py`, `if not _git_dir_back(git_before)`), runs no git, and keeps the commit and every write.
   - The bytes git would read are unchanged in all three.
3. **New false positive: `git status` in a filtered repo.** With a byte-rewriting filter and a racy fresh checkout, an agent that only runs `git status` refreshes the index, the file leaves the diff, and the head stops with `doc.txt — REVERTED-BY-AGENT (uncommitted work lost, cannot restore)`. `main` passes. Nothing was written or lost.

B3, the stat-dirty filtered file's kept write, is the exception the engineer accepted in `-4`. It meets the bar as decided.

## The change

1. **See a submodule's work tree without git.** Keep SSSF's git out of the submodule: no child process starts there, and `--ignore-submodules=dirty` can stay. Add what the snapshot can't see by reading the submodule's work-tree files directly, in Python, skipping its `.git`: for each gitlink in `HEAD` or the index, record a digest of the files under it, and in the snapshot after the agent, a changed digest reads as that gitlink modified.
   - It's part of the existing snapshot, a wider read of the same thing, not a new mode. Say in the spec why it narrows nothing else.
   - The digest reads no config, follows no symlink out of the submodule, and runs no program. A file it can't read counts as changed, as `-5`'s fail-closed rule says.
   - The stop for such a path says SSSF can't roll back inside a submodule and the files under it may hold the agent's writes, and how to check: `git -C <path> status` and `git -C <path> diff`. It never says `rolled back` for content it didn't restore.
   - The planner weighs the digest's cost on a large submodule and says what it chose: a full hash, or size and mtime with a hash only on a mismatch.
2. **Judge "put back" by what git reads.** A watched config path counts as put back when the bytes git reads through it are the same as before the call, whatever the path's shape. A folder moved and linked back with the same bytes then rolls back as on `main`. Narrow `_differs` or `guard["restored"]` to make this; don't add a mode. A path whose bytes did change, or that git now reads from somewhere the agent can write later in the same chain, still runs no git, as the engineer decided in `-2` and `-3`.
3. **No false loss report.** A path in `before` that has left the diff is `REVERTED-BY-AGENT` only when its content changed: compare what's on disk with what the before snapshot recorded. If the bytes are the same, it isn't a finding, and the run doesn't stop for it.

## The bar

- `-7`'s bar holds, with its decided exceptions.
- **B1 is closed.** Each of the reviewer's submodule-edit scenarios stops the run, on both paths, naming the gitlink, with the message from change 1. No marker planted in the submodule's config runs. A test covers a submodule under a protected path.
- **B2 is closed.** The three moved-folder scenarios undo the commit and roll the writes back, as on `main`. A config whose bytes changed still runs no git.
- **The false positive is gone.** The `git status` scenario passes as on `main`. A test shows an engineer's dirty file that the agent really reverted still reports `REVERTED-BY-AGENT`.
- **Nothing else changes.** With no submodule, no moved config and no filter, the tree, commits, trace events and exception are as on d6aa79a.

## Running the tests

As in `-5`, including its section on tests that agentsh refuses: run the touched files with `-m 'not pushes and not host_only'`, mark any test that starts a command past agentsh's argument limits `host_only`, and never run those in an agent's run.

## Out of scope

- `-7`'s out-of-scope list.
- The old holes the reviewer listed on both versions: a new file under a folder made unreadable, a new untracked file inside a submodule, a read-only `.git` that makes the undo fail, a new nested repo staged as a gitlink, and a file hidden by the agent's own `.gitignore` edit.

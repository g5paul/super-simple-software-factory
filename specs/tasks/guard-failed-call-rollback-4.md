# Roll back a failed agent call's writes, fourth run

This finishes the rollback briefs `specs/tasks/guard-failed-call-rollback.md`, `-2.md` and `-3.md`. Read all three; their bars carry over, except where this brief changes the approach. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where the earlier runs got to

- **101cc8b, run d339590e,** on branch `sssf/d339590e`. It passed the chain, and it's the starting point: `git diff main...sssf/d339590e`. The `bypass-reviewer` subagent found a bypass in it: with `.git/hooks` locked it runs no git, so the agent's protected writes stay, where `main` rolls them back with hooks off. The subagent's probes are in `adws/adw_data/sessions/d339590e/bypass_probes/`.
- **Run af243d43** tried to fix that by splitting the watched paths into a config group and the rest. Its spec is `specs/af243d43_rollback-config-split_v2.md` on branch `sssf/af243d43`, and its report is `adws/adw_data/sessions/af243d43/context_handoff/guard_report.md`. It stopped on a mechanism, a list of strings that moved a hooks folder into the config group when any config value mentioned it. That list caused two bypasses and missed spellings that caused a harm. The engineer didn't accept it. Read that report: its bypasses, harms, broken promise and old hole are this run's test cases.

The engineer's earlier decision holds. When a file git reads as config couldn't be put back, SSSF runs no git and says the tree may still hold the agent's writes.

## The engineer picked option A

Run 2c4e2faa planned this brief and stopped at its first revision, on 2026-11-16, for a decision. With a byte-rewriting filter, such as git-lfs or git-crypt, "no filter program runs" and "the rollback puts back the same file contents as `main`" can't both hold. Its spec, `specs/2c4e2faa_rollback-no-programs_v2.md` in `adws/adw_data/worktrees/2c4e2faa`, sets out options A and B. The engineer picked **A** on 2026-11-16: filters off, and `checkout` writes what git stores. B would let one `.gitattributes` line shield any protected file.

Start from that spec. Its design, its four exceptions to the bar and its "Must never" list stand. Exceptions 1 and 2 are no longer open: they're the decided cost of A, and a reviewer files a finding that matches them as meeting the bar. When the engineer chose, no repo on this machine configured a filter, and neither git-lfs nor git-crypt was installed.

**Document the scenario for SSSF's users.** The engineer asked for this explicitly. `config.md`'s "Write permissions" gets a short subsection for a repo that uses a byte-rewriting filter. It says, in the reader's terms:
- that SSSF's rollback runs no filter, and why: the filter program could be one the agent rewrote;
- the two effects in such a repo, with an example for git-lfs. A rolled-back filtered file holds its pointer or ciphertext, not its contents. A filtered file the agent touched without changing it, outside its `writes`, stops the run and is rewritten the same way;
- how to recover: `git lfs checkout <path>`, or delete the file and run `git checkout HEAD -- <path>`, and the git-crypt equivalent;
- that an agent's write to a filtered file is still rolled back, never kept;
- the other two exceptions, replace refs and grafts, and partial clones, in one line each.

`docs/guide/` gets a one-line pointer to that subsection wherever it covers what a failed run leaves behind.

## The approach: no program runs during SSSF's own git

af243d43's report shows the root cause. When SSSF rolls back, `git diff`, `status`, `ls-files` and `hash-object` run a configured clean filter, and `git checkout` runs its smudge filter. If a filter points at a program the agent can write, the agent's code runs in the chain's process. That covers a tracked `scripts/clean.sh`, `.git/filt` and anything under a hooks folder. It happens on `main`'s success path today, and 101cc8b's failure path reaches it too.

SSSF already turns some programs off for its git commands: `_SAFE` sets `core.fsmonitor=` and `_NO_HOOKS` sets `core.hooksPath=/dev/null`, in `permissions.py`. This run turns the rest off the same way, so no program can run, wherever it lives. Then the split needs no string list.

1. **Every git command `undo_commits`, `enforce`, `snapshot` and the restore run has no program it could start.**
   - For each filter name in the config git will read, pass `-c filter.<name>.clean= -c filter.<name>.smudge= -c filter.<name>.process= -c filter.<name>.required=false`. The engineer's session checked this on git 2.43: with a filter set, plain `diff` and `checkout` ran it 5 times; with those four settings it ran 0 times, and `checkout` still restored the file.
   - Add `--no-textconv` and `--no-ext-diff` where a command takes them, and `GIT_GRAFT_FILE=/dev/null`, `--no-replace-objects` and `GIT_PAGER=cat`, as `_COMMIT_PATHS` already does.
   - The planner lists every config key that makes one of these commands start a program, citing git's docs for each, and neutralizes it, or shows why that command never reads it.
   - Read the filter names after the restore, from the config the command will then read, so a name the agent added is either restored away or covered.
   - Neutralize by settings on the command line, the way `_SAFE` and `_NO_HOOKS` do. That narrows an existing check, and is not a new mode.
2. **Run no git only when a file git reads as config couldn't be put back.** That's `.git/config`, a global or system config reached through a link, and an include. It doesn't depend on what any config value says. Everything else, `hooks/` and `info/` included, rolls back with programs off, as `main` does, then stops with a `PermissionBreach` naming the paths left changed. Make the split by narrowing `guard["restored"]` or `_git_dir_back`. Don't scan config values, and don't add a second compare mode. If af243d43's by-group compare is needed, say in the spec why it's a narrowing.
3. **A git failure during rollback reports cleanly.** af243d43's broken promise: 17 MB of random bytes in `.git/info/grafts`, with `.git/info` locked, crashes the rollback with a `UnicodeDecodeError` and the writes stay. Ignore grafts as change 1 says. Any other git failure during undo or rollback becomes the "may still hold the agent's writes" report, with the original exception kept on the failure path.
4. **The `-3` brief's other two changes carry over.** `_roll_back_failed` never raises, and a broken trace doesn't skip the rollback.
5. **Docs.** `config.md`'s "Write permissions" says SSSF's own git runs no hook, filter or diff program. It names the limit left, a config file that couldn't be put back, and says SSSF then runs no git.

## The bar

- The earlier briefs' bars hold. That includes every failure route, the resume case, the Ctrl-C points, a reporting failure in `_roll_back_failed` and a broken trace.
- **d339590e's bypass is closed.** With `.git/hooks` or `.git/info` locked, on both paths, the protected writes are rolled back, the commit is undone, no planted hook runs, and the run stops. A resume commits none of the planted files.
- **af243d43's cases are closed:**
  - With the engineer's `core.fsmonitor` or an alias naming `.git/hooks`, the writes are rolled back, as on `main`.
  - With a filter program the agent rewrote, the program never runs, on either path. That covers a tracked script, `.git/filt`, a file in a hooks folder, and each spelling in its harm 1.
  - The grafts case reports cleanly.
- **A planted config still runs nothing.** A filter in `.git/config` or a linked `~/.gitconfig` that couldn't be put back never runs, on either path.
- **Each neutralized key has a test.** Plant a marker program behind it, run the rollback, and the marker doesn't exist.
- **Nothing else changes.** When the git directory is clean, or was put back, and no filter is configured, the tree, commits, trace events and exception are as on `main`. With a filter that leaves bytes alone, the rollback puts back the same file contents as `main` does. With a byte-rewriting filter, the spec's exceptions 1 and 2 apply, and a test pins each.
- **The scenario is documented.** A test checks that `config.md`'s subsection names git-lfs, the stored form, both recovery commands and that the agent's write is still rolled back.

## Running the tests

Run the test files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify.

## Out of scope

- `git_helper.commit_all` running hooks and filters on the chain's own commit, which is `docs/open.md`'s P2 High row and gets its own run.
- `permissions.py`'s rollback rules: the gitignored-file deletion, the index reset, the corrupted index and the engineer's edits made during the call. Those are the P2 Medium row.
- A check that refuses a resume after a "may still hold" stop.
- Commits on another branch, and a chain process killed outright.

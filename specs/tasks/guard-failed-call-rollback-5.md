# Roll back a failed agent call's writes, fifth run

This finishes `specs/tasks/guard-failed-call-rollback-4.md`. Read it, and the three briefs before it. Their bars and the engineer's decisions recorded in `-4` carry over: option A for byte-rewriting filters, never recursing into submodules, and the kept write for a stat-dirty filtered file for now. Run it with `--worktree` and `--base main`, on the hub's own config.

## Where run 0c741413 got to

Guard run 0c741413 built `-4` and passed the chain on 2026-11-16 after three review rounds, as a2f166c on branch `sssf/0c741413`. Its spec is `specs/0c741413_rollback-no-programs_v2.md` on that branch, and its report is `adws/adw_data/sessions/0c741413/context_handoff/guard_report.md`. Start from that code: `git diff main...sssf/0c741413` is the starting point.

The `bypass-reviewer` subagent then found a High bypass, and the engineer's session reproduced it. The engineer didn't merge. The probes are in `adws/adw_data/sessions/0c741413/bypass_probes/`: `p1_longname.py`, `p1b_why.py` and `exec/test_exec_probe.py`, with `common.py` as their fixture.

## The bypass

The agent:
1. makes a nested repo `sub` with one commit, and stages it as a gitlink with `git add sub`;
2. appends a filter section with a very long name to `sub/.git/config`. One name of 40,000 characters is enough, and so are 300 names of 200 characters;
3. edits tracked protected files, such as `justfile` and `adws/adw_modules/permissions.py`, and reports success.

`_filters_off` walks into `sub` and turns each filter name into four `--config-env=filter.<name>.*` arguments. Git copies those into `GIT_CONFIG_PARAMETERS`, and once that one environment string passes Linux's 128 KB limit for a single string (`MAX_ARG_STRLEN`), the `git status` child that `diff HEAD` starts in `sub` can't be started. Git exits 128. `_git` turns a non-zero exit into `""`, so `snapshot` sees no changes at all, and `enforce` finds nothing to roll back.

On `main`, the snapshot shows `justfile` modified and `sub` added, and `enforce` rolls them back. On a2f166c the call returns success and `justfile` keeps the agent's bytes. The planted config stays, so every later call's baseline is blind too. On the failure path the writes are also kept, while a2f166c says it rolls back failed calls. A name list past the whole-argv limit, about 2 MB, should make `subprocess.run` raise `OSError(E2BIG)` for SSSF's own git instead.

The root fault is older than 0c741413. `_git` returning `""` on failure, which the snapshot reads as no changes, is the same fault as the corrupted-index item in `docs/open.md`'s P2 Medium row on `permissions.py`'s rollback.

## The change

1. **Fail closed on a git failure.** A git command that fails during `snapshot`, `undo_commits`, `enforce` or the restore never reads as "no changes" or "nothing to undo". That covers a non-zero exit and an `OSError` from starting it, `E2BIG` included.
   - In the snapshot taken after the agent, and in `enforce`, the failure is a `PermissionBreach` naming the command and its error. On the failure path, the original exception still wins, with the breach reported as 101cc8b reports a failed rollback.
   - In the baseline snapshot, before the agent, a failure stops the call before the agent runs, with the same message. A repo where `diff HEAD` already fails can't be guarded, and the engineer hears so.
   - Commands whose non-zero exit is an answer, not a failure, such as `check-ignore` with no match or `rev-parse --verify` on a missing ref, keep their meaning. The spec lists each `_git` call site and says which kind it is.
   - Name the corrupted-index item in the P2 row as closed by this, if the bar's test for it passes.
2. **Git starts no child in a submodule during SSSF's own git.** Fail closed alone would turn the bypass into a loud stop that keeps the writes, where `main` rolls them back. So stop git from needing the submodule's config at all.
   - The planner weighs `--ignore-submodules=dirty` on `diff` and `status`, which should still report a changed submodule commit without starting a child `status`, against capping the walked names and refusing past the cap. Another option is fine if it's narrower.
   - Whatever the choice, show on git 2.43 that SSSF's git starts no process in a submodule, and that a staged, committed or changed gitlink still shows in the snapshot and is rolled back as on `main`.
   - If the submodule walk is no longer needed, drop it, as `-4` said.
   - A change to a submodule's own work tree that `main` reported and the head no longer does is a finding unless the spec shows `main` couldn't roll it back either. Say which in the spec.
3. **Test the empty filter name.** The subagent left this as an untested lead: a submodule config with `[filter ""]` and `filter=` in that submodule's `.gitattributes`. `_filters_off` skips an empty name. Test whether git matches it and runs the driver. If it does and change 2 doesn't already make it moot, neutralize it, or fail closed when a config names an empty filter.

## The bar

- `-4`'s bar holds, with its decided exceptions.
- **The bypass is closed.** `p1_longname.py`'s case, at the sizes `p1b_why.py` used, and the 2 MB case: on both paths, the protected writes are rolled back as on `main`, or, where they can't be, the run stops naming them. Never a silent success.
- **A failing git is a breach.** A test makes each `_git` call in the snapshot, undo, rollback and restore fail, by a non-zero exit and by an `OSError`. None reads as "no changes".
- **The corrupted index.** `printf garbage > .git/index` after the agent's writes gives a breach, not a silent pass.
- **No child in a submodule.** A test plants a marker filter, hook and fsmonitor in a submodule's config, on both paths. Nothing runs, and the gitlink still rolls back.
- **The empty filter name** has a test either way.
- **Nothing else changes.** With no submodule and no filter configured, and git succeeding, the tree, commits, trace events and exception are as on `main`.

## Running the tests

Run the test files the change touches, from `.claude/skills/sssf`, with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. `test_config_md_documents_the_scenario` reads `docs/guide/run.md` from the repo root, so run it from a full checkout.

## Tests that agentsh refuses

Run 41342dd9 planned this brief, and its spec passed review on the second round: `specs/41342dd9_git-fail-closed_v2.md` on branch `sssf/41342dd9`. Start from that spec. Its build was stopped on 2026-11-16 after agentsh refused 204 commands by rule `argv_truncated`.

The bar's long-name and 2 MB cases make SSSF's own git start with an argument list past agentsh's limits, 128 KiB per argument and 2 MiB in all (`docs/designs/agentsh-local-sandbox.md`, "Long arguments are refused"). Under agentsh each such start is refused, and each refusal counts toward the three-refusal stop. So:
- Mark each test that starts a command past those limits `host_only`, with a comment saying it passes only outside agentsh because of `argv_truncated`. Verify runs outside agentsh with no `-m`, so it runs them, and its result is the one that counts.
- Never run those tests in an agent's own run, not even once to see them fail. Run the touched files with `-m 'not pushes and not host_only'`, as before.
- Where a test only needs SSSF's git to fail to start, make it fail by raising the `OSError` in place of starting git, as 41342dd9's `test_when_sssfs_own_git_cant_start_the_snapshot_raises` does, so it runs under agentsh too.

## Out of scope

- The rest of the P2 Medium row: the gitignored-file deletion, the index reset and the engineer's edits during the call.
- The stat-dirty filtered file's kept write, which has its own P2 row.
- `git_helper.commit_all` running hooks and filters, the P2 High row.
- A submodule's unwatched git directory, as the old hole 0c741413's report names, except where change 2 needs it.

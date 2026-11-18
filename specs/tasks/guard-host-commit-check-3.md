# Bound the fold, and stop a VM commit that doesn't land

This follows `specs/tasks/guard-host-commit-check-2.md`, which guard run 8dee7020 built and the engineer merged on 2026-11-17 as d1e6011. Read that brief and its spec, `specs/8dee7020_narrow-host-check_v2.md`; their bars carry over. Run it with `--worktree` and `--base main`, on the hub's own config.

The `bypass-reviewer` subagent found both holes reviewing 8dee7020. Their rows are in `docs/open.md` at P2 Medium. Both reach VM runs, so under decision 14 of `docs/designs/openshell-sandbox.md` they block. Its probes are in `adws/adw_data/sessions/8dee7020/bypass_probes_2/`: `longname_judge.py`, `nfd_cost.py`, `perm_speed.py` and `test_zz_probe_8dee_vm.py`, with their results beside them.

## The holes

### A long filename stalls the host check

`permitted()` folds a path with `_fold_part` in `permissions.py`, which runs NFD normalization. NFD is quadratic in a run of combining marks, and the fold runs once per protected entry for each agent that ran. A VM agent can write a tree entry with any name through `git mktree`, so it controls that cost.

- `"src" + "́"*10000 + "̖"*10000 + ".py"` takes 8.0 s per path in `judge_commits` on the head, against 0.01 s on the base. At 4,000 marks it takes 0.4 s.
- `_fold_part` alone takes 28 s on 160,000 marks, and the subagent puts 200,000 marks at about 25 minutes per path. A commit with many such names holds the host for hours.
- Nothing lands. The branch stays at `synced`, and the later `git merge --ff-only` would fail anyway, because no disk takes a name that long.

New in 8dee7020. On 20,000 ordinary paths `permitted()` also went from 0.3 s to 4.7 s; that's a cost to note, not a hole.

### A plan commit that doesn't land ends no grant

`Remote.commit` runs `git commit` in the VM and then `commits_out(only, wrote)`. `commits_out` returns at once when the VM's HEAD equals `synced` (`sandbox_remote.py`, about line 882), before `permissions.commit_passed` records the plan commit's `run.taken` pairs.

The planner plants a one-shot `.git/hooks/post-commit` that runs `git reset -q --soft HEAD~1` and deletes itself. The plan commit exits 0 with HEAD at `synced`, `run.taken` stays empty, and the builder's rewrite of `specs/old.md` lands in the next full commit. Without the hook it's refused. It needs the hook to get past the in-VM git directory guard, the same condition as every host check finding.

A full commit undone the same way leaves `run.ran` uncleared, so a later agent's commit is judged with the undone commit's agents' grant too.

Open on `main` before 8dee7020 too. In local mode, `check_local_commit` sees an empty walk, passes, and records the grant, so local mode is fine.

## The change

1. **Refuse an over-long component before any fold.** A path with a component longer than any of Linux, macOS and Windows takes is protected, and nothing unlocks it, on the refusing side of `permitted()` before `_fold_part` runs. Linux caps a name at 255 bytes, while APFS and NTFS count characters or UTF-16 units, so a non-ASCII name they take can be longer in bytes. The spec cites each limit and sets the cap at the largest. Show the fold's worst-case cost under that cap, for one path and for a commit of `COMMITS_MAX` commits.
2. **A VM commit that exits 0 must move the branch.** When `Remote.commit`'s `git commit` exits 0 and the VM's HEAD still equals `synced`, refuse it: a `PermissionBreach` that says the commit didn't land, a trace event, and the branch at `synced`. That holds for an `only` commit and a full one. `commits_out` called from `close()` keeps its early return, where no new commits is the normal case.
3. **Docs.** `config.md` drops the sentence on a plan commit that doesn't move the branch, added in d1e6011, and says such a commit stops a VM run. "Write permissions" names the cap. Both `docs/open.md` rows close.

## The bar

- **The hang is bounded.** `longname_judge.py`'s name at 4,000, 20,000 and 200,000 marks, through `judge_commits` and through a VM stand-in from `tests/test_sandbox_remote.py`: each is refused within a second, and the branch stays at `synced`.
- **The cap refuses nothing a disk could take.** A component at the cap, ASCII and multi-byte, is judged as on `main`. One past it is refused for every agent, including one whose `writes` names it.
- **The plan commit that doesn't land.** `test_vm_plan_commit_that_does_not_land`'s hook, on both providers' stand-ins: the run stops at the plan commit with the branch at `synced`, and `specs/old.md` never changes on the host. The same hook on a full commit stops the run there.
- **No false stop.** Every existing test passes, and every shipped chain's normal commits pass as on `main`, through `close()` with nothing new to bring back too.
- **Nothing else changes.** When every commit passes, the branch, the trace events apart from the new one, and the saved patch are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A 200,000-mark name is about 400 KB in UTF-8, past agentsh's 128 KiB limit for one argument: build it inside the test, never pass it on a command line, and mark any test that does `host_only`. No test starts a real VM.

## Out of scope

- The other rows from 8dee7020's review: the builder undoing the plan commit under a dirty launch tree (P3, local mode) and `_in_git_dir` lacking the fold (P3).
- The fold's cost on ordinary paths, unless the cap's design changes it.
- Making `sandbox: openshell` the default, which waits on the engineer (`docs/open.md`).

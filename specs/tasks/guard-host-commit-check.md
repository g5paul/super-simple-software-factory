# Check a VM run's commits on the host before they land

The engineer asked for this check on 2026-11-17, after the backlog was sorted by where each hole reaches ([openshell-sandbox.md](../../docs/designs/openshell-sandbox.md), decision 14). Run it with `--worktree` and `--base main`, on the hub's own config. Guard run ea9dc12f's index fix was merged on 2026-11-17 as 696b7ec. `sandbox_remote.py` isn't on CLAUDE.md's guard list, but it decides what lands on the engineer's branch, so the change goes through this chain.

## The hole

Under `--sandbox openshell`, `vercel` or `e2b`, every check runs in the VM, against a `.git` the agent controls. The host then trusts the result. `Remote.commits_out` (`templates/adws/adw_modules/sandbox_remote.py`) makes a bundle of `synced..HEAD` in the VM, fetches it on the host and runs `git merge --ff-only FETCH_HEAD` on the run's branch, with no check. It runs after every `git commit` the runner starts that succeeds, and again in `close()`, on success, a stop or a failure.

So anything an agent gets into a commit in the VM lands on the host branch, and from there in `just merge`:
- content staged only in the index, or behind a forged cache-tree, that the chain's commit takes (`specs/tasks/guard-index-only-staged.md` and `-2`);
- a change `git replace` or a graft hid from the in-VM check;
- what a planted hook or filter changes in the chain's own commit (`docs/open.md`'s P2 row on `commit_all`);
- an agent commit the VM's `undo_commits` failed to undo before `close()` brought it back.

The `bypass-reviewer` subagent found two more on 2026-11-17, reviewing ea9dc12f. Both are open on `main` and in local mode, and both get past the in-call check that run added:
- **A forged cache-tree in the plan commit.** The planner leaves every index entry as `HEAD` has it, then points the index's cache-tree nodes for `adws` and `adws/adw_modules` at trees it wrote that hold its own `permissions.py`. The in-call check rightly finds no staged change. `commit_all("plan", ["specs/"])` then commits `M adws/adw_modules/permissions.py` with the agent's content. The next full commit puts it back, but between the two `HEAD` holds it.
- **An unsorted index.** The agent hand-writes `.git/index` with a new `adws/zz.py`, or its own `permissions.py`, as the first entry, out of order. The in-call check stops the run but doesn't repair the index, and the next `commit_all("build")` commits the agent's file and deletes 17 tracked files.

The probes are in `adws/adw_data/sessions/ea9dc12f/bypass_probes/`: `harness/scenarios.py` holds each candidate (`c4_inverse_forged_tree_plan_commit`, `c2_unsorted_new_protected_then_build`, `c2_unsorted_P_replaced_then_build`, `c2_gitdir_entries_real_guard`), and `results/` their outcomes on base and head.

Each of these has been fixed, or is being fixed, one at a time inside the VM. The host never looks.

## The change

1. **The host judges each commit it brings back.** In `commits_out`, fetch the bundle without moving the branch, then read every new commit with the host's own git before the fast-forward:
   - The bundle's only prerequisite is `synced`, the new commits form one chain from `synced` to the bundle's head, and none is a merge.
   - Each commit's changed paths come from `diff-tree -r --raw --no-renames -z` against its parent, run with no replace objects, grafts off, no hooks, no external diff or textconv, and `submodule.recurse=false`, as `permissions.py`'s `_COMMIT_PATHS` does. The host reads tree objects, never an index.
   - A path passes only when `permissions.permitted` allows it for one of the agents that ran in the VM since the last sync, or it's under the run's own session files the chain writes. The plan commit's paths, from `commit_all(message, only)`, pass only for the planner.
   - A gitlink, a symlink and a mode change are judged by their path like any file.
2. **A refusal keeps the branch where it was.** If any commit fails, the host branch stays at `synced`, the bundle is kept as `sandbox-refused.bundle` in the run's session folder, and the run stops with a `PermissionBreach` naming each commit and path. A trace event records it. In `close()`, a refusal on the failure path is reported the way `_roll_back_failed` reports, and the original exception still wins.
3. **The uncommitted patch says what it holds.** `close()` saves the VM's uncommitted work as `sandbox-uncommitted.patch`, never applied. When it touches a path no agent of the run may write, the console note names those paths, so the engineer sees them before applying it.
4. **The same check after the chain's commit in local mode.** Under `sandbox: none`, after each `commit_all`, the same function judges the commit it made, with the repo's git under the same settings, and on a refusal resets the branch to the commit before and stops the run. This closes the index and cache-tree class in local mode too, since the commit's tree is what lands. The spec says what an agent that controls the local `.git` can still do to that read (alternates, a planted config the git directory guard didn't put back) and says it in `config.md`.
5. **The rollback never deletes in the git directory.** The same review found that when an agent stages index entries named `.git/config`, `.git/hooks/pre-push` or `.git/sssf-private-names`, `_roll_back` in `permissions.py` reports them "unstaged and deleted" and removes the engineer's real files, after the git-directory guard put them back. Make `_roll_back` unstage such an entry and never delete a path under `.git/`, in any case or spelling git treats as the git directory. That narrows an existing branch; it isn't a new mode. It's local-mode only under decision 14, but it touches the same module, so it rides with this run.
6. **Docs.** `config.md`'s "Write permissions" says the host checks every commit a VM run brings back, and every commit the chain makes locally, against the agents' `writes`. `docs/designs/remote-sandboxes.md` gets a line at its "As built" section.

## The engineer's decisions

These came from the hub session, so they were put to the engineer before the run, as `docs/open.md`'s P2 row on guard briefs asks. The engineer confirmed all three on 2026-11-17, and a reviewer files a finding that matches one as meeting the bar.

1. **Attribution.** A path passes if any agent that ran since the last sync may write it. Stricter, per-commit attribution would need the chain to label each commit with its phase, and the chain makes one commit per sync anyway.
2. **Local mode in the same run.** Yes, since change 4 is the same function and closes the index class locally. If the chain's review finds the local read can't be trusted enough, the spec says so and local mode keeps the in-call check alone.
3. **The host merge.** `_host_git` runs a plain `git merge --ff-only`, which runs the engineer's own `post-merge` hook and checkout filters on the agent's content. Keep it, since those are the engineer's own programs and the content has passed the check, and say so in `config.md`.

## The bar

- **Each route lands nothing.** With a VM stand-in from `tests/test_sandbox_remote.py`, an agent that gets a protected path into a commit by each route above, on the success and the failure path, leaves the host branch at `synced`, the run stopped naming the path, and the bundle kept.
- **Bundle shape.** A bundle with a merge commit, a second root, a prerequisite other than `synced`, or a head that isn't a descendant of `synced` is refused before any commit is read.
- **Hidden in git's internals.** A replace ref, a graft and a forged cache-tree in the VM's `.git` change nothing on the host: the host reads the commit's real tree.
- **The review's two holes.** `c4_inverse_forged_tree_plan_commit` and both `c2_unsorted_*` candidates, in local mode and through a VM stand-in: the commit that takes the agent's file is refused, the branch is back at the commit before (local) or at `synced` (VM), and the run stops naming the path.
- **The git directory.** `c2_gitdir_entries_real_guard`, with the real guard: `.git/config`, `.git/hooks/pre-push` and `.git/sssf-private-names` are the engineer's after the call, on both paths, and the protected write is rolled back.
- **Local mode.** The index and cache-tree cases from `-2`'s bar, with the in-call check stubbed to see nothing, are refused by the commit check, and the branch is back at the commit before.
- **No false stop.** Every existing VM test and every chain's normal commits pass as on `main`: a plan commit of `specs/`, a builder's commit inside its `writes`, the documenter's, the session files the chain writes.
- **Nothing else changes.** When every commit passes, the host branch, the trace events apart from the new one, and the saved patch are as on `main`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. Mark any test that starts a command past agentsh's argument limits, 128 KiB per argument or 2 MiB in all, `host_only`, and never run those in an agent's run. No test starts a real VM.

## Out of scope

- Refs other than the run's branch, other worktrees and ignored files: local-mode limits under decision 14, which a VM run never brings back.
- Making `sandbox: openshell` the default, which waits on the engineer (`docs/open.md`).
- The in-VM checks themselves, which stay as they are.

# Commit only the tree verify passed

Guard change, run 3d9f277f. It builds on guard run dafc21f0 (verify in a fresh VM), which is not on `main`. This is the third version of the spec, revised after spec review 2. What changed from v2: the "round's head" is gone. While a verified tree is in force, every commit that comes back from the build VM is held to it, with no exemption that rests on the VM's own word.

## Problem

Under a VM sandbox, each verify round takes the builder's work out of the build VM as a patch (`Remote._patch_out`), judges it on the host (`Remote._judge_patch`), and checks it in a fresh VM. The review and the commit then use the build VM's tree and index. Nothing ties the commit to the tree that verify passed. Four ways through, all reproduced:

1. **Force-added ignored file.** `_patch_out` starts from `git read-tree HEAD` and `git add -A`, which skips ignored files. `git_helper.commit_all` commits the real index, which holds a file staged with `git add -f`.
2. **A failing `git diff` gives an empty patch.** The script ends in `git diff ... | base64 -w0`, so git's exit status is lost. Verify then checks the last sync alone, and the bad commit lands at `close()`.
3. **skip-worktree.** Bad bytes staged and hidden with `git update-index --skip-worktree`, good bytes on disk. Verify reads the disk, the commit takes the index.
4. **A process left running** changes the tree after the patch is taken.

Two harms came with dafc21f0: `VerifyBox` has no read methods, and `plan_checks.verify` returns a `run_quality` result unchanged when the plan has no checks.

## Threat model

The builder runs arbitrary code in the build VM as the VM's user. It owns that VM's worktree, index, object store, `.git/config` and any process it leaves behind. So everything the build VM's git says is the agent's word: the patch can be forged (`diff.external`, a shimmed `git`, a missing object), `HEAD` can be anything at the moment it is read, the tree can change at any moment, and a left process can make commits of its own at any moment, which the next sync brings to the host.

What the agent cannot reach: the host process, the host's object store except through a bundle the host unbundles and hashes itself, and the fresh verify VM (no agent starts there).

So the guard that holds is on the host, and it uses host facts only: the tree the host built from the judged patch (`write-tree`), the last sync (`self.synced`, set by the host), and the trees of the commits the host unbundled. Change 1 below (the index copy, no pipe) makes honest runs and the common plants fail early and clearly. Change 2 (the tree comparison) is the one a forged patch cannot get past.

What this guard can't stop at its level: the reviewer agent reads the build VM's tree, which a left process can dress up for the review. The local sandbox (`sandbox: none`) has no second machine, so the agent runs as the engineer's own user there and nothing here binds its tree.

## Baseline

`guard/verify-fresh-vm` at `7e2d3c1` (`main` plus `sssf/dafc21f0`). Every finding is judged against that commit. `main` is the code before dafc21f0.

## Bar

Must do, where the baseline does otherwise:

- Each of these plants, run through the agent guard sequence and a stand-in `Remote`, lands nothing: the run's host branch stays at the last sync. On the baseline each lands the bad tree.
  1. the force-added ignored file
  2. the missing blob, no process left running
  3. the missing blob, put back after the patch is taken
  4. skip-worktree
  5. a file changed between a green verify and the commit
  6. after a green round, a commit X with a bad tree followed by a commit Y that restores the verified tree
- One more plant lands only the verified tree: after a green round a left process commits the verified tree, a sync lands it, then a file changes. `commit_build` is refused, and the host branch stays at the synced commit, whose tree is the verified one.
- Any git failure while the patch is taken stops the round with `SandboxFailed` before any VM is made and before any check runs.
- While a verified tree is in force, every commit that comes back from the build VM, at a sync or at `close()`, must hold that tree, except at the `wrote` paths of the call that brings it back. If one does not, the whole bundle is refused as a `PermissionBreach`, nothing of it lands, and it is kept as `sandbox-refused.bundle`, as for any refused commit.
- The verified tree stays in force until the chain's own commit lands (`Remote.commit`) or a new verify round opens. A sync that lands agent commits does not end it.
- Inside a verify round under a VM, `sandbox.current()` answers `exists`, `is_file`, `size` and `read_text` against the fresh VM.
- `plan_checks.verify` returns `passed=not failures` on every path, under every sandbox, as `main` does.

Must never do:

- No agent file kept, no agent code run, no engineer file deleted or overwritten, beyond what the baseline does.
- Nothing an agent may do changes: no edit to the VM's policy, any agent prompt, any rule file, or `templates/sssf.config.yaml`.
- A real run is unchanged: an ordinary change, a staged rename, a deleted file and a binary file each pass verify and land with the same commit as on the baseline. So does the work of a builder that committed during its own call, since the agent guards undo that commit and keep its changes, as on the baseline.
- A verify round lands nothing and moves no ref, on the host or in the build VM, as on the baseline.
- No decision rests on a `HEAD`, a ref or a commit list read from the build VM. The comparison uses the host's `synced`, the host's judged tree and the commits the host unbundled.
- The verify VM gains no `start`, `commit`, `install`, `write_text`, `remove` or `open_verify`.
- No new module-level state. The verified tree is one string attribute on the `Remote` instance.
- Every existing test passes on the host.

One accepted change against the baseline: a commit made in the VM by an agent's left process, found while a verified tree is in force and holding another tree, is now refused at `close()` too, where the baseline landed it on a run that was not accepted. That is the rule the request asks for.

### Which commits land, under a VM

"Verified tree" means the host's tree from `_judge_patch` for the last verify round, kept only if that round went green. It is forgotten when a new round opens and when the chain's own commit lands. It is not forgotten when a sync lands commits an agent made.

Why no commit needs an exemption: after every agent call, `agents.execute` runs `permissions.undo_commits`, which resets an agent's own commits and keeps their changes in the tree. So in an honest run the build VM's `HEAD` is the last sync when a round opens, the patch is the whole of the builder's work, and the only commit that follows is the chain's own. A commit in `synced..tip` that the chain did not make was made by a process an agent left running, after the guards ran.

| Chain | Commit | Held to |
|---|---|---|
| `adw_simple_sdlc.py` | `commit_plan` (`only` the spec, before any verify) | `judge_commits` only, as on the baseline |
| | `commit_build` | must equal the verified tree of the last green round (`verify_N` or `reverify`) |
| | `commit_docs` | made after `commit_build` ended the verified tree: `judge_commits` only, which holds it to the documenter's `writes` because `commit_build` cleared `run.ran` |
| `adw_tickets.py` | each `tNN_commit` | must equal that ticket's verified tree, except at the paths in `wrote` (the ticket file the chain marks done after verify) |
| | `commit_review_fix` | must equal the review round's verified tree |
| | the documenter's commit | `judge_commits` only, as `commit_docs` |
| `adw_build_test.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py` | the commit after the green verify | must equal the verified tree |
| | any commit before the first verify (a plan commit) | `judge_commits` only |
| `adw_plan_build.py`, `adw_build.py` | every commit | no verify in the chain: `judge_commits` only, as on the baseline |
| `adw_quality.py` | none made by the chain | a commit found at `close()` after its green round must equal the verified tree |
| every chain above | any other commit found at a sync or at `close()` while a verified tree is in force | must equal the verified tree; landing it does not end the verified tree |
| guard chain (`adws/adw_guard_change.py`) | all | takes no `--sandbox`, runs under `Local`; only the `plan_checks.verify` fix applies |

An `only` commit after a green round is held to the same rule and would be refused. No shipped chain makes one.

This closes `docs/open.md`'s P2 row "The verified tree is not bound to the committed tree" for VM runs.

## Changes

Write the tests first and run them against the unchanged code, so the red is seen (see Verification).

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

**`Remote.__init__`**: add `self._verified = ""`: the tree id of the last green verify round, or `""`.

**`_patch_out`**: replace the script. It must:

- start with `set -e`;
- make the temporary index path as now (`mktemp -u`) and a temp file for the patch, both removed by the `trap`;
- find the real index with `git rev-parse --git-path index` (before `GIT_INDEX_FILE` is set), and copy it to the temporary index if it exists. No `git read-tree HEAD`. An absent index stays absent, which is an empty index in both places;
- `export GIT_INDEX_FILE`, run `git add -A`;
- run `git diff --cached --binary "$1" > "$out"` as its own command, then `base64 -w0 "$out"`. No git command writes into a pipe.

`_must` already raises `SandboxFailed` on a non-zero exit, so a failing `git add` or `git diff` stops the round. Decode with `base64.b64decode(..., validate=True)` and turn a decode error into `SandboxFailed`. Update the docstring: the patch is what a commit made now would hold (the real index plus `git add -A`), not the worktree against `HEAD`.

**`_judge_patch(patch) -> str`**: return the tree id from `write-tree`. For an empty patch return `_host_git("rev-parse", f"{self.synced}^{{tree}}", cwd=self.tree)` and judge nothing. Everything else stays.

**`open_verify`**:

- first statement: `self._verified = ""` (a new round forgets the old green);
- `_patch_out` as now; `SandboxFailed` propagates, and the `OSError` wrap stays;
- `tree = self._judge_patch(patch)`, called for an empty patch too;
- `box = VerifyBox(inner, self, tree)`;
- add `"tree": tree[:12]` to the `sandbox_verify` event.

It reads no `HEAD` from the build VM and syncs nothing.

**`VerifyBox`**:

- `__init__(self, inner, outer, tree)`: keep `outer` and `tree`.
- `mark_green(self) -> None`: `self.outer._verified = self.tree`. Log a `sandbox_verified` event with the build VM's name and `tree[:12]`. It may be called after `close()`.
- `exists`, `is_file`, `size`, `read_text`: delegate to `self.inner`'s methods of the same name. Nothing else is added.
- Update the class docstring: it can run a command, read a file and be closed.

**`commits_out`**: inside the existing `try`, after `judge_commits` returns, and only if it refused nothing and `self._verified` is set:

- `judge_commits` has already refused a merge, a root and a tip that doesn't descend from the sync, so `synced..tip` is one line of commits. List it: `_host_git("rev-list", "--reverse", f"{self.synced}..{tip}", cwd=self.tree)`;
- for every commit in it, with none skipped, `got = _host_git("rev-parse", f"{commit}^{{tree}}", cwd=self.tree)`. If `got` is not the verified tree, list the differing paths with `_host_git("diff-tree", "-r", "--no-renames", "--name-only", "-z", self._verified, got, cwd=self.tree)`, split on `\0`, and drop the paths that are in `wrote`, compared the same literal way `permissions.judge_commits` compares `path in wrote` (read `permissions.py` near line 1064; do not edit that file, it is protected);
- for each commit with a path left, add `(commit, ["not the tree verify passed"])` to `refused`, and set `text` to a sentence that names the verified tree, the commit, and up to 20 of the paths (through `permissions._said_line`), and says every commit made after the last green verify must hold exactly what it checked.

The existing `if refused:` branch then keeps the bundle, logs `sandbox_commits_refused` and raises `PermissionBreach`. Add `"verified": self._verified[:12]` to that event's payload when a verified tree is in force. A `SandboxFailed` from these git calls is already caught there and refuses as "unreadable".

After the `merge --ff-only` succeeds, set `self._verified = ""` only when `must_move` is true. `Remote.commit` is the one caller that sets it, so only the chain's own commit ends the verified tree. A sync from `_run` or `close()` leaves it in force.

Update the module docstring's verify bullet and `commits_out`'s docstring to say every commit after a green round must be the verified tree, and when the verified tree ends.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py`

In `verify`:

- `with sandbox.verifying(run) as fresh:` around the same two lines;
- delete `if not extra: return result`; always build the `QualityResult` with `passed=not failures`, as `main` does;
- after the `with` block, if the result passed and `fresh` is not `None`, call `fresh.mark_green()`. No `getattr`: every box `open_verify` returns has the method;
- update the docstring: no path returns `base`'s own result, and a green round under a VM records its tree for the commits that follow.

Update the module docstring's lines about the fresh VM (near lines 12 and 80) if they say the commit is unbound.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

Docstring of `verifying` only: the yielded box is what `plan_checks.verify` marks green, so a box's `open_verify` must return something with `mark_green`. No code change.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`

Docstring of `commit_all` only: in a VM, a commit after a green verify must hold the verified tree except the `wrote` paths, or it is refused. No code change: the commit keeps using the real index, and verify now reads the same index.

### 5. Tests: new `.claude/skills/sssf/tests/test_verify_commit_tree.py`

Import `rig`, `_verify`, `_block`, `_git`, `_in_build` from `test_verify_fresh_vm` (the fixture imports by name). The config is the shipped `templates/sssf.config.yaml`, as `rig` already loads it.

Two helpers:

- `_as_builder(rig, plant)`: run the plant inside the same guard calls `agents.execute` makes around an agent, in its order (read `agents.py` lines 330 to 440): `permissions.snapshot`, `permissions.head`, `permissions.guard_git_dir` before; `git_dir_changed`, `restore_git_dir`, `permissions.undo_commits`, `permissions.enforce` after. It asserts no breach, so each test shows the plant gets past the agent guards. Reuse an existing helper if `tests/conftest.py` or `test_agent_failed.py` has one that fits.
- `_chain(rig, check)`: what a chain does. `plan_checks.verify`; if it passed, `git_helper.commit_all("build", run=rig.run_)`; then `rig.close(stop=True)` in every case. It returns what happened (the result, or the exception). Each plant test asserts on the outcome and on `git rev-parse HEAD` in `rig.host_tree`.

Plant tests (red on the baseline, where each lands the bad tree). Unless said otherwise, the host branch ends at the sync taken before the plant:

1. `test_a_force_added_ignored_file_reaches_verify_and_nothing_lands`: `.gitignore` holds `local_settings.py` and the tracked `app.py` is the request's three lines, both committed and synced in setup. The plant is `printf "OK = 0\n" > local_settings.py && git add -f local_settings.py`. The check is `python3 -c 'import app,sys; sys.exit(0 if app.OK == 1 else 1)'`. After: verify fails.
2. `test_a_missing_blob_stops_the_round_and_nothing_lands`: unpack the build VM's pack into loose objects, delete the loose object of a tracked file's blob, delete that file, break `app.py`. After: `SandboxFailed`, `len(rig.made) == 1`, no check ran.
3. `test_a_missing_blob_put_back_after_the_patch_still_lands_nothing`: as 2, with `rig._patch_out` wrapped so the object is put back as soon as the call ends, by return or by raise. This stands for the left process. After: `SandboxFailed`, nothing lands.
4. `test_skip_worktree_bytes_reach_verify_and_nothing_lands`: stage bad bytes for `app.py`, `git update-index --skip-worktree app.py`, write good bytes to disk. After: verify fails.
5. `test_a_file_changed_after_a_green_verify_is_refused_at_the_commit`: verify passes; the test then rewrites a file in `rig.vm_tree`. After: `commit_all` raises `PermissionBreach`, `sandbox-refused.bundle` exists, the host branch is at the sync.
6. `test_a_bad_commit_then_one_that_restores_the_verified_tree_lands_nothing`: verify passes. Outside `_as_builder`, as a left process would, the test commits X in `rig.vm_tree` with `app.py` broken, then commits Y with the verified bytes back. `rig.close(stop=True)` raises `PermissionBreach`; the host branch is at the sync and holds neither X nor Y. A second case does the same and then calls `commit_all`, which finds nothing to commit; `close` still refuses.
7. `test_a_synced_commit_of_the_verified_tree_does_not_end_the_check`: verify passes with uncommitted work. The left process's part: the test stages that work in `rig.vm_tree` and commits it through `rig.run(["git", "commit", "-qm", "x"])`, whose exit 0 makes `_run` sync. The sync lands, the host tip's tree is the verified tree, and `rig._verified` is still set. The test then rewrites a file. `commit_all` raises `PermissionBreach`, and the host branch stays at the synced commit. On the baseline the changed file lands.
8. `test_a_bad_commit_made_before_the_round_is_refused`: with no green round yet, a left process (outside `_as_builder`) commits X with `app.py` broken and then puts good bytes in the tree. Verify passes on the good tree. `commit_all` raises `PermissionBreach` because X is in the range and is not the verified tree; nothing lands. This is the plant from spec review 2, with no head to lie about.

The rule's edges:

- a builder that commits during its own call: the commit is made inside `_as_builder`, whose `undo_commits` resets it and keeps the changes. Verify passes and `commit_all` lands one commit holding the verified tree;
- a path in `wrote` that differs from the verified tree lands; the same change with a second, unlisted path is refused;
- an `only` commit before any verify lands (the plan commit);
- after the chain's own commit lands, `rig._verified == ""`, and a later commit with no verify since lands (the documenter's commit);
- opening a new round forgets the earlier green: after a green round and then a red one, `rig._verified == ""`;
- a verify round moves nothing: the host `HEAD`, `rig.synced` and the build VM's `HEAD` are the same before and after, and no call in the round reads `HEAD` from the build VM beyond what `_patch_out`'s script needs.

A real run is unchanged: one parametrized test over an ordinary edit, `git mv b.txt d.txt` (staged), a deleted file and a changed `c.bin`. Each passes verify, `commit_all` lands, and the host `HEAD`'s tree equals the tree in the `sandbox_verify` event.

The harms (both red on the baseline):

- `test_the_verify_box_answers_the_read_calls`: a `base=` block calls `read_text`, `exists`, `is_file` and `size` on `sandbox.current()` for a file the builder's patch added, and gets the fresh VM's answers.
- `test_a_base_that_says_passed_with_a_failure_listed_fails_verify`: under `Local`, `base` returns `QualityResult(passed=True, checks=[], failures=["x"])`, no plan checks. `verify(...).passed is False`.

### 6. Tests: existing files

- `.claude/skills/sssf/tests/test_adw_tickets.py`: give the `_VerifyTree` stand-in (near line 521) a `mark_green` that does nothing, so `plan_checks.verify` needs no `getattr`.
- `.claude/skills/sssf/tests/test_verify_fresh_vm.py`: `test_under_local_verifying_yields_none_and_verify_runs_as_before` hands `verify` a string as the base's result and expects it back. Give it a real `QualityResult` and assert the returned `passed`, `checks`, `failures` and `artifacts` equal the base's.
- Same file, `test_the_verify_vm_has_no_start_commit_or_install_...`: add `remove` and `handoff_out` to the names it must not have.
- Search the other test files for a test that expects `verify` to return the base's own object, or a stand-in whose `open_verify` returns a box without `mark_green`, and fix each the same way.

### 7. Docs

- `.claude/skills/sssf/references/config.md`, "Where verify runs under a VM": say the patch is what the commit would hold (the build VM's index plus `git add -A`, so a force-added ignored file and a skip-worktree entry are in it); that a git failure while taking it stops the run; that every commit that comes back after a green round must equal the tree verify passed, or the bundle is refused and kept as `sandbox-refused.bundle`; and that this holds until the chain's own commit lands. Remove "a process still running" from the list of things that cannot reach the result, and say instead that a process left running can still change the build VM's tree or commit there, and that a commit holding such a change is refused. Add to the "What changes for a repo" list: a `quality.py` block may read files through `sandbox.current()` in the round; a chain that writes a file between verify and the commit must name it in `wrote`.
- `docs/open.md`: delete the P2 row that starts "The verified tree is not bound to the committed tree" (the file's rule is that a closed row is deleted). If the row above it, about the plan check in a VM, or any other row points at that row, reword it. Add one P3 row, type Bug, severity Low, status Ready, dated 2026-11-27, linked to this spec, for two findings this run does not fix: `sandbox_usage.Totals` counts each verify VM's `sandbox_end` as a run, and the dashboard's `openSandboxes` (`.claude/skills/sssf/apps/visualizer/server/db.ts`) never lists a verify VM as open, so a leaked one does not show (the provider sweep still deletes it). Add a P3 row, or extend that one, for the Out of scope items below that have no row yet.
- `docs/designs/remote-sandboxes.md`: where it describes `open_verify` or the fresh VM, add the same facts (index copy, every later commit bound to the verified tree, when the binding ends).
- The repo keeps no CHANGELOG. `README.md` and `SKILL.md` do not describe this and need no change.
- For the documenter: any `app_docs/` write-up of run dafc21f0 that says a process left running cannot reach the result now describes old behaviour. Note it in the new write-up; do not edit the old one.

## Out of scope

- **`sandbox: none`.** No tree is bound there, so skip-worktree and a process left running still work on the host, as on `main`. The `/tmp/conftest.py` row stays open. Only the `plan_checks.verify` fix applies.
- **Commits that come back while no verified tree is in force.** A commit a left process makes before the first green round of a chain that then never goes green, after a red or failed round, in a chain with no verify (`adw_plan_build.py`, `adw_build.py`), or after the chain's own commit landed, comes back at the next sync or at `close()` judged by path only, as on the baseline. An agent's commit made during its own call is not one of these: the guards undo it.
- **The reviewer reads the build VM's tree**, not the verified one. A left process can show the reviewer one tree and leave another for the commit; the commit is then refused or equals what verify passed, but the review did not read it.
- **The documenter's commit is not verified.** It is held to the documenter's `writes` (`app_docs/`, `docs/`, any `.md`), so a left process can still change a Markdown file after `commit_build` and have it land there.
- **A path in `wrote`** is not compared, as `judge_commits` does not judge it.
- **A chain a repo wrote itself** that calls `sandbox.verifying` without `plan_checks.verify` marks no round green, so its commits are not bound.
- The usage totals and the dashboard (the new P3 row).
- The text guard. Its patches are frozen.

## Verification

Before the code changes, run the new test file and confirm each plant test and both harm tests fail on the baseline, and the "real run is unchanged" test passes. Say so in the build report.

After, from the repo root, run only the touched files, the way the repo's suite does:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_verify_commit_tree.py tests/test_verify_fresh_vm.py tests/test_sandbox_remote.py tests/test_adw_tickets.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

Add any other test file changed under step 6. All must pass. The whole suite is left to SSSF's verify, which already runs `tests/` and so runs the new file; the plan adds no extra check.

Confirm by reading the diff against `7e2d3c1` that it touches none of: `templates/sssf.config.yaml`, any prompt under `templates/`, `rules.py`, `permissions.py`, `sandbox_openshell.py`, `agentsh_sssf.yaml`, or any other protected guard file not named above.

No test here starts a real VM. The stand-ins share the host's git, so the real-VM proof is the engineer's.

## For the engineer

- Rerun the two 2026-11-26 plants on a real VM (`--sandbox openshell`) after the build: the force-added ignored file and the missing blob with and without a process that puts it back. Agents under agentsh can't start a VM.
- The hub's own `adws/adw_modules/` is a separate copy and differs from the templates. This run does not touch it (`adws/` is protected). Update the hub's SSSF after the merge so its chains get the change. `adws/adw_guard_change.py` needs no change.

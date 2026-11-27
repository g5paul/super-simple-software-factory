# Keep the verified tree bound until the chain ends it

Guard change, run 1befc519. It picks up guard run 3d9f277f, whose round 1 head `680da06` is the code this run starts from. The earlier spec is `specs/3d9f277f_commit-verified-tree_v3.md`; this one replaces its rule for when the verified tree ends. Everything else in that spec (the index copy in `_patch_out`, `_judge_patch` returning the tree, the read methods on `VerifyBox`, `plan_checks.verify`) is already in the code and stays.

## Problem

Under a VM, `Remote._verified` holds the tree of the last green verify round, and `commits_out` refuses a commit that comes back with another tree, except at `wrote` paths. Two things clear it at the baseline, and each is a fault.

1. **A false positive: only the chain's own commit ends the binding** (`commits_out`, `if must_move: self._verified = ""`). In `adw_tickets.py` the last `tNN_commit` lands, `review_fix` leaves no change, and `verify("review", …)` goes green with an empty patch. The binding is then the synced tree. `git_helper.work_in_progress` is false, so `commit_review_fix` is skipped and nothing ends the binding. The documenter's docs-only commit differs at `app_docs/…` and is refused with `PermissionBreach`.
2. **An old hole: a new round clears the binding before it runs** (`open_verify`, first statement `self._verified = ""`). After a green round, a red or failed one leaves nothing bound, so a commit made by a process the builder left running lands with any tree at the next sync or at `close()`. The left process can cause the failed round itself, by making `_patch_out`'s git fail.

Round 2 of 3d9f277f fixed the first by binding nothing when the build VM's patch was empty. Whether the patch is empty is the build VM's word, so a left process could turn the check off (`git config diff.external true`, or good bytes put back while `_patch_out` runs). That code is not on the baseline, and this plan must not bring it back in any form.

## Threat model

The builder runs arbitrary code in the build VM as the VM's user. It owns that VM's worktree, index, object store, `.git/config` and any process it leaves running. So everything the build VM reports is the agent's word: the patch (empty or not), `HEAD`, what `git diff` or `git status` prints, which commits exist, and when. A left process can change any of these at any moment, including while `_patch_out` runs, and can make `_patch_out` fail.

What the agent cannot reach: the host process, the host's object store except through a bundle the host unbundles and hashes itself, and the fresh verify VM (no agent starts there).

So the rule is: **the host alone decides when a commit must hold the verified tree.** The binding changes only at four host events: the chain opens a verify round (`open_verify`), a round's checks pass in the fresh VM (`mark_green`), the host fast-forwards to the chain's own commit (`commits_out` with `must_move`), and the chain says its verify is over (`end_verified`, new). None of them reads the patch's size, a `HEAD`, or any git output from the build VM.

What this guard can't stop at its level:

- The reviewer agents read the build VM's tree, which a left process can dress up.
- `sandbox: none` has no second machine. The agent runs as the engineer's own user there and nothing binds its tree.
- A path in `wrote` is not compared, and the documenter's commit is held to the documenter's `writes` only, so a left process can still change a Markdown file there.

## Baseline

`guard/verify-bind-2` at `2c0de8f`. Its code is `680da06` (round 1 of run 3d9f277f: dafc21f0's fresh VM per verify round, plus 3d9f277f's binding); `2c0de8f` adds only this run's brief. Every finding is judged against `2c0de8f`.

## Bar

Must do, where the baseline does otherwise:

- **The false positive lands.** A `tickets` run where the review fix changes nothing, then a docs-only commit: refused on the baseline, lands after.
- **Red after green is refused.** A green round, then a red or failed round, then a left process's commit with a bad tree: on the baseline it lands at a sync and at `close()`. After, it is refused as a `PermissionBreach`, nothing of it lands, and the bundle is kept as `sandbox-refused.bundle`.
- From a chain's first verify round on, every commit that comes back must hold the tree of the last green round, except at `wrote` paths. A red or failed round does not clear it. A new green round replaces it. When the chain's own commit lands, the binding becomes that commit's tree.
- Before any round has gone green, nothing that comes back after the first round opened lands.
- Commits that came back before the first round opened (the plan commit) are judged by `judge_commits` only, as on the baseline.
- The binding ends only at the chain's own host call (`sandbox.verify_done()`), which first brings back what the VM holds while the binding is still in force.
- An empty patch binds the last sync's tree, read on the host from `self.synced`.

Must never do:

- No decision about whether a commit is checked rests on anything the build VM reports.
- Round 2's three plants are refused: `diff.external` forging an empty patch; good bytes put back while `_patch_out` runs and bad ones after; a left-process commit with a bad tree, then the good bytes put back.
- Round 1's plants stay refused: the force-added ignored file, the missing blob with and without a process putting it back, skip-worktree, a file changed between verify and the commit.
- Ordinary runs are unchanged: for each chain that verifies under a VM, an ordinary change, a rename, a delete and a binary file pass, and each of its commits lands, the documenter's included.
- No agent file kept, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise.
- Nothing an agent may do changes: no edit to the VM's policy, any agent prompt, any rule file, `permissions.py`, or `templates/sssf.config.yaml`.
- No new module-level state. The binding stays one attribute on the `Remote` instance.
- Every existing test passes on the host, except the three named in step 4, which assert the baseline's two faults and are rewritten.

### Which commits are held, per chain

"Bound" means `Remote._verified` is not `None`. `C` is the tree of the chain's last landed commit, `T` the tree of the last green round.

| Chain | Commit | Held to | Where the binding ends |
|---|---|---|---|
| `adw_simple_sdlc.py` | `commit_plan` (`only` the spec, before the first round) | `judge_commits` only | |
| | `commit_build` | `T` of the last green round (`verify_N` or `reverify`) | |
| | `commit_docs` | `judge_commits` only, held to the documenter's `writes` | `sandbox.verify_done()` at the top of the `changes` phase, after `commit_build` |
| | run not verified | no commit; `close()` refuses anything that isn't bound | not ended |
| `adw_tickets.py` | each `tNN_commit` | that ticket's `T`, except the `wrote` paths (the done mark) | |
| | `commit_review_fix` (only when the fix changed something) | the review round's `T` | |
| | the documenter's commit | `judge_commits` only, held to the documenter's `writes` | `sandbox.verify_done()` at the top of the `changes` phase, reached with and without a review fix |
| | a ticket or the review fix stays red (`give_up`) | no commit; `close()` refuses | not ended |
| `adw_build_test.py`, `adw_plan_build_test.py`, `adw_plan_build_test_quality.py` | the commit after the green round | `T` | not ended: no documenter, and `close()` then refuses anything that isn't `C` |
| `adw_quality.py` | none | a commit found at `close()` must hold `T`, or nothing lands if the round was red | not ended |
| guard chain (`adws/adw_guard_change.py`, hub only) | `commit_plan` (through `commit_spec`, before the first round) | `judge_commits` only | |
| | round snapshots | made with `commit-tree`, move no ref, never come back | |
| | `commit_build` | `T` of the last green round | not ended: no documenter, `close()` refuses the rest |
| every chain | any other commit found at a sync, at `verify_done()` or at `close()` while bound | `T` (or `C`), or refused; all refused before the first green | |
| `adw_plan_build.py`, `adw_build.py`, `adw_build_review.py`, `adw_document.py` | every commit | no verify round, never bound: `judge_commits` only, as on the baseline | |

One accepted change against the baseline: after the chain's own commit lands, a later commit that differs from it is refused until the chain calls `sandbox.verify_done()`. A chain a repo wrote itself that verifies, commits, and then commits something else must make that call first. `references/config.md` says so.

## Changes

Write the tests first (step 4) and run them against the unchanged code, so the red is seen.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

**`Remote.__init__`**: `self._verified: str | None = None`. Three values, one attribute:

- `None`: not bound. No verify round has opened in this chain, or the chain ended the binding.
- `""`: bound, and no round has gone green yet. Every commit that comes back is refused.
- a tree id: bound to that tree.

**`open_verify`**: replace the first statement `self._verified = ""` with

```python
if self._verified is None:
    self._verified = ""
```

It stays the first statement, before `_patch_out`, so a round that fails while the patch is taken is still a round that opened. Nothing else in `open_verify` changes: `box = VerifyBox(inner, self, tree)` stays as it is, with `tree` from `_judge_patch`, which already returns the last sync's tree from the host's own `self.synced` for an empty patch. Do not make `tree` depend on `patch`.

**`VerifyBox.mark_green`**: no code change. It replaces the binding with this round's tree.

**`commits_out`**:

- Change the guard `if not refused and self._verified:` to `if not refused and self._verified is not None:`.
- Inside, when `self._verified == ""`: refuse every commit of the line with `(commit, ["no verify round has passed"])`, and set `text` to a sentence that says no verify round has gone green since this chain's first round opened, so no commit made in the VM may land. Run no `diff-tree` in this case.
- Otherwise the comparison is as now.
- In the `sandbox_commits_refused` payload, add `"verified"` when `self._verified is not None` (`self._verified[:12]`, which is `""` before the first green).
- After the merge, replace `if must_move: self._verified = ""` with:

```python
if must_move and self._verified is not None:
    # The chain's own commit landed: what comes back next must hold its tree.
    self._verified = _host_git("rev-parse", f"{tip}^{{tree}}", cwd=self.tree)
```

A `must_move` commit that lands while not bound (the plan commit) leaves `None`.

**New `Remote.end_verified(self) -> None`**, next to `commit`:

```python
def end_verified(self) -> None:
    """The chain's verify is over (sandbox.verify_done). Bring back what the VM holds
    while the binding is still in force, then end it. Raises PermissionBreach, and
    leaves the binding in force, if a commit there doesn't hold the verified tree."""
    from . import permissions
    if self._verified is None:
        return
    self.commits_out()
    self._verified = None
    permissions.commit_passed(self.run_, None)
    self._log("sandbox_verified_end", {"vm": self.vm, "synced": self.synced[:12]})
```

Why each line:

- `commits_out()` first: a bad commit a left process made before the end is refused here, under the binding, and can't ride in with the documenter's commit. If it raises, the binding is not ended, so `close()` refuses again.
- `permissions.commit_passed(self.run_, None)` clears `run.ran` and `run.taken`, as a landed full commit does. Without it, in the `tickets` path with no review-fix commit, `run.ran` still holds `ticket_builder`, and `judge_commits` would let the documenter's commit carry a bad `app.py` a left process wrote after the end. With it, that commit is held to the documenter's `writes`, as the table says. In an honest run nothing of the builder's is uncommitted at this point: the host's last green round was on the tree the host last landed, or the chain committed it. Call the function; do not edit `permissions.py`.

`VerifyBox` gets no `end_verified`.

**Docstrings**: the module docstring's verify bullet (lines 25 to 33), `commits_out`, `open_verify` and `VerifyBox.mark_green`. Each must say: bound from the chain's first round; a red or failed round keeps the last green tree; the chain's own commit moves the binding to its tree; only `end_verified` ends it; before the first green nothing lands.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox.py`

Add one function after `verifying`:

```python
def verify_done() -> None:
    """A chain calls this once its verify is over and before a commit verify did not
    check (the documenter's). Under a VM it brings back what is there, still held to
    the verified tree, then ends the binding (`Remote.end_verified`). Under `Local`
    it does nothing. Raises PermissionBreach."""
    end = getattr(_current, "end_verified", None)
    if end is not None:
        end()
```

Use the module's own name for the current box (`_current`; read the file and match it). Update `verifying`'s docstring where it says when the green round's tree stops binding.

### 3. Chains under `.claude/skills/sssf/templates/adws/`

- `.claude/skills/sssf/templates/adws/adw_simple_sdlc.py`: import `sandbox` from `adw_modules`. Make `sandbox.verify_done()` the first statement inside the `changes` phase (after `commit_build`, before the changeset is read). Update the module docstring's phase line and the paragraph on the three commits: the docs commit is not held to the verified tree.
- `.claude/skills/sssf/templates/adws/adw_tickets.py`: the same call as the first statement inside the `changes` phase in `build_feature`. Import `sandbox` if the file doesn't. Update the docstring's phase line.
- `.claude/skills/sssf/templates/adws/adw_build_test.py`, `.claude/skills/sssf/templates/adws/adw_plan_build_test.py`, `.claude/skills/sssf/templates/adws/adw_plan_build_test_quality.py`, `.claude/skills/sssf/templates/adws/adw_quality.py`: no code change. Add one docstring sentence to each: under a VM the binding is not ended, so `close()` refuses any commit that isn't the verified tree.

Before editing, run a search for `plan_checks.verify` and `sandbox.verifying` over `templates/adws/*.py` and confirm the table above names every chain that has one. If it misses one, add it to the table in the build report and treat it by the same rule.

### 4. Tests

All in `.claude/skills/sssf/tests/`. The config is the shipped `templates/sssf.config.yaml`, which `rig` already loads. A "left process" is the test acting on `rig.vm_tree` outside `_as_builder`.

**`.claude/skills/sssf/tests/test_verify_commit_tree.py`, rewrite three tests** (each asserts a fault of the baseline):

- `test_an_only_commit_before_any_verify_lands`: the last assert becomes `rig._verified is None`.
- `test_after_the_chains_own_commit_the_check_ends_and_a_later_commit_lands` becomes `test_after_the_chains_own_commit_the_binding_is_its_tree_until_the_chain_ends_it`: after `commit_all("build")`, `rig._verified` equals the host `HEAD`'s tree. A docs-only commit is then refused. A second case calls `sandbox.verify_done()` first: `rig._verified is None`, `rig.run_.ran == []` before the documenter is appended, and the docs commit lands.
- `test_opening_a_new_round_forgets_the_earlier_green` becomes `test_a_red_round_keeps_the_earlier_green`: after a green round and a red one, `rig._verified` is the green round's tree.

Search every test file for `_verified` and fix any other assert on `""` the same way.

**New tests, same file.**

Red on the baseline, green after:

1. `test_a_tickets_run_whose_review_fix_changes_nothing_lands_its_docs_commit`: `_setup`, a ticket change through `_as_builder`, verify green, `commit_all("t01", run=…)`. Then the review round: no change, `_verify(rig, CHECK)` green. `sandbox.verify_done()`. Append the documenter to `run.ran`, write `app_docs/x.md`, `commit_all("docs")`. The host `HEAD` holds `app_docs/x.md`. To see the baseline's red for the right reason, first run it on the unchanged code with the `verify_done()` line left out: it must fail with `PermissionBreach` naming `app_docs/x.md`. Then add the line.
2. `test_a_bad_commit_after_a_green_round_and_a_red_or_failed_one_lands_nothing`, parametrized over the second round (`red`: `_verify(rig, "false")`; `failed`: `rig._patch_out` patched to raise `SandboxFailed`, standing for a left process that breaks git) and over where it comes back (`sync`: `rig.commits_out()`; `close`: `rig.close(stop=True)`). After the second round a left process commits a broken `app.py` in `rig.vm_tree`. `PermissionBreach` is raised, the host `HEAD` is the sync, `sandbox-refused.bundle` exists.
3. `test_nothing_lands_before_the_first_green_round`: the first round is red (and a second case: fails in `_patch_out`); a left-process commit, even one whose tree equals the sync's tree plus a docs file, is refused at `close()`.

Round 2's plants (green on the baseline and after; red with round 2's line):

4. `test_a_forged_empty_patch_still_binds_the_last_sync`: `_setup`; a left process breaks `app.py` and runs `git config diff.external true` in `rig.vm_tree`. Verify with `"true"` goes green on the last sync. `commit_all("build")` raises `PermissionBreach`; the host `HEAD` is the sync. If `git add -A` or `git diff --cached` in the stand-in ignores `diff.external`, forge the empty patch by patching `rig._patch_out` to return `b""` and say so in a comment; keep the `diff.external` case as a second parameter if it works.
5. `test_good_bytes_during_the_patch_and_bad_ones_after_land_nothing`: wrap `rig._patch_out` so the synced bytes of `app.py` are on disk while it runs and the broken ones are written back when it returns. Verify is green; `commit_all` raises `PermissionBreach`; nothing lands.
6. `test_a_bad_commit_then_the_good_bytes_back_lands_nothing`: a left process commits a broken `app.py`, then restores the synced bytes. Verify is green with an empty patch. Refused at `commit_all`'s place (nothing to commit, so use `sandbox.verify_done()`) and, in a second case, at `rig.close(stop=True)`.

For 4, 5 and 6, show each fails with round 2's line in place. In this code the same line is `box = VerifyBox(inner, self, tree if patch else None)` in `open_verify`, which ends the binding on a green round with an empty patch. Put it in, run the three tests, see each fail because the bad tree landed, take it out, and say so in the build report. Do not leave it in.

No decision reads the VM:

7. `test_what_the_build_vm_reports_never_turns_the_check_off`, parametrized over what the build VM reports in a round: an honest patch; `_patch_out` returning `b""`; `_patch_out` raising `SandboxFailed`; `diff.external true`; the VM's `HEAD` moved by a left-process commit. In every case `rig._verified is not None` once `open_verify` has been entered, and a later commit that breaks `app.py` (not a `wrote` path) is refused with the host `HEAD` at the sync.
8. `test_only_the_chains_call_ends_the_binding`: after a green round, each of a sync that lands a commit of the verified tree, a red round, a failed round and a landed chain commit leaves `rig._verified is not None`. `sandbox.verify_done()` makes it `None`. `verify_done()` with a bad left-process commit in the VM raises `PermissionBreach` and leaves it bound, and `close(stop=True)` then refuses too.

Ordinary runs, per chain:

9. `test_each_chains_ordinary_run_lands_every_commit`, parametrized over the chain and over `_edit`, `_rename`, `_delete`, `_binary`. Each chain is a small function that makes that chain's host calls in its order against `rig`:
   - `simple_sdlc`: an `only` commit of a spec file (the planner in `run.ran`), the change through `_as_builder`, verify, `commit_all("build")`, `sandbox.verify_done()`, the documenter's `app_docs/x.md`, `commit_all("docs")`, `close(stop=True)`.
   - `tickets`: two tickets, each a change, verify, a done mark written with `rig.write_text` and `commit_all(..., wrote=[mark])`; then a review fix (one case with a change and its commit, one with none); `verify_done()`; the docs commit; `close(stop=True)`.
   - `build_test`: the change, a red round, a fix, a green round, `commit_all`, `close(stop=True)`.
   - `guard`: a spec commit made with `rig.run(["git", "commit", …])` before the first round, the change, verify, a `commit-tree` snapshot through `rig.run` (no ref moves), a second green round, `commit_all("build")` with no `run`, `close(stop=True)`.
   - `quality`: verify, `close(stop=True)`.
   Each asserts every commit landed on the host and no `sandbox-refused.bundle` exists.
10. `test_the_chains_end_the_binding_before_the_documenter`: read the two chain files as text. In `adw_simple_sdlc.py` and `adw_tickets.py`, `sandbox.verify_done()` appears exactly once, after the last `plan_checks.verify(` and the last build or review-fix commit, and before `owner="documenter"`. No other file under `templates/adws/` calls it. This ties test 9's sequences to the chain files, in the manner of `test_no_chain_template_calls_the_blocks_directly`.

**`.claude/skills/sssf/tests/test_adw_tickets.py`**: give `VMTree` an `end_verified` that appends to a list, with `open_verify` and the documenter's call recorded in the same list (use what the `vm` fixture's tests already record). In the existing sandbox run test, or one new test beside it, assert `end_verified` was called once, after the last verify round and before the documenter ran.

**`.claude/skills/sssf/tests/test_verify_fresh_vm.py`** and **`.claude/skills/sssf/tests/test_sandbox_remote.py`**: no change planned. Run them; fix only an assert that held the baseline's `""`.

Round 1's plant tests in `test_verify_commit_tree.py` (force-added file, missing blob twice, skip-worktree, file changed after verify, bad commit then restore, synced commit, bad commit before the round, `wrote`) stay as they are and must pass unchanged.

### 5. Docs

- `.claude/skills/sssf/references/config.md`, "Where verify runs under a VM": replace "That holds until the chain's own commit lands or a new round opens." Say: from a chain's first verify round on, every commit that comes back must hold the tree of the last green round; a red or failed round doesn't lift it, and before the first green round nothing lands; the chain's own commit moves the binding to its tree; the binding ends only when the chain calls `sandbox.verify_done()`. Name the commits that are not held and why: any commit made before the first round (the plan commit; nothing is verified yet), and the documenter's (verify did not check it; it is held to the documenter's `writes`, so a left process can still change a Markdown file there). Say the decision never uses what the build VM reports, an empty patch included, which binds the last sync's tree. Add to "What changes for a repo": a chain a repo wrote itself that commits again after its verified commit must call `sandbox.verify_done()` first, or the commit is refused.
- `docs/open.md`: reword the P3 row that starts "Verify VMs are missed in the usage totals and the dashboard, and the verify-commit guard leaves edges": drop the edge this run closes (a commit after a red or failed round, and one found after the chain's own commit), keep the usage and dashboard part and the edges still open (see Out of scope), and link this spec beside the old one. Search the file for any other row that says the binding ends at the chain's commit or at a new round, and correct it. Do not touch the `sandbox: none` `/tmp/conftest.py` row.
- `docs/designs/remote-sandboxes.md`, the paragraph near line 201 on `open_verify`: the same facts as config.md, in two or three sentences.
- `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`, `commit_all`'s docstring, and `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py`, `verify`'s docstring: where each says when the verified tree binds or ends, match the new rule. Docstrings only.
- The repo keeps no CHANGELOG. `README.md` and `SKILL.md` don't describe this.
- For the documenter: any `app_docs/` write-up of run 3d9f277f or dafc21f0 that says the verified tree ends at the chain's own commit or when a new round opens describes old behaviour. Note it in the new write-up; do not edit the old one.

## Out of scope

- **The documenter's own commit**, beyond `judge_commits`. It is held to the documenter's `writes` (`app_docs/`, `docs/`, any `.md`), so a left process can still change a Markdown file after `verify_done()` and have it land.
- **A run with no verify round** (`adw_plan_build.py`, `adw_build.py`, a `tickets` rerun where every ticket is done and the review blocks on nothing): never bound, `verify_done()` does nothing, commits are judged by path, as on the baseline.
- **A path in `wrote`** is not compared, and the binding after the chain's commit takes that path's bytes from the commit.
- **The reviewers read the build VM's tree**, not the verified one.
- **A chain a repo wrote itself** that calls `sandbox.verifying` without `plan_checks.verify` marks no round green; under this change its commits after the round are all refused, not unbound. One that never calls `verify_done()` has its later commits refused.
- **`sandbox: none`**, and its `/tmp/conftest.py` row.
- The usage totals and the dashboard (the P3 row).
- The text guard. Its patches are frozen.

## Verification

Before the code changes, write the step 4 tests and run the file. Report which fail on the baseline: tests 1 (with the `verify_done()` line left out, failing with `PermissionBreach` at `app_docs/x.md`), 2 and 3 must be red; 4, 5 and 6 must be green on the baseline once `verify_done` exists, and red with round 2's line (`tree if patch else None`) put in after the change.

After, from `.claude/skills/sssf`, run only the touched files:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_verify_commit_tree.py tests/test_verify_fresh_vm.py tests/test_sandbox_remote.py tests/test_adw_tickets.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

All must pass. Add any other test file the `_verified` search made you change. The whole suite is left to SSSF's verify, which runs `tests/` and so runs the new tests; the plan adds no extra check.

Read the diff against `2c0de8f` and confirm:

- it touches none of `templates/sssf.config.yaml`, any prompt under `templates/`, `rules.py`, `permissions.py`, `sandbox_openshell.py`, `agentsh_sssf.yaml`, or anything under `adws/`, `specs/` or `app_docs/`;
- `open_verify` has no expression that depends on `patch` other than the two that were there (`inner._must("git", ["apply", …])` under `if patch:` and `patch_bytes` in the event);
- `self._verified` is assigned in exactly four places: `__init__`, `open_verify`, `commits_out` (under `must_move`), `end_verified`, plus `VerifyBox.mark_green`;
- no new module-level name holds state in `sandbox_remote.py` or `sandbox.py`.

No test here starts a real VM. The stand-ins share the host's git, so the real-VM proof is the engineer's.

## For the engineer

- The hub's own `adws/` is a separate copy of the templates and is protected, so this run does not touch it. After the merge, update the hub's SSSF so `adws/adw_modules/sandbox_remote.py`, `adws/adw_modules/sandbox.py`, `adws/adw_simple_sdlc.py` and `adws/adw_tickets.py` get the change. Until then the hub's chains run the old rule.
- `adws/adw_guard_change.py` needs no edit: it commits the spec before its first round, its snapshots move no ref, its one later commit is `commit_build` after the last green round, and it has no documenter, so `close()` refuses the rest. If a documenter is ever added to it, it needs `sandbox.verify_done()` before that phase.
- On a real VM (`--sandbox openshell`), rerun the reviewer's probes: a `tickets` run whose review fix changes nothing (the docs commit must land), `git config diff.external true` with a bad `app.py` (must be refused), and a left process that commits a bad tree after a red round (must be refused at `close()`). Agents under agentsh can't start a VM.

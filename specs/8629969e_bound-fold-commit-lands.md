# Bound the fold, and stop a VM commit that doesn't land

Follows `specs/tasks/guard-host-commit-check-2.md` and `specs/8dee7020_narrow-host-check_v2.md` (merged as d1e6011); their bars carry over. Closes two P2 Medium rows in `docs/open.md`, found by the `bypass-reviewer` subagent. Its probes are in `adws/adw_data/sessions/8dee7020/bypass_probes_2/` in the main checkout, outside the worktree: `longname_judge.py`, `nfd_cost.py`, `perm_speed.py`, `test_zz_probe_8dee_vm.py`.

## Goal

1. `permitted()` refuses a path with an over-long component before any fold, so the fold's cost per code point of path has a fixed ceiling.
2. A `git commit` that `Remote.commit` runs, that exits 0 and leaves the VM's HEAD at `synced`, stops the run.

All module paths are under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

## What the planner found: the stall does not reproduce on this machine

Read this before writing the tests.

- `nfd_cost.py`, run on this worktree at `8f4321e`, takes 0.016 s for `_fold_part` on 160,000 marks. The review recorded 28 s.
- `permitted()` on `longname_judge.py`'s name at 20,000 marks takes 0.03 s for the builder. The review recorded 8.0 s per path.
- The Python here is 3.12.3, built 2026-08-31, Unicode 15.0, both as `python3` and as `uv run python`. Its NFD is linear on these names. Which Python the review ran on was not found.

So the quadratic cost belongs to some Python builds and not to this one. The change is still needed: the host's Python is whatever `uv` picks, and the guard must not depend on its NFD being fast. But a test that only times the refusal passes here with no fix. Each timing test therefore also proves the cap by structure: it wraps `permissions._fold_part` and asserts no call got a component over the cap (step 3).

## Threat model

- The agent runs as the engineer's user. In a VM run it controls the whole VM: every in-VM check, the clone's `.git` and its hooks, and each answer the VM gives, including the exit code of `git commit` and the answer to `git rev-parse HEAD`. It cannot reach the host's `.git`.
- It can write a tree entry with any name and any length with `git mktree`, and send any number of paths. The host's time in `judge_commits` is the agent's to spend, in proportion to what it sends. This change can't stop that; it can only keep the cost linear, with a constant close to an ordinary path's.
- A hook that undoes a commit needs a write inside the VM's `.git`, past the in-VM git directory guard: the same condition as every host check finding.
- The host can't make a VM commit land. It can only refuse to go on when one didn't. A VM that lies about HEAD to `commits_out` gains nothing new: a lie of "moved" leads to a bundle the host judges, and a lie of "not moved" is what this change refuses.
- In local mode the agent controls the `.git` the check reads; 755d88ad's limits stand.

## Baseline

`main` at `8f4321e`. Every finding is judged against that commit. There:

- `permitted()` calls `_folds_to`, and so `_fold_part`, on the whole path for each of the 18 fixed and `protected_files` entries, for each agent in `run.ran`, with no limit on a component's length.
- `Remote.commits_out` returns at once when the VM's HEAD equals `synced`, whoever called it. A plan commit undone by a `post-commit` hook records nothing in `run.taken`, and the builder's rewrite of `specs/old.md` lands in the next full commit (`test_vm_plan_commit_that_does_not_land`, `post-commit-reset`: "PASS landed"). A full commit undone the same way leaves `run.ran` uncleared.

## Bar

Against `8f4321e`: no agent file kept on the host branch, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise.

1. **The hang is bounded.** `longname_judge.py`'s name at 4,000, 20,000 and 200,000 marks, through `judge_commits` and through the VM stand-in: each is refused within a second, the branch stays at `synced`, and `_fold_part` is never called with a component over the cap.
2. **The cap refuses nothing a disk could take.** A component at the cap, ASCII and multi-byte, gets the answer the baseline gives. One past it is refused for every agent, including one whose `writes` names it, and under the session folder.
3. **The plan commit that doesn't land.** With `test_vm_plan_commit_that_does_not_land`'s hook, on both providers' stand-ins: the run stops at the plan commit with a `PermissionBreach`, the host branch is at `synced`, and `specs/old.md` never changes on the host. The same hook on a full commit stops the run there.
4. **No false stop.** Every existing test passes. Every shipped chain's normal commits pass as on the baseline, and `close()` with nothing new to bring back raises nothing and logs no new event.
5. **Nothing else changes.** When every commit passes: same branch, same trace events, same saved patch. The one new event appears only on the new refusal.

## The cap

A name's limit on each system the host's checkout could be on:

| System | Limit | Source |
|---|---|---|
| Linux (ext4, xfs, btrfs, tmpfs) | 255 bytes | `NAME_MAX 255` in `include/uapi/linux/limits.h`; read here at `/usr/include/linux/limits.h` line 12 |
| macOS, APFS | 255 UTF-8 characters, so up to 1,020 bytes | Apple File System Reference, `j_drec_hashed_key_t`: the name length is the low 10 bits of `name_len_and_hash` (`J_DREC_LEN_MASK`), which fits 255 four-byte characters. From memory, not re-read |
| macOS, HFS+ | 255 UTF-16 units | Apple TN1150, `HFSUniStr255`. From memory, not re-read |
| Windows, NTFS (also exFAT and FAT long names) | 255 UTF-16 units | Microsoft, "Naming Files, Paths, and Namespaces"; `GetVolumeInformation`'s `lpMaximumComponentLength`. An NTFS stream name has the same limit. From memory, not re-read |

A code point is at least one byte and at least one UTF-16 unit, so the largest of these is **255 code points**. That is the cap, `NAME_MAX = 255`, measured with `len()` on a Python `str`. The builder checks the three "from memory" sources if it can reach them, and marks any it can't "(not re-read)" in the code comment. If APFS turns out to count bytes, the cap is only more generous than it needs to be, and the cost bound below is the same.

**What is measured.** The path is split on `/`, `\` and `:`, and each piece is measured. `\` is a separator on Windows and `:` starts an NTFS stream, so `adws\x` or `name:stream` can be longer than 255 as one Linux name and still be taken by NTFS. A path with any piece over 255 code points is over-long. Paths from git are decoded with `surrogateescape`, where an undecodable byte is one code point, so a 255-byte name of any bytes is never over the cap.

**Why this bounds the fold.** `_fold` splits on `/` and `\` before it normalizes, and `/`, `\` and `:` are starters (combining class 0). So no run of combining marks that reaches NFD is longer than 255 code points, which is at most 510 marks after decomposition (U+0344 decomposes to two marks). The cost of a path is then linear in its length.

**Worst-case cost under the cap**, measured here with the shipped config (18 entries, 8 agents), by a script outside the repo:

| Path | Builder alone | All 8 agents in `run.ran` |
|---|---|---|
| An ordinary 34-character path | 0.13 ms | 1.1 ms |
| One component of 255 code points, worst found (`U+1F82` × 255) | 1.7 ms | 13.5 ms |
| 128 such components, 32,767 code points | 191 ms | 1.55 s |

- Per code point, the worst path costs about 1.4 times an ordinary one (47 µs against 33 µs, all 8 agents). The agent gets no leverage from the name's content.
- On a Python with the quadratic NFD, add the reorder cost. From the review's 28 s for 160,000 marks, one run of 510 marks costs about 0.3 ms per `_fold_part` call, so about 5 ms more per component per agent. This is derived, not measured.
- **A walk of `COMMITS_MAX` (256) commits.** Neither the number of paths in a commit nor a path's total length is capped, on the baseline or here, so the walk has no fixed ceiling. It is linear in the code points of the paths sent. For scale: 256 commits that each add 1,000 worst one-component names cost about 7 minutes for the builder alone (256,000 × 1.7 ms), against 34 s for as many ordinary names. Before 8dee7020 the same count cost about 4 s. That ratio is 8dee7020's per-entry fold, which the brief leaves out of scope.

## Design notes for the reviewer

- **The cap sits first in `permitted()`**, before the always-writable test, so nothing unlocks an over-long path: not `writes`, not `writes: None`, not the session runtime. No disk holds such a name, so no real session file is refused. Every caller of `permitted()` is covered by the one line: `enforce`, `ref_moves`, `Remote._patch_note`, `adw_tickets.py`.
- **`judge_commits` refuses it beside `_in_git_dir`.** Without that, the session-folder, `wrote` and `carried` clauses could still pass an over-long path, and `_in_carried` would get the name as a git argument. It only refuses.
- **The stop is on `Remote.commit` alone**, as the brief says. `commits_out` gets a keyword, `must_move`, which only `commit` sets. `close()` and a plain `run` of `git commit` keep the early return.
- **Refuse, not record.** The row in `docs/open.md` offers "record `taken`" as the other fix. A commit that exits 0 and doesn't land means the VM's `.git` was changed, and the brief asks for the stop.
- **Nothing to keep.** HEAD equals `synced`, so there is no bundle to save. The planner's work is still in the VM's tree, and `close()` saves it as the patch, not applied.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: the cap

- Beside `_SHORT`: `NAME_MAX = 255`, with a comment that gives the table above in three lines, and
  `_too_long(path) -> bool`: true when any piece of `re.split(r"[/\\:]", path)` has `len() > NAME_MAX`.
- `permitted()`: first statement, `if _too_long(path): return False`. Extend the docstring: a path with a component no disk takes is refused for every agent, before any fold.
- `judge_commits`: `ok = not _in_git_dir(path) and not _too_long(path)`. Nothing else in it changes.
- `_fold_part` docstring: one sentence, that `permitted()` never passes it a component over `NAME_MAX`.
- Module docstring, the commit-check paragraph: after "A path with a `.git` component never passes", add that a path with a component over 255 code points never passes either.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`: the commit that doesn't land

- `commits_out(self, only=None, wrote=(), *, must_move: bool = False)`. Where `head == self.synced`:
  - `must_move` false: `return`, as now.
  - `must_move` true: log `sandbox_commit_not_landed` with `type="error"` and payload `{"vm": self.vm, "synced": self.synced[:12], "only": list(only) if only else None}`, then raise `permissions.PermissionBreach`: "`git commit` exited 0 in the VM but its HEAD is still at the last sync (`<synced[:12]>`), so the commit didn't land and the run stops; the host branch is still at `<synced[:12]>`." `run.ran` and `run.taken` are not touched.
- `commit()`: `sync=lambda: self.commits_out(only, wrote, must_move=True)`. Docstring: a commit that exits 0 and doesn't move the VM's HEAD is refused.
- `_run`'s default `sync` and `close()` are not changed.
- Docstring of `commits_out`, and the module docstring's "After each `git commit`" bullet: one sentence each on the stop.

### 3. Tests

Config from `.claude/skills/sssf/templates/sssf.config.yaml` always; a test that needs another `writes` entry copies the loaded config and changes that field. No real VM. Build every long name inside the test and feed it to `git mktree` on stdin (`input=`), as the probes do; never put it in an argv. No test here passes it as an argument, so none needs `host_only`; mark one that does.

Shared helper in each file, `watch_fold(monkeypatch)`: wraps `permissions._fold_part`, records the longest `part` it was given, and returns the record. "Structure" below means `max(record, default=0) <= permissions.NAME_MAX`.

- `.claude/skills/sssf/tests/test_factory_files.py`:
  - `_too_long`: 255 `a` is not; 256 is; 255 × `é` (510 bytes) is not; 255 × `U+1F600` (1,020 bytes) is not; 256 of each is; `"a" * 200 + "\\" + "b" * 200` and `"a" * 200 + ":" + "b" * 200` are not; a 256 piece after `\` or `:` is.
  - bar 2: for `src/<component>` and `specs/<component>` with each at-cap component above, `permitted()` equals a copy of the baseline's answer for the shipped builder, planner, documenter and a `writes: None` agent. Reuse the baseline copy 8dee7020's "no new pass" test keeps in this file if it has one; otherwise compare with the same path whose component is one shorter.
  - bar 2: one past the cap is refused for every shipped agent, for `writes: None`, for an agent whose `writes` is `["**", "src/", "src/<that name>"]`, and at `adws/adw_data/sessions/abc/<that name>`.
  - bar 1, structure: `permitted()` on the 4,000, 20,000 and 200,000 mark names with `watch_fold`: refused, and `_fold_part` was not called at all.
  - the invariant: a path of 40 components that alternate 255 marks of class 230 and 255 of class 220 is not over-long, and `watch_fold` shows no part over the cap.
- `.claude/skills/sssf/tests/test_commit_check.py`:
  - bar 1 through `judge_commits`, parametrized over 4,000, 20,000 and 200,000 marks, built as `longname_judge.py` does (`hash-object -w`, `ls-tree`, `mktree` on stdin, `commit-tree`): refused for the builder, the refusal names the commit, under 1 s by `time.perf_counter()`, and structure holds.
  - the same commit judged with `session=` set to a folder the long name sits under (a second tree built with `mktree` twice): refused.
  - a name at the cap in `src/`: passes for the builder as on the baseline.
- `.claude/skills/sssf/tests/test_sandbox_remote.py`, with `jbox` (both providers):
  - bar 1: `test_vm_longname`'s setup at 4,000, 20,000 and 200,000 marks, then `jbox.commit(["git", "commit", "--allow-empty", "-qm", "build"])`: `PermissionBreach`, under 1 s, host HEAD and `jbox.synced` unchanged, `sandbox-refused.bundle` kept, structure holds. If host git itself refuses the tree at unbundle, that is still a `PermissionBreach`; keep the assertions.
  - bar 3, plan commit: plant the probe's hook in `jbox.vm_tree / ".git/hooks/post-commit"`, set `ran=[planner]`, write `specs/old.md`, `git add -A`, then `jbox.commit(..., only=["specs/"])` raises `PermissionBreach` matching "didn't land". Assert host HEAD equals `synced`, `sandbox_commit_not_landed` is in the events, `sandbox_commits` is not, `run.taken` is empty, `run.ran` is `[planner]`, and `specs/old.md` is not in the host tree or host HEAD. Then `jbox.close()` raises nothing, and the host still has no `specs/old.md`.
  - bar 3, control: no hook, the same plan commit passes; append the builder; its rewrite of `specs/old.md` is refused (already covered by `test_a_vm_builder_change_to_a_plan_committed_spec_is_refused`; add only what is missing).
  - bar 3, full commit: the hook, then a builder change to `app2.py` and `jbox.commit(["git", "commit", "-qm", "build"])`: `PermissionBreach`, host at `synced`, `run.ran` unchanged, the event logged.
  - bar 4: after a passing commit, `jbox.close()` raises nothing and logs no `sandbox_commit_not_landed`. A plain `jbox.run(["git", "commit", ...])` under the hook returns exit 0 and raises nothing, as on the baseline (pins the out-of-scope door).
  - bar 5: `test_a_clean_commit_changes_nothing_else` still passes unchanged.

### 4. Docs

- `.claude/skills/sssf/references/config.md`, "Write permissions, `writes` and `protected_files`":
  - drop the two sentences "A plan commit that doesn't move the branch ends no grant: in a VM run, a hook that undoes the commit leaves the planner's grant in place." Put in their place: in a VM run, a commit that exits 0 and leaves the VM's branch where it was stops the run (`sandbox_commit_not_landed`), for a plan commit and a full one.
  - in the paragraph that starts "A protected path is matched in any case", add: a path with a component over 255 code points, measured between `/`, `\` and `:`, is refused for every agent whatever its `writes`, since no Linux, macOS or Windows disk takes such a name.
  - in the known limits, add the new Out of scope items below.
- `docs/open.md`: delete the two P2 rows (the long filename stall; the plan commit that doesn't land), as the file's header says. Leave the `_in_git_dir` row and the others.
- `docs/designs/remote-sandboxes.md`: one "As built" line for each change, where 8dee7020's line is.
- The repo keeps no `CHANGELOG.md`.
- For the documenter: an `app_docs/` write-up of 8dee7020 or 755d88ad that says a plan commit that doesn't move the branch ends no grant, or that a no-op VM commit is passed over, now describes old behaviour.

## Verification

From `.claude/skills/sssf`:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -m 'not pushes and not host_only' tests/test_commit_check.py tests/test_sandbox_remote.py tests/test_factory_files.py tests/test_factory_holes.py tests/test_agent_commits.py tests/test_git_dir_guard.py tests/test_tickets.py tests/test_adw_tickets.py`

All pass. The whole suite is verify's. Every new test is in a file the suite already runs, so the plan adds no extra check and no new dependency.

Also run the two probes against the built tree, from the main checkout's probe folder, and put their output in the report: `nfd_cost.py` (unchanged cost, since it calls `_fold_part` directly) and `longname_judge.py` at 4,000 and 20,000 marks (verdict "refused").

## Out of scope

Old holes a reviewer files as such:

- The fold runs once per entry per agent, so an ordinary path costs about 15 times what it did before 8dee7020. A commit with very many paths, or very long ones, still holds the host in proportion to what it sends (the table above). No cap on paths per commit or on a path's total length.
- A plain `run` of `git commit` through the box, not `Remote.commit`, keeps the early return. No shipped chain commits that way.
- A VM that answers `git rev-parse HEAD` falsely. The host judges whatever bundle follows.
- The `sandbox_commits_refused` and `commit_refused` events hold each refused path in full, however long, as on the baseline.
- The builder undoing the plan commit under a dirty launch tree (P3, local mode), and `_in_git_dir` lacking the fold (P3).
- A filesystem that takes a name over 255 code points. None of Linux, macOS or Windows does.
- Making `sandbox: openshell` the default.

Known false stops, none hit by a shipped chain:

- A chain that runs `git commit --dry-run`, or any `git commit` that exits 0 without making a commit, through `Remote.commit`.

## For the engineer

- The stall did not reproduce here: this machine's Python 3.12.3 (built 2026-08-31) normalizes 160,000 marks in 16 ms. The cap is still worth having, since SSSF doesn't pin the host's Python. If the review ran on another Python, naming it in `docs/open.md`'s history would help the next reviewer.
- The cap is 255 code points, not the 255 bytes the `docs/open.md` row suggested, because APFS and NTFS take non-ASCII names longer than 255 bytes.
- The per-entry fold is now the larger cost: folding a path once per call of `permitted()`, not once per entry, would cut it about 18 times. Worth a row in `docs/open.md` if you want it; the builder is not asked to add one.
- `adws/`: run `just sssf-update` after the merge, so the hub's own chain gets both changes.

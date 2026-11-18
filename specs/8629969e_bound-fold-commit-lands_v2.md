# Bound the fold, and stop a VM commit that doesn't land (v2)

This replaces `specs/8629969e_bound-fold-commit-lands.md`. Spec review 1 approved that plan with four notes, and a first build of it is in the worktree, uncommitted. What changed here:

- The baseline is `main` at `24f1d30`, not `8f4321e`. The only difference between the two is one unrelated row in `docs/open.md`.
- The design is the same. The first build's code already matches it, so the builder keeps it and makes the changes marked **New in v2** below.
- Spec review 1's notes are folded in: the `_fold_part` docstring and the structure check speak of runs between `/`, `\` and `:`; the hook and the long-name setup are written out; a hook that moves HEAD below `synced` gets a test; the derived cost is corrected.
- The first bypass review did not finish. Its probes are in `adws/adw_data/sessions/8629969e/context_handoff/bypass_review_1/` in the main checkout. Its colon probe and its hook variants become tests here.

Follows `specs/tasks/guard-host-commit-check-2.md` and `specs/8dee7020_narrow-host-check_v2.md` (merged as d1e6011); their bars carry over. Closes two P2 Medium rows in `docs/open.md`. The 8dee7020 probes are in `adws/adw_data/sessions/8dee7020/bypass_probes_2/` in the main checkout.

## Goal

1. `permitted()` and `judge_commits` refuse a path with an over-long piece before any fold, so the fold's cost is linear in the path's length.
2. A `git commit` that `Remote.commit` runs, that exits 0 and leaves the VM's HEAD at `synced`, stops the run.

All module paths are under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

## The stall does not reproduce on this machine

- `nfd_cost.py` on this tree takes 0.016 s for `_fold_part` on 160,000 marks; the 8dee7020 review recorded 28 s. Python here is 3.12.3, built 2026-08-31, as `python3` and as `uv run python`. Which Python the review used was not found.
- So a test that only times the refusal passes with no fix. Every long-name test also checks structure (step 3).

## Threat model

- The agent runs as the engineer's user. In a VM run it controls the whole VM: every in-VM check, the clone's `.git` and hooks, and each answer the VM gives, including `git commit`'s exit code and `git rev-parse HEAD`. It cannot reach the host's `.git`.
- It can write a tree entry of any name and length with `git mktree`, and send any number of paths. Host time in `judge_commits` is the agent's to spend in proportion to what it sends. This change keeps that cost linear; it can't cap it.
- A hook that undoes a commit needs a write inside the VM's `.git`, past the in-VM git directory guard.
- The host can't make a VM commit land; it can only stop when one didn't. A VM that says HEAD moved sends a bundle the host judges. A VM that says it didn't is what this change refuses.
- In local mode the agent controls the `.git` the check reads; 755d88ad's limits stand.

## Baseline

`main` at `24f1d30`. Every finding is judged against that commit. There:

- `permitted()` runs `_fold_part` on the whole path for each of the 18 fixed and `protected_files` entries, for each agent in `run.ran`, with no length limit.
- `Remote.commits_out` returns at once when the VM's HEAD equals `synced`, whoever called it. A plan commit undone by a `post-commit` hook records nothing in `run.taken`, and the builder's rewrite of `specs/old.md` lands (`bypass_review_1/vm_base.txt`: soft, hard and mixed reset and a detach at `synced` all read "then-build PASS").

## Bar

Against `24f1d30`: no agent file kept on the host branch, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise.

1. **The hang is bounded.** `longname_judge.py`'s name at 4,000, 20,000 and 200,000 marks, through `judge_commits` and the VM stand-in: refused within a second, branch at `synced`, and `_fold_part` not called on it.
2. **The cap refuses nothing a disk could take.** A piece at the cap, ASCII and multi-byte, gets the baseline's answer. One past it is refused for every agent, including one whose `writes` names it, and under the session folder.
3. **The commit that doesn't land.** With each hook in step 3, on both providers' stand-ins: the run stops at the plan commit, the host branch is at `synced`, and `specs/old.md` never changes on the host. The same on a full commit.
4. **No false stop.** Every existing test passes, every shipped chain's normal commits pass as on the baseline, and `close()` with nothing new raises nothing and logs no new event.
5. **Nothing else changes.** When every commit passes: same branch, same trace events, same saved patch.

## The cap

| System | A name's limit | Source |
|---|---|---|
| Linux (ext4, xfs, btrfs, tmpfs) | 255 bytes | `NAME_MAX 255`, read here at `/usr/include/linux/limits.h` line 12 |
| macOS, APFS | 255 UTF-8 characters, up to 1,020 bytes | Apple File System Reference, `j_drec_hashed_key_t`, `J_DREC_LEN_MASK`. From memory, not re-read |
| macOS, HFS+ | 255 UTF-16 units | Apple TN1150, `HFSUniStr255`. From memory, not re-read |
| Windows, NTFS, exFAT, FAT long names | 255 UTF-16 units; an NTFS stream name the same | Microsoft, "Naming Files, Paths, and Namespaces". From memory, not re-read |

A code point is at least one byte and one UTF-16 unit, so the largest is **255 code points**: `NAME_MAX = 255`, measured with `len()`. If APFS counts bytes, the cap is only more generous than needed.

The path is split on `/`, `\` and `:` and each piece is measured, since `\` separates on Windows and `:` starts an NTFS stream. All three are starters, so no run of combining marks that reaches NFD is longer than 255 code points before decomposition. `_fold` splits on `/` and `\` only, so `_fold_part` can still get a long argument made of `:`-joined pieces; its cost is linear in that length.

**Cost under the cap**, shipped config (18 entries, 8 agents), measured here:

| Path | Builder alone | All 8 agents |
|---|---|---|
| Ordinary, 34 characters | 0.13 ms | 1.1 ms |
| One piece of 255 code points, worst found (`U+1F82` × 255) | 1.7 ms | 13.5 ms |
| 128 such components, 32,767 code points | 191 ms | 1.55 s |
| 800 pieces of 255 marks joined by `:`, 204,805 code points (the reviewer's `colon_head.txt`) | 0.40 s | 3.3 s |

- The colon path costs the same on the baseline (`colon_base.txt`: 0.37 s and 3.1 s). Per code point every row is within about 1.5 times an ordinary path.
- On a Python with quadratic NFD, add about 10 ms per piece per agent: derived from the review's 28 s for 160,000 marks, two NFD passes, 18 entries. Not measured.
- **A walk of `COMMITS_MAX` (256) commits** has no fixed ceiling, here or on the baseline: paths per commit and a path's total length are not capped. It is linear in the code points sent. For scale, 256 commits that each add 1,000 worst one-piece names cost the builder alone about 7 minutes, against 34 s for ordinary names.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

Already in the tree, keep: `NAME_MAX`, `_too_long`, the first statement of `permitted()`, the `judge_commits` clause `not _in_git_dir(path) and not _too_long(path)`, the module docstring sentence.

**New in v2:** the `_fold_part` docstring's first sentence is wrong. Replace it: in a part `permitted()` passes here, no run between `:` is over `NAME_MAX` code points, so the cost is linear in the part's length.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

Already in the tree, keep: `commits_out(..., must_move=False)`, the `sandbox_commit_not_landed` event and `PermissionBreach`, `commit()` passing `must_move=True`, the docstrings. `_run`'s default `sync` and `close()` stay unchanged. No change in v2.

### 3. Tests

Config from `.claude/skills/sssf/templates/sssf.config.yaml` always. No real VM. Build long names inside the test and give them to `git mktree` on stdin; never in an argv. Keep the first build's tests, with these changes.

**New in v2, structure.** The helper that wraps `permissions._fold_part` records, for each call, `max(len(piece) for piece in part.split(":"))`. "Structure holds" means that maximum is at most `NAME_MAX`. For the three long names it also means no call at all.

**New in v2, the setups written out:**

- The long name: `"src" + "́" * (n // 2) + "̖" * (n // 2) + ".py"`. In the repo (or `jbox.vm_tree`): `git hash-object -w a.txt`; `git ls-tree HEAD` plus the line `100644 blob <sha>\t<name>\n` to `git mktree` on stdin; `git commit-tree <tree> -p HEAD -m long`. For the VM, `git update-ref HEAD <commit>`, then `jbox.commit(["git", "commit", "--allow-empty", "-qm", "build"])`.
- The hook, at `jbox.vm_tree / ".git/hooks/post-commit"`, mode 0755: `#!/bin/sh`, then one action line, then `rm -f "$0"`.

Tests by file:

- `.claude/skills/sssf/tests/test_factory_files.py`: add the colon path. `"src/x:" + ":".join(["́" * 255] * 80)` is not over-long, `permitted()` gives the builder the answer it gives `src/x`, and structure holds.
- `.claude/skills/sssf/tests/test_commit_check.py`: no change beyond the structure helper.
- `.claude/skills/sssf/tests/test_sandbox_remote.py`:
  - parametrize the plan-commit test over the action line: `git reset -q --soft HEAD~1` (the brief's), `git reset -q --hard HEAD~1`, `git checkout -q --detach HEAD~1`. Each: `PermissionBreach` matching "didn't land", host HEAD at `synced`, `sandbox_commit_not_landed` logged, `run.taken` empty, no `specs/old.md` on the host, and `jbox.close()` raises nothing.
  - below `synced`: land one passing commit first, then the hook `git reset -q --soft HEAD~2` on a plan commit. Expected by reading the code, not run: `commit` raises `SandboxFailed` (the bundle is empty), host HEAD at `synced`, `run.taken` empty. If it raises something else that also stops the run, pin what it raises and say so in the report.
  - keep the full-commit, plain-`run` and `close()` tests.

No hook in a test runs `git commit`: the reviewer's base probe of that shape hung for 180 s in the stand-in.

### 4. Docs

Already in the tree, keep: `.claude/skills/sssf/references/config.md` (the stop replaces d1e6011's sentence; the cap in "Write permissions"; the known limits), `docs/designs/remote-sandboxes.md`, and the two rows deleted from `docs/open.md`.

**New in v2:**

- `config.md`: where it names the cap, say it is measured between `/`, `\` and `:`.
- `docs/open.md`: touch nothing but the two deleted rows. `main` changed the agentsh event-read row after this branch was cut, and an edit there would conflict.
- The repo keeps no `CHANGELOG.md`.
- For the documenter: an `app_docs/` write-up of 8dee7020 or 755d88ad that says a plan commit that doesn't move the branch ends no grant now describes old behaviour.

## Verification

From `.claude/skills/sssf`:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -m 'not pushes and not host_only' tests/test_commit_check.py tests/test_sandbox_remote.py tests/test_factory_files.py tests/test_factory_holes.py tests/test_agent_commits.py tests/test_git_dir_guard.py`

All pass. The whole suite is verify's. Every new test is in a file the suite already runs, so the plan adds no extra check and no new dependency. The planner did not run this command for v2.

## Out of scope

Old holes a reviewer files as such:

- The fold runs once per entry per agent, so a commit with very many or very long paths still holds the host in proportion to what it sends, `:`-joined pieces included. No cap on paths per commit or on a path's total length.
- A hook that undoes the commit and makes another, so HEAD moves. The host judges that bundle as any other, and `run.taken` is recorded for the `only` entries. Not tested here.
- A plain `run` of `git commit` through the box keeps the early return. No shipped chain commits that way.
- A VM that answers `git rev-parse HEAD` falsely.
- The refusal events hold each refused path in full, however long, as on the baseline.
- The builder undoing the plan commit under a dirty launch tree (P3, local mode), and `_in_git_dir` lacking the fold (P3).
- Making `sandbox: openshell` the default.

Known false stop, hit by no shipped chain: a `git commit` that exits 0 without making a commit (`--dry-run`) through `Remote.commit`.

## For the engineer

- The stall did not reproduce on this machine's Python. The cap is still worth having, since SSSF doesn't pin the host's Python.
- The cap is 255 code points, not the 255 bytes the `docs/open.md` row suggested: APFS and NTFS take non-ASCII names longer than 255 bytes.
- The branch was cut at `8f4321e`; `main` is one `docs/open.md` row ahead. The merge should be clean.
- Folding a path once per `permitted()` call, not once per entry, would cut the remaining cost by roughly the entry count (an estimate). Worth a row in `docs/open.md` if you want it.
- `adws/`: run `just sssf-update` after the merge.

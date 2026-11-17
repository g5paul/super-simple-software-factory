# See an agent's change staged only in the index, second run

This finishes `specs/tasks/guard-index-only-staged.md` under the second brief, `specs/tasks/guard-index-only-staged-2.md`. It starts from guard run 64bd47ec's spec and code and adds three things: the rename fix in the plan commit, a compare of the index with `HEAD` that a forged cache-tree can't fool, and the two old holes named in the docs.

## What the planner could and couldn't read

- **Read:** the first brief, run 64bd47ec's spec (`git show sssf/64bd47ec:specs/64bd47ec_index-staged-check_v2.md`), its report and both reviews, and its code as commit `69a33f7` ("sssf(64bd47ec): guard change, round 2", parent `edbaebb`), which is in this repo's object store. That is the head review 2 judged.
- **Not read:** `adws/adw_data/worktrees/64bd47ec` itself. This session's sandbox denies it (`Permission denied`, though the folder's mode allows it), so the planner left it alone. The builder will meet the same denial and must not work around it. Whether the worktree's uncommitted files equal `69a33f7` is unchecked; see "For the engineer".
- **Not tested:** the cache-tree design below. The planner ran no prototype. Step 2 makes the builder prove it first, with a rule for what to do if it fails.

## Baseline

`main` at `b3a5378`. Every finding is judged against that commit.

## Threat model

Unchanged from run 64bd47ec's spec. The agent runs as the engineer's own user with `bash`. It can write the index any way git allows, and it can hand-write `.git/index`: entries, stat data, flags, any extension (`TREE`, `link`, `sdir`) and the trailing checksum. It can delete loose objects and write `.gitattributes`. The check runs after the agent has returned, in the SSSF process.

What this guard can't stop at its level: anything the agent leaves running, a write between two commands of one snapshot, an agent that kills the chain or puts its own `git`, `sh`, `ls` or `python3` first on `PATH`, the index inside a submodule, and gitignored files.

So nothing git says *about* the index can be trusted where the agent can forge it: not a flag, not stat data, not the cache-tree. Only the entries themselves (path, mode, object id, stage) are facts, since they are what a commit would take.

## Design

### 1. Start from run 64bd47ec's code

Take these six files as they are at `69a33f7`, then apply the changes below:

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
- `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`
- `.claude/skills/sssf/tests/test_factory_holes.py`
- `.claude/skills/sssf/tests/test_failed_call_rollback.py`
- `.claude/skills/sssf/references/config.md`
- `docs/open.md` (only the removal of the P1 row that starts "An agent's change staged only in the index isn't seen"; `main` has since reworded that row, so delete it by hand rather than applying the old hunk)

`git diff b3a5378 69a33f7 -- <the first five paths>` applies cleanly in principle: `main` moved only `docs/open.md` and a brief since `fc34698`. Write the result into the work tree only; never stage or commit. Don't take `69a33f7`'s `specs/` changes: its diff against `main` deletes this run's brief.

### 2. The accepted changes, with the link exception narrowed

`kept`, the `_roll_back` exception for a staged-new path under an untracked folder, and the link exception stay as written at `69a33f7`, with one change in `_staged_hidden`:

- The rule `old == "120000" and c == "F" + ident` applies only when `core.symlinks` is false. `snapshot` reads it once, only when some staged record has mode `120000`, with `_git(["config", "--type=bool", "--get", "core.symlinks"], root, off, answers=(1,), literal=False)`; an empty answer (unset) is true, git's default. It is one of the snapshot's own commands, so it reads the same config and runs after `restore_git_dir` has put the git directory back, as the snapshot's other commands do. Pass the answer into `_staged_hidden` as an argument.

### 3. The plan commit keeps both halves of a rename

In `commit_all` (`git_helper.py`), add `--no-renames` to `git diff --cached --name-only -z`. That is the only diff the function reads names from; the builder confirms that by reading the function, and adds the flag to any other it finds.

### 4. The index is compared with `HEAD` through an index SSSF builds itself

**Why the suggested fix isn't taken as written.** A copy of the index with no `TREE` extension makes git walk every tree in `HEAD` on every snapshot. `main` reads only the trees of folders that differ. An agent that deletes the tree object of an untouched folder would then turn a rollback into a `GitFailed` stop with its writes kept, and `test_a_deleted_tree_object_of_an_untouched_folder_still_rolls_back` and its failed-call sibling would fail. The brief allows a narrower fix; this is one that reads no extra tree.

**The fix.** The copy keeps a cache-tree, but SSSF computes it from the entries, so it can't lie.

1. `snapshot` already runs `ls-files --no-recurse-submodules -s -z`, which lists every entry's mode, object id, stage and path as they are, whatever the cache-tree or stat data say. Move that call before the cached diff and keep its output for the gitlink list as now.
2. A new script `_INDEX`, run as `python3 -I -c` through `sandbox.current().run` (the way `_DIGEST` runs, so the file is made where git runs), is given the snapshot's `ls-files` argv (`_gitargv([...], off=off)`) as its arguments. `sandbox.run` gives no stdin and argv is capped, so the script runs that argv itself with `subprocess`, non-zero exit on any failure. It then writes a version 2 index file and prints its path:
   - **Entries:** one per `ls-files` record, in the order listed. Stat fields all zero, the mode, the raw object id, flags `stage << 12 | min(len(name), 0xFFF)`, the name, NUL padding to a multiple of 8.
   - **A `TREE` extension it computes:** for each folder, git's tree id of its entries (`<mode without leading zeros> <name>\0<raw id>`, a subfolder as `40000`, hashed as `tree <len>\0…`; sha1 for 40-character ids, sha256 for 64). A folder holding any entry with stage other than 0, and each of its ancestors, is written invalid (entry count `-1`, no id), so git reads those itself.
   - **The trailing checksum** of the file, same hash.
   - **Where:** a `tempfile.mkdtemp()` folder (mode 0700). If its real path is inside the work tree or the git directory (the script asks `rev-parse --show-toplevel` and `--absolute-git-dir` with the same argv prefix), it tries `/tmp`, then `/var/tmp`; with none outside, it exits non-zero.
3. The cached diff is the same command as at `69a33f7`, with `GIT_INDEX_FILE=<that path>` added to its `env` prefix (a keyword on `_gitargv`/`_try_git`/`_git`, default none, so every other command's argv is byte-for-byte as now).
4. A `finally` removes the temp folder through the sandbox (`rm -rf -- <folder>`).
5. Failure of any step raises `GitFailed`, like `_failed_digest`: the script not starting, a non-zero exit, a printed path that isn't absolute.

The script reads no object and no part of `.git/index` itself. Git reads the built file as it would its own, so `diff --cached` stays git's code and its records keep their format; everything downstream (`_staged_hidden`, the token, `enforce`, `_roll_back`) is untouched.

**Object reads.** With an honest cache-tree, git reads a folder's tree from `HEAD` only when the folder's entries really differ from `HEAD`'s, or it holds an unmerged entry. Unforged, that is a subset of what `main`'s `diff HEAD` reads, which also reads every folder whose own cache-tree node is merely invalid. Forged, it reads one tree `main` doesn't: the folder the forged cache-tree vouched for. It must, since naming the path needs `HEAD`'s side of that folder. No blob is read.

**What changes with nothing forged.** One case, named here so a reviewer files it as known: an intent-to-add entry (`git add -N`). `ls-files -s` lists it as an ordinary entry with the empty blob's id, so the cached diff now prints it as `A`, where git's own index hid it. It then reads as a staged entry the work tree doesn't show unless the file is empty, and gets a `staged-A-…` token. Traced through `enforce` and `_roll_back` at `69a33f7`: an engineer's `-N` file has the same token before and after, so a call that doesn't touch it is unaffected; an agent's `git add -A` over it drops the token, no finding; an agent's own `git add -N` on a path it may not write was already kind `A` and is still unstaged and deleted. The tests below pin all three.

**Stat data, after the fix.** Forged stat data no longer hides anything the index holds: every entry is compared by id. It still hides what `main` lets it hide in the work tree: the first diff (`diff HEAD`) trusts stat data, so a file whose bytes the agent changed, with the entry's stat fields forged to match the new file, isn't listed by that diff when the entry still equals `HEAD`. That is `main`'s behaviour and out of scope. One thing improves: when the entry itself is forged, the path is now staged, so `_CONTENT` hashes the file's bytes and the token is decided by bytes, not stat.

**Prove it first.** Before the rest, the builder writes the forge helper and the first cache-tree test (below) and the two existing deleted-tree tests, and runs them. If git rejects the built index, or a deleted-tree test fails, or the forged entry isn't printed, stop: write what git said in the report and leave the cache-tree part unbuilt. Don't fall back to an index with no `TREE` on your own; that trades this hole for the deleted-tree harm and needs the engineer's yes.

## Step by step

Builder: `guard_builder`.

1. Bring the six files to `69a33f7`'s content as in Design 1. Run the verification command; it should pass as review 2 saw it (453 passed).
2. `.claude/skills/sssf/tests/test_failed_call_rollback.py`: add `forge_cache_tree(repo, rel, blob)` beside `forge_index`. It runs `git read-tree HEAD` and `git update-index -q --refresh`, sets every tracked file's times to an hour ago with `os.utime` before the refresh (so no entry is racy; no sleeping), then rewrites `rel`'s object id in the index file, keeping its stat bytes and the `TREE` extension, and fixes the trailing checksum. Review 1's `probe2.py`, in `adws/adw_data/sessions/64bd47ec/context_handoff/bypass_review_1/`, has the reviewer's version to read. The helper asserts `git diff --cached HEAD --quiet` and `git diff HEAD --quiet` both exit 0 afterwards: that is the hole, and a test built on a forge git already sees proves nothing.
3. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: Design 4 (`_INDEX`, the runner, the `GIT_INDEX_FILE` keyword, the reordered `ls-files -s`, the cleanup), then Design 2 (`core.symlinks`). Update the module docstring, `snapshot`'s docstring (the index is rebuilt from its entries; what an intent-to-add entry reads as) and `_staged_hidden`'s.
4. `.claude/skills/sssf/templates/adws/adw_modules/git_helper.py`: Design 3, and a line in `commit_all`'s docstring.
5. Tests, in `.claude/skills/sssf/tests/test_factory_holes.py` and `.claude/skills/sssf/tests/test_failed_call_rollback.py`, as listed under Tests.
6. `.claude/skills/sssf/references/config.md`:
   - "How the check sees a change": replace "with no index flag or stat data trusted" with what is true: the index is compared with `HEAD` entry by entry, through an index SSSF rebuilds from the entries, so no index flag, stat data or cache-tree is trusted for it; the work-tree compare trusts stat data as git does.
   - One line: a file an agent stages under a folder that was untracked before the call, such as your own nested repo, is unstaged and left on disk, and the run stops naming it.
   - One line: the index compare needs `python3` where the work tree is, as the submodule digest does.
   - Under "More known limits", two lines: (a) once an agent stages anything inside your untracked nested repo, git lists your other files there as untracked and the rollback deletes them; (b) a link whose target ends in a newline is read without it, so an agent's staged link to the same target without the newline isn't seen; it needs such a link in `HEAD`.
7. `docs/open.md`: remove the P1 row (Design 1) and add a P2 row, type Bug, for the nested-repo deletion: what happens, that it is on `main` too, found by the `bypass-reviewer` subagent on 2026-11-17 in run 64bd47ec, and the likely fix (the `?` branch of `_roll_back` never deletes under a `before` key ending in `/`).

No edit to `app_docs/`. If a write-up there says the check compares only `HEAD` with the work tree, the documenter notes that it describes old behaviour.

## Tests

Every guard test builds its config from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, through the files' existing `make()`, `pairs()`, `world`, `stops` and `turns`.

- **Cache-tree** (`forge_cache_tree`, files aged with `os.utime`): the reviewer on `PROMPT`, the planner on `src/fixtures/system.md`, the builder on `.claude/settings.json`, each through `stops(world, True, …)` and `stops(world, False, …)`. The stop names the path; afterwards `git diff --cached HEAD --quiet -- <path>` and `git diff HEAD --quiet -- <path>` exit 0 and `git ls-files -s -- <path>` shows `HEAD`'s id. One more: after the builder's stop, edit a sibling file and call `git_helper.commit_all("build")`; `git show HEAD:<path>` isn't the forged blob.
- **Cache-tree plus a deleted tree:** the forge on one folder and an untouched folder's tree object deleted: the path is still named and rolled back.
- **No false stop:** with nothing staged during the call, `enforce` returns the same on (a) a fresh `git worktree add` checkout, (b) a checkout after `git read-tree HEAD` (valid cache-tree), (c) an index rewritten with no `TREE` (`git update-index --add --cacheinfo` of an existing entry's own id on every folder, or `forge_index`). Each asserts the snapshot equals `{}` or `main`'s values, and the temp folder is gone afterwards.
- **Intent-to-add:** the three cases under "What changes with nothing forged".
- **Fail closed:** `python3` failing (a `Counting`-style sandbox stub that returns exit 1 for the `_INDEX` run) raises `GitFailed`; the temp folder is removed when the diff fails.
- **Other commands unchanged:** a recording sandbox shows `GIT_INDEX_FILE` on the cached diff's argv only.
- **Rename:** `git mv specs/old.md specs/new.md`, then `commit_all("plan", ["specs/"])`: `git show --name-status --no-renames HEAD` lists `D specs/old.md` and `A specs/new.md`, and `git diff --cached --quiet` exits 0. The same after deleting `specs/old.md` and writing a near-copy `specs/old_v2.md`.
- **Links:** `core.symlinks=false`, the engineer's dirty link-file, the agent's `git add -A`: passes. `core.symlinks=true`, the engineer's plain file over a `HEAD` link, the agent's `update-index --cacheinfo 120000,<blob of its bytes>,<path>`: named. `core.symlinks` unset behaves as true.
- **Kept, already at `69a33f7`:** the `kept_*` and `nested_*` cases stay as tests; add any of review 2's result names that has none.

Mark `host_only` any test that starts a command past agentsh's limits (128 KiB per argument, 2 MiB in all). None of these should: `_INDEX` gets a short argv and reads the entries itself.

## Bar

Against `b3a5378`:

- The first brief's bar holds, with the three accepted changes: `kept`; the link exception only under `core.symlinks=false`; a staged-new path under a folder untracked before the call is unstaged, left on disk and named.
- **Rename.** The plan commit holds the add and the delete, for `git mv` and for delete-and-write-similar.
- **Links.** As in the Links tests above.
- **Cache-tree.** For the three agents and paths above, on both paths, the run stops naming the path, the index entry and work tree match `HEAD` afterwards, and the build commit never holds the forged blob.
- **No false stop.** A fresh `--worktree` checkout, a valid cache-tree and an index with no `TREE` give `main`'s result when nothing was staged during the call.
- **No new object read,** but the one tree named under "Object reads". Both deleted-tree tests pass unchanged.
- **Nothing else changes.** With nothing staged during the call, the tree, commits, trace events and exception are as on `main`. The snapshot starts one more process (`python3`) and writes one temp folder outside the work tree and git directory, removed before `snapshot` returns.
- No agent file kept, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise, but for accepted change 3 and run 64bd47ec's two decided exceptions (the filter case; a path that held the engineer's hidden staged entry).

A reviewer files the built index, `_INDEX` and the `GIT_INDEX_FILE` keyword as part of the cache-tree fix, not as a new mechanism.

## Out of scope

Old holes this change leaves open:

- A staged change inside a submodule.
- The nested-repo deletion and the newline link target; this run names them in `config.md` and `docs/open.md`.
- A plan commit during a merge, which fails closed.
- Forged stat data hiding a work-tree change from `diff HEAD` when the entry equals `HEAD`, as on `main`.
- An agent's own `python3` first on `PATH`, as for `git` and `sh`.
- The index reset when an agent's commit is undone.
- The other P2 rows on `permissions.py`, and the gitignore row.

## Verification

From `.claude/skills/sssf`, the touched files only; the whole suite is left to verify:

`uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_factory_holes.py tests/test_failed_call_rollback.py tests/test_agent_commits.py -m 'not pushes and not host_only' -q`

It exits 0 with the new tests in it. Never run a `host_only` test in an agent's run. The planner did not run this command. Run 64bd47ec's planner found it stops at collection in a sandbox that denies the hub's `.env`; where `.env` is readable it runs.

## For the engineer

- **The other worktree is unreadable from this run.** The plan starts from commit `69a33f7`, not from `adws/adw_data/worktrees/64bd47ec`. If that worktree holds anything newer than `69a33f7`, it isn't in this plan; `git -C adws/adw_data/worktrees/64bd47ec diff 69a33f7 --stat` from the hub answers it.
- **The cache-tree fix differs from the one you put in scope.** It builds the index from `ls-files -s` with a cache-tree SSSF computes, rather than stripping `TREE` from a copy, to keep the deleted-tree tests passing. It is unprototyped. If the builder's first test shows git won't take it, the run stops there and the choice is yours: accept the stripped copy and the deleted-tree harm, or another design.
- **Intent-to-add entries** now read as staged empty files. Say if that needs to stay invisible.
- The hub's installed copies, `adws/adw_modules/permissions.py` and `adws/adw_modules/git_helper.py`, stay at the old guard until you run `just sssf-update` after the merge.

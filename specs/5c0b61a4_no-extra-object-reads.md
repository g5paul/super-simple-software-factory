# Read no git object `main` doesn't, digest only old gitlinks, and make the failed-call note true

Tenth run of the failed-call rollback work (`specs/tasks/guard-failed-call-rollback-10.md`, which finishes `-9`). It starts from guard run 3d1ef579's code (`6fa3b47` on `sssf/3d1ef579`) and closes the three findings its bypass review left: B1, F2 and F1. It also records the engineer's decision on a staged change inside a submodule.

## What the planner read, and what it didn't

- The `-10` and `-9` briefs, whole. 3d1ef579's spec (`specs/3d1ef579_drop-same-bytes-skip_v2.md` on its branch), its `guard_report.md`, and its probes: `scenarios.py`, `harness.py`, `run.sh`, and the recorded results in `bypass_probes/work/out1.txt` and `out2.txt`.
- In `permissions.py` at `6fa3b47`: `_filters_off`, `_CONTENT`, `_DIGEST`, `_digests`, `snapshot`, `_head`, `undo_commits`, `_roll_back_gitlink`, `_roll_back` and `enforce`. In `agents.py` at `6fa3b47`: the permission check in `execute`, its `except BaseException` branch, and `_roll_back_failed`.
- In `config.md` at `6fa3b47`: the failed-call paragraph, the list under "The rollback has limits on both paths", and the end of the submodule list. Not the rest.
- On `main`, only the list of git commands in `snapshot` and `undo_commits`. **Not read on `main`: `_roll_back` and the descent check in `undo_commits`.** The object table below marks the two rows that rest on that.
- **Not read:** `ref_moves`, the git directory guard, and both test files beyond their helper names. The builder reads `permissions.py`, `agents.py` and `config.md` whole, and both test files, before step 2.
- One probe, on git 2.43, in a throwaway repo: with the tree object of an untouched folder deleted, `git diff HEAD --ignore-submodules=dirty --no-renames --no-ext-diff --no-textconv --raw --no-abbrev -z` exits 0 and `git ls-tree -r -z HEAD` fails. The raw output gives old mode `160000` for a gitlink removed from the index (`D`, with `submodule.<name>.ignore = all` set) and for one replaced by a file (`T`), and new mode `160000` for a newly staged one (`A`). Its status letters are the same as `--name-status` prints.

## Threat model

The agent runs as the engineer's own user, with a shell in the repo. It can write any file, stage and commit, rewrite `.git/index`, and delete or rewrite any file under `.git/objects`. SSSF's write guard works after the fact: a snapshot before the call, then an undo of the agent's commits and a rollback of writes outside its `writes`.

What this guard can't stop at its level:

- Anything outside the repo, or a process that outlives the call. `--sandbox` and `--container` isolate.
- **`.git/objects` isn't watched, and can't cheaply be.** An agent can delete any object. Every git command SSSF runs after the call fails if it needs a deleted object, so the guard can only keep the set of objects it needs as small as `main`'s. An object that `main`'s own step needs is an old hole: delete the blob of the file being rolled back, or a tree on the way to it, and neither version can restore it.
- **A staged change inside a submodule.** The digest reads files, never a submodule's index, so content staged with `git -C <path> add` and then put back in the work tree isn't seen. Seeing it needs git run inside the submodule, under a config the agent can write, or a parser for git's index.
- A write inside a submodule can be seen but not rolled back, a submodule's git directory and `.git` file aren't watched, and a file under a nested `.git` inside a submodule isn't seen, all as in `-9`.
- The guard's programs are found on `PATH`: `sh`, `env`, `git`, `tar`, and `python3` for the digest. An agent that puts its own `git` or `python3` in a folder first on `PATH`, such as a gitignored venv's `bin` under `uv run`, has SSSF run it.

## Baseline

`main` at `e4b6daa`. Every finding is judged against it. Its guard code is `5c64cf9`'s: the one commit since is the `-10` brief and one row of `docs/open.md`.

On `e4b6daa`:

- `snapshot` runs `diff HEAD --name-status`, `ls-files --others` and `ls-files -v`, and no `ls-tree`. With a tree object of an untouched folder gone, a finished call's protected write is rolled back.
- No `python3` runs. A newly staged gitlink is an added path: it's unstaged, and the folder stays (`unstaged; could not delete`).
- A failed call gets no undo and no rollback, so its writes stay, with no note.
- A staged change inside a submodule stops the call, because `diff HEAD` starts a `status` child there, and isn't rolled back.

`6fa3b47` (branch `sssf/3d1ef579`) is the code this run builds on, and the bar's "nothing else changes" line is measured against it.

## Bar

- `-9`'s bar holds, and `-8`'s to `-5`'s through it, with their decided exceptions.
- **B1 is closed.** `missing_tree_object` ends in a `PermissionBreach` naming `adws/adw_modules/permissions.py — rolled back`, with the file back at `HEAD`'s text, as on `e4b6daa`. `snapshot` runs no `ls-tree`. For every git command the snapshot, undo and rollback run, the table below says it reads no object `e4b6daa`'s same step doesn't, or why it must.
- **B1 on the failure path is never silent.** In `fail_missing_tree_object`, either the write is rolled back, or the console says the tree may still hold the agent's writes. See "For the engineer": the planner expects the first.
- **F2 is closed.** `new_gitlink_no_python` ends as on `e4b6daa`: the protected write rolled back, `tools/x` unstaged, and no `python3` started. A gitlink that was in the before snapshot is still digested, and still stops the run when `python3` can't start.
- **F1 is closed.** The failed-call note says "rolled back" only when `enforce` reports every breached path put back and no ref left moved. In `fail_undeletable`, `fail_revert_engineer` and `fail_ref_only` the note has no "rolled back", names what wasn't put back, and has the sentence "The tree may still hold the agent's writes." `fail_control` still gets "undid 1 commit(s) (…) and rolled back its unauthorized writes."
- **The docs.** `config.md`'s known limits name the staged change inside a submodule with `git -C <path> diff --cached`, after the `<path>/.git` check, and name `PATH` shadowing in one line.
- **Never:** an agent file kept, agent code run, or an engineer file deleted or overwritten, where `e4b6daa` does otherwise.
- **Nothing else changes.** With no gitlink, no moved config, no filter and git objects intact, the tree, commits, trace events and exception are as on `6fa3b47`, message texts included. The `failed_call_rollback` event's payload doesn't change in any case.

### What each git command reads

After this change. "Same" means the same objects as `e4b6daa`'s same step.

| Step | Command | Objects read |
|---|---|---|
| snapshot | `config --list --name-only -z` | None: config files. `e4b6daa` doesn't run it. |
| snapshot | `rev-parse -q --verify HEAD` | A ref. At most `HEAD`'s commit, which `e4b6daa`'s `diff HEAD` reads. |
| snapshot | `diff HEAD --ignore-submodules=dirty … --raw --no-abbrev -z` | Same walk as `e4b6daa`'s `diff HEAD … --name-status -z`: `HEAD`'s commit, its root tree, and each subtree the index doesn't hold as unchanged. `--raw` prints modes and ids that walk already has. `--no-abbrev` stops git searching the object store for a short unique id. It reads less than `e4b6daa` in one way: no `status` child in a submodule. |
| snapshot | `ls-files --others --exclude-standard -z`, `ls-files -v -z` | None: the index and work tree. Same. |
| snapshot | `ls-files -s -z` | None: the same index. `e4b6daa` doesn't run it. |
| snapshot | `hash-object --no-filters -- <path>` | None: it hashes a work tree file and writes nothing. Same. |
| snapshot | `python3 -I -c <digest>` | Not git. |
| undo | `rev-parse -q --verify HEAD`, `symbolic-ref -q HEAD` | Refs. Same. |
| undo | `merge-base --is-ancestor <before> <after>` | The commits from `<after>` back to `<before>`. `e4b6daa` makes the same descent check; **the builder confirms its command**. |
| undo | `rev-list --abbrev-commit <before>..<after>` | Those commits. Same. |
| undo | `reset -q --no-recurse-submodules <before>` | `<before>`'s commit and trees. Same as `e4b6daa`'s `reset -q <before>`. Runs only when `HEAD` moved. |
| rollback | `config --list --name-only -z` | None. |
| rollback | `update-index -q --no-skip-worktree` / `--no-assume-unchanged -- <path>` | None: the index. |
| rollback | `checkout --no-recurse-submodules HEAD -- <path>` | `HEAD`'s commit, the trees on the way to `<path>`, and its blob. **It must:** that blob is the content being put back. `e4b6daa` restores the path from `HEAD` too; **the builder confirms its command**. |
| rollback | `rm -q --cached --force -- <path>`, `check-ignore -q --no-index -- <path>` | None: the index and ignore files. |
| rollback | `ref_moves`' commands | Unchanged by this run. They read refs and the commits a moved ref gained, as on `e4b6daa`. |

The builder corrects any row its reading of `main` disproves, in the build report, and stops if a command reads an object `main`'s step doesn't and the row gives no reason.

## Decided difference this plan records

A reviewer files a finding that matches this as meeting the bar.

**A staged change inside a submodule passes.** The agent writes a file in a submodule, stages it with `git -C <path> add`, and puts the file back. `git -C <path> rm --cached` and a staged `chmod +x` do the same. `e4b6daa` stops the call, though it can't roll it back either. This build passes it, and the engineer's next commit in the submodule takes the agent's content. The engineer chose on 2026-11-17 (brief `-10`) to document it and not fix it. `-9`'s decided exception and `-8`'s decided differences carry over.

## Steps

### 1. Bring in 3d1ef579's code

From the repo root, apply the branch's diff to the work tree, without the two spec files, which the builder can't write:

`git diff main...sssf/3d1ef579 -- . ':(exclude)specs' ':(exclude)docs/open.md' | git apply`

- `git apply` without `--index` stages nothing. Don't use `git checkout sssf/3d1ef579 -- <paths>`, `git merge` or `git cherry-pick`.
- The planner ran this with `--check` and it applies. It changes twelve files: `.claude/skills/sssf/cookbooks/install.md`, `.claude/skills/sssf/references/config.md`, `.claude/skills/sssf/templates/adws/adw_modules/agents.py`, `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`, `.claude/skills/sssf/templates/adws/adw_modules/worktree.py`, `.claude/skills/sssf/tests/test_agent_commits.py`, `.claude/skills/sssf/tests/test_failed_call_rollback.py`, `.claude/skills/sssf/tests/test_rollback_no_programs.py`, `.claude/skills/sssf/tests/test_rules.py`, `.claude/skills/sssf/tests/test_secret_files.py`, `.claude/skills/sssf/tests/test_worktree.py`, and `docs/guide/run.md`.
- `docs/open.md`, by hand: with it in the patch, the apply fails, because `main`'s brief commit `e4b6daa` rewrote the failed-call row. `git diff main...sssf/3d1ef579 -- docs/open.md` changes three rows. The failed-call row keeps `main`'s sentences and gains the branch's; say in the build report what you merged. The other two rows take the branch's text.
- If the apply is refused or fails, stop and report it. Don't rebuild the code from the old specs.
- Don't write under `adws/adw_data/sessions/3d1ef579/`, and don't run its `run.sh`: it tests the two exports there, not this build.

### 2. B1: list gitlinks from the index and the diff

In `snapshot`, in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- The `diff HEAD` command gets `--raw --no-abbrev` in place of `--name-status`. Every other argument stays.
- Its output is still two NUL-ended fields per path: a record `:<old mode> <new mode> <old id> <new id> <status>`, then the path. The status is the record's last word, and goes to `add(path, status)` as now. A record that doesn't start with `:` or doesn't have five words, or an odd number of fields, raises `GitFailed` in the words of the file-hash failure ("SSSF can't tell what changed in the tree, so it stopped…"). It's never skipped.
- A path whose old mode is `160000` is a gitlink in `HEAD`. Keep those in a list while reading the diff.
- The `ls-files -s` loop stays as it is. After it, append each of the diff's `HEAD` gitlinks that isn't in `gitlinks` yet. That keeps the snapshot's key order as on `6fa3b47`: a gitlink only in `HEAD` is in the diff, as `D` or `T`, so it already has its place in `kinds`.
- **Delete the `ls-tree -r -z HEAD` loop.** Nothing replaces it. A gitlink in `HEAD` and unchanged in the index is in `ls-files -s`. One that left the index or changed type is in the diff, with `submodule.<name>.ignore = all` set too.
- Update the comment above the gitlink list and the docstring line "Every gitlink is listed": the list comes from the index and from the old modes of the diff against `HEAD`, and the snapshot reads no tree the diff didn't.

### 3. F2: digest only a gitlink that was there before the call

Same file:

- `snapshot(run, before=None)`. With `before`, a gitlink folder is digested only if `before` holds that path with `sub` among its kinds. Without `before`, every gitlink folder is digested, as now.
- `enforce` calls `snapshot(run, before)`. `agents.execute`'s call for the before snapshot doesn't change.
- A gitlink new in the after snapshot keeps the content `D`. It isn't in `before`, so it's a changed path, and `_roll_back` already takes it down the added-path branch, as on `e4b6daa`.
- A path that was in `before` without `sub` and is a gitlink now also keeps `D`. Its kind changed, so it's still a changed path, and `_roll_back_gitlink` leaves it as-is, as now.
- Docstring: "`python3` starts only when a gitlink that is a folder was there before the call, or for the before snapshot."
- Add no module-level name.

### 4. F1: the failed-call note matches the outcome

In `permissions.py`:

- `PermissionBreach` gets two class attributes with defaults: `left = None` and `refs_left = False`. `None` means no rollback list came with the breach.
- In `enforce`, the breach it raises at the end carries `left`, the breached paths whose outcome is not exactly `rolled back`, `deleted` or `unstaged and deleted`, in `outcomes`' order, and `refs_left = bool(moved)`. Its message, type and the order of work don't change. No other raise sets them.

In `_roll_back_failed`, in `.claude/skills/sssf/templates/adws/adw_modules/agents.py`:

- Keep the breach object, not only its text. `complete` is true when `breach.left == []` and not `breach.refs_left`.
- When `complete`, the note is as now: `… SSSF [undid N commit(s) (…) and ]rolled back its unauthorized writes.`
- Otherwise the note has no "rolled back". It says, in one line:
  `{agent}'s call failed ({kind}); SSSF [undid N commit(s) (…), but ]could not put back {what}. The tree may still hold the agent's writes. Check `git status` and `git log` by hand before the next run, commit or `just`.`
  - `{what}` is the first five paths of `left`, then `and N more` past five. When `refs_left`, add `the commits on other refs named below`. When `left` is `None`, `{what}` is `everything the agent wrote`.
  - The two sentences after `{what}` are the same words the success path's stop and the first branch of this function already use. Don't reword them.
- The `permissions.IN_SUBMODULE in found` test and the "except inside a submodule" wording go. A gitlink's outcome is never one of the three, so its path is in `left` and the general rule covers it. `IN_SUBMODULE` stays in `permissions.py`: the messages use it.
- `advise(found)` under the note, the `failed_call_rollback` event and its payload, and both `except BaseException` branches stay as they are.
- Update the function's docstring with the rule.

### 5. Docs

`.claude/skills/sssf/references/config.md`, all inside "## Write permissions":

- **The failed-call paragraph** ("A `failed_call_rollback` trace event names each path and commit, and the console says when the tree may still hold the agent's writes"): add that the console says SSSF rolled back the writes only when every path was put back and every commit undone, and otherwise names what it couldn't put back.
- **The list under "The rollback has limits on both paths"**, right after the bullet that starts "An agent can rewrite a submodule's `.git` file", gets:
  > - A staged change inside a submodule isn't seen. An agent can write a file there, stage it with `git -C <path> add` and put the file back; `git -C <path> rm --cached` and a staged `chmod +x` pass the same way. The call isn't stopped, and your next commit in the submodule takes what the agent staged. To check, first see that `<path>/.git` still points at `.git/modules/<name>` (`cat <path>/.git`), then run `git -C <path> diff --cached`.
  > - SSSF finds `git`, `python3`, `sh`, `env` and `tar` on `PATH`. An agent that puts its own `git` or `python3` in a folder first on `PATH`, such as a venv's `bin`, has SSSF run it.
- **The same list's bullet "When SSSF's own git fails"**: add one sentence, that a git object the agent deleted is one such failure, and that SSSF's snapshot reads no object beyond the ones `git diff HEAD` reads.
- **The submodule list** (`### Repos that use git-lfs, git-crypt, another filter, or submodules`): the bullet that says an index-only change in the submodule isn't seen points at the new limits bullet. The bullet "A repo with a submodule needs `python3`" adds: a submodule the agent adds during a call needs none, since it's rolled back as a new path.

`docs/open.md`: in the row "SSSF's git directory guard doesn't watch a submodule's git directory", add that a staged change inside a submodule passes the write check, as a limit the engineer decided on 2026-11-17, with the `config.md` check. In the failed-call rollback row, add that the failed-call note says "rolled back" only when everything was. Change no other row.

Docstrings and comments: as steps 2 to 4 name. The repo keeps no CHANGELOG at its root, and no README or help text describes these messages.

For the documenter: no `app_docs/` write-up describes the failed-call note yet, since this work hasn't shipped. If one says a staged change inside a submodule stops a call, it now describes old behaviour.

### 6. Tests

Build every config from `.claude/skills/sssf/templates/sssf.config.yaml`, through the files' own helpers (`world`, `stops`, `turns`, `said`, `vendor_lib`). Port each scenario from `adws/adw_data/sessions/3d1ef579/bypass_probes/probes/scenarios.py` into the test file; don't import it.

In `.claude/skills/sssf/tests/test_failed_call_rollback.py`:

- **B1.** `test_a_deleted_tree_object_of_an_untouched_folder_still_rolls_back`: commit `deep/a/b.txt` before the call; the agent writes a protected file, then deletes the loose object `git rev-parse HEAD:deep/a` names. On the success path the call raises a `PermissionBreach` that isn't a `GitFailed`, names the path `— rolled back`, and the file holds `HEAD`'s text.
- **B1, failure path.** `test_a_deleted_tree_object_on_a_failed_call_is_rolled_back_or_said`: the same plant, then the call fails. Assert the file is back at `HEAD`'s text, or `said(world)` has "The tree may still hold the agent's writes". Then pin the one the build shows with a second assert, and say which in the build report.
- **B1, the list.** With `vendor_lib(world)`, parametrized over how the gitlink left the index before the call: `git rm --cached vendor/lib`, and the same with `submodule.vendor/lib.ignore` set to `all`. The agent edits a file under `vendor/lib`. The call stops naming `vendor/lib`. A third case replaces the gitlink with a staged file before the call and expects the same.
- **B1, no `ls-tree`.** `"ls-tree"` is not in `inspect.getsource(permissions.snapshot)`.
- **F2.** `test_a_new_gitlink_rolls_back_without_python3`, on both paths: no submodule before; the agent writes a protected file, makes a nested repo with a commit in `tools/x`, runs `git add tools/x`, and puts first on `PATH` a `python3` that writes a marker and exits 127. The protected file is back, the `tools/x` line starts with `unstaged`, no `GitFailed` is raised or advised, and no marker exists.
- **F2, old gitlinks.** With `vendor_lib(world)` and the same broken `python3`, an agent that edits `vendor/lib/file.txt` still stops with the digest message. If a test already pins this, keep it and add none. A second test, with a working `python3` and a recording sandbox: the agent adds `tools/x` beside `vendor/lib`, and the after snapshot's `python3` command names `vendor/lib` and not `tools/x`.
- **F1.** Three tests on the failure path, from `fail_undeletable`, `fail_revert_engineer` and `fail_ref_only`. In each: no entry of `world.notes` has "rolled back"; one has "The tree may still hold the agent's writes."; and it names `locked/f.py`, `src/app.py`, or "other refs". `said(world)` still holds the advice (`could not delete`, `REVERTED-BY-AGENT`, `refs/heads/other`). The undeletable test puts the folder's mode back in a `finally`, and skips when run as root.
- **F1, the true case.** A failed call that committed and wrote a protected file gets the note ending "and rolled back its unauthorized writes." If a routes test already asserts it, keep it.
- **F1, a breach with no list.** With `permissions.enforce` patched to raise a plain `PermissionBreach("x")`, the note has no "rolled back" and has the may-still-hold sentence.
- **The submodule test** that asserts `"except inside a submodule"` asserts the new note: no "rolled back", `vendor/lib` named, the may-still-hold sentence. Search all test files for that phrase and for `rolled back its unauthorized writes`.

In `.claude/skills/sssf/tests/test_rollback_no_programs.py`:

- **`test_config_md_documents_the_scenario`** gains, on its whitespace-flattened "Write permissions" text: one bullet with "staged change inside a submodule", `git -C <path> add`, and `cat <path>/.git` before `git -C <path> diff --cached`; and a bullet with `PATH` and `python3`.
- **A recorded run.** In the test that records every command of a full `execute` in a repo with a submodule, assert no recorded argument is `ls-tree`.

None of these should start a command past agentsh's argument limits. If one does, mark it `host_only`, with a comment that it passes only outside agentsh because of `argv_truncated`, and don't run it.

## Verification

Run from `.claude/skills/sssf`, with the repo's own test runner:

1. `tests/test_failed_call_rollback.py` and `tests/test_rollback_no_programs.py` with `-m 'not pushes and not host_only'`. All pass, step 6's among them.
2. `tests/test_rules.py`, `tests/test_secret_files.py`, `tests/test_worktree.py`, `tests/test_agent_commits.py`, `tests/test_git_dir_guard.py` and `tests/test_factory_files.py`, with the same `-m`. All pass.
3. Never run a `host_only` test in an agent's run. Verify runs the whole suite outside agentsh, and its result is the one that counts.
4. `test_config_md_documents_the_scenario` reads `docs/guide/run.md` from the repo root, so run it from the full checkout.

From the repo root:

5. `git diff sssf/3d1ef579 -- .claude/skills/sssf/templates/adws/adw_modules` shows changes in `permissions.py` and `agents.py` only. In `permissions.py` they are in `PermissionBreach`, `snapshot` and `enforce`. In `enforce` they are the `snapshot(run, before)` call and the two attributes on the breach. In `agents.py` they are in `_roll_back_failed`.
6. `permissions.py` has no `ls-tree`.
7. `git diff sssf/3d1ef579 -- docs/open.md` shows step 5's two rows, the failed-call row carrying `main`'s `e4b6daa` text too.
8. The build report gives the two confirmations the object table asks for, and the outcome the failure-path B1 test pinned.

For the bypass reviewer, not the builder: `harness.py` takes any folder that holds `.claude/skills/sssf` as its version, so the eight scenarios the brief names run against this build with this worktree as that argument and a new empty folder as the work folder.

The repo's own test command covers steps 1 to 4, so the plan adds no extra check and no new dependency.

## Out of scope

- `-9`'s list, which includes `-8`'s to `-5`'s.
- **A staged change inside a submodule.** The decided difference above. `config.md` names it; nothing detects it.
- **A deleted git object that `main`'s own step needs:** the blob or a tree on the way to a file being rolled back, a tree the diff must walk, or `<before>`'s trees when a commit is undone. Both versions fail there. This build says so and stops.
- An agent that moves `HEAD`, which has its own P2 row in `docs/open.md`.
- The `tests_pass(cmd)` gate running before the permission check.
- A `git` or `python3` shadowed in a folder first on `PATH`. `config.md` names it in one line; nothing detects it.
- A `.pth` file in a venv's `site-packages`, which `python3 -I` still loads, and whether the digest should get `-S`.
- The folder of a newly staged gitlink stays on disk after it's unstaged (`unstaged; could not delete`), as on `e4b6daa`.

## For the engineer

- **The bar's line on `fail_missing_tree_object` reads as a description of `6fa3b47`, and this build should do better.** The failure path failed there on the same `ls-tree` as the success path. With it gone, the failed call's write should be rolled back too, where `e4b6daa` keeps it. The test pins whichever the build shows and never allows a silent keep. Say if you meant the failure path to keep the write.
- **3d1ef579's two spec files stay on its branch.** `specs/` is protected, so step 1 leaves out `specs/3d1ef579_drop-same-bytes-skip.md` and its `_v2`. Bring them over when you merge, if you want the record on `main`.
- **The hub's own copies** of `permissions.py`, `agents.py` and `worktree.py` under `adws/adw_modules/` are yours to update after the merge. The builder can't write there.
- **F1 adds two attributes to `PermissionBreach`.** The note can't tell a full rollback from a partial one by the message text without parsing paths out of it, and a path can hold any character. The attributes change no message and no event. A breach without them reads as "not everything put back", so the note errs toward the warning.
- **A newly staged gitlink at a path you held as an untracked nested repo** wasn't probed. Git lists the untracked one with a trailing slash, so the staged one is a new path and takes the added-path rollback, as on `e4b6daa`. That rollback's delete fails on a folder in the probes, so the folder should stay.
- **Two rows of the object table rest on code the planner didn't read on `main`:** the descent check in `undo_commits`, and `_roll_back`'s restore. The builder confirms both.
- The brief dates your decision 2026-11-17 and this run's clock says 2026-11-16. The plan quotes the brief's date.

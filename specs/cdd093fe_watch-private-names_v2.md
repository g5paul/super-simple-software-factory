# Plan: the git-directory guard watches `sssf-private-names`

## Request

The git-directory guard doesn't watch `.git/sssf-private-names`. The skill's
pre-push hook (`.claude/skills/sssf/templates/githooks/pre-push`, line 28) reads
`$(git rev-parse --git-common-dir)/sssf-private-names` and refuses a push whose
messages or added lines name a listed project. An agent that empties, edits or
deletes that file, or creates one where there was none, changes what the
engineer's next push checks. The guard doesn't put it back and doesn't report it.

Make the guard treat `sssf-private-names` like the other git-directory files it
watches: a change during an agent call is put back, and the run stops with a
git-guard breach that names the file. A worktree run watches the common git
directory's copy, which is the one the hook reads.

## Baseline

`main` at `9d0f2c4` ("Bring the chain design in line with the build"). Every
finding is judged against it. At that commit
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py` has

```python
GIT_WATCHED = ("hooks", "info/exclude", "info/attributes", "info/sparse-checkout",
               "info/grafts", "config", "config.worktree", "commondir")
```

and `_watched()` builds `f"{d}/{name}"` for every `name` in `GIT_WATCHED` and
every `d` in `git rev-parse --path-format=absolute --git-common-dir --git-dir`
(deduplicated). So in a plain clone it watches `.git/<name>`. In a worktree it
watches both `<main>/.git/<name>` (common) and `<main>/.git/worktrees/<x>/<name>`.
`sssf-private-names` is in neither list, so the guard sees no change to it.

The hub's installed copy, `adws/adw_modules/permissions.py`, is byte-identical
to the template at the baseline.

## Threat model

The agent runs as the engineer's own user, with bash. It can:

- empty, edit, truncate, delete or `chmod` `.git/sssf-private-names`, or create
  one where none existed (an empty file, or one listing names of its choosing);
- do that in the common git directory from inside a worktree run (the
  worktree's `.git` file points there, and `git rev-parse --git-common-dir`
  names it);
- replace the file itself with a symlink, or swap `.git` or one of its parents for a
  symlink;
- change the hook that reads it instead (`core.hooksPath`, `.githooks/pre-push`
  in the tree, `.git/hooks/pre-push`);
- leave a background process that writes the file after the guard's check, or
  kill the chain before the check runs.

What this guard does at its level: before each agent call it packs the watched
paths into a tar stream held in the SSSF process. After the call it compares
the packs, unpacks the changed paths, and raises `PermissionBreach` naming them.
Adding the file to `GIT_WATCHED` puts it under that same machinery, symlink
handling included:

- **A symlink that replaces the file itself** is a changed member of the
  no-follow pack. The file's parent still resolves to itself, so the restore
  removes the link and puts the saved file back.
- **A symlink on the path to it**, such as a swapped `.git` or a parent directory, is
  reported and stops the run, but nothing is written through it or to it. The
  breach says "NOT all put back".

What the guard can't stop, since the agent runs as the engineer's user: a write after
the check (from a background process), a killed chain, or anything an agent does
outside an agent call that SSSF wraps. `--sandbox` and `--container` provide the
isolation for those.

## Bar

Against the baseline, the change must:

1. **Put back an edited, emptied, chmodded, deleted or symlink-replaced
   `sssf-private-names`** in the git directory after an agent call. Its bytes
   and mode must come back as they were, and the run must stop with the existing
   git-guard `PermissionBreach`, whose path list names `.git/sssf-private-names`.
2. **Remove a `sssf-private-names` the agent created** where none existed before
   the call, and stop the run with the same breach naming it.
3. **In a worktree run, watch the common git directory's file** (the one the
   hook reads). A change there is put back and named in the breach.
4. **Leave a run that doesn't touch the file unchanged**: no breach whether the
   file is absent or present, and an absent file stays absent.

It must never, where the baseline does otherwise:

- keep an agent's file (here: an agent-created or agent-edited
  `sssf-private-names`);
- run agent code;
- delete or overwrite an engineer file, with the one exception below. The
  file's contents are the engineer's private project names. They must never
  appear in a breach message, trace event, log line, or test fixture beyond a
  made-up placeholder. The breach names only the path, as it does today.

**The one sanctioned exception.** The request says any change to the file
during an agent call is put back. The guard can't tell who made a change, so an
edit the engineer makes to `sssf-private-names` during an agent call is put
back too. So is a file the engineer creates during the call, and the run stops
with the breach. This is new behaviour against the baseline, where the file is
never touched, and the request sanctions it. It is the same cost the guard
already has for git config. The guard touches the file only between the pack
before an agent call and the restore after it. An edit the engineer makes
outside an agent call, between calls or with no run going, is never read back
or changed. The docs below state this cost.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- A write after the check by a process the agent left behind, or a chain the
  agent killed before the check ran. `references/config.md` already lists both.
- The agent editing the hook instead. `.githooks/pre-push` in the working tree
  is guarded only by `protected_files` and the change-set check, not by this
  guard. `core.hooksPath` and `.git/hooks` are already watched.
- A change made through a symlink on the path to the file (a swapped `.git` or
  parent) is reported but not written back, as for every watched path.
- `git push --no-verify` by the engineer, and the hook's own logic.
- A file over `PACK_CAP` (16 MiB) behaves as any oversized watched path does
  today.
- Inside a `--sandbox` VM the guard watches the VM's git directory, not the
  engineer's host clone.
- A submodule's git directory, as for every other watched path.
- A worktree run also watches `.git/worktrees/<x>/sssf-private-names`, because
  `GIT_WATCHED` applies to both directories. The hook never reads that path.
  Watching it is harmless and keeps the change to one tuple entry.

## Changes

This is the smallest change: one entry in an existing tuple. It adds no
module-level state, no `global`, and no shell code, so it adds no new mechanism.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

- Append `"sssf-private-names"` to `GIT_WATCHED`:

  ```python
  GIT_WATCHED = ("hooks", "info/exclude", "info/attributes", "info/sparse-checkout",
                 "info/grafts", "config", "config.worktree", "commondir",
                 "sssf-private-names")
  ```

- Extend the comment block above `GIT_WATCHED` (the `# ── the git directory and
  git config ──` section) with one sentence: `sssf-private-names` lists the
  projects the pre-push hook refuses to name, so an agent that empties or
  deletes it turns that check off for the engineer's next push. The file is
  optional, and one the agent creates counts as a change too.
- Leave the breach message text as is. It already lists every changed path,
  which names the file. Existing tests match on `"git directory"` and
  `"put back as they were"`.

Nothing else in the module changes:

- `_PACK` skips a path that doesn't exist, so an absent file packs as nothing.
- `_differs` then sees a created file as a new member.
- `_UNPACK` runs `rm -rf` on it and extracts nothing for it, which removes it.

`config.worktree` and `commondir` take this same path today
(`test_a_commondir_redirect_is_put_back`).

### 2. `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section `# ── the private-names file ──` with these tests.

- Use the existing `repo` fixture, the `git()` helper and
  `SimpleNamespace(repo_root=...)`, as the other `restore_git_dir` tests do.
  `guard_git_dir` and `restore_git_dir` read no config, so these tests need none.
- If the builder adds a test that calls `permissions.enforce`, build its config
  from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, never a
  test-only one.
- Put a made-up name such as `secret-ledger` in the file, never a real project.

The tests:

- `test_the_private_names_file_is_put_back(repo, change)`
  1. Parametrize over `"empty"`, `"edit"`, `"delete"`, `"chmod"` and `"symlink"`.
     For `"symlink"`, replace the file with a link to a file elsewhere under
     `tmp_path`.
  2. Write `.git/sssf-private-names` as `"# private\nsecret-ledger\n"` (mode
     0o600) and record its bytes and mode.
  3. Call `guard_git_dir`, then apply the change.
  4. Wrap `restore_git_dir` in
     `pytest.raises(PermissionBreach, match="git directory")`.
  5. Assert that `".git/sssf-private-names"` is in the message and
     `"secret-ledger"` is **not**.
  6. Assert that the path is a regular file again (not a link) with its bytes
     and mode back.
- `test_a_created_private_names_file_is_removed(repo)`
  1. Start with no file and call `guard_git_dir`.
  2. Have the agent write `.git/sssf-private-names` (`"\n"`).
  3. Assert that the breach names `.git/sssf-private-names`.
  4. Assert that the file doesn't exist afterwards.
- `test_an_engineer_edit_during_a_call_is_put_back(repo)`
  1. Pin the sanctioned exception: an edit made between `guard_git_dir` and
     `restore_git_dir` is put back and breaches, whoever made it.
  2. Then, with no guard active, edit the file and run a fresh `guard_git_dir`
     and `restore_git_dir` pair.
  3. Assert the pair returns `[]` and keeps the new bytes. An edit made
     outside a call is never touched.
- `test_an_untouched_private_names_file_is_no_breach(repo, present)`
  1. Parametrize over `True` and `False` (file present or absent).
  2. Assert that guard and restore return `[]`.
  3. Assert that the file's presence and bytes are unchanged.
- `test_a_worktree_guards_the_shared_private_names_file(repo, tmp_path)`
  1. Write `repo/.git/sssf-private-names`, then run
     `git worktree add -q -b run <tree>`.
  2. Guard with `repo_root=tree`. Assert that
     `str((repo / ".git" / "sssf-private-names").resolve())` is in
     `guard["paths"]`.
  3. Delete the common file.
  4. Assert that the breach message contains that resolved path, and that the
     file is back with its bytes.
- `test_the_hook_reads_the_watched_file()`
  1. Read `.claude/skills/sssf/templates/githooks/pre-push` and assert that
     `'--git-common-dir)/sssf-private-names"'` is in the text.
  2. Assert that `"sssf-private-names" in permissions.GIT_WATCHED`.

  A rename on either side then fails a test.

Also update the module docstring's line "guard_git_dir copies hooks/, info/,
config and config.worktree aside" to add "and sssf-private-names".

The existing `state()` helper iterates `GIT_WATCHED`, so
`test_a_change_is_put_back_and_stops_the_run` now also covers the file when
present. That test needs no change.

### 3. Docs the change makes out of date

- `.claude/skills/sssf/references/config.md`, git-directory section (around
  lines 159–200):
  - In "Before each agent, SSSF saves `hooks/`, `info/`, `config`,
    `config.worktree` and `commondir`…", add `sssf-private-names`.
  - In the paragraph that says why each path matters, add: emptying or deleting
    `sssf-private-names` would turn off the pre-push hook's private-names check
    for the engineer's next push, and one the agent creates is a change too.
  - In "Two costs", extend "An edit you make to your git config while an agent
    runs is put back too" to cover `.git/sssf-private-names`. Say to edit it
    between runs.
- `.claude/skills/sssf/cookbooks/install.md`, the **Private names** bullet
  (line 248): add that SSSF's git-directory guard puts the file back and stops
  the run if it changes during an agent call, the engineer's own edit included,
  so edit it between runs.
- `docs/secret-scan.md`, the **Private names** bullet: add the same sentence.
- The repo keeps no CHANGELOG, so there is none to update.
- `app_docs/` doesn't exist in this repo and isn't to be edited. For the
  documenter: `docs/postmortems/git-dir-guard.md` describes the watched set as
  it shipped. If it needs a note, say there that the set later gained
  `sssf-private-names`; don't rewrite the post-mortem.

## Verification

Run from the repo root; each must exit 0.

1. The guard tests, including the new ones:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q`
2. The rest of the skill suite still passes (the hook tests skip without
   gitleaks):
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q`
3. `grep -n '"sssf-private-names"' .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
   shows the entry inside `GIT_WATCHED`.
4. `git diff --stat 9d0f2c4 -- .claude/skills/sssf/templates/adws/adw_modules/`
   shows only `permissions.py`, and its diff adds no module-level name and no
   `global` statement.
5. `git diff 9d0f2c4 -- .claude/skills/sssf/tests/test_git_dir_guard.py` uses
   only placeholder names (`secret-ledger`), no real project name.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy and is
  protected. It was byte-identical to the template at `9d0f2c4`. After the
  build lands, bring it back in line: use the skill's update path, or copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it. The
  hub's own runs then watch `.git/sssf-private-names` too.

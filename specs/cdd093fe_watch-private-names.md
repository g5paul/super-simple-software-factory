# Plan: the git-directory guard watches `sssf-private-names`

## Request

The git-directory guard doesn't watch `.git/sssf-private-names`. The skill's
pre-push hook (`.claude/skills/sssf/templates/githooks/pre-push`, line 28) reads
`$(git rev-parse --git-common-dir)/sssf-private-names` and refuses a push whose
messages or added lines name a listed project. An agent that empties, edits or
deletes that file, or creates one where there was none, changes what the
engineer's next push checks, and the guard neither puts it back nor reports it.

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
(deduplicated). So in a plain clone it watches `.git/<name>`, and in a worktree
both `<main>/.git/<name>` (common) and `<main>/.git/worktrees/<x>/<name>`.
`sssf-private-names` is in neither, so no change to it is seen.

The hub's installed copy, `adws/adw_modules/permissions.py`, is byte-identical
to the template at the baseline.

## Threat model

The agent runs as the engineer's own user, with bash. It can:

- empty, edit, truncate, delete or `chmod` `.git/sssf-private-names`, or create
  one where none existed (an empty file, or one listing names of its choosing);
- do that in the common git directory from inside a worktree run (the
  worktree's `.git` file points there, and `git rev-parse --git-common-dir`
  names it);
- replace the file with a symlink, or swap `.git` or a parent for a symlink;
- change the hook that reads it instead (`core.hooksPath`, `.githooks/pre-push`
  in the tree, `.git/hooks/pre-push`);
- leave a background process that writes the file after the guard's check, or
  kill the chain before the check runs.

What this guard does at its level: before each agent call it packs the watched
paths into a tar stream held in the SSSF process; after the call it compares
and unpacks the changed ones, and raises `PermissionBreach` naming them. Adding
the file to `GIT_WATCHED` puts it under exactly that machinery, symlink
handling included (a change through or to a link is reported, never written
back).

What it can't stop, since the agent is the engineer's user: a write after the
check (background process), a killed chain, or anything an agent does outside
an agent call SSSF wraps. `--sandbox` / `--container` are the isolation for
those.

## Bar

Against the baseline, the change must:

1. **Put back an edited, emptied, chmodded or deleted `sssf-private-names`** in
   the git directory after an agent call, with its bytes and mode as they were,
   and stop the run with the existing git-guard `PermissionBreach` whose path
   list names `.git/sssf-private-names`.
2. **Remove a `sssf-private-names` the agent created** where none existed before
   the call, and stop the run with the same breach naming it.
3. **In a worktree run, watch the common git directory's file** (the one the
   hook reads): a change there is put back and named in the breach.
4. **Leave a run that doesn't touch the file unchanged**: no breach whether the
   file is absent or present, and an absent file stays absent.

It must never, where the baseline does otherwise:

- keep an agent's file (here: an agent-created or agent-edited
  `sssf-private-names`);
- run agent code;
- delete or overwrite an engineer file. The file's contents are the engineer's
  private project names: they must never appear in a breach message, trace
  event, log line or test fixture name beyond a made-up placeholder. The
  breach names only the path, as it does today.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- A write after the check, by a process the agent left behind, or a chain the
  agent killed before the check ran (`references/config.md` already lists both).
- An edit the engineer makes to `sssf-private-names` while an agent runs is put
  back too, like an edit to git config (an existing cost, now covering one more
  file; documented below).
- The agent editing the hook instead: `.githooks/pre-push` in the working tree
  is guarded only by `protected_files` and the change-set check, not this
  guard; `core.hooksPath` and `.git/hooks` are already watched.
- `git push --no-verify` by the engineer, and the hook's own logic.
- A file over `PACK_CAP` (16 MiB) behaves as any oversized watched path does today.
- Inside `--sandbox` VMs the guard watches the VM's git directory, not the
  engineer's host clone.
- A submodule's git directory, as for every other watched path.
- The extra, unused `.git/worktrees/<x>/sssf-private-names` path a worktree run
  also watches (`GIT_WATCHED` applies to both directories). The hook never
  reads it; watching it is harmless and keeps the change to one tuple entry.

## Changes

Smallest change: one entry in an existing tuple. No new module-level state, no
new `global`, no new shell code, so no new mechanism.

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
  deletes it turns that check off for the engineer's next push; it is optional,
  and one the agent creates counts as a change too.
- Leave the breach message text as is. It already lists every changed path,
  which names the file; existing tests match on `"git directory"` and
  `"put back as they were"`.

Nothing else in the module changes. `_PACK` skips a path that doesn't exist, so
an absent file packs as nothing; `_differs` then sees a created file as a new
member; `_UNPACK` `rm -rf`s it and extracts nothing for it, which removes it.
That is the same path `config.worktree` and `commondir` take today
(`test_a_commondir_redirect_is_put_back`).

### 2. `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section `# ── the private-names file ──` with these tests. Use the
existing `repo` fixture, `git()` helper and `SimpleNamespace(repo_root=...)`, as
the other `restore_git_dir` tests do; `guard_git_dir` / `restore_git_dir` read
no config, so these tests need none. (If the builder adds one that calls
`permissions.enforce`, build its config from the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`, never a test-only one.) Use a
made-up name such as `secret-ledger` in the file, never a real project.

- `test_the_private_names_file_is_put_back(repo, change)` parametrized over
  `"empty"`, `"edit"`, `"delete"`, `"chmod"`: write
  `.git/sssf-private-names` as `"# private\nsecret-ledger\n"` (mode 0o600),
  record bytes and mode, `guard_git_dir`, apply the change, then
  `pytest.raises(PermissionBreach, match="git directory")` around
  `restore_git_dir`; assert `".git/sssf-private-names"` is in the message,
  `"secret-ledger"` is **not** in the message, and bytes and mode are back.
- `test_a_created_private_names_file_is_removed(repo)`: no file before;
  `guard_git_dir`; agent writes `.git/sssf-private-names` (`"\n"`); breach
  names `.git/sssf-private-names`; afterwards the file doesn't exist.
- `test_an_untouched_private_names_file_is_no_breach(repo, present)`
  parametrized over `True`/`False`: with the file present or absent, guard and
  restore return `[]`; the file's presence and bytes are unchanged.
- `test_a_worktree_guards_the_shared_private_names_file(repo, tmp_path)`:
  write `repo/.git/sssf-private-names`, `git worktree add -q -b run <tree>`,
  guard with `repo_root=tree`, assert
  `str((repo / ".git" / "sssf-private-names").resolve())` is in
  `guard["paths"]`; delete the common file; the breach message contains that
  resolved path; the file is back with its bytes.
- `test_the_hook_reads_the_watched_file()`: read
  `.claude/skills/sssf/templates/githooks/pre-push`, find the
  `sssf-private-names` file name it reads (assert
  `'--git-common-dir)/sssf-private-names"'` is in the text) and assert
  `"sssf-private-names" in permissions.GIT_WATCHED`, so a rename of either
  side fails a test.

Also update the module docstring's line "guard_git_dir copies hooks/, info/,
config and config.worktree aside" to add "and sssf-private-names".

The existing `state()` helper iterates `GIT_WATCHED`, so
`test_a_change_is_put_back_and_stops_the_run` now also covers the file when
present; no change needed there.

### 3. Docs the change makes out of date

- `.claude/skills/sssf/references/config.md`, git-directory section (around
  lines 159–200):
  - In "Before each agent, SSSF saves `hooks/`, `info/`, `config`,
    `config.worktree` and `commondir`…", add `sssf-private-names`.
  - In the paragraph listing why each matters, add: emptying or deleting
    `sssf-private-names` would turn off the pre-push hook's private-names check
    for the engineer's next push, and one the agent creates is a change too.
  - In "Two costs", extend "An edit you make to your git config while an agent
    runs is put back too" to cover `.git/sssf-private-names`.
- `.claude/skills/sssf/cookbooks/install.md`, the **Private names** bullet
  (line 248): add one sentence that SSSF's git-directory guard puts the file
  back and stops the run if an agent changes, deletes or creates it.
- `docs/secret-scan.md`, the **Private names** bullet: same one sentence.
- The repo keeps no CHANGELOG; none to update.
- `app_docs/` doesn't exist in this repo and isn't to be edited. For the
  documenter: `docs/postmortems/git-dir-guard.md` describes the watched set as
  it shipped; note there, if anywhere, that the set later gained
  `sssf-private-names`, rather than rewriting it.

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
  protected. After the build lands, bring it in line with the template (it was
  byte-identical at `9d0f2c4`), e.g. by the skill's update path or by copying
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it, so
  the hub's own runs watch `.git/sssf-private-names` too.

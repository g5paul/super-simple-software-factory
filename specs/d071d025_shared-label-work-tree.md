# Label only files outside the repo as shared in the git-guard breach

## Request

When the git-directory guard puts back a git config or ignore file outside
the repo's git directory, the breach labels it as outside the repo and shared
with everything the engineer runs, says the change may have come from outside
the agent, and lists the keys or line counts that changed. If every changed
path is such a file, the breach opens with "A git config or ignore file
outside the repo changed while the agent ran".

A bypass review found that the label also lands on files inside the repo's
working tree. `_watched` marks a path as shared when it is outside the git
directories, but doesn't leave out `run.repo_root`. So a file the agent can
edit itself, such as `team.gitconfig` in the work tree named by
`include.path`, or `.myignore` named by `core.excludesFile`, gets the label,
and the message points the blame away from the agent. A relative include
like `../team.gitconfig` stays unmarked only because its path is built
through `.git/..`.

Mark a path as shared only when its physical path is outside both the git
directories and the repo's working tree. That must hold however the path was
written: absolute, relative through `.git/..`, or through a symlink. Every
watched file is still watched, put back and reported exactly as today. Only
the label and the lead sentence change. In a worktree run, the work tree to
leave out is the run's own.

Reproduction: run `git config include.path "$PWD/team.gitconfig"` in the
repo, and have the agent append `[alias] z = y` to `team.gitconfig`. Today
the breach says the file is outside the repo and may not be the agent's.

## Baseline

`main` at `4afef72` ("Stamp the gitconfig-keys guard into the hub, and count
the run"). The branch `guard-shared-label` is at the same commit. Judge every
finding against `4afef72`. At that commit the hub's installed
`adws/adw_modules/permissions.py` is byte-identical to the template
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. This was
checked with `diff -q` during planning.

What the baseline does, in the template's `permissions.py`:

- `_watched(run)` (line 248) collects `paths` and records a kind for some of
  them in a local dict `kind` (`"config"` or `"lines"`). It resolves each
  path's parent physically with `_REAL` (`cd -P … && pwd -P`) to get `r`. The
  final component is kept as written, so a symlinked file stays the link
  itself. Then, at lines 339–342:

  ```python
  if p in kind and not any(x.startswith(d) for d in inside for x in (p, r)) \
          and p.rstrip("/") + "/" not in inside and r.rstrip("/") + "/" not in inside:
      if kinds.get(r) != "config":
          kinds[r] = kind[p]
  ```

  `inside` holds the git directories from `git rev-parse
  --path-format=absolute --git-common-dir --git-dir`, each with a trailing
  `/`. Git prints these as real paths. Planning checked this: a repo reached
  through a symlinked parent prints its physical `.git`. The test runs against
  both the logical `p` and the physical `r`. A path *written* through `.git/`
  is therefore left out even when it resolves outside the repo
  (`.git/../../outside.inc`, `.git/lnk/../evil.inc`). A path in the work tree
  but outside `.git` (`/repo/team.gitconfig`) is marked.
- `kinds` goes into the guard dict. `_shared` builds detail lines only for
  paths in `kinds`. `restore_git_dir` adds `SHARED` to those lines, switches
  the lead to "A git config or ignore file outside the repo changed…" when
  every changed path has details, and adds the closing "Files marked as
  shared…" paragraph whenever any details exist. With no details, the message
  is the plain "The agent changed the git directory or the git config…" form.

## Threat model

The agent runs as the engineer's own user and has bash. It can write any file
the engineer can, including files in the work tree, `~/.gitconfig`, any
included config file, and the symlinks on the way to them. It can also leave
a process running. It can't read or change memory in the SSSF process, so it
can't touch the saved packs or the `kinds` map once `guard_git_dir` has
built them.

This change adds no power to restore or stop a run. It only decides which
breach lines carry the "shared, may not be the agent's" label. So the threat
is the message misleading the engineer:

- **Blame pointed away from the agent.** This is today's bug. A file the agent
  can edit as part of its own work (anything in the run's work tree) is
  labelled as maybe someone else's change. The fix decides by the physical
  path, so neither a different spelling nor a symlinked parent can move a
  work-tree file outside the work tree.
- **Blame pointed at the agent.** This is the cost of the fix. A file really
  outside the repo must keep the label. The work tree is resolved physically
  too, so a symlinked `run.repo_root` doesn't fail to match. If the work tree
  can't be resolved, the code falls back to its lexical form, which can only
  leave the label on a file it would otherwise drop, never drop it from a
  shared one.
- **The agent shaping `kinds`.** `kinds` is computed in `guard_git_dir`
  before the agent runs, from config the agent hasn't touched in this call.
  An agent that planted an include or a symlink in an earlier call is already
  stopped by that call's breach.

What this guard can't stop at its level, since the agent is the engineer's
own user: it can't tell who wrote a file; a process the agent left behind can
write after the check; an agent that kills the chain first skips the check.
`--sandbox` and `--container` isolate.

## Bar

Against `4afef72`, the change must:

- put back exactly the paths the baseline puts back, raise `PermissionBreach`
  in exactly the cases the baseline raises it, and return the same `shown`
  list. `guard["paths"]`, `guard["links"]`, both packs and `guard["parents"]`
  must be identical to the baseline's for every repo;
- mark a path in `kinds` only when its physical path `r` is neither equal to
  nor under any git directory, and neither equal to nor under the physical
  work tree of `run.repo_root`. In a worktree run that means the run's own
  worktree, never the main checkout;
- decide by `r` alone, never by the path as written. `.git/../team.gitconfig`,
  `$PWD/team.gitconfig` and `$LINK/team.gitconfig` (with `$LINK` a symlink
  to the repo) are all unmarked. `.git/../../outside.inc` and
  `.git/lnk/../evil.inc` that resolve outside the repo are marked;
- for the reproduction, give the plain baseline message: the "The agent
  changed the git directory or the git config…" lead, no `SHARED` label, no
  key lines and no closing "Files marked as shared…" paragraph;
- keep the label, keys and lead for `~/.gitconfig`, `~/.config/git/*` and any
  included file outside the repo, as today.

It must never:

- keep an agent file, run agent code, or delete or overwrite an engineer
  file, where the baseline doesn't;
- add a file write, a new shell script, or a git call that reads agent
  config. The only new call resolves `run.repo_root` with the existing
  `_REAL` script;
- change any wording of `SHARED`, `UNLISTED`, either lead, the outcome
  strings or the closing paragraph.

## Out of scope

These old holes stay open on purpose:

- **A symlink as the final component.** A watched file that is itself a
  symlink is judged by where the link sits, not by its target, as today. The
  guard never writes through a link. A change through one is reported with
  "NOT all put back" and has its details dropped, so no label shows for it
  anyway.
- **A work tree that contains `HOME`.** An example is a dotfiles repo checked
  out at `~`. `~/.gitconfig` is then inside the work tree and loses the label,
  so an engineer's own change there reads as the agent's. The request asks
  for exactly this, and the file is still put back.
- **The main checkout in a worktree run.** A file in the main repo's work
  tree, named by an include, keeps the label. The request leaves out only the
  run's own work tree.
- Everything in the out-of-scope list of
  `specs/d6fb947e_gitconfig-change-keys.md` stays out of scope: the
  engineer's own change put back, `breach=False` tracing, secrets in
  non-URL key names, `SEEN` / `TOO_LARGE` wording.
- So does everything under "The check catches mistakes and casual attempts"
  in `.claude/skills/sssf/references/config.md`: killing the chain, a leftover
  writer, submodules, `--skip-worktree`, hard links.

## Changes

All code changes are in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. Tests go in
`.claude/skills/sssf/tests/test_git_dir_guard.py`. Docs go in
`.claude/skills/sssf/references/config.md`. No other file changes.

### 1. `_watched`: decide the label by the physical path, leaving out the work tree

In `_watched` (line 248):

1. Just before the resolve loop (`resolved = []` / `kinds = {}` /
   `inside = …`), resolve the work tree physically with the existing `_REAL`
   script. Passing `root + "/."` makes `_REAL` `cd -P` into the root itself:

   ```python
   tree = _sh(_REAL, f"{root}/.").stdout.split("\0")[0]
   tree = tree if tree.startswith("/") else "/" + posixpath.normpath(root).lstrip("/")
   tree = tree.rstrip("/") or "/"
   ```

   `root` is `str(run.repo_root).rstrip("/")`, the run's own work tree. In a
   worktree run, `run.repo_root` is the worktree. Don't use `git rev-parse
   --show-toplevel`; the request names `run.repo_root`.

2. Replace the `inside` list and the marking condition. Keep everything else
   in the loop (the root check, `resolved.append(r)`, and "config" wins over
   "lines"):

   ```python
   outside = [d.rstrip("/") for d in dirs] + [tree]

   def within(path: str) -> bool:
       return any(path == d or path.startswith(d + "/") for d in outside)
   ...
   for p, r in zip(paths, real):
       ...
       resolved.append(r)
       if p in kind and not within(r):
           if kinds.get(r) != "config":
               kinds[r] = kind[p]
   ```

   `p`, the path as written, is no longer tested. `r` is already physical in
   its parent, or lexically normalised where the parent doesn't exist, and
   `dirs` are real paths from git. If `tree` is `/`, `within` is true for
   every path and nothing is marked. That is the safe direction, since the
   breach then blames the agent. No special case is needed.

3. Update the docstring's last paragraph:

   > The third value maps a resolved path to "config" or "lines" for each
   > config, ignore or attributes file whose physical path is outside the
   > repo's git directories and outside the work tree at `run.repo_root`
   > (the run's own, in a worktree run): files shared with everything else
   > the engineer runs. A file in the work tree is the agent's to edit, so
   > it isn't one, however its path was written.

`posixpath` is already imported in the function, before this point (line
317). Make sure the new lines come after that import.

Nothing else changes: `_shared`, `restore_git_dir` and the lead logic stay as
they are. With fewer paths in `kinds`, the reproduction's details come out
empty and the plain message follows on its own.

### 2. Comment above `KEYS_PER_FILE`

At line 452, change "A git config file outside the repo's git directory is
shared with everything else the engineer runs" to "A git config file outside
the repo, meaning both its git directory and its work tree, is shared with
everything else the engineer runs". Leave the rest of the comment as it is.

### 3. Tests, in `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add these tests in the `# ── shared git config files … ──` section, after
`test_a_value_added_to_an_existing_key_is_marked_changed`. Build `run` with
the existing `shipped(repo_root)` helper. It loads the shipped
`templates/sssf.config.yaml` and sets `repo_root`. Then call
`permissions.guard_git_dir(run)` and use `_breach(run, guard)`. `PLAIN` below
is `"The agent changed the git directory or the git config"`.

1. `test_a_work_tree_include_is_not_labelled_shared` is the reproduction.
   Write `repo/team.gitconfig` = `"[user]\n\tname = t\n"`, then run
   `git(repo, "config", "include.path", str(repo / "team.gitconfig"))`.
   Guard, then append `"[alias]\n\tz = y\n"` to the file. Assert:
   - `str((repo / "team.gitconfig").resolve()) not in guard["kinds"]`, and
     `str((repo / "team.gitconfig").resolve()) in guard["paths"]` (it is
     still watched);
   - `msg.startswith(PLAIN)`;
   - `"outside the agent" not in msg`, `"Files marked as shared" not in msg`,
     `"alias.z" not in msg`;
   - `"team.gitconfig" in msg` and `"put back as they were" in msg`;
   - the file's bytes equal the saved ones.
2. `test_a_work_tree_ignore_file_is_not_labelled_shared`: write
   `repo/.myignore` = `"keep\n"`, run `git(repo, "config",
   "core.excludesFile", str(repo / ".myignore"))`, guard, append
   `"evil\n"`. Assert `msg.startswith(PLAIN)`, `"line(s) added" not in msg`,
   `"outside the agent" not in msg`, `".myignore" in msg`, and the file is
   back to `"keep\n"`.
3. `test_a_work_tree_include_through_a_symlinked_root_is_not_shared`: create
   `link = tmp_path / "linkrepo"` as a symlink to `repo`, and use
   `run = shipped(link)`. Write `repo/team.gitconfig`, then
   `git(repo, "config", "include.path", f"{link}/team.gitconfig")`. Guard,
   append `"[alias]\n\tz = y\n"`, then assert as in test 1: plain lead, no
   label, file restored.
4. `test_a_dotdot_include_into_the_work_tree_is_not_shared`: run
   `git(repo, "config", "include.path", "../team.gitconfig")`, with
   `repo/team.gitconfig` written first. Same change and assertions as test 1.
   This pins down the `.git/..` spelling now that `p` isn't tested.
5. `test_a_dotdot_include_outside_the_repo_is_still_shared`: write
   `(repo / ".git" / "../../outside.inc").resolve()` =
   `"[user]\n\tname = t\n"`, then run `git(repo, "config", "--add",
   "include.path", "../../outside.inc")`. Guard, append
   `"[alias]\n\tz = y\n"`. Assert `"outside the agent" in msg`,
   `"+ alias.z" in msg`, and
   `msg.startswith("A git config or ignore file outside the repo changed")`.
   The file is restored. (The baseline left this unmarked. The new rule marks
   it, because its physical path is outside.)
6. `test_a_worktree_run_leaves_out_its_own_tree_only`: add a worktree with
   `git(repo, "worktree", "add", "-q", "-b", "run", str(tree))`, where
   `tree = tmp_path / "tree"`, and use `run = shipped(tree)`. Write
   `tree/own.gitconfig` and `repo/main.gitconfig`, both
   `"[user]\n\tname = t\n"`. Add both as includes in the shared config
   with `git(repo, "config", "--add", "include.path", <absolute path>)`.
   Guard. Assert `str((tree / "own.gitconfig").resolve()) not in
   guard["kinds"]` and `guard["kinds"].get(str((repo /
   "main.gitconfig").resolve())) == "config"`. Then append
   `"[alias]\n\tz = y\n"` to `own.gitconfig` only, and assert the plain lead
   and no `"outside the agent"`. Both files end with their saved bytes.
7. `test_the_home_gitconfig_is_still_shared`: under `shipped(repo)`,
   `guard["kinds"]` maps `str((home / ".gitconfig").resolve())` to
   `"config"`. This guards the fallback.

Existing tests must pass unchanged. Planning checked the ones near this code:
- `test_an_include_through_dotdot_is_watched_and_kept[../../outside.inc]`
  also changes a hook, so not every changed path is shared. The lead stays
  the plain one, and its `match="put back as they were"` still holds.
- `test_an_include_through_a_symlinked_parent_is_watched` matches
  `"evil.inc"`, and that still holds with the label added.
- `test_a_repo_config_change_keeps_todays_message` and the other shared-key
  tests use `~/.gitconfig`, `~/.config/git/*` or `repo/.git/config`. None of
  their outcomes change.

In the test module docstring, change "(one outside the repo, like
~/.gitconfig)" to "(one outside the repo's git directory and work tree, like
~/.gitconfig)".

### 4. Docs: `.claude/skills/sssf/references/config.md`

In the "Two costs." paragraph, change "A git config or ignore file outside
the repo is shared with everything you run, so the breach marks it as maybe
not the agent's change." to:

> A git config or ignore file outside the repo, meaning outside both its git
> directory and its working tree, is shared with everything you run, so the
> breach marks it as maybe not the agent's change. A file in the working
> tree, such as one an `include.path` or `core.excludesFile` names there, is
> the agent's to edit, so it gets no mark, however its path is written. In a
> worktree run that's the run's own worktree.

Leave the rest of the paragraph as it is.

### Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: see step 4.
- The `_watched` docstring and the comment above `KEYS_PER_FILE` in
  `permissions.py`: steps 1 and 2.
- The module docstring of `test_git_dir_guard.py`: step 3.
- The repo has no CHANGELOG and no `app_docs/`, so the documenter has no
  older write-up to flag. `specs/d6fb947e_gitconfig-change-keys.md` says
  `kinds` covers paths "outside the repo's git directories". It's a record of
  what was asked then, and stays as written.
- `README.md` doesn't describe the breach message, so nothing changes there.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, the new ones included:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite. The hook tests skip without gitleaks:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 4afef72 -- .claude/skills/sssf/templates/` shows only
   `adws/adw_modules/permissions.py`. In that diff, only `_watched` and the
   comment above `KEYS_PER_FILE` change. `_shared`, `restore_git_dir`,
   `_PACK`, `_UNPACK`, `_REAL` and `_LINKS` are untouched.
4. The reproduction by hand, outside pytest, in a scratch repo with a scratch
   `HOME`. Run `git config include.path "$PWD/team.gitconfig"`, call
   `permissions.guard_git_dir`, append `[alias] z = y` to `team.gitconfig`,
   then call `permissions.restore_git_dir`. The breach opens with "The agent
   changed the git directory or the git config", doesn't contain "outside
   the agent", and `team.gitconfig` is back as it was. Test 1 automates
   this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `4afef72`. Until then the hub's own runs keep the baseline label.

# Refuse every watched path with a `..` that doesn't fully resolve

## Request

Run d817e62b made the git-directory guard refuse a watched path with a `..`
right after a part that `cd` can't enter (`_LOOSE_DOTDOT`). Its bypass review
found the mirror case still open: `<repo>/lnk/../missing/x.inc`, with
`<repo>/lnk -> out/a`. The `..` comes *before* the missing part, so
`_LOOSE_DOTDOT` passes it. `_REAL` can't resolve the parent
(`<repo>/lnk/../missing` doesn't exist), so `_watched` falls back to the
lexical `<repo>/missing/x.inc`. The agent runs `mkdir out/missing` and writes
`[core] fsmonitor = touch PWNED` to `out/missing/x.inc`. Git now loads that
file, and `restore_git_dir` returns `[]`.

The fix: refuse every watched path that has a `..` component and doesn't
fully resolve, wherever the missing part sits. The message stays exactly as it
is now. A path with `..` that fully resolves, and a path with no `..`, keep
exactly today's behaviour.

## Baseline

`main` at `176e885` ("Stamp the include-refuse guard into the hub"). Every
finding is judged against that commit. At `176e885`, in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- `_REAL` prints, for each path, its parent resolved with `cd -P` and the
  leaf appended. Where the leaf is `..` or `.`, it resolves the whole path. It
  prints an empty field where `cd -P` fails: a part of the parent is missing,
  a dangling link, not a directory, or not searchable.
- `_watched` calls `real = _sh(_REAL, *paths)`. Then, for the paths with a `..`
  component (`dotted`), it runs `_LOOSE_DOTDOT`, which flags a path only when
  a `..` comes right after a prefix that `[ -d ]`/`[ -x ]` rejects. A flagged
  path raises the `PermissionBreach` "A git path SSSF watches has `..` after a
  directory that doesn't exist yet, …". Every other path whose `_REAL` field
  is empty takes the lexical fallback `"/" + posixpath.normpath(p).lstrip("/")`
  in the `for p, r in zip(paths, real)` loop. That fallback, for a path with
  `..`, is the hole: normpath cancels `lnk/..` lexically, but the kernel
  goes through the link.
- `_LOOSE_DOTDOT` flags a subset of "has `..` and `_REAL` field is empty":
  a `..` after a prefix `cd` can't enter means `cd -P` on the parent (or, for
  a trailing `..`, on the whole path) must fail too.
- `git rev-parse --git-path hooks` normalizes `core.hooksPath` lexically, so
  the hooks entry in `paths` never has `..`. The git dirs come from
  `rev-parse --path-format=absolute` and have none either.

## Threat model

The agent runs as the engineer's own user and has bash. It can create
directories and symlinks anywhere that user can write, chmod the user's own
directories, and write any file the user can. It can't change the packs, which
live in SSSF's process memory, and it can't change the engineer's config from
before the run started.

The trigger is a setup the engineer made: a watched path (an `include.path`,
`includeIf.*.path`, `core.excludesFile` or `core.attributesFile` value) with a
`..` and a part that doesn't exist yet or can't be entered. The agent doesn't
build that setup. It only creates the missing directory (or opens it up); the
kernel then resolves `..` physically, through whatever symlink sits before it,
to a file the guard doesn't watch.

Because the agent is the engineer's own user, this guard can't stop, at its
level:

- killing the chain before the check runs;
- a process left behind that writes after the check;
- writes to paths the guard doesn't watch;
- a race between the check and the next git command.

`--sandbox` and `--container` are the isolation for those. This change only
closes the lexical fallback for paths with `..`.

## Bar

Against `176e885`, the change must:

- stop the run in `guard_git_dir`, before the next agent runs, with
  `PermissionBreach` whenever a watched path has a `..` component and `_REAL`
  gives it no physical path (its field doesn't start with `/`). That covers
  the mirror case `<repo>/lnk/../missing/x.inc`, a deeper one
  `<repo>/lnk/../missing/deeper/x.inc`, and every shape the baseline already
  refuses (`<repo>/lnk/missing/../../x.inc`, `<repo>/missing/../lnk2/../x.inc`,
  `<repo>/lnk/missing/../../rl/x.inc`, `<repo>/missing/../x.ignore`), for
  `include.path`, `includeIf.*.path`, `core.excludesFile` and
  `core.attributesFile`.
- keep the breach message byte-for-byte as at `176e885`: same text, same
  per-path line (`  - <key> in <source>: <path>`, or `  - <path>` when no key
  named it), same closing "Write the path without `..`, or create the missing
  directory first, then run again."
- create, delete, chmod or write nothing when it refuses. The check reads only
  `_REAL`'s output, which `_watched` already computes.
- leave every other path as it is today:
  - a path with `..` whose `_REAL` field is a physical path is resolved and
    watched exactly as today;
  - a path with no `..` component, missing parent or not, takes today's
    lexical fallback and never trips the check;
  - the check costs no extra shell call (the baseline's `_LOOSE_DOTDOT` call
    goes away).

Where the baseline doesn't, the change must never keep an agent file, run
agent code, or delete or overwrite an engineer file. It changes no pack,
restore, unpack, link, label or message logic.

One cost the request accepts by saying "wherever the missing part sits": a
relative include written into `.git/config` as `../team/x.inc`, where `team/`
doesn't exist yet, becomes `<repo>/.git/../team/x.inc` in `paths` and is now
refused, though `.git` is a real directory. The fix the message gives (write
it without `..`, or create the directory) works for it. `config.md` says so.

## Out of scope

Old holes this change leaves open. A reviewer should file them as old:

- A missing directory with no `..` anywhere in the path, which the agent
  creates as a symlink (`include.path = <repo>/missing/x.inc`,
  `ln -s /out <repo>/missing`). The guard sees the change but won't write
  through the link, so the run stops with "NOT all put back", as `config.md`
  documents.
- The guard turning itself off when git can't read the config (`config
  --list` fails, so `_watched` sees no includes). Named in `176e885`'s commit
  message as a follow-up; untouched here. This is also why an include behind
  an unsearchable directory never reaches the check in a test: git fails first.
- `core.hooksPath` with `..`: git normalizes it lexically
  (`rev-parse --git-path hooks`), so no `..` reaches `paths`, and the baseline
  handling stands.
- A `..` path that resolves today, where the agent later swaps a symlink on
  the way. The existing `_LINKS` / `parents` logic covers that; unchanged.
- The message's first sentence says "`..` after a directory that doesn't exist
  yet", which reads loosely for the mirror case (the `..` comes before the
  missing part). The request fixes the message as it is; wording only.
- Everything the Threat model lists as beyond this guard.

## Design

One condition replaces one script. It predicts nothing: when it passes, the
guard watches the same paths it does today.

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. Delete the `_LOOSE_DOTDOT` constant and the five-line comment above it
   (currently lines 234–250). Nothing else references it outside
   `_watched` and the test file (check with
   `grep -rn _LOOSE_DOTDOT .claude/skills/sssf`).
2. In `_watched`, replace the two lines

   ```python
   dotted = [p for p in paths if ".." in p.split("/")]
   loose = [q for q in _sh(_LOOSE_DOTDOT, *dotted).stdout.split("\0") if q] if dotted else []
   ```

   with

   ```python
   # A path with ".." that cd -P can't resolve would take the lexical fallback
   # below, but the kernel resolves ".." physically, maybe through a link, once
   # the missing part exists, so git can read a file the guard doesn't watch.
   loose = [p for p, r in zip(paths, real) if not r.startswith("/") and ".." in p.split("/")]
   ```

   Keep `if loose:`, `which()` and the `raise PermissionBreach(...)` exactly as
   they are. `not r.startswith("/")` is the same test the fallback loop uses,
   so the refused set is exactly "has `..` and would fall back". `zip` drops
   `real`'s trailing empty field.
3. Update the comments that describe the rule:
   - `_watched` docstring, last paragraph, becomes: "A watched path with `..`
     that doesn't fully resolve, because a part of it doesn't exist yet or
     can't be entered, stops the run with `PermissionBreach`, since the guard
     can't know which file git will read."
   - The comment above `import posixpath` ends "…lexically where it doesn't,
     except that a `..` after a part cd can't enter refuses the run (see
     _LOOSE_DOTDOT)." Change the tail to "…lexically where it doesn't, except
     that a path with `..` that doesn't resolve refuses the run."

Don't touch `_PACK`, `_UNPACK`, `_REAL`, `_LINKS`, `_pack`, `_members`,
`_differs`, `guard_git_dir`, `restore_git_dir`, `_shared`, `_shown_key`,
`GIT_WATCHED`, or the breach text.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

All in the section `# ── a ".." after a missing directory ──` (from line
~1367). Keep using `shipped(repo)` (which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`), `_dotdot_setup`,
`FSMONITOR` and `state`. Rename the section header to
`# ── a ".." in a path that doesn't resolve ──`.

1. `_dotdot_values`: append two values,
   `f"{repo}/lnk/../missing/x.inc"` and `f"{repo}/lnk/../missing/deeper/x.inc"`,
   and widen `test_a_dotdot_after_a_missing_dir_stops_the_run_first`'s
   parametrize to `[0, 1, 2, 3, 4]`. Rename it
   `test_a_dotdot_path_that_does_not_resolve_stops_the_run_first`. Add
   `assert not (out / "missing").exists()` beside the existing "nothing made"
   asserts.
2. Add `test_the_mirror_case_once_the_dir_exists_watches_the_real_file`,
   modelled on `test_once_the_missing_dir_exists_the_real_file_is_watched`:
   `include.path = f"{repo}/lnk/../missing/x.inc"`; `guard_git_dir` raises;
   then, as the agent would, `(out / "missing").mkdir()` and write `FSMONITOR`
   to `out / "missing" / "x.inc"`; `git config --show-origin --get
   core.fsmonitor` shows the value and `pwned`, which proves the shape is real;
   `guard_git_dir(run)` now succeeds and `str((out / "missing" /
   "x.inc").resolve())` is in `guard["paths"]`, while the lexical
   `f"{repo}/missing/x.inc"` is not.
3. `test_a_missing_dir_without_dotdot_after_it_is_unchanged`: drop the
   `"lnk-dotdot"` parameter (it is now refused, in test 1). Rename to
   `test_a_missing_dir_without_dotdot_is_unchanged` and parametrize over
   `f"{repo}/missing/x.inc"` and `f"{repo}/missing/deeper/x.inc"`. Same asserts:
   no raise, `posixpath.normpath(value) in guard["paths"]`,
   `restore_git_dir(run, guard) == []`.
4. Add `test_a_dotdot_path_that_resolves_is_unchanged`: `_dotdot_setup`, then
   `include.path = f"{repo}/lnk/../x.inc"` with `out/x.inc` not created (the
   parent resolves, only the leaf is missing). `guard_git_dir(run)` doesn't
   raise; `str(out.resolve() / "x.inc")` is in `guard["paths"]`;
   `restore_git_dir(run, guard) == []`. (The existing
   `test_an_include_through_dotdot_is_watched_and_kept` and
   `test_an_include_through_a_symlinked_parent_is_watched` cover `..` paths to
   existing files and must pass unchanged.)
5. Delete `test_a_path_without_dotdot_is_never_checked`: it monkeypatches the
   removed `_LOOSE_DOTDOT`, and the new check runs no shell at all. Test 3
   covers the no-`..` behaviour.
6. `test_an_excludes_file_dotdot_after_a_missing_dir_is_refused`: parametrize
   the value over `f"{repo}/missing/../x.ignore"` and
   `f"{repo}/lnk/../missing/x.ignore"` (call `_dotdot_setup(repo, tmp_path)`
   first). The breach names `core.excludesfile` and the value.
7. Add `test_an_attributes_file_dotdot_that_does_not_resolve_is_refused`:
   `_dotdot_setup`, `core.attributesFile = f"{repo}/lnk/../missing/x.attr"`;
   the breach names `core.attributesfile` and the value.
8. Replace `test_a_dotdot_after_an_unsearchable_dir_is_flagged` with
   `test_a_dotdot_after_an_unsearchable_dir_does_not_resolve`. Keep the
   `skipif(os.geteuid() == 0, …)` and the comment that git fails on
   `config --list` first, so the shipped script is checked directly. With
   `value = f"{repo}/lnk/../x.inc"` and `os.chmod(out / "a", 0o600)` inside a
   `try/finally` that restores `0o755`: assert
   `permissions._sh(permissions._REAL, value).stdout.split("\0")[0] == ""` (no
   physical path, so `_watched` refuses it) and that `out / "a"` still has mode
   `0o600`. After the `finally`, assert the field is now
   `str(out.resolve() / "x.inc")`.
9. The module docstring line "A watched path with `..` after a directory that
   doesn't exist yet, or can't be entered, stops the run first." becomes "A
   watched path with `..` that doesn't fully resolve stops the run first."

Every other test in the file must pass unchanged.

### `.claude/skills/sssf/references/config.md`

Replace the paragraph that begins "An include path, or another git path SSSF
watches, that has `..` after a directory…" (after the pack-cap paragraph) with:

> An include path, or another git path SSSF watches, that has `..` in it and
> a directory on the way that doesn't exist yet, or that can't be entered,
> can't be checked. Once that directory is made or opened up, git resolves
> `..` through the real directories, maybe through a symlink, and reads a
> different file from the one SSSF watches. So the run stops before the next
> agent with a breach naming the key, the config file and the path. That
> includes a relative include such as `../team/x.inc` in `.git/config` while
> `team/` doesn't exist. Write the path without `..`, or create the missing
> directory first. A path with `..` whose directories all exist, and a path
> with no `..` at all, are watched as before. If the agent makes a missing
> directory in a path without `..` a symlink, the run stops with "NOT all put
> back".

## Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: the paragraph above.
- The `_watched` docstring and the comment above `import posixpath`, and the
  deleted `_LOOSE_DOTDOT` comment: Design steps 1 and 3.
- The module docstring of `.claude/skills/sssf/tests/test_git_dir_guard.py`:
  test step 9.
- No README usage or help text covers this; the repo keeps no CHANGELOG.
  There is no `app_docs/` write-up of the git guard for the documenter to flag.
- `specs/d817e62b_refuse-dotdot-include_v2.md` lists the mirror case as out of
  scope. It is a record of that run and stays as it is.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, including the new ones:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite (the hook tests skip without gitleaks):
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `grep -rn _LOOSE_DOTDOT .claude/skills/sssf` finds nothing (exit 1 is the
   pass here; judge it by hand, not as a check).
4. `git diff --stat 176e885 -- .claude/skills/sssf/templates/` lists only
   `adws/adw_modules/permissions.py`, and that diff touches only the removed
   `_LOOSE_DOTDOT`, the `loose =` line with its comment, the `_watched`
   docstring and the `import posixpath` comment. The breach string is
   unchanged: `git diff 176e885 -- .claude/skills/sssf/templates/adws/adw_modules/permissions.py | grep '^[-+].*didn.t exist yet'`
   prints nothing.
5. The mirror reproduction by hand, outside pytest, in a scratch repo with a
   scratch `HOME`: `mkdir -p /tmp/out/a`, `ln -s /tmp/out/a lnk`,
   `git config include.path "$PWD/lnk/../missing/x.inc"`, then call
   `permissions.guard_git_dir`. It raises the breach naming `include.path` and
   the path, and neither `/tmp/out/missing` nor `$PWD/missing` exists. Test 1
   (value 3) automates this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`. It needs no new tool or dependency.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `176e885`. Until then, the hub's own runs keep the `176e885` behaviour,
  with the mirror case open.

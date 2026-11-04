# Refuse a watched path with a `..` after a directory that doesn't exist

## Request

The git-directory guard watches every file an `include.path` or
`includeIf.*.path` names. Before the agent runs, `_watched` resolves each one
to a physical path with `_REAL`. `_REAL` runs `cd -P` on the parent. When the
parent has a directory that doesn't exist yet, followed later by `..`, the
`cd` fails, and `_watched` falls back to the lexical path
(`posixpath.normpath`). Once the agent creates the missing directory, the
kernel resolves the path through any symlink on the way, and git loads a
different file from the one the guard watches.

Reproduction: `include.path = /repo/lnk/missing/../../x.inc`, with
`/repo/lnk -> /out/a`. The guard watches `/repo/x.inc`. The agent runs
`mkdir /out/a/missing` and writes `[core] fsmonitor = touch PWNED` to
`/out/x.inc`. Git now loads `/out/x.inc`, and `restore_git_dir` returns `[]`.
Two more cases of the same shape: `<repo>/missing/../lnk2/../x.inc`, and
`<repo>/lnk/missing/../../rl/x.inc` where `rl` links back into the repo.

The fix: before the agent runs, if any watched path has a `..` right after a
directory that doesn't exist (or isn't a directory), the run stops with a
`PermissionBreach`, the way the pack cap does. The message names the include
and the path, and says to write it without `..`, or to create the missing
directory first. Nothing else changes. We refuse instead of predicting the
file git would load: an earlier run tried predicting, kept missing cases, and
deleted engineer files when it put back a predicted path that turned out to be
a directory or the work tree.

## Baseline

`main` at `946a38c`. Every finding is judged against that commit. At
`946a38c`, in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- `_watched` builds `paths` from the git dirs, every `--show-origin` source,
  each `core.excludesFile` / `core.attributesFile` value, each `include.path`
  and `includeIf.*.path` value (made absolute by `absolute()`), the
  `rev-parse --git-path hooks` result, and the four home paths.
- `real = _sh(_REAL, *paths)`. Where a field is empty (the parent didn't
  resolve), the loop uses `"/" + posixpath.normpath(p).lstrip("/")`, the
  lexical path. That is the hole.
- `git rev-parse --git-path hooks` already normalizes `core.hooksPath`
  lexically (checked: `core.hooksPath=$PWD/missing/../h` gives `$PWD/h`), so
  the hooks entry in `paths` never has `..`.
- The only breaches `_watched` raises are "Could not resolve the git paths to
  watch" and "A git path resolves to the filesystem root".

## Threat model

The agent runs as the engineer's own user and has bash. It can create
directories and symlinks anywhere that user can write, including a missing
directory on the way to a watched path, and it can write any file the user
can. It can't change the packs, which live in SSSF's process memory, and it
can't change the engineer's config from before the run started.

The trigger is a setup the engineer made: an include path with `..` after a
directory that doesn't exist yet. The agent doesn't build it. It only creates
the missing directory, so the kernel's physical resolution of `..` lands
somewhere the guard doesn't watch.

What this guard can't stop at its level, since the agent is the engineer's
user: killing the chain before the check runs, a process left behind that
writes after the check, writes to paths the guard doesn't watch, and a race
between the check and the next git command. `--sandbox` and `--container`
are the isolation. This change only closes the lexical fallback for paths
with `..` after a missing directory.

## Bar

Against `946a38c`, the change must:

- stop the run in `guard_git_dir`, before the first agent runs, when any
  watched path has a `..` component right after a prefix that isn't a
  directory (missing, a dangling link, a file, or unsearchable). It raises
  `PermissionBreach`. The message names the key and config file that set the
  path, where it came from one, and the path itself. It says the agent did not
  run, and to write the path without `..` or create the missing directory
  first. This covers all three reproduction shapes, for `include.path` and
  `includeIf.*.path`.
- create, delete or write nothing when it refuses. The check only tests
  paths with `[ -d ]`.
- leave every other path as it is today. A path that resolves today, `..` or
  not, never trips the check: `cd -P` succeeding means every prefix before
  each `..` was a searchable directory. A path with a missing part and no `..`
  after it doesn't trip it either. Paths without a `..` component cost no extra
  shell call.

It must never, where the baseline doesn't: keep an agent file, run agent code,
or delete or overwrite an engineer file. It changes no pack, restore,
unpack, link or label logic.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- A missing directory with no `..` after it, which the agent creates as a
  symlink, for example `include.path = /repo/missing/x.inc` and
  `ln -s /out /repo/missing`. As `config.md` documents, the guard sees the
  change but won't write through the link, so the run stops with
  "NOT all put back".
- A missing directory with no `..` after it, but with a `..` before it that
  passes through a symlink. For example, `include.path = /repo/lnk/../missing/x.inc`
  with `lnk -> /out/a` falls back to the lexical `/repo/missing/x.inc`,
  while the kernel would read `/out/missing/x.inc` once the agent makes
  `/out/missing`. The request keeps today's behaviour for a path whose missing
  part has no `..` after it, so this stays as on `946a38c`. File it as old.
- `core.hooksPath` with `..`: git normalizes it lexically itself
  (`rev-parse --git-path hooks`), so no `..` reaches `paths`. The check never
  fires on it, and the baseline handling stands.
- A `..` path that resolves today, where the agent later swaps a symlink on
  the way. The existing `_LINKS` / `parents` logic covers that, unchanged.
- Everything under "What this guard can't stop" in the threat model.

## Design

One new shell script and one new check in `_watched`. No predicting, and no
change to what gets watched when the check passes.

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. After `_LINKS`, add a script with a comment:

   ```python
   # Prints, NUL-separated, each path with a ".." right after a part that isn't
   # a directory yet (missing, a dangling link, a file). cd -P can't resolve
   # such a path, and once the agent makes that part, the kernel resolves ".."
   # through it, maybe into a link, so git reads a file the guard doesn't watch.
   # Nothing is created or written.
   _LOOSE_DOTDOT = """for p in "$@"; do
     q=; rest=${p#/}
     while [ -n "$rest" ]; do
       c=${rest%%/*}
       case $rest in */*) rest=${rest#*/} ;; *) rest= ;; esac
       if [ "$c" = .. ] && [ ! -d "${q:-/}" ]; then printf "%s\\0" "$p"; break; fi
       q=$q/$c
     done
   done
   """
   ```

   Keep the `\\0` escaping the same as `_REAL` and `_LINKS`, which are Python
   non-raw strings.

2. In `_watched`, record where each config-named path came from. Next to
   `kind`, add `named: dict[str, tuple[str, str]] = {}`. In the
   `--show-origin` loop, wherever a path is appended for
   `core.excludesfile` / `core.attributesfile` or an include key, also set
   `named.setdefault(paths[-1], (key, source))`. Here `key` is the key as git
   listed it (for example `include.path`, `includeif.onbranch:main.path`) and
   `source` is the absolute config file it was read from.

3. Right after the `if len(real) != len(paths) + 1:` check, and before the
   `tree = ...` line, add:

   ```python
   dotted = [p for p in paths if ".." in p.split("/")]
   loose = [q for q in _sh(_LOOSE_DOTDOT, *dotted).stdout.split("\0") if q] if dotted else []
   if loose:
       def which(p: str) -> str:
           key, source = named.get(p, ("", ""))
           return (f"  - {_shown_key(key)} in {source}: {p}" if key else f"  - {p}")
       raise PermissionBreach(
           "A git path SSSF watches has `..` after a directory that doesn't exist yet. "
           "Once something is made there, git resolves `..` through it and can read a "
           "different file from the one SSSF watches, so the agent did not run, and no run "
           "will until that changes:\n" + "\n".join(which(p) for p in loose) + "\n\n"
           "Write the path without `..`, or create the missing directory first, then run again.")
   ```

   `_shown_key` is defined later in the module. It is only called at run
   time, so that's fine. It masks URL userinfo and non-printable characters in
   an `includeIf` condition.

4. Update the `_watched` docstring with one sentence: a watched path with
   `..` after a directory that doesn't exist yet stops the run
   (`PermissionBreach`), since the guard can't know which file git will read.
   Update the comment above `import posixpath` ("Each parent is resolved with
   cd -P where it exists, lexically where it doesn't") to add: "...except
   that a `..` after a part that doesn't exist refuses the run (see
   `_LOOSE_DOTDOT`)."

Don't touch `_PACK`, `_UNPACK`, `_REAL`, `_LINKS`, `_pack`, `_members`,
`_differs`, `guard_git_dir`, `restore_git_dir`, `_shared` or `GIT_WATCHED`.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section `# ── a ".." after a missing directory ──` at the end. Build
each run with the existing `shipped(repo)` helper, which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`. Use the existing `FSMONITOR`
and `state` helpers. A shared fixture or helper can set up
`out = tmp_path / "out"`, `(out / "a").mkdir(parents=True)` and
`(repo / "lnk").symlink_to(out / "a")`.

1. `test_a_dotdot_after_a_missing_dir_stops_the_run_first`, parametrized over
   the three shapes. Shape 2 also needs `(repo / "lnk2").symlink_to(out / "a")`,
   and shape 3 needs `(out / "rl").symlink_to(repo)`. The values:
   - `f"{repo}/lnk/missing/../../x.inc"`
   - `f"{repo}/missing/../lnk2/../x.inc"`
   - `f"{repo}/lnk/missing/../../rl/x.inc"`

   Run `git config --add include.path <value>`, then take `before = state(repo)`
   and the text of `home/.gitconfig`. Assert `permissions.guard_git_dir(run)`
   raises `PermissionBreach`. Its message contains `include.path`, the value,
   "did not run", "without `..`" and "create the missing directory". Then
   assert nothing was made: `not (out / "a" / "missing").exists()`,
   `not (repo / "missing").exists()`, `state(repo) == before`, and
   `~/.gitconfig` unchanged.
2. `test_once_the_missing_dir_exists_the_real_file_is_watched`. This is shape 1
   end to end. After the refusal, play the agent: `mkdir out/a/missing` and
   write `FSMONITOR` to `out/x.inc`. Check with
   `git config --show-origin --get core.fsmonitor` that git now loads
   `out/x.inc`, which proves the shape is real. Then call `guard_git_dir(run)`
   again. It does NOT raise, because the path now resolves physically, and
   `str((out / "x.inc").resolve())` is in `guard["paths"]`. So the refusal only
   fires while the directory is missing, and once it exists, today's physical
   resolution watches the file git reads.
3. `test_an_include_if_dotdot_after_a_missing_dir_names_its_key`.
   `git config includeIf.onbranch:main.path f"{repo}/lnk/missing/../../x.inc"`.
   The breach contains `includeif.onbranch:main.path` and the path.
4. `test_a_missing_dir_without_dotdot_after_it_is_unchanged`, parametrized
   over `f"{repo}/missing/x.inc"` and `f"{repo}/lnk/../missing/x.inc"`.
   `guard_git_dir(run)` does not raise, and `guard["paths"]` contains
   `posixpath.normpath(value)`, the lexical path the baseline falls back to,
   since `cd -P` fails on both parents. `restore_git_dir(run, guard) == []`.
5. `test_a_path_without_dotdot_is_never_checked`. Monkeypatch
   `permissions._LOOSE_DOTDOT` to `'printf "%s\\0" "$@"'`, which flags every
   path it's given. With no `..` in any config, `guard_git_dir(shipped(repo))`
   still succeeds. That shows only paths with a `..` component reach the
   script.
6. `test_an_excludes_file_dotdot_after_a_missing_dir_is_refused`.
   `git config core.excludesFile f"{repo}/missing/../x.ignore"`. The breach
   names `core.excludesfile`.

Every existing test in the file must pass unchanged, in particular
`test_an_include_through_dotdot_is_watched_and_kept` and
`test_an_include_through_a_symlinked_parent_is_watched`, which have `..`
paths that resolve today.

Add one sentence to the module docstring: "A watched path with `..` after a
directory that doesn't exist yet stops the run first."

### `.claude/skills/sssf/references/config.md`

In the git-directory section, after the paragraph ending "...the run stops
with "NOT all put back"." (the pack-cap paragraph), add a paragraph:

> An include path, or another git path SSSF watches, that has `..` after a
> directory that doesn't exist yet can't be checked. Once something is made
> there, git resolves `..` through it, maybe through a symlink, and reads a
> different file from the one SSSF watches. So the run stops before the first
> agent with a breach naming the key, the config file and the path. Write the
> path without `..`, or create the missing directory first. A path with a
> missing directory and no `..` after it is watched as before, and if the
> agent makes that directory a symlink, the run stops with "NOT all put back".

## Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: the paragraph above.
- The `_watched` docstring and the comment above `import posixpath`:
  step 4.
- The module docstring of `.claude/skills/sssf/tests/test_git_dir_guard.py`:
  one sentence.
- No README usage, help text or CHANGELOG covers this. The repo keeps no
  CHANGELOG. There is no `app_docs/` write-up of the git guard to flag for
  the documenter.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, the new ones included:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite. The hook tests skip without gitleaks:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 946a38c -- .claude/skills/sssf/templates/` shows only
   `adws/adw_modules/permissions.py`. In that diff only `_LOOSE_DOTDOT` (new),
   `_watched` and its comments change. `_PACK`, `_UNPACK`, `_REAL`, `_LINKS`,
   `_differs`, `guard_git_dir` and `restore_git_dir` are untouched.
4. The reproduction by hand, outside pytest, in a scratch repo with a scratch
   `HOME`: `ln -s /tmp/out/a lnk`,
   `git config include.path "$PWD/lnk/missing/../../x.inc"`, then
   `permissions.guard_git_dir`. It raises a breach naming `include.path` and
   the path, and `/tmp/out/a/missing` still doesn't exist. Test 1 automates
   this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`. No new tool or dependency.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `598e800`. Until then the hub's own runs keep the baseline fallback.

# Refuse a watched path with a `..` after a directory that doesn't exist

Revision 2. Spec review 1 found that the Bar promised to refuse an
unsearchable prefix, but `_LOOSE_DOTDOT` only tested `[ -d ]`. The script now
also tests `[ -x ]`, and test 7 covers that case. The text also now says
"before the next agent runs" instead of "before the first agent", because the
guard runs before every agent.

## Request

The git-directory guard watches every file an `include.path` or
`includeIf.*.path` names. Before each agent runs, `_watched` resolves each one
to a physical path with `_REAL`. `_REAL` runs `cd -P` on the parent. Say the
parent has a directory that doesn't exist yet, followed later by `..`. The
`cd` fails, and `_watched` falls back to the lexical path
(`posixpath.normpath`). Once the agent creates the missing directory, the
kernel resolves the path through any symlink on the way, so git loads a
different file from the one the guard watches.

Reproduction: `include.path = /repo/lnk/missing/../../x.inc`, with
`/repo/lnk -> /out/a`. The guard watches `/repo/x.inc`. The agent runs
`mkdir /out/a/missing` and writes `[core] fsmonitor = touch PWNED` to
`/out/x.inc`. Git now loads `/out/x.inc`, and `restore_git_dir` returns `[]`.
Two more cases have the same shape: `<repo>/missing/../lnk2/../x.inc`, and
`<repo>/lnk/missing/../../rl/x.inc`, where `rl` links back into the repo.

The fix: before the next agent runs, if any watched path has a `..` right
after a prefix that can't be entered (missing, not a directory, or not
searchable), the run stops with a `PermissionBreach`, the way the pack cap
does. The message names the include and the path. It says to write the path
without `..`, or to create the missing directory first. Nothing else changes.

The guard refuses these paths instead of predicting the file git would load.
An earlier run tried predicting and kept missing cases. It also deleted the
engineer's files when it put back a predicted path that turned out to be a
directory or the work tree.

## Baseline

`main` at `946a38c`. Every finding is judged against that commit. At
`946a38c`, in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- `_watched` builds `paths` from these sources:
  - the git dirs
  - every `--show-origin` source
  - each `core.excludesFile` and `core.attributesFile` value
  - each `include.path` and `includeIf.*.path` value, made absolute by
    `absolute()`
  - the `rev-parse --git-path hooks` result
  - the four home paths
- `real = _sh(_REAL, *paths)`. Where a field is empty because `cd -P` on the
  parent failed, the loop uses `"/" + posixpath.normpath(p).lstrip("/")`,
  the lexical path. That is the hole. `cd -P` fails on a prefix before `..`
  that is missing, not a directory, or not searchable.
- `git rev-parse --git-path hooks` already normalizes `core.hooksPath`
  lexically. I checked: `core.hooksPath=$PWD/missing/../h` gives `$PWD/h`. So
  the hooks entry in `paths` never has `..`.
- `_watched` raises only two breaches: "Could not resolve the git paths to
  watch" and "A git path resolves to the filesystem root". Its only caller is
  `guard_git_dir`, which runs before each agent.

## Threat model

The agent runs as the engineer's own user and has bash. It can create
directories and symlinks anywhere that user can write, and it can chmod the
user's own directories. That includes making a missing or unsearchable
directory on the way to a watched path usable. It can write any file the user
can. It can't change the packs, which live in SSSF's process memory. It can't
change the engineer's config from before the run started.

The trigger is a setup the engineer made: a watched path with `..` after a
prefix that can't be entered yet. The agent doesn't build that setup. It only
creates the missing directory or makes it searchable. The kernel then
resolves `..` physically, to somewhere the guard doesn't watch.

The agent is the engineer's own user, so there are things this guard can't
stop:

- killing the chain before the check runs
- a process left behind that writes after the check
- writes to paths the guard doesn't watch
- a race between the check and the next git command

`--sandbox` and `--container` are the isolation for those. This change only
closes the lexical fallback for paths with `..` after a prefix that can't be
entered.

## Bar

Against `946a38c`, the change must:

- stop the run in `guard_git_dir`, before the next agent runs, when any
  watched path has a `..` component right after a prefix that is missing, a
  dangling link, not a directory, or a directory without search permission.
  It raises `PermissionBreach`. The message names the path. If a config key
  set the path, it also names the key and the config file. It says the agent
  did not run, and to write the path without `..` or create the missing
  directory first. This covers all three reproduction shapes, for
  `include.path` and `includeIf.*.path`, and the unsearchable-directory case.
- create, delete, chmod or write nothing when it refuses. The check only
  tests paths, with `[ -d ]` and `[ -x ]`.
- leave every other path as it is today:
  - A path that resolves today, with or without `..`, never trips the
    check. `cd -P` succeeding means every prefix before each `..` was a
    searchable directory.
  - A path with a missing part and no `..` after it doesn't trip it either.
  - A path with no `..` component costs no extra shell call.

Where the baseline doesn't, the change must never keep an agent file, run
agent code, or delete or overwrite an engineer file. It changes no pack,
restore, unpack, link or label logic.

## Out of scope

These are old holes the change leaves open. A reviewer should file them as
old:

- A missing directory with no `..` after it, which the agent creates as a
  symlink. Example: `include.path = /repo/missing/x.inc` and
  `ln -s /out /repo/missing`. As `config.md` documents, the guard sees the
  change but won't write through the link, so the run stops with
  "NOT all put back".
- A missing directory with no `..` after it, but with a `..` before it that
  passes through a symlink. Example: `include.path = /repo/lnk/../missing/x.inc`
  with `lnk -> /out/a`. The guard falls back to the lexical
  `/repo/missing/x.inc`, but once the agent makes `/out/missing`, the kernel
  reads `/out/missing/x.inc`. The request keeps today's behaviour for a path
  whose missing part has no `..` after it, so this stays as it is on
  `946a38c`.
- `core.hooksPath` with `..`. Git normalizes it lexically itself
  (`rev-parse --git-path hooks`), so no `..` reaches `paths`. The check never
  fires on it, and the baseline handling stands.
- A `..` path that resolves today, where the agent later swaps a symlink on
  the way. The existing `_LINKS` / `parents` logic covers that, and it
  doesn't change.
- Everything listed in the Threat model as what this guard can't stop.

## Design

The change is one new shell script and one new check in `_watched`. It
predicts nothing. When the check passes, the guard watches the same paths it
does today.

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. After `_LINKS`, add a script with a comment:

   ```python
   # Prints, NUL-separated, each path with a ".." right after a part that cd
   # can't enter yet: missing, a dangling link, not a directory, or not
   # searchable. cd -P can't resolve such a path. Once the agent makes that part
   # usable, the kernel resolves ".." through it, maybe into a link, so git reads
   # a file the guard doesn't watch. Nothing is created, changed or written.
   _LOOSE_DOTDOT = """for p in "$@"; do
     q=; rest=${p#/}
     while [ -n "$rest" ]; do
       c=${rest%%/*}
       case $rest in */*) rest=${rest#*/} ;; *) rest= ;; esac
       if [ "$c" = .. ] && { [ ! -d "${q:-/}" ] || [ ! -x "${q:-/}" ]; }; then
         printf "%s\\0" "$p"; break
       fi
       q=$q/$c
     done
   done
   """
   ```

   Keep the `\\0` escaping the same as in `_REAL` and `_LINKS`, which are
   Python non-raw strings.

2. In `_watched`, record where each config-named path came from. Next to
   `kind`, add `named: dict[str, tuple[str, str]] = {}`. In the
   `--show-origin` loop, a path is appended for `core.excludesfile`,
   `core.attributesfile` and each include key. Each time, also set
   `named.setdefault(paths[-1], (key, source))`. Here `key` is the key as git
   listed it, for example `include.path` or `includeif.onbranch:main.path`.
   `source` is the absolute path of the config file it was read from.

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
           "A git path SSSF watches has `..` after a directory that doesn't exist yet, "
           "or that can't be entered. Once that changes, git resolves `..` through it and "
           "can read a different file from the one SSSF watches, so the agent did not run, "
           "and no run will until the path is fixed:\n"
           + "\n".join(which(p) for p in loose) + "\n\n"
           "Write the path without `..`, or create the missing directory first, then run again.")
   ```

   `_shown_key` is defined later in the module. That's fine, because it is
   only called at run time. It masks URL userinfo and non-printable
   characters in an `includeIf` condition.

4. Add one sentence to the `_watched` docstring: a watched path with `..`
   after a directory that doesn't exist yet, or can't be entered, stops the
   run with `PermissionBreach`, since the guard can't know which file git
   will read. The comment above `import posixpath` ends "Each parent is
   resolved with cd -P where it exists, lexically where it doesn't". Add to
   it: "...except that a `..` after a part cd can't enter refuses the run
   (see `_LOOSE_DOTDOT`)."

Don't touch `_PACK`, `_UNPACK`, `_REAL`, `_LINKS`, `_pack`, `_members`,
`_differs`, `guard_git_dir`, `restore_git_dir`, `_shared` or `GIT_WATCHED`.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section `# ── a ".." after a missing directory ──` at the end. Build
each run with the existing `shipped(repo)` helper, which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`. Use the existing `FSMONITOR`
and `state` helpers. A shared fixture or helper can set up three things:
`out = tmp_path / "out"`, `(out / "a").mkdir(parents=True)` and
`(repo / "lnk").symlink_to(out / "a")`.

1. `test_a_dotdot_after_a_missing_dir_stops_the_run_first`, parametrized over
   the three shapes. Shape 2 also needs `(repo / "lnk2").symlink_to(out / "a")`.
   Shape 3 also needs `(out / "rl").symlink_to(repo)`. The values:
   - `f"{repo}/lnk/missing/../../x.inc"`
   - `f"{repo}/missing/../lnk2/../x.inc"`
   - `f"{repo}/lnk/missing/../../rl/x.inc"`

   Run `git config --add include.path <value>`. Then save `before = state(repo)`
   and the text of `home/.gitconfig`. Assert that
   `permissions.guard_git_dir(run)` raises `PermissionBreach`, and that its
   message contains `include.path`, the value, "did not run", "without `..`"
   and "create the missing directory". Then assert nothing was made:
   - `not (out / "a" / "missing").exists()`
   - `not (repo / "missing").exists()`
   - `state(repo) == before`
   - `~/.gitconfig` is unchanged
2. `test_once_the_missing_dir_exists_the_real_file_is_watched`. This runs
   shape 1 end to end. After the refusal, act as the agent: `mkdir out/a/missing`
   and write `FSMONITOR` to `out/x.inc`. Check with
   `git config --show-origin --get core.fsmonitor` that git now loads
   `out/x.inc`, which proves the shape is real. Then call `guard_git_dir(run)`
   again. It does not raise, because the path now resolves physically, and
   `str((out / "x.inc").resolve())` is in `guard["paths"]`. This shows the
   refusal fires only while the directory is missing. Once it exists, today's
   physical resolution watches the file git reads.
3. `test_an_include_if_dotdot_after_a_missing_dir_names_its_key`.
   Set `git config includeIf.onbranch:main.path f"{repo}/lnk/missing/../../x.inc"`.
   The breach contains `includeif.onbranch:main.path` and the path.
4. `test_a_missing_dir_without_dotdot_after_it_is_unchanged`, parametrized
   over `f"{repo}/missing/x.inc"` and `f"{repo}/lnk/../missing/x.inc"`.
   `guard_git_dir(run)` does not raise. `guard["paths"]` contains
   `posixpath.normpath(value)`, the lexical path the baseline falls back to,
   since `cd -P` fails on both parents. `restore_git_dir(run, guard) == []`.
5. `test_a_path_without_dotdot_is_never_checked`. Monkeypatch
   `permissions._LOOSE_DOTDOT` to `'printf "%s\\0" "$@"'`, which flags every
   path it gets. With no `..` in any config, `guard_git_dir(shipped(repo))`
   still succeeds. So only paths with a `..` component reach the script.
6. `test_an_excludes_file_dotdot_after_a_missing_dir_is_refused`.
   Set `git config core.excludesFile f"{repo}/missing/../x.ignore"`. The
   breach names `core.excludesfile`.
7. `test_a_dotdot_after_an_unsearchable_dir_is_refused`. Skip it when
   `os.geteuid() == 0`, since root ignores search permission. Set
   `include.path = f"{repo}/lnk/../x.inc"`, with `lnk -> out/a`, and
   `os.chmod(out / "a", 0o600)`. Put the restore,
   `os.chmod(out / "a", 0o755)`, in a `try/finally` or a fixture finalizer,
   so pytest can clean up `tmp_path`. Assert `guard_git_dir(run)` raises
   `PermissionBreach` naming `include.path` and the value. Assert that
   `out / "a"` still has mode `0o600` (`stat.S_IMODE`), so the check
   changed nothing. This is the review's case: `[ -d repo/lnk ]` is true, but
   `cd -P repo/lnk/..` fails, so the baseline watches the lexical
   `repo/x.inc`.

Every existing test in the file must still pass unchanged. That includes
`test_an_include_through_dotdot_is_watched_and_kept` and
`test_an_include_through_a_symlinked_parent_is_watched`, which have `..`
paths that resolve today.

Add one sentence to the module docstring: "A watched path with `..` after a
directory that doesn't exist yet, or can't be entered, stops the run first."

### `.claude/skills/sssf/references/config.md`

In the git-directory section, the pack-cap paragraph ends "...the run stops
with "NOT all put back".". After it, add this paragraph:

> An include path, or another git path SSSF watches, that has `..` after a
> directory that doesn't exist yet, or that can't be entered, can't be
> checked. Once that directory is made or opened up, git resolves `..`
> through it, maybe through a symlink, and reads a different file from the
> one SSSF watches. So the run stops before the next agent with a breach
> naming the key, the config file and the path. Write the path without `..`,
> or create the missing directory first. A path with a missing directory and
> no `..` after it is watched as before. If the agent makes that directory a
> symlink, the run stops with "NOT all put back".

## Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: the paragraph above.
- The `_watched` docstring and the comment above `import posixpath`:
  see Design step 4.
- The module docstring of `.claude/skills/sssf/tests/test_git_dir_guard.py`:
  one sentence.
- No README usage or help text covers this, and the repo keeps no CHANGELOG.
  There is no `app_docs/` write-up of the git guard to flag for the
  documenter.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, including the new ones:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite. The hook tests skip without gitleaks:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 946a38c -- .claude/skills/sssf/templates/` lists only
   `adws/adw_modules/permissions.py`. In that diff, the only changes are the
   new `_LOOSE_DOTDOT`, `_watched` and its comments. `_PACK`, `_UNPACK`,
   `_REAL`, `_LINKS`, `_differs`, `guard_git_dir` and `restore_git_dir` are
   untouched.
4. The reproduction by hand, outside pytest, in a scratch repo with a scratch
   `HOME`:
   1. Run `ln -s /tmp/out/a lnk`.
   2. Run `git config include.path "$PWD/lnk/missing/../../x.inc"`.
   3. Call `permissions.guard_git_dir`.

   It raises a breach naming `include.path` and the path, and
   `/tmp/out/a/missing` still doesn't exist. Test 1 automates this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`. It needs no new tool or dependency.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it, or
  run `install.py --update`. Then update `adws/adw_sssf_config/stamp.json` as
  in `598e800`. Until then, the hub's own runs keep the baseline fallback.

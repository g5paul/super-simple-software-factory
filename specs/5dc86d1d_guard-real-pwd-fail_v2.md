# Treat a failed `pwd -P` as a path the guard can't resolve

## Summary

`_REAL` and `_WHERE` in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
read a directory's physical path with `cd -P "$d" && { pwd -P; echo x; }`. The
group's status is `echo`'s, so a failed `pwd -P` still succeeds. The planner
checked this on this machine, where `sh` is dash. With a physical path over
4096 characters behind two relative links and an ancestor at mode 311:

- dash: `cd -P` exits 0. `pwd -P` prints `getcwd() failed` to stderr, then **exits 0 and prints an empty line**.
- bash: `cd -P` exits 0. `pwd -P` exits 1 and prints nothing.

In both shells `_REAL` then reads the directory as empty and prints `/x.inc`
for `$L/A/B/x.inc`. `_WHERE` already prints an empty field in both shells, but
only by accident: the sentinel stripping happens to empty `\nx` and `x`.

Because dash's `pwd -P` exits 0, checking its status alone is not enough. The
fix is to accept `pwd -P`'s output only when it starts with `/`. A `pwd -P`
that succeeds always prints an absolute path (POSIX), so the check never
changes a path where `pwd -P` succeeds.

`_REAL` prints a **marker field, `?`**, for a parent that `cd -P` enters but
`pwd -P` can't name. It keeps the empty field for a parent `cd -P` can't
enter. Every Python caller but one treats any value not starting with `/` as
not resolving: `loose` and the lexical fallback in `_watched`, the `tree`
lookup, and the TMPDIR fallback's `== git_dir + "/x"`. So for them `?` means
exactly what `""` means, which is what the request asks for. The restore's
filter, `r == p or (r == "" and not under(p))`, matches neither `?` nor the
baseline's bogus `/x.inc`, so it still writes nothing there and stays
unchanged. That's the bar: nothing can check where links under such a
directory now point, and the baseline never wrote there either.

## Threat model

The agent runs as the engineer's own user. It can create, chmod, rename and
symlink anything the engineer can, including directories on the way to the
engineer's git config and includes, and it can make a physical path longer
than `PATH_MAX` behind short symlinks. At this level the guard can't stop the
agent from editing or deleting files directly. It can only (a) see a change to
a watched git path before SSSF's own git loads it, then stop the run, and
(b) avoid writing or deleting anywhere it can't verify. What matters is that
git never loads agent config (`core.hooksPath`, `core.fsmonitor`, filters)
that the guard didn't see. Root can read through mode 311 directories, so
this hole, and the tests that need a real unnameable directory, don't apply
when running as root.

## Baseline

`guard-prefix-gone` at commit `c5306d6` ("Untangle the config.md paragraph on
removed directories"). Every finding is judged against that commit's
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`.

## Bar

Against the baseline, the change must:

- Make `_REAL` print a field that doesn't start with `/` (the marker `?`) for a path whose parent `cd -P` enters but `pwd -P` can't name, whether `pwd -P` fails or exits 0 without printing an absolute path. `_WHERE` must print an empty resolved-dir field for such a prefix.
- So in `_watched`, such a watched path with `..` in it stops the run before the agent with the existing `..` breach. One without `..` is watched at its lexical path (`/` + `posixpath.normpath(p)`), as a missing path is. It must not be watched at `/<basename>`.
- Make an agent edit to an include under such a directory a breach: `git_dir_changed` is true and `restore_git_dir` raises `PermissionBreach` ("NOT all put back").
- Make a prefix that resolved before the agent and now fails this way count as moved in `_moved`, for `gone=True`, and for `gone=False` when the prefix is a link. This falls out of the `_WHERE` field being `""` and needs no change to `_moved`.

It must never:

- Keep an agent file, run agent code, or delete or overwrite an engineer file in a case where the baseline does otherwise. In particular, `restore_git_dir` must never `rm -rf` or extract a path whose parent can be entered but not named. The baseline never did either.
- Change any result where `pwd -P` succeeds, or where `cd -P` fails: the same `_REAL` output (`""` stays `""`), `_WHERE` output, watched paths, breaches and restores. That includes the newline sentinel (`d` and `d\n` stay different).
- Add a shell call anywhere. `test_a_readable_repo_runs_no_extra_command` and `test_the_prefixes_are_walked_in_one_shell_call` keep passing.

## Out of scope

These are old holes. A reviewer should file them as such:

- A shell whose failed `pwd -P` prints a stale or logical **absolute** path. The `/` check can't tell that from a real answer. dash and bash don't do this.
- A watched path without `..` under a directory `pwd -P` can't name is watched lexically, through its links. The guard can't record where those links point (their `_WHERE` field is `""` before the agent), so an agent that retargets such a link is caught only if the content at the lexical path changes. That's the same as any prefix that never resolved (`test_a_prefix_that_never_resolved_is_not_compared`).
- Under such a directory, an agent edit is reported ("NOT all put back") but not written back. The engineer puts it back by hand, as with a changed link. The baseline didn't put it back either; it didn't even see it.
- Physical paths over `PATH_MAX` that *do* resolve (unlocked): tar may fail to pack them, which stops the run before the agent ("Could not save"). That's unchanged.
- Everything else `.claude/skills/sssf/references/config.md` already lists as a gap.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: `_REAL`

Replace the script with the following. Inside the Python triple-quoted string,
keep `\\0` as the file has it now.

```sh
for p in "$@"; do
  d=${p%/*}; b=${p##*/}
  if [ "$b" = .. ] || [ "$b" = . ]; then d=$p; b=; fi
  if r=$(cd -P "${d:-/}" 2>/dev/null && { pwd -P 2>/dev/null && echo x || :; }); then
    r=${r%x}; r=${r%?}
    case $r in
      /*) if [ -n "$b" ]; then r="${r%/}/$b"; fi; printf "%s\\0" "$r" ;;
      *) printf "?\\0" ;;
    esac
  else
    printf "\\0"
  fi
done
```

The substitution's status is `cd -P`'s: `|| :` keeps a failed `pwd -P` from
looking like a failed `cd -P`. The planner ran this on dash and bash and got
the same output as today for `/x`, `/`, a missing parent and a directory whose
name ends in a newline. It printed `?` for `$L/A/B/x.inc` and `$L/A/B/..`
under the locked tree, and for an existing directory when `pwd` is replaced by
a shell function that returns 1, prints an empty line, or prints `rel`.

Rewrite the comment above `_REAL`. It prints each path with its parent
resolved physically, or an empty field where the parent doesn't exist yet or
can't be entered. It prints `?` where `cd -P` enters the parent but `pwd -P`
can't name it (a physical path over the system limit under a directory that
can't be read; dash's `pwd -P` exits 0 then, so the output must start with
`/`). Callers treat any field not starting with `/` as not resolving. The
restore writes only under `""`, never under `?`, since nothing can check where
the links on the way to `?` now point.

### 2. Same file: `_WHERE`

Change the two resolution lines to:

```sh
    r=$(cd -P "$q" 2>/dev/null && pwd -P 2>/dev/null && echo x) || r=
    if [ -n "$r" ]; then r=${r%x}; r=${r%?}; fi
    case $r in /*) ;; *) r= ;; esac
```

`_WHERE` needs no marker: an empty field already means "resolves nowhere" to
`_moved`. Leave everything else alone. In its comment, change "empty if it
isn't a directory that can be entered" to "...entered, or whose physical path
`pwd -P` can't name".

### 3. Same file: `_watched` wording only

- Docstring: "A watched path with `..` that doesn't fully resolve, because a part of it doesn't exist yet, can't be entered, or can't be named by `pwd -P`, stops the run...".
- The comment before `loose`: mention the same third reason.
- The breach message: change `"A git path SSSF watches has `..` after a directory that doesn't exist yet, or that can't be entered. "` to `"A git path SSSF watches has `..` after a directory that doesn't exist yet, that can't be entered, or whose real path can't be read. "`. Keep the rest of the message as it is. Existing tests assert `"did not run"`, ``"without `..`"`` and `"create the missing directory"`.

No logic change in `_watched`, `_moved`, `_where`, `_differs` or
`restore_git_dir`.

### 4. `.claude/skills/sssf/tests/test_git_dir_guard.py`: new tests

Add a section `# ── a directory pwd -P can't name ──` at the end. Add a line
to the module docstring: "A directory on the way that `pwd -P` can't name reads
as not resolving."

Fixture `unnameable(tmp_path)`, a generator fixture:

- Skip when `os.geteuid() == 0`.
- Let `n = "a" * 200` and `base = Path(os.path.realpath(tmp_path))`. Create `base/long` and 22 nested `n` directories under it **stepwise with `dir_fd`**, because `os.makedirs` fails past `PATH_MAX`: `os.mkdir(n, dir_fd=fd)` then `fd = os.open(n, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)`. Close every fd.
- Write `x.inc` in the deepest directory with `os.open("x.inc", os.O_WRONLY | os.O_CREAT, 0o644, dir_fd=fd)`, content `b"[user]\n\tname = t\n"`.
- Make `base/A` a symlink to `"long/" + "/".join([n] * 11)`. Inside the 11th-level directory, make `B` a symlink to `"/".join([n] * 11)` with `os.symlink(target, "B", dir_fd=fd11)`.
- Expose `base`, `inc = f"{base}/A/B/x.inc"`, `physical = f"{base}/long/" + "/".join([n] * 22) + "/x.inc"`, and `lock()` / `unlock()`, which chmod `base/long` to `0o311` / `0o755`.
- Add a helper `require_unnameable(u)`, called after `lock()`, that skips the test if `permissions._sh('cd -P "$1" && pwd -P', f"{u.base}/A/B").stdout` starts with `/` (this filesystem can still name it).
- On teardown, always `unlock()`.

Each guard run uses `shipped(repo)`, the config SSSF ships.

1. `test_a_failed_pwd_reads_as_not_resolving(tmp_path, fake)`: parametrize `fake` over `"pwd() { return 1; }"`, `"pwd() { echo; }"`, `"pwd() { echo rel; }"`. Make a real directory `d = tmp_path / "d"`.
   - `permissions._sh(fake + "\n" + permissions._REAL, f"{d}/x", f"{tmp_path}/missing/x").stdout.split("\0")[:2] == ["?", ""]`.
   - Every resolved field of `permissions._sh(fake + "\n" + permissions._WHERE, f"{d}/x")` is `""`: split on NUL, drop the trailing empty field, and take fields `1::3`.
   - Control, without the fake: `_REAL` gives `f"{os.path.realpath(d)}/x"`.
   - Needs no fixture and runs as root too.
2. `test_real_of_an_unnameable_dir_is_marked(unnameable)`: after `lock()` and `require_unnameable`:
   - `_REAL(inc)` is `"?"`. It is neither `""` nor `"/x.inc"`.
   - The `_WHERE` field for `f"{base}/A/B"` is `""`, while the one for `f"{base}/A"` still starts with `/`.
   - After `unlock()`, `_REAL(inc) == physical` (today's behaviour).
3. `test_an_include_under_an_unnameable_dir_is_watched_and_caught(repo, unnameable)`: lock, then `require_unnameable`.
   - `git(repo, "config", "--add", "include.path", inc)`, then `guard = permissions.guard_git_dir(shipped(repo))`.
   - Assert `"/x.inc" not in guard["paths"]` and `inc in guard["paths"]`.
   - Simulate the agent: `git(repo, "config", "-f", inc, "core.hooksPath", str(repo / "evil"))`.
   - **After** that edit, record `os.stat(inc).st_ino` and `Path(inc).read_bytes()`. `git config -f` writes a lock file and renames it, so recording earlier would fail.
   - Assert `permissions.git_dir_changed(guard)`. Wrap `restore_git_dir` in `pytest.raises(permissions.PermissionBreach, match="NOT all put back")`, and check the message contains `inc`.
   - Assert the inode and bytes match what was recorded after the edit: the restore wrote and deleted nothing there.
4. `test_a_dotdot_include_under_an_unnameable_dir_stops_the_run_first(repo, unnameable)`: lock, then `require_unnameable`.
   - Add `include.path` = `f"{base}/A/B/../B/x.inc"` and record `state(repo)`.
   - `guard_git_dir(shipped(repo))` raises `PermissionBreach`. Its message has `"include.path"`, the value, `"did not run"`, ``"without `..`"`` and `"whose real path can't be read"`.
   - Assert `state(repo)` is unchanged.
5. `test_a_prefix_that_becomes_unnameable_has_moved(unnameable)`:
   - Unlocked: `walked = [inc]` and `guard = {"walked": walked, "where": permissions._where(walked)}`. Assert `guard["where"][f"{base}/A/B"][0]` starts with `/`.
   - Then `lock()` and `require_unnameable`. Assert `f"{base}/A/B"` is in `permissions._moved(guard)` and in `permissions._moved(guard, gone=False)` (it is a link).
   - This is a unit-level check because a guard run can't pack the over-`PATH_MAX` physical path before the lock; see Out of scope.

Don't change any existing test.

### 5. `.claude/skills/sssf/references/config.md`

- In the paragraph that starts "An include path, or another git path SSSF watches, that has `..` in it and a directory on the way that doesn't exist yet, or that can't be entered", add "or whose real path can't be read (a path longer than the system limit under a directory you can't read)" to the list of reasons, so it covers this case.
- Right after the sentence "If the agent makes a missing directory in a path without `..` a symlink, the run stops with "NOT all put back".", add: "A path without `..` under a directory whose real path can't be read is watched as written. If the agent changes it, the run stops with "NOT all put back" and SSSF doesn't write it back, since it can't check where the links on the way lead."

No README or CHANGELOG changes. The repo keeps no CHANGELOG, and the README doesn't describe guard path resolution. Nothing in `app_docs/` mentions `_REAL`, `_WHERE` or `pwd -P`, so no older write-up goes stale.

## Verification

Run from the repo root:

1. `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -rs` passes, new tests included. Run it as a non-root user and confirm in the skip summary that tests 2–5 ran.
2. `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q` passes (whole skill suite, including `test_update.py`).
3. `! grep -nF '{ pwd -P; echo x; }' .claude/skills/sssf/templates/adws/adw_modules/permissions.py` exits 0 (the old group is gone from both scripts).
4. `git diff c5306d6 -- .claude/skills/sssf/templates/adws/adw_modules/permissions.py` touches only `_REAL`, `_WHERE`, their comments, and the `_watched` docstring, comment and message wording. `restore_git_dir` is unchanged.
5. Manual check against the baseline: with `git show c5306d6:.claude/skills/sssf/templates/adws/adw_modules/permissions.py`, test 3's setup gives `_REAL(inc) == "/x.inc"` and `restore_git_dir` raises nothing. With the change it raises "NOT all put back" and leaves the file alone.

## For the engineer

- After the guard-change chain passes and you merge, stamp the template's `permissions.py` into the hub's protected copy at `adws/adw_modules/permissions.py` (and `adws/adw_sssf_config/stamp.json`), as `e378497` did. The hub copy is already behind `af83d3d`, so this stamp carries both.

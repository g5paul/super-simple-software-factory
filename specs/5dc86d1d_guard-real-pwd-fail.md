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
for `$L/A/B/x.inc`. So the guard watches `/x.inc`, and an agent edit to the
real include goes unseen. `_WHERE` already prints an empty field in both
shells, but only because the sentinel stripping happens to empty `\nx` and
`x`. It doesn't check what `pwd -P` printed.

Because dash's `pwd -P` exits 0, checking its status alone is not enough. The
fix in both scripts is to chain `pwd -P && echo x` and to accept the result
only if it starts with `/`. A `pwd -P` that succeeds always prints an absolute
path (POSIX), so the `/` check never changes a path where `pwd -P` succeeds.

The restore needs one more narrow change to stay within the bar (see
"Restore" below). Today, a path whose parent can be entered but not named
always gets a bogus `/b` from `_REAL`, which never equals the path, so the
restore never writes there. After the fix that path reads as `""`, and the
restore's `r == ""` branch would start writing through links it can't check.
The restore must keep writing nothing there.

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
this hole and its tests don't apply when running as root.

## Baseline

`guard-prefix-gone` at commit `c5306d6` ("Untangle the config.md paragraph on
removed directories"). Every finding is judged against that commit's
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`.

## Bar

Against the baseline, the change must:

- Make `_REAL` print an empty field for a path whose parent `cd -P` enters but `pwd -P` can't name, whether `pwd -P` fails or exits 0 without printing an absolute path. `_WHERE` must print an empty resolved-dir field for such a prefix.
- So in `_watched`, such a watched path with `..` in it stops the run before the agent with the existing "has `..` after a directory that doesn't exist yet, or that can't be entered" breach. One without `..` is watched at its lexical path (`/` + `posixpath.normpath(p)`), the same as a missing path. It must not be watched at `/<basename>`.
- Make an agent edit to an include under such a directory a breach: `git_dir_changed` is true and `restore_git_dir` raises `PermissionBreach`.
- Make a prefix that resolved before the agent and now fails this way count as moved in `_moved`, for both `gone=True` and `gone=False` when the prefix is a link. This falls out of the `_WHERE` field being `""` and needs no change to `_moved`.

It must never:

- Keep an agent file, run agent code, or delete or overwrite an engineer file in a case where the baseline does otherwise. In particular, `restore_git_dir` must never `rm -rf` or extract a path whose `_REAL` is `""` while its parent can be entered, because it can't check where that write lands. The baseline never wrote such a path either.
- Change any result where `pwd -P` succeeds: the same `_REAL` output, `_WHERE` output, watched paths, breaches and restores. That includes the newline-sentinel handling (`d` and `d\n` stay different) and the case where `cd -P` fails.
- Add a shell call to `guard_git_dir` on a repo where every path resolves (`test_a_readable_repo_runs_no_extra_command` and `test_the_prefixes_are_walked_in_one_shell_call` keep passing).

## Out of scope

These are old holes. A reviewer should file them as such:

- A shell whose failed `pwd -P` prints a stale or logical **absolute** path. The `/` check can't tell that from a real answer. dash and bash, the shells SSSF's sandboxes use, don't do this.
- A watched path without `..` under a directory `pwd -P` can't name is watched lexically, through its links. The guard can't record where those links point (their `_WHERE` field is `""` before the agent), so an agent that retargets such a link is caught only if the content at the lexical path changes. That's the same as any prefix that never resolved (`test_a_prefix_that_never_resolved_is_not_compared`).
- Under such a directory, an agent edit is reported ("NOT all put back") but not written back. The engineer puts it back by hand, as with a changed link. The baseline didn't put it back either; it didn't even see it.
- Physical paths over `PATH_MAX` that *do* resolve: tar may fail to pack them, which stops the run before the agent ("Could not save"). That's unchanged.
- Everything else `references/config.md` already lists as a gap (ignore files changed through a link, `.env` un-ignored, and so on).

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: `_REAL`

Replace the body of the loop so that a failed or non-absolute `pwd -P` prints
an empty field:

```sh
for p in "$@"; do
  d=${p%/*}; b=${p##*/}
  if [ "$b" = .. ] || [ "$b" = . ]; then d=$p; b=; fi
  r=$(cd -P "${d:-/}" 2>/dev/null && pwd -P 2>/dev/null && echo x) || r=
  r=${r%x}; r=${r%?}
  case $r in
    /*) if [ -n "$b" ]; then r="${r%/}/$b"; fi; printf "%s\\0" "$r" ;;
    *) printf "\\0" ;;
  esac
done
```

(Inside the Python triple-quoted string, keep `\\0` as the file has it now.)
The planner checked this on dash and bash. A normal directory, `/x`, `/`, a
missing parent, a `..` tail and a directory whose name ends in a newline all
give the same output as today. The unnameable directory gives `""`, and so
does a `pwd` shell function that returns 1, prints an empty line, or prints
`rel`.

Update the comment above `_REAL`. It prints an empty field where the parent
doesn't exist yet, can't be entered, **or can't be named by `pwd -P`** (a
physical path over the system limit under a directory that can't be read). It
must note that dash's `pwd -P` exits 0 in that case, which is why the output
has to start with `/`.

### 2. Same file: `_WHERE`

Change the two resolution lines to:

```sh
    r=$(cd -P "$q" 2>/dev/null && pwd -P 2>/dev/null && echo x) || r=
    if [ -n "$r" ]; then r=${r%x}; r=${r%?}; fi
    case $r in /*) ;; *) r= ;; esac
```

Leave everything else in `_WHERE` alone. Update its comment so "empty if it
isn't a directory that can be entered" reads "...entered, or whose physical
path `pwd -P` can't name".

### 3. Same file: `restore_git_dir`

Add a script next to `_REAL`:

```python
# Prints 1 for each path whose parent cd -P can enter, else 0, with no
# separator. _REAL prints an empty field both for a parent that can't be
# entered and for one that can be entered but not named by pwd -P; only the
# first is a missing path the restore may write under.
_ENTERS = """for p in "$@"; do
  d=${p%/*}
  if (cd -P "${d:-/}" 2>/dev/null); then printf 1; else printf 0; fi
done
"""
```

In `restore_git_dir`, where it filters `paths` after computing `now`, `moved`,
`roots` and `under`:

- Only when some `r` in `now` (for the paths being filtered) is `""`, run `entered = _sh(_ENTERS, *paths).stdout`. Otherwise use `entered = "0" * len(paths)`, so a run with no empty field makes no extra shell call.
- If `len(entered) != len(paths)`, or it contains anything but `0` and `1`, treat every path as `1` (unsure means not written).
- Change the filter to `r == p or (r == "" and not under(p) and entered[i] == "0")`.

Extend the comment block above it: a path whose parent can be entered but not
named is never written, since nothing can check where the links on its way now
point. This matches the baseline, where `_REAL` gave such a path a bogus
`/<name>` and the path was skipped. A changed path that is skipped this way
leaves `paths` empty, so the existing `restored = False` path reports "NOT all
put back".

Leave `_watched`, `_moved`, `_where`, `_differs`, the `tree` lookup and the
TMPDIR fallback (`_REAL(git_dir + "/x") == git_dir + "/x"`) unchanged. They
already treat `""` as not resolving. The fallback still refuses a git dir it
can't name, as the baseline did with its bogus `/x`.

Update the `_watched` docstring sentence on `..`: "A watched path with `..`
that doesn't fully resolve, because a part of it doesn't exist yet, can't be
entered, or can't be named by `pwd -P`, stops the run...". Update the inline
comment before `loose` the same way.

### 4. `.claude/skills/sssf/tests/test_git_dir_guard.py`: new tests

Add a section `# ── a directory pwd -P can't name ──` at the end. Add a line
to the module docstring: "A directory on the way that `pwd -P` can't name reads
as not resolving."

Fixture `unnameable(tmp_path)`, a generator fixture:

- Skip when `os.geteuid() == 0`.
- Let `n = "a" * 200` and `base = Path(os.path.realpath(tmp_path))`. Create `base/long` and 22 nested `n` directories under it **stepwise with `dir_fd`**, because `os.makedirs` fails past `PATH_MAX`: `os.mkdir(n, dir_fd=fd)` then `fd = os.open(n, os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)`. Close every fd.
- Write `x.inc` in the deepest directory with `os.open("x.inc", O_WRONLY|O_CREAT, 0o644, dir_fd=fd)`, content `b"[user]\n\tname = t\n"`.
- Make `base/A` a symlink to `"long/" + "/".join([n] * 11)`. Inside the 11th-level directory, make `B` a symlink to `"/".join([n] * 11)`, using `os.symlink(..., "B", dir_fd=fd11)`.
- Let `inc = f"{base}/A/B/x.inc"` (short logical path) and `physical = f"{base}/long/" + "/".join([n] * 22) + "/x.inc"`.
- Return a namespace with `base`, `inc`, `physical`, and `lock()` / `unlock()` helpers that chmod `base/long` to `0o311` / `0o755`.
- After `lock()`, skip if `permissions._sh('cd -P "$1" && pwd -P', f"{base}/A/B").stdout` starts with `/` (pwd can still name it on this filesystem). Do this inside each test that needs it, or in a helper, `require_unnameable(u)`.
- On teardown, always `unlock()`.

Tests. Each guard run uses `shipped(repo)`, the config SSSF ships.

1. `test_a_failed_pwd_reads_as_not_resolving(tmp_path, fake)`: parametrize `fake` over `"pwd() { return 1; }"`, `"pwd() { echo; }"`, `"pwd() { echo rel; }"`. Make `d = tmp_path / "d"` (real). Assert `permissions._sh(fake + "\n" + permissions._REAL, f"{d}/x").stdout.split("\0")[0] == ""`. Assert every resolved field of `permissions._sh(fake + "\n" + permissions._WHERE, f"{d}/x")` is `""`: split on NUL, drop the trailing empty field, and take fields `1::3`. Control: without the fake, `_REAL` gives `f"{os.path.realpath(d)}/x"`. This needs no fixture and runs as root too.
2. `test_real_of_an_unnameable_dir_is_empty(unnameable)`: after `lock()`, `_REAL(inc)` is `""` (not `"/x.inc"`), and the `_WHERE` field for `f"{base}/A/B"` is `""` while `f"{base}/A"` still resolves. After `unlock()`, `_REAL(inc) == physical` (today's behaviour).
3. `test_an_include_under_an_unnameable_dir_is_watched_and_caught(repo, unnameable)`: lock. `git(repo, "config", "--add", "include.path", inc)`. `guard = permissions.guard_git_dir(shipped(repo))`. Assert `"/x.inc" not in guard["paths"]` and `inc in guard["paths"]`. Record `os.stat(inc).st_ino`. Simulate the agent: `git(repo, "config", "-f", inc, "core.hooksPath", str(repo / "evil"))`. Assert `permissions.git_dir_changed(guard)`. `pytest.raises(PermissionBreach, match="NOT all put back")` around `restore_git_dir`, and the message contains `inc`. Assert the inode is unchanged and the file still exists: the restore wrote and deleted nothing there.
4. `test_a_dotdot_include_under_an_unnameable_dir_stops_the_run_first(repo, unnameable)`: lock. Add `include.path` = `f"{base}/A/B/../B/x.inc"`. `guard_git_dir(shipped(repo))` raises `PermissionBreach` whose message has `"include.path"`, the value, `"did not run"` and ``"without `..`"``. Assert `state(repo)` is unchanged.
5. `test_a_prefix_that_becomes_unnameable_has_moved(unnameable)`: unlocked, `walked = [inc]`, `guard = {"walked": walked, "where": permissions._where(walked)}`. Assert `guard["where"][f"{base}/A/B"][0]` starts with `/`. Then `lock()`. Assert `f"{base}/A/B"` is in `permissions._moved(guard)` and in `permissions._moved(guard, gone=False)` (it is a link). This is a unit-level check because a guard run can't pack the over-`PATH_MAX` physical path before the lock; see Out of scope.

Don't change any existing test.

### 5. `.claude/skills/sssf/references/config.md`

- In the paragraph that starts "An include path, or another git path SSSF watches, that has `..` in it and a directory on the way that doesn't exist yet, or that can't be entered", add "or whose real location the system can't name (a path longer than the system limit under a directory you can't read)" to the list of reasons, so it covers this case.
- Right after the sentence "If the agent makes a missing directory in a path without `..` a symlink, the run stops with "NOT all put back".", add: "A path without `..` under a directory whose real location the system can't name is watched as written. If the agent changes it, the run stops with "NOT all put back" and SSSF doesn't write it back, since it can't check where the links on the way lead."

No README or CHANGELOG changes. The repo keeps no CHANGELOG, and the README doesn't describe guard path resolution. Nothing in `app_docs/` mentions `_REAL`, `_WHERE` or `pwd -P`, so no older write-up goes stale.

## Verification

Run from the repo root:

1. `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q` passes, new tests included. Run it as a non-root user so the `unnameable` tests run rather than skip. Confirm in the `-rs` summary that tests 2–5 were not skipped.
2. `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q` passes (whole skill suite, including `test_update.py`).
3. `! grep -nF '{ pwd -P; echo x; }' .claude/skills/sssf/templates/adws/adw_modules/permissions.py` exits 0 (the old group is gone from both scripts).
4. Manual check of the bar against the baseline: run test 3's setup against `git show c5306d6:.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. There, `_REAL(inc)` gives `/x.inc` and `restore_git_dir` raises nothing. With the change it raises "NOT all put back" and leaves the inode alone.

## For the engineer

- After the guard-change chain passes and you merge, stamp the template's `permissions.py` into the hub's protected copy at `adws/adw_modules/permissions.py` (and `adws/adw_sssf_config/stamp.json`), as `e378497` did. The hub copy is already behind `af83d3d`, so this stamp carries both.

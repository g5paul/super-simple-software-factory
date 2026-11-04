# Stop the run when a prefix of a watched path that resolved no longer does

## Changes since spec review 1

- Test 1 used `"x.inc" not in git(repo, "config", "--list", "--show-origin")`
  to show the hole. That check can never pass: the output always contains
  the `include.path=…/home/d/../x.inc` line itself. It is now
  `"hookspath=/dev/null" not in git(repo, "config", "--list").lower()`.
- Test 1 and test 6 now look for the exact breach line `  - {home}/d`
  (`f"  - {home / 'd'}" in str(caught.value).splitlines()`), not a
  substring. `$HOME/d/..` is reported too, and it also contains
  `str(home / "d")`.
- Nothing else changed: the design, the Bar and the other tests are as in
  review 1.

## Summary

Run 6bbc5ca1 records where every prefix of each watched git path resolves
before the agent (`guard["where"]`, from `_where`). After the agent, `_moved`
reports a prefix that resolves somewhere else. It skips a prefix that
resolved before and now resolves **nowhere**, unless a symlink is involved.
Its comment says "a removed real directory is left to the path packs". That
is the hole. Take `include.path = $HOME/d/../x.inc`: `rmdir ~/d`, or
replacing `~/d` with a regular file, makes git silently stop loading
`~/x.inc`. No watched file changes, so `restore_git_dir` returns `[]`.

The change has two parts:

1. **Detection.** In `_moved`, a prefix that resolved before counts as moved
   when it now resolves to anything but the same directory. That includes
   another directory, and nowhere: missing, a regular file, unsearchable,
   dangling, or anything else `cd -P` can't enter. `_differs`,
   `git_dir_changed` and the check after the restore all see it, so the run
   stops and the breach names the prefix.
2. **Writing stays as at the baseline.** `restore_git_dir` keeps the
   baseline set of prefixes it must not write under: those that resolve
   elsewhere, and those that were or are a symlink. A real directory that
   now resolves nowhere is reported, but is not added to those roots. Its
   watched files are put back exactly as today: tar remakes the parents as
   real directories, and nothing there can be a link the agent planted.
   - If that remakes the prefix, the after-restore `_differs` sees it
     resolve to the same place again, and the breach says "put back as they
     were".
   - If nothing remakes it, the prefix stays in `left`, and the breach says
     "NOT all put back". That happens in the reproduction, where `~/d` holds
     no watched file.

## Baseline

`main` at `e378497` ("Stamp the prefix-moves guard into the hub"). Every
finding is judged against that commit. The template module is
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. The hub's
`adws/adw_modules/permissions.py` is an identical stamped copy, and it is
protected.

## Threat model

The agent runs as the engineer's own user, with the same HOME and the same
write access. During its turn it can `rmdir`, `rm -rf`, `mv`, `chmod`,
`ln -s` and write any file the engineer can, inside or outside the repo.

Its goal here is to make git **stop** loading a config file the engineer
relies on, without changing any file the guard packs. That could be an
include that sets `core.hooksPath = /dev/null`, or one that turns off a
filter. The agent breaks a directory on the way to the include, and git's
include lookup then fails silently (ENOENT or ENOTDIR). The engineer's
protective line is gone, both for SSSF's next git command and for the
engineer's.

The hole, present at the baseline:

- `~/.gitconfig` has `include.path = $HOME/d/../x.inc`.
- `~/d` is a real directory.
- `~/x.inc` holds `core.hooksPath = /dev/null`.

The agent runs `rmdir ~/d`, `rmdir ~/d && touch ~/d`, or `chmod 000 ~/d`.
Nothing packed changes, and `restore_git_dir` returns `[]`.

What this guard can't stop at its level is unchanged from the baseline:

- It checks twice: mid-run in `git_dir_changed`, and at the end of the turn
  in `restore_git_dir`. A background process the agent leaves running can
  break a prefix after the last check.
- If the agent breaks a prefix and remakes it between checks, only the
  existing `seen` sentinel catches it, and only if `git_dir_changed` ran
  while the prefix was broken.
- It can't stop the agent reading files.
- It can't tell the agent's change from one the engineer made in another
  terminal.

## Bar

Against the baseline, the change **must**:

- In the reproduction, for each of `rmdir ~/d`, `~/d` replaced by a regular
  file, and `chmod 000 ~/d`:
  - `git_dir_changed(guard)` returns `True`;
  - `restore_git_dir` raises `PermissionBreach` with "NOT all put back",
    and the breach lists `…/home/d`;
  - nothing is written at `~/d`: a removed `~/d` stays missing, and the
    agent's regular file stays as the agent left it;
  - `~/x.inc` is unchanged.
- Count as moved every prefix that resolved before and now doesn't resolve
  to the same directory, whatever the reason.
- Keep today's behaviour for a prefix that didn't resolve before. It is
  never compared, so if the agent creates it as a real directory or as a
  file, that is left to the path packs, as now.
- Keep today's behaviour for every prefix that resolves the same. For
  example, `test_unchanged_prefixes_change_nothing` still returns `[]`.
- Put back every changed watched path the baseline puts back. That includes
  a removed real directory that holds a watched file, as in
  `test_a_removed_real_dir_on_the_way_is_put_back`, and a removed
  `.git/hooks`. Once the directory resolves to the same place again, the
  breach says "put back as they were".
- Pass all of `.claude/skills/sssf/tests/test_git_dir_guard.py` and the
  whole skill suite, with no existing assertion rewritten.

It must **never**, where the baseline does otherwise:

- keep an agent file that the baseline deletes or puts back;
- let agent code run that the baseline stops;
- delete or overwrite an engineer file that the baseline leaves alone;
- write under a prefix that the baseline's rule skips. The roots that
  `restore_git_dir` won't write under must be exactly the baseline's.

One widening is allowed. Say the agent removes a real directory on the way
to a watched path that doesn't exist, such as an empty `~/.config` when
`~/.config/git/config` doesn't exist. The run now stops with "NOT all put
back", where at the baseline it went on. That is what the request asks for.

## Design

All code changes are in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`.

### `_moved(guard, *, gone=True)`

Add a keyword-only parameter `gone: bool = True`:

```python
def _moved(guard: dict, *, gone: bool = True) -> list[str] | None:
    """The recorded prefixes that resolved before the agent and now resolve
    somewhere else or nowhere, or None if they could not be walked again.
    With gone=False, only those the restore must not write under: ones that
    resolve somewhere else, or that resolve nowhere and were or are a symlink.
    """
    then = guard.get("where", {})
    if not then:
        return []
    now = _where(guard.get("walked", []))
    if now is None:
        return None
    none = ["", "-"]
    return [q for q, (r, l) in then.items()
            if r and now.get(q, none)[0] != r
            and (gone or now.get(q, none)[0] or l == "L" or now.get(q, none)[1] == "L")]
```

Replace the comment block above the return so that it says:

- A prefix that didn't resolve before is never compared. The path packs
  cover it.
- A prefix that resolved before and now resolves anywhere else, or nowhere
  (missing, a regular file, unsearchable, a dangling link), has moved.
- With `gone=False`, a real directory that now resolves nowhere and isn't a
  link is left out. When the restore puts back a watched file under it, tar
  remakes it as a real directory, so writing there is as safe as at the
  baseline.

Rename the local `gone` list to `none`, so it doesn't shadow the parameter.

### `_differs`

No code change. It calls `_moved(guard)`, which now includes the prefixes
that no longer resolve. In the comment above `found = moved + …`, add that a
directory on the way that no longer resolves is reported too.

### `restore_git_dir`

- Inside `if paths:`, change `moved = _moved(guard)` to
  `moved = _moved(guard, gone=False)`.
- Add one sentence to the comment above `roots`: a real directory that now
  resolves nowhere is reported (through `_differs`) but isn't a root. Tar
  remakes it as a real directory, and the check after the restore sees
  whether it resolves to the same place again.
- Leave the `changed is None` fallback alone. It calls `_moved(guard)` only
  to report.
- The docstring's contract doesn't change, and the "Narrow on purpose"
  comment there stays true.

### Why the reproduction stops

`changed` is `[$HOME/d]`, plus `$HOME/d/..` when that is recorded. Neither
is in `guard["paths"]`, so `paths` is empty and `restored = False`. The
breach says "NOT all put back" and lists them. `_UNPACK` never runs.

## Files to change

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: `_moved`,
  the comment in `_differs`, and one line plus a comment in
  `restore_git_dir`, as above.
- `.claude/skills/sssf/tests/test_git_dir_guard.py`: the new tests below.
  Also add one line to the module docstring: "A directory on the way to a
  watched path that resolved before the agent and no longer resolves stops
  the run."
- `.claude/skills/sssf/references/config.md`, in the git-guard paragraph
  around lines 179-191 ("SSSF also records where every directory…"). After
  "and a directory a link points at that was swapped in turn.", add:
  - It also notices a directory on the way that the agent removed, replaced
    with a file, or made impossible to enter. Git would silently skip an
    include through it, such as `~/d` in `~/d/../x.inc`.
  - The run stops with "NOT all put back" and names the directory.
  - A watched file under such a directory is still put back. If that remakes
    the directory, the breach says "put back as they were".

  Match the paragraph's style: short, plain sentences and no new headings.

The repo has no CHANGELOG and no `app_docs/` directory. No help text or
README describes this behaviour.

## Tests (`.claude/skills/sssf/tests/test_git_dir_guard.py`)

Add these after
`test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it`,
under a comment `# ── a prefix that no longer resolves ──`. Use the existing
`shipped(repo)` helper, which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`, and the existing fixtures
(`repo`, `home`, `_hole1`). Write each test before its code change and watch
it fail at the baseline. Tests 1, 2 and 6 fail at the baseline. Tests 3 to 5
pass there and must keep passing.

1. `test_a_dir_before_dotdot_that_no_longer_resolves_is_seen`, parametrized
   with `how` in `["removed", "file", "locked"]`:
   - Set up with `_hole1(repo, home, tmp_path)`. Then write
     `"[core]\n\thooksPath = /dev/null\n"` to `home / "x.inc"`, and check
     that `git(repo, "config", "core.hooksPath") == "/dev/null"`.
   - Set `run = shipped(repo)` and `guard = permissions.guard_git_dir(run)`.
   - Make the agent's move:
     - `removed`: `(home / "d").rmdir()`;
     - `file`: rmdir, then `(home / "d").write_text("agent\n")`;
     - `locked`: `(home / "d").chmod(0)`. Skip this case when
       `os.geteuid() == 0`, and restore `0o755` in `finally`.
   - For `removed` and `file` only, show the hole:
     `assert "hookspath=/dev/null" not in git(repo, "config", "--list").lower()`.
     Skip this for `locked`, because git dies with EACCES there.
   - `assert permissions.git_dir_changed(guard)`.
   - `restore_git_dir(run, guard)` raises with `match="NOT all put back"`.
     Check that `f"  - {home / 'd'}" in str(caught.value).splitlines()`.
   - Afterwards:
     - `removed`: `not (home / "d").exists()`;
     - `file`: `(home / "d").read_text() == "agent\n"`;
     - every case: `(home / "x.inc").read_text()` is unchanged.
2. `test_a_removed_empty_dir_on_the_way_stops_the_run`:
   - Before the guard, `(home / ".config").mkdir()`. `~/.config/git/config`
     doesn't exist.
   - Then `(home / ".config").rmdir()`.
   - `restore_git_dir` raises "NOT all put back", and
     `f"  - {home / '.config'}"` is one of the message's lines.
   - `not (home / ".config").exists()`.
3. `test_a_removed_hooks_dir_is_put_back`:
   - Before the guard, write `.git/hooks/keep`.
   - Then `shutil.rmtree(repo / ".git" / "hooks")`.
   - The restore raises with `match="put back as they were"`, and
     `(repo / ".git" / "hooks" / "keep").exists()`.

   This pins that a real prefix that no longer resolves, and that is itself
   a watched path, is still written back.
4. `test_a_prefix_that_never_resolved_is_not_compared`:
   - Before the guard there is no `~/.config`.
   - The agent writes `home / ".config"` as a regular file.
   - `permissions.restore_git_dir(shipped(repo), guard) == []`.
5. Keep these four tests unchanged:
   - `test_a_removed_real_dir_on_the_way_is_put_back`;
   - `test_a_missing_dir_created_as_a_real_dir_is_still_put_back`;
   - `test_unchanged_prefixes_change_nothing`;
   - `test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it`.

   They pin the baseline's write behaviour, and they must pass untouched.
6. `test_moved_without_gone_matches_the_baseline_roots`:
   - Set up with `_hole1`, take the guard, then `rmdir ~/d`.
   - `str(home / "d") in permissions._moved(guard)`.
   - `str(home / "d") not in permissions._moved(guard, gone=False)`.

## Verification

Run each command from the repo root. Each must exit 0.

1. The guard tests:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. The restore still uses the baseline roots:
   `grep -n "_moved(guard, gone=False)" .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
4. By hand, outside pytest, in a scratch HOME and a scratch repo, against
   the template module. Reproduce the hole: set
   `include.path = $HOME/d/../x.inc`, put `core.hooksPath = /dev/null` in
   `x.inc`, then run `rmdir ~/d`. Confirm that the breach names `~/d` and
   says "NOT all put back".

## Out of scope (old holes, filed as such)

- **Changes outside the checks.** A background process can break a prefix
  after the end-of-turn check. A prefix broken and remade between checks is
  caught only as far as `seen` catches it.
- **Prefixes that did not resolve before.** These are still never compared,
  as the request asks. A change there is left to the path packs.
- **Writing under a real directory that now resolves nowhere.** The restore
  writes there as at the baseline, with `_UNPACK`'s `rm -rf` and `tar -x`.
  If the directory was replaced by a regular file or made impossible to
  enter, tar fails there, and the check after the restore reports "NOT all
  put back". The change adds no new write path.
- **Git refusing instead of skipping.** With `chmod 000 ~/d`, git itself
  errors on the include (EACCES) instead of skipping it. The guard now
  reports this, but SSSF's later git calls behave as at the baseline.
- **Everything else, unchanged.** The `..` refusal in `_watched`, the
  ignore-file rollback gap noted in `config.md`, unwatched paths (names with
  a newline, the credential store), the agent reading files, and telling the
  engineer's concurrent change from the agent's.

## For the engineer

- Once the build passes, stamp the change into the hub the way `e378497`
  did: copy `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
  to `adws/adw_modules/permissions.py`, and refresh
  `adws/adw_sssf_config/stamp.json`. Both are protected, so the builder must
  not touch them.
- For the documenter: if a later write-up says a removed real directory on
  the way is "left to the path packs", note that it now stops the run.
  `specs/6bbc5ca1_guard-prefix-moves.md` says so in its Bar edge cases and
  its Out of scope. That spec is a record, and it stays as it is.

# Stop the run when a prefix of a watched path that resolved no longer does

## Summary

Run 6bbc5ca1 records where every prefix of each watched git path resolves
before the agent (`guard["where"]`, from `_where`). After the agent,
`_moved` reports a prefix that resolves somewhere else. But it skips a prefix
that resolved before and now resolves **nowhere**, unless a symlink is
involved. Its comment says "a removed real directory is left to the path
packs". That is the hole. With `include.path = $HOME/d/../x.inc`, `rmdir ~/d`
or replacing `~/d` with a regular file makes git silently stop loading
`~/x.inc`. No watched file changes, so `restore_git_dir` returns `[]`.

The change has two parts:

1. **Detection.** In `_moved`, a prefix that resolved before and now resolves
   to anything other than the same directory counts as moved. That covers
   another directory and nowhere (missing, a regular file, unsearchable,
   dangling, anything `cd -P` can't enter). `_differs`, `git_dir_changed` and
   the after-restore check all pick it up, so the run stops and the breach
   names the prefix.
2. **Writing stays as at the baseline.** `restore_git_dir` keeps using the
   baseline set to decide which paths it must not write under: a prefix that
   resolves elsewhere, or that was or is a symlink. A real directory that now
   resolves nowhere is reported but is not added to those roots. Its watched
   files are put back exactly as today: tar remakes the parents as real
   directories, and nothing there can be a link the agent planted. If that
   remakes the prefix, the after-restore `_differs` sees it resolve to the
   same place again, and the breach says "put back as they were". If nothing
   remakes it, as in the reproduction where `~/d` holds no watched file, the
   prefix is still in `left` and the breach says "NOT all put back".

## Baseline

`main` at `e378497` ("Stamp the prefix-moves guard into the hub"). Every
finding is judged against that commit. The template module is
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. The hub's
`adws/adw_modules/permissions.py` is an identical stamped copy, and it is
protected.

## Threat model

The agent runs as the engineer's own user, with the same HOME and the same
write access. During its turn it can `rmdir`, `rm -rf`, `mv`, `chmod`,
`ln -s` and write any file the engineer can, inside or outside the repo. Here
its goal is to make git **stop** loading a config file the engineer relies
on, such as an include that sets `core.hooksPath = /dev/null` or turns off a
filter, without changing any file the guard packs. It does that by breaking a
directory on the way to the include, which makes git's include lookup fail
silently (ENOENT or ENOTDIR). The engineer's protective line is then gone for
SSSF's next git command and for theirs.

The hole, present at the baseline: `~/.gitconfig` has
`include.path = $HOME/d/../x.inc`, `~/d` is a real directory, and `~/x.inc`
holds `core.hooksPath = /dev/null`. The agent runs `rmdir ~/d`, or
`rmdir ~/d && touch ~/d`, or `chmod 000 ~/d`. Nothing packed changes, and
`restore_git_dir` returns `[]`.

What this guard can't stop at its level, unchanged from the baseline:

- It checks twice: mid-run in `git_dir_changed`, and at the end of the turn
  in `restore_git_dir`. A background process the agent leaves running can
  break a prefix after the last check.
- A prefix broken and remade between checks is caught only by the existing
  `seen` sentinel, and only if `git_dir_changed` ran while it was broken.
- It can't stop the agent reading files, and it can't tell the agent's change
  from one the engineer made in another terminal.

## Bar

Against the baseline, the change **must**:

- In the reproduction, for each of `rmdir ~/d`, `~/d` replaced by a regular
  file, and `chmod 000 ~/d`:
  - `git_dir_changed(guard)` returns `True`;
  - `restore_git_dir` raises `PermissionBreach` with "NOT all put back" and
    names `…/home/d`;
  - nothing is written at `~/d`: a removed `~/d` stays missing, and the
    agent's regular file stays as the agent left it;
  - `~/x.inc` is unchanged.
- Count as moved every prefix that resolved before and now doesn't resolve
  to the same directory, whatever the reason.
- Keep today's behaviour for a prefix that didn't resolve before. It is never
  compared, so creating it as a real directory or as a file is left to the
  path packs, as now.
- Keep today's behaviour for every prefix that resolves the same. In
  particular, `test_unchanged_prefixes_change_nothing` still returns `[]`.
- Put back every changed watched path that the baseline puts back. A removed
  real directory that holds a watched file, like
  `test_a_removed_real_dir_on_the_way_is_put_back`, or a removed
  `.git/hooks`, is still put back. The breach says "put back as they were"
  once the directory resolves to the same place again.
- Pass all of `.claude/skills/sssf/tests/test_git_dir_guard.py` and the whole
  skill suite, with no existing assertion rewritten.

It must **never**, where the baseline does otherwise:

- keep an agent file that the baseline deletes or puts back;
- let agent code run that the baseline stops;
- delete or overwrite an engineer file that the baseline leaves alone;
- write under a prefix that the baseline's rule skips. The roots that
  `restore_git_dir` won't write under must be exactly the baseline's.

An allowed widening: an agent that removes a real directory on the way to a
watched path that doesn't exist (for example an empty `~/.config` when
`~/.config/git/config` doesn't exist) now stops the run with "NOT all put
back". At the baseline the run went on. That is the request.

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

Replace the comment block above the return so it says:

- a prefix that didn't resolve before is never compared, and the path packs
  cover it;
- one that resolved before and now resolves anywhere else, or nowhere
  (missing, a regular file, unsearchable, a dangling link), has moved;
- with `gone=False`, a real directory that now resolves nowhere and isn't a
  link is left out. Tar remakes it as a real directory when it puts back a
  watched file under it, so writing there is as safe as at the baseline.

Rename the local `gone` list to `none` so it doesn't shadow the parameter.

### `_differs`

No code change. It calls `_moved(guard)`, which now includes gone prefixes.
In the comment above `found = moved + …`, add that a directory on the way that
no longer resolves is reported too.

### `restore_git_dir`

- Change `moved = _moved(guard)`, inside `if paths:`, to
  `moved = _moved(guard, gone=False)`.
- Add one sentence to the comment above `roots`: a real directory that now
  resolves nowhere is reported (through `_differs`) but isn't a root. Tar
  remakes it as a real directory, and the after-check sees whether it
  resolves to the same place again.
- Leave the `changed is None` fallback alone. It calls `_moved(guard)` for
  reporting only.
- No change to the docstring's contract. The module-level "Narrow on
  purpose" comment stays true.

### Why the reproduction stops

`changed` is `[$HOME/d]` (plus `$HOME/d/..` when it is recorded). Neither is
in `guard["paths"]`, so `paths` is empty, and `restored = False`. The breach
says "NOT all put back" and lists them. Nothing runs `_UNPACK`.

## Files to change

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`: `_moved`,
  the comment in `_differs`, and one line plus a comment in `restore_git_dir`,
  as above.
- `.claude/skills/sssf/tests/test_git_dir_guard.py`: the new tests below, and
  one line in the module docstring: "A directory on the way to a watched path
  that resolved before the agent and no longer resolves stops the run."
- `.claude/skills/sssf/references/config.md`, in the git-guard paragraph
  around lines 179-191 ("SSSF also records where every directory…"): after
  "and a directory a link points at that was swapped in turn.", add that it
  also notices a directory on the way that the agent removed, replaced with a
  file, or made unenterable. That would make git silently skip an include
  through it, such as `~/d` in `~/d/../x.inc`. The run stops with "NOT all
  put back" and names it. A watched file under such a directory is still put
  back, and if that remakes the directory, the breach says "put back as they
  were". Keep the file's sentence style: short, plain, no new headings.

There is no CHANGELOG and no `app_docs/` directory in this repo. No help text
or README names this behaviour.

## Tests (`.claude/skills/sssf/tests/test_git_dir_guard.py`)

Add these after `test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it`,
under a comment `# ── a prefix that no longer resolves ──`. Use the existing
`shipped(repo)` helper, which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`, and the existing fixtures
(`repo`, `home`, `_hole1`). Write each test before its code change and watch
it fail at the baseline. Tests 1 and 2 fail at the baseline. Tests 3 to 5
pass at the baseline and must keep passing.

1. `test_a_dir_before_dotdot_that_no_longer_resolves_is_seen`, parametrized
   `how` in `["removed", "file", "locked"]`:
   - Set up `_hole1(repo, home, tmp_path)`. Then write
     `"[core]\n\thooksPath = /dev/null\n"` to `home / "x.inc"`, and check
     `git(repo, "config", "core.hooksPath") == "/dev/null"`.
   - `guard = permissions.guard_git_dir(shipped(repo))`.
   - The agent's move:
     - `removed`: `(home / "d").rmdir()`;
     - `file`: rmdir, then `(home / "d").write_text("agent\n")`;
     - `locked`: `(home / "d").chmod(0)`. Skip when `os.geteuid() == 0`, and
       restore `0o755` in `finally`.
   - For `removed` and `file` only, show the hole: `"x.inc" not in git(repo,
     "config", "--list", "--show-origin")`. Skip this for `locked`, because
     git dies with EACCES there.
   - `assert permissions.git_dir_changed(guard)`.
   - `restore_git_dir` raises with `match="NOT all put back"`, and
     `str(home / "d")` is in the message.
   - Afterwards: `removed`: `not (home / "d").exists()`; `file`:
     `(home / "d").read_text() == "agent\n"`; every case:
     `(home / "x.inc").read_text()` is unchanged.
2. `test_a_removed_empty_dir_on_the_way_stops_the_run`: `(home /
   ".config").mkdir()` before the guard (`~/.config/git/config` doesn't
   exist). Then `(home / ".config").rmdir()`. `restore_git_dir` raises
   "NOT all put back" naming `str(home / ".config")`, and
   `not (home / ".config").exists()`.
3. `test_a_removed_hooks_dir_is_put_back`: write `.git/hooks/keep` before
   the guard, then `shutil.rmtree(repo / ".git" / "hooks")`. The restore
   raises `match="put back as they were"`, and `(repo / ".git" / "hooks" /
   "keep").exists()`. This pins that a gone real prefix that is itself a
   watched path is still written back.
4. `test_a_prefix_that_never_resolved_is_not_compared`: with no `~/.config`
   before the guard, the agent writes `home / ".config"` as a regular file.
   `permissions.restore_git_dir(shipped(repo), guard) == []`.
5. Keep `test_a_removed_real_dir_on_the_way_is_put_back`,
   `test_a_missing_dir_created_as_a_real_dir_is_still_put_back`,
   `test_unchanged_prefixes_change_nothing` and
   `test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it`
   unchanged. They pin the baseline write behaviour, and they must pass
   untouched.

Also add a direct unit check to test 1, or a separate small test 6
`test_moved_without_gone_matches_the_baseline_roots`. After `rmdir ~/d` in
`_hole1`, `str(home / "d") in permissions._moved(guard)` and
`str(home / "d") not in permissions._moved(guard, gone=False)`.

## Verification

Run each command from the repo root. Each must exit 0.

1. The guard tests:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. The restore still uses the baseline roots:
   `grep -n "_moved(guard, gone=False)" .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
4. By hand, outside pytest, in a scratch HOME and a scratch repo, against the
   template module: reproduce the hole (`include.path = $HOME/d/../x.inc`,
   `x.inc` with `core.hooksPath = /dev/null`, then `rmdir ~/d`). Confirm that
   the breach names `~/d` and says "NOT all put back".

## Out of scope (old holes, filed as such)

- **Changes outside the checks.** A prefix broken after the end-of-turn
  check by a background process, or broken and remade between checks beyond
  what `seen` catches.
- **Prefixes that did not resolve before.** These are still never compared,
  as the request says. A change there is left to the path packs.
- **Writing under a real directory that now resolves nowhere.** The restore
  writes there as at the baseline (`_UNPACK` with `rm -rf` and `tar -x`). That
  covers one replaced by a regular file or made unenterable: tar fails there,
  and the after-check reports "NOT all put back". No new write path is added.
- **Git refusing rather than skipping.** With `chmod 000 ~/d`, git itself
  errors on the include (EACCES) instead of skipping it. The guard now
  reports it, but how SSSF's later git calls behave there is unchanged.
- **The `..` refusal in `_watched`**, the ignore-file rollback gap noted in
  `config.md`, unwatched paths (newline names, the credential store), reading
  files, and telling the engineer's concurrent change from the agent's: all
  unchanged.

## For the engineer

- Once the build passes, stamp the change into the hub the way `e378497`
  did. Copy `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
  to `adws/adw_modules/permissions.py`, and refresh
  `adws/adw_sssf_config/stamp.json`. Both are protected, so the builder must
  not touch them.
- For the documenter: if a later write-up says that a removed real directory
  on the way is "left to the path packs", note that it now stops the run.
  `specs/6bbc5ca1_guard-prefix-moves.md` says that under its Bar edge cases
  and Out of scope. That spec is a record, and it stays as it is.

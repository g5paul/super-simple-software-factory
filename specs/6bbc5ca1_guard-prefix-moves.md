# Stop the run when any prefix of a watched path resolves somewhere new

This builds on the design from run 7bc35cc5 (v2). That review passed its size,
threat model, tests and Out of scope, and blocked only on what
`restore_git_dir` writes once a prefix has moved. Changes from that design:

- The restore filter no longer skips every path under the place a moved
  prefix resolved to before. It skips a path only when its current `_REAL` is
  `""` **and** it sits under a moved prefix (Design, `restore_git_dir`).
- The moved prefixes come from `_moved(guard)`, the prefix comparison, and
  never from the list of changed paths. A `hooks` or `core.hooksPath`
  directory whose contents changed has not moved.
- A prefix's earlier resolution is used as a root only when its last
  component isn't `.` or `..`. `$HOME/d/..` resolved to `$HOME`, which has
  not moved.
- There are two new tests, 10 and 11. They pin that a path whose `_REAL` is
  unchanged is still put back.

## Summary

The git-directory guard (`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`)
keeps a list of the symlinks on the way to each watched path (`_LINKS`,
`guard["links"]`, `guard["parents"]`). It reports a link that now points
somewhere else, but it records only what was a symlink before the agent ran.
That misses two moves: a real directory on the way turned into a link, and a
link further along a link's target that was retargeted.

The change replaces that list with a record of where **every prefix** of
each watched path resolves. Each path is walked as written, the way the
kernel walks it, and walked again after the agent. If a prefix now resolves
somewhere else, the run stops with "NOT all put back" and names it. Nothing is
written through it.

## Baseline

`main` at `00890ed` ("Stamp the link-before-dotdot guard into the hub"). Every
finding is judged against that commit. The template module there is
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. The hub's
`adws/adw_modules/permissions.py` is a stamped copy of the same code.

## Threat model

The agent runs as the engineer's own user, with the same HOME and write
access. During its turn it can `rmdir`, `mv`, `ln -s`, and write any file the
engineer can, including outside the repo: `$HOME`, `$TMPDIR`, or any directory
a watched path goes through. Its goal here is to get git to load a config line
the agent wrote, such as `core.fsmonitor`, `core.hooksPath`, a hook or a
filter. Git then runs it on SSSF's next command or the engineer's, even though
every watched file still reads the same.

The three holes this change closes, all present at the baseline:

1. `include.path = $HOME/d/../x.inc`, with `~/d` a real directory. The agent
   runs `rmdir ~/d; ln -s $T/evil/a ~/d` and writes `fsmonitor` into
   `$T/evil/x.inc`. Git loads it, and `restore_git_dir` returns `[]`.
2. `.git/config` has `include.path = ../shared.inc`. The agent moves `.git` to
   `$T/evil/g`, links `.git` there, and writes `$T/evil/shared.inc`. Every
   watched file reads the same through the link.
3. `include.path = $HOME/lnk/../x.inc`, with `~/lnk -> $T/out/a`. The agent
   leaves `~/lnk` and replaces the real `$T/out/a` with a link to `$T/evil/a`.

What this guard can't stop at its level:

- It checks twice: mid-run in `git_dir_changed`, and at the end of the turn in
  `restore_git_dir`. A background process the agent leaves running can change
  anything after the last check.
- A change the agent makes and undoes between checks is seen only by the
  existing `seen` sentinel.
- It can't stop the agent reading files.
- It can't tell the agent's change from one the engineer made in another
  terminal.

Those stay as at the baseline.

## Bar

Against the baseline, the change **must**:

- Stop the run in each of the three holes above:
  - `git_dir_changed` returns `True`;
  - `restore_git_dir` raises a `PermissionBreach` that says "NOT all put
    back" and names the moved prefix (`…/home/d`, `…/repo/.git`,
    `…/home/lnk`).
- Write nothing through a moved prefix:
  - the agent's link stays as the agent left it;
  - nothing is created in the directory it points at, or where it would point
    if that directory existed;
  - a watched path whose current `_REAL` is `""` and that sits under a moved
    prefix is not put back.
- Put back every other changed path exactly as the baseline does: a path whose
  current `_REAL` equals the path, or is `""` and sits under no moved prefix.
  So in hole 1, an `fsmonitor` line the agent also adds to `~/.gitconfig` is
  put back, and the run still says "NOT all put back".
- Keep every breach a baseline test expects. All of
  `.claude/skills/sssf/tests/test_git_dir_guard.py` passes. The only
  assertions rewritten are those on the removed internals, listed below.

It must **never**, where the baseline does otherwise:

- keep an agent file the baseline deletes or puts back;
- let agent code run that the baseline stops;
- delete or overwrite an engineer file the baseline leaves alone.

The edge cases:

- **A prefix created as a real directory.** A prefix that did not exist, or
  was not a directory, before the run may be created as a **real** directory
  during it. That is not a "NOT all put back" stop. At the baseline the new
  watched file under it is put back (deleted), and it still is.
- **A real directory removed.** That is left to today's path checks, which
  put its watched files back.
- **A watched path that is itself the moved prefix.** An example is a real
  `hooks` directory swapped for a link. Its `_REAL` is unchanged, because its
  parent hasn't moved, so it is put back as at the baseline. `_UNPACK`
  removes the link and extracts the saved directory, so it never writes
  through the link.
- **A broad ancestor that hasn't moved.** Examples are `$HOME` behind
  `$HOME/d/..`, or `realhome` behind a retargeted HOME link. Paths there whose
  `_REAL` is unchanged are put back as at the baseline.

Allowed narrowings. The baseline reports these and the new guard won't, but
none of them lets git read a different file:

- a link whose text changes but which still resolves to the same directory;
- a dangling link on the way, retargeted to another place that doesn't
  resolve either.

If either change makes git read a file, the existing path packs already see
it.

## Design

### New shell script `_WHERE` (replaces `_LINKS`)

Delete `_LINKS` and its comment. Add this next to `_REAL`:

```sh
# Prints, NUL-separated, a triple for every prefix of each path as written,
# the kernel's way: the prefix, the directory it resolves to (cd -P; empty if
# it isn't a directory that can be entered), and L if it is a symlink, else -.
# Empty components are skipped; . and .. are kept, as the kernel takes them.
for p in "$@"; do
  rest=${p#/}; q=
  while [ -n "$rest" ]; do
    c=${rest%%/*}
    case $rest in */*) rest=${rest#*/};; *) rest=;; esac
    [ -z "$c" ] && continue
    q="$q/$c"
    r=$(cd -P "$q" 2>/dev/null && pwd -P) || r=
    if [ -L "$q" ]; then l=L; else l=-; fi
    printf "%s\\0%s\\0%s\\0" "$q" "$r" "$l"
  done
done
```

Inside the Python `"""` string, escape `\0` as `\\0`, the same way `_REAL`
does.

### `_where` and `_moved` (new, in `permissions.py`)

```python
def _where(walked: list[str]) -> dict[str, list[str]] | None:
    """Where each prefix of `walked` resolves: {prefix: [dir or "", "L" or "-"]},
    or None if the output can't be read."""
```

1. Return `{}` for an empty list.
2. Otherwise run `_sh(_WHERE, *walked)` once and split its stdout on `\0`.
3. Drop the trailing empty field.
4. If the field count is not a multiple of three, return `None`. That is the
   only failure mode.
5. Take the fields in triples, and keep the first entry for each prefix
   (`dict.setdefault`).

```python
def _moved(guard: dict) -> list[str] | None:
    """The recorded prefixes that now resolve somewhere else, or None if they
    could not be walked again."""
    then = guard.get("where", {})
    if not then:
        return []
    now = _where(guard.get("walked", []))
    if now is None:
        return None
    gone = ["", "-"]
    return [q for q, (r, l) in then.items()
            if r and now.get(q, gone)[0] != r
            and (now.get(q, gone)[0] or l == "L" or now.get(q, gone)[1] == "L")]
```

Above the comprehension, add a comment that states the rule in plain words:

- A prefix that did not resolve before is never compared, and today's path
  packs cover it. That means a missing prefix, one that isn't a directory,
  and one that can't be searched.
- A prefix that resolved before and now resolves to a different directory
  has moved.
- A prefix that resolved before and now resolves nowhere has moved only if it
  was a symlink or is one now: a removed link, or a real directory swapped for
  a dangling link.
- A removed real directory is left to the path packs, which put its watched
  files back as today.

### `_watched`

- Keep `written = [p for p in dict.fromkeys(logical + paths) if p.startswith("/")]`
  and `logical = ["/" + posixpath.normpath(p).lstrip("/") for p in written]`.
- Delete the `links = … _LINKS …` line, and the whole `dotted` / `found`
  block with its comment.
- The first return value becomes `walked = list(dict.fromkeys(written + logical))`:
  each watched path as written **and** its normalized form. Walking the
  normalized form too keeps every link the baseline's normalized `_LINKS`
  walk covered in the same map, for example `~/l3` in `~/lnk/../l3/x.inc`.
- Delete the final `links = [q for q in dict.fromkeys(links) …]` line, and
  return `walked, resolved, kinds`.
- Update the docstring. The first value is every watched path as written plus
  its normalized form, and `guard_git_dir` records where each of their
  prefixes resolves. Drop the `_LINKS` sentence.

The `loose` refusal (a `..` path that doesn't resolve) stays as it is.

### `guard_git_dir`

- `walked, paths, kinds = _watched(run)`.
- `plain = _pack(paths, follow=False)`. Links are no longer added to it.
- After both packs succeed, set `where = _where(walked)`. If it is `None`,
  raise `PermissionBreach("Could not resolve the git paths to watch, so the agent did not run.")`.
- Return `{"walked": walked, "where": where, "paths": paths, "plain": plain,
  "followed": followed, "kinds": kinds, "done": False}`. Drop `"links"` and
  `"parents"`.

### `_differs`

- `every` goes away. Use `plain = _pack(guard["paths"], follow=False)`.
- Replace the `links` / `moved` / `parents` block with
  `moved = _moved(guard)`. If it is `None`, return `None`, the same as a
  failed plain pack ("could not compare").
- `found = moved + [p for p in guard["paths"] if p not in moved and any(...)]`.
  The `p not in moved` dedupes: a full path such as a `hooks` directory can
  also be a prefix.
- Keep the `TOO_LARGE` tail and the follow handling as they are.
- Delete the comment about a parent swapped under a relative link. In its
  place, say that resolving every prefix fully covers it.

### `restore_git_dir`

1. In the `if changed is None:` branch (could not compare), set
   `changed = list(dict.fromkeys((_moved(guard) or []) + list(guard["paths"])))`
   where it was `links + paths`.
2. Replace the `_REAL` filter:

   ```python
   paths = [p for p in guard["paths"] if p in changed]
   if paths:
       now = _sh(_REAL, *paths).stdout.split("\0")[:len(paths)]
       paths = [p for p, r in zip(paths, now) if r in ("", p)]
   ```

   with this:

   ```python
   paths = [p for p in guard["paths"] if p in changed]
   if paths:
       now = _sh(_REAL, *paths).stdout.split("\0")[:len(paths)]
       moved = _moved(guard)
       where = guard.get("where", {})
       # A prefix that moved roots the paths under it, by the prefix as written
       # and, unless it ends in . or .., by where it resolved before: $HOME/d/..
       # resolved to $HOME, which hasn't moved. Only a path whose parent no
       # longer resolves ("") is skipped there: tar would write through a
       # dangling link. A path whose _REAL is unchanged is safe, as today.
       roots = [] if moved is None else [
           s for q in moved
           for s in ([q] + ([where[q][0]] if q.rsplit("/", 1)[-1] not in (".", "..") else []))]

       def under(p: str) -> bool:
           return moved is None or any(p.startswith(s.rstrip("/") + "/") for s in roots)
       paths = [p for p, r in zip(paths, now) if r == p or (r == "" and not under(p))]
   ```

   - `moved` comes from `_moved(guard)` and never from `changed`. A `hooks`
     directory whose contents changed is in `changed` but has not moved, so
     it roots nothing.
   - "Under" means strictly under: `p` starts with the root plus `/`.
     Watched paths are physical, so the earlier resolution catches
     `<realhome>/d/x.inc` when HOME was reached through a link.
   - If `_moved` returns `None`, which happens only when `_WHERE`'s output is
     malformed, every `""` path is treated as under a moved prefix and
     skipped. Paths whose `_REAL` is unchanged are still put back. That fails
     safe, since nothing is written where the guard can't tell what moved.
3. Leave the rest as it is:
   - If `paths` ends up empty and `changed` is not the sentinel, the existing
     `if changed and not paths and not sentinel: restored = False` line
     already gives "NOT all put back".
   - Otherwise, the later `left = _differs(guard)` walks the prefixes again.
     A prefix that is still moved stays in `left`, so `restored` is false and
     the outcome is "NOT all put back", even when every listed path was put
     back. That is what test 10 pins.
   - Moved prefixes are never in `guard["paths"]` unless they are watched
     paths themselves, so they are never written. Sometimes the put-back
     itself makes a prefix resolve as before, as with a `hooks` directory
     swapped for a link, which `_UNPACK` replaces. That prefix drops out on
     the second `_differs`, which is today's outcome for that case.
4. Add one clause to the "Narrow on purpose" comment: nothing whose parent no
   longer resolves is put back under a prefix that moved.

### Breach text

There is no new message. A moved prefix is one of the paths listed under the
existing "NOT all put back. A change through a symlink is reported, never
written back; …" outcome. It is shown relative to the repo root when it is
inside the repo.

## Files to change

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`, with the
  design above:
  - delete `_LINKS`;
  - add `_WHERE`, `_where` and `_moved`;
  - update `_watched`, `guard_git_dir`, `_differs` and `restore_git_dir`;
  - update the docstrings and comments that mention links on the way: the
    `_watched` docstring, the `_REAL`/`_LINKS` comments, and the
    `restore_git_dir` "Narrow on purpose" comment.
- `.claude/skills/sssf/tests/test_git_dir_guard.py`, with the tests below.
- `.claude/skills/sssf/references/config.md`: rewrite the paragraph that
  starts "SSSF also notices when a symlink on the way to one of these
  paths…" (around line 179). It should say:
  - SSSF records where every directory on the way to each of these paths
    resolves, walking the path as git and the kernel do, `..` included.
  - So it notices a symlink on the way that now points somewhere else: a
    symlinked `~/.config`, `HOME` or `.git`, or `lnk` in `~/lnk/../x.inc`.
  - It also notices a real directory on the way that the agent swapped for a
    symlink, and a directory a link points at that was swapped in turn.
  - Nothing is written through such a directory.

  Keep the sentences after that as they are: it reports, never writes back,
  says "NOT all put back", and asks you to check by hand. The ignore-file
  limit around line 213 is still true and stays as it is.

The repo has no CHANGELOG, and no README or help text names these internals.

## Tests (`.claude/skills/sssf/tests/test_git_dir_guard.py`)

Each test builds its config from the shipped
`.claude/skills/sssf/templates/sssf.config.yaml` through the existing
`shipped(repo)` helper wherever a config is needed. The guard functions
themselves take only `run`. Reuse the `home` and `repo` fixtures, `_link_out`,
`_evil`, `FSMONITOR`, `plant_hook` and `commit_runs_no_hook`.

### Rewrite (assertions on removed internals only)

- `test_a_link_before_dotdot_in_the_gitconfig_is_recorded_and_seen`: change
  `str(home / "lnk") in guard["links"]` to `str(home / "lnk") in guard["where"]`.
- `test_a_link_before_dotdot_is_recorded_for_every_key`: the same change,
  with `str(repo / "lnk") in guard["where"]`.
- `test_a_link_after_dotdot_is_recorded_where_the_kernel_goes`: replace the
  two `guard["links"]` asserts with `str(home / "lnk") in guard["where"]` and
  `f"{home}/lnk/../l3" in guard["where"]`. The behaviour asserts stay.
- `test_a_dotdot_path_keeps_the_baseline_links_and_adds_the_kernels`: keep
  its `restore == []` assert. Replace the `guard["links"]` loop with a
  parametrized behaviour check over each link the loop named (`home/lnk`,
  `out/l3`, `home/l3`):
  1. run `guard_git_dir`;
  2. retarget that one link: unlink it, then `symlink_to` a fresh directory
     under `tmp_path`;
  3. assert that `restore_git_dir` raises "NOT all put back".

  Make it a separate parametrized test if that is cleaner.
- `test_a_path_without_dotdot_makes_no_extra_shell_call`: `_LINKS` no longer
  exists, so drop the `extra` column. Rename the test
  `test_the_prefixes_are_walked_in_one_shell_call`. For both
  parametrizations, assert:
  - `scripts.count(permissions._WHERE) == 1` during `guard_git_dir`;
  - `scripts.count(permissions._REAL) == 2` during `guard_git_dir`, once for
    the paths and once for the work tree;
  - `str(home / "lnk") in guard["where"]`;
  - `restore_git_dir(...) == []`.

### Add

1. `test_a_real_dir_before_dotdot_swapped_for_a_link_is_seen` (hole 1).
   - Setup: make `home/d` a real directory and `home/x.inc` an empty file,
     then run `git config --global --add include.path {home}/d/../x.inc`.
     Guard.
   - Agent: `rmdir home/d`, then `home/d.symlink_to(evil/"a")` with
     `evil = _evil(tmp_path)`, and write `FSMONITOR` to `evil/"x.inc"`.
   - Assert:
     - `"pwned"` is in `git(repo, "config", "--show-origin", "--get", "core.fsmonitor")`
       (the hole is real);
     - `git_dir_changed(guard)` is True;
     - `restore_git_dir` raises "NOT all put back", naming `str(home / "d")`;
     - `os.readlink(home / "d") == str(evil / "a")`;
     - `(evil / "x.inc").read_text() == FSMONITOR`;
     - `(home / "x.inc").read_text() == ""`.
2. `test_a_moved_dot_git_with_a_relative_include_is_seen` (hole 2).
   - Setup: run `git(repo, "config", "include.path", "../shared.inc")`, and
     make `repo/shared.inc` empty. Guard.
   - Agent: `evil = tmp_path/"evil"; evil.mkdir()`, then
     `(repo/".git").rename(evil/"g")`, `(repo/".git").symlink_to(evil/"g")`,
     and `(evil/"shared.inc").write_text(FSMONITOR)`.
   - Assert:
     - `git_dir_changed`;
     - `restore_git_dir` raises "NOT all put back", with `.git` in the
       message;
     - `os.readlink(repo / ".git") == str(evil / "g")`;
     - `(repo/"shared.inc").read_text() == ""`;
     - `sorted(p.name for p in evil.iterdir()) == ["g", "shared.inc"]`.
   - Also assert the `"pwned"` precondition, as in test 1. If git on the
     build machine doesn't load it, keep every breach assertion and drop only
     that precondition. Say why in a comment and in the build report.
3. `test_a_dir_a_link_points_at_swapped_for_a_link_is_seen` (hole 3).
   - Setup: `out = _link_out(tmp_path)`, `home/lnk -> out/a`, and a global
     include of `{home}/lnk/../x.inc`, with `out/x.inc` empty. Guard.
   - Agent: `(out/"a").rmdir()`, `evil = _evil(tmp_path)`,
     `(out/"a").symlink_to(evil/"a")`, and `FSMONITOR` written to
     `evil/"x.inc"`.
   - Assert:
     - the `"pwned"` precondition;
     - `git_dir_changed`;
     - "NOT all put back", naming `str(home / "lnk")`;
     - `os.readlink(out / "a") == str(evil / "a")`;
     - `(out/"x.inc").read_text() == ""`.
4. `test_a_missing_dir_created_as_a_real_dir_is_still_put_back`. This pins
   the edge where no agent file is kept, the same as the baseline.
   - Setup: no `~/.config` before the run. Guard.
   - Agent: `(home/".config"/"git").mkdir(parents=True)`, then
     `(home/".config"/"git"/"config").write_text(FSMONITOR)`.
   - Assert:
     - `restore_git_dir` raises with "put back as they were", not "NOT all
       put back";
     - `not (home/".config"/"git"/"config").exists()`.
   - This assumes a physical `tmp_path`, as on Linux. Skip it, with a reason,
     when `tmp_path.resolve() != tmp_path`.
5. `test_a_removed_real_dir_on_the_way_is_put_back`.
   - Setup: `home/d` is a real directory with an empty `home/d/x.inc`, and a
     global include of `{home}/d/x.inc`. Guard.
   - Agent: `shutil.rmtree(home/"d")`.
   - Assert "put back as they were", and `(home/"d"/"x.inc").read_text() == ""`.
6. `test_a_removed_link_on_the_way_is_still_reported`.
   - Setup: as in test 3. Guard.
   - Agent: `(home/"lnk").unlink()`.
   - Assert "NOT all put back", naming `str(home/"lnk")`. The baseline reports
     this too, and it must not go silent.
7. `test_unchanged_prefixes_change_nothing`: parametrize over the three hole
   setups with no agent action, and assert
   `restore_git_dir(run, guard) == []`.
8. `test_a_real_dir_swapped_for_a_dangling_link_writes_nothing_through_it`.
   - Setup: `home/d` is a real directory, `home/d/x.inc` contains
     `"[user]\n"`, and there is a global include of `{home}/d/x.inc`. Guard.
   - Agent: `shutil.rmtree(home/"d")`, then
     `(home/"d").symlink_to(tmp_path/"evil"/"a")`. Don't create
     `tmp_path/"evil"`.
   - Assert:
     - `git_dir_changed(guard)`;
     - `restore_git_dir` raises "NOT all put back", naming `str(home / "d")`;
     - `os.readlink(home / "d") == str(tmp_path/"evil"/"a")`;
     - `not (tmp_path/"evil").exists()`.
9. `test_a_hooks_dir_swapped_for_a_link_is_still_put_back`. This pins the
   edge where a watched path is itself the moved prefix.
   - Setup: `plant_hook(tmp_path/"evilhooks")`, then guard.
   - Agent: `shutil.rmtree(repo/".git"/"hooks")`, then
     `(repo/".git"/"hooks").symlink_to(tmp_path/"evilhooks")`.
   - Assert:
     - `restore_git_dir` raises;
     - `repo/".git"/"hooks"` is no longer a symlink;
     - `commit_runs_no_hook(repo)`.

   That is the baseline outcome.
10. `test_a_moved_dotdot_prefix_still_puts_back_the_gitconfig`. This is the
    review's first case: `$HOME` is not a root.
    - Setup: as in test 1, with `~/.gitconfig` holding the include line.
      Save `before = (home/".gitconfig").read_text()`. Guard.
    - Agent: as in test 1, and also append `FSMONITOR` to
      `home/".gitconfig"`.
    - Assert:
      - `restore_git_dir` raises "NOT all put back", naming `str(home / "d")`
        and `.gitconfig`;
      - `(home/".gitconfig").read_text() == before`;
      - `os.readlink(home / "d") == str(evil / "a")`.
11. `test_a_retargeted_home_link_still_puts_back_the_physical_gitconfig`. This
    is the review's second case: the earlier resolution of a moved link,
    `realhome`, roots no path whose `_REAL` is unchanged.
    - Setup: as in `test_a_retargeted_home_link_is_seen`. `realhome/.gitconfig`
      holds `"[user]\n\tname = t\n"`, HOME is `linkhome -> realhome`. Guard.
    - Agent: append `FSMONITOR` to `realhome/.gitconfig`, then retarget
      `linkhome` to `evilhome`, as that test does.
    - Assert:
      - `restore_git_dir` raises "NOT all put back";
      - `(realhome/".gitconfig").read_text() == "[user]\n\tname = t\n"`;
      - `os.readlink(linkhome) == str(evilhome)`;
      - `evilhome/.gitconfig` is unchanged.

Every existing test must pass without edits, apart from the rewrites above.
That includes:

- `test_a_retargeted_config_dir_link_is_reported_not_written`
- `test_a_retargeted_home_link_is_seen`
- `test_a_retargeted_dot_git_link_is_seen`
- `test_a_retargeted_hooks_path_link_is_seen`
- `test_a_parent_swapped_under_a_relative_link_is_seen`
- `test_nothing_is_written_through_a_link`
- `test_a_restore_keeps_nested_home_links`
- the `..`-refusal tests

## Verification

Run each command from the repo root. Each must exit 0.

1. The guard tests:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite, to show nothing else relied on `guard["links"]`:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. No use of the removed internals is left in the template or the guard
   tests:
   `! grep -rn "_LINKS\|guard\[\"links\"\]\|\"parents\"" .claude/skills/sssf/templates/adws/adw_modules/permissions.py .claude/skills/sssf/tests/test_git_dir_guard.py`
4. By hand, outside pytest: in a scratch repo under a scratch HOME, reproduce
   hole 1 (`rmdir ~/d; ln -s …`) against the template module. Confirm that
   the breach names `~/d` and says "NOT all put back".

## Out of scope (old holes, filed as such)

- **Changes outside the checks.** An agent that acts after the end-of-turn
  check, through a background process, or that changes and reverts something
  between checks beyond what the `seen` sentinel catches.
- **Prefixes that did not resolve before.** A prefix that was missing, a
  file, unsearchable, or a dangling link before the run is not compared. A
  change there is left to today's path packs, which see any file git would
  read through it. A dangling link retargeted to another dangling place is no
  longer reported, since nothing there can be read.
- **Same-resolution links.** A link whose text changes but which resolves to
  the same directory is no longer reported. Git reads the same files.
- **A real directory replaced by a regular file.** This is left to today's
  path checks. Git can't resolve through a file. `_REAL` of a path under it
  is `""`, and the prefix hasn't moved by `_moved`'s rule, so the put-back
  runs as at the baseline. `_UNPACK` acts on a regular file and never goes
  through a link.
- **The ignore-file rollback gap in `config.md`, unchanged.** `config.md`
  says, around line 213, that an ignore file the agent changed through a link
  can hide one of its own new files from the rollback. The prefix record now
  reports a moved directory on the way to `info/exclude`, `core.excludesFile`
  or `~/.config/git/ignore`, and the run stops. But the change-set rollback in
  `enforce` still reads the ignore rules as they are, so an agent file hidden
  that way can still survive the rollback, as at the baseline.
- **The `..` refusal, unchanged.** A watched path with `..` after a directory
  that doesn't exist yet, or can't be entered, still stops the run before the
  agent runs (the `loose` check in `_watched`). This change neither widens
  nor narrows it.
- **Unwatched paths.** Paths with a newline in them, the credential store,
  and anything outside the watched set, as at the baseline.
- **Reading, and who made a change.** The agent reading watched files, and
  telling the engineer's own concurrent change from the agent's.

## For the engineer

- Once the build passes, stamp the change into the hub the way `00890ed`
  did. Copy `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
  to `adws/adw_modules/permissions.py`, and refresh
  `adws/adw_sssf_config/stamp.json`. Both are protected, so the builder must
  not touch them.
- For the documenter: if a write-up in `app_docs/` describes
  `guard["links"]`, or "the symlinks on the way", as what the guard records,
  note that the guard now records where every prefix of each watched path
  resolves.

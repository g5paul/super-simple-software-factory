# Record a symlink that a later `..` cancels on a watched path

## Request

The git-directory guard records every symlink on the way to a watched path in
`guard["links"]`, saves where each points, and stops the run with "NOT all put
back" if one points somewhere else after the agent. A bypass review in run
2d444669 found an old hole: in `_watched`, the walk that collects the links
(`_LINKS`) runs on `posixpath.normpath(p)`, which removes `lnk/..` before the
walk sees `lnk`. The kernel follows `lnk` first, then takes its parent.

Reproduction: `~/.gitconfig` has `include.path = $HOME/lnk/../x.inc`,
`$HOME/lnk -> out/a`, `out/x.inc` exists, so git loads `out/x.inc`. The guard
watches `out/x.inc` and `guard["links"]` is empty. The agent runs
`rm ~/lnk; ln -s evil/a ~/lnk` and writes
`[core] fsmonitor = touch /nonexistent/pwned` to `evil/x.inc`. Git now loads
`evil/x.inc`, and `restore_git_dir` returns `[]`.

Fix: for each watched path with `..`, also collect the links the kernel meets
walking it as written, following each symlink before a later `..`. A path
with no `..`, and the paths the guard watches, keep exactly today's
behaviour.

This is the second version of this plan. The first added a new shell walk
(`_WALK`); the spec review showed the two existing scripts, `_LINKS` and
`_REAL`, give the same links, so this version adds no shell script.

## Baseline

`main` at `4293881` ("Stamp the dotdot and unreadable-config guards into the
hub"). Every finding is judged against that commit. At `4293881`, in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- `_watched` builds `logical` = `{root}/.git`, the absolute `core.hooksPath`
  value, and every entry of `paths`, each passed through
  `"/" + posixpath.normpath(p).lstrip("/")`, then
  `links = [q for q in _sh(_LINKS, *logical).stdout.split("\0") if q]`.
- `_LINKS` prints every lexical prefix of each path (the path itself and each
  parent, made by cutting `/…` off the end) that `[ -L ]` says is a symlink.
- `_REAL` prints each path with its parent resolved by `cd -P` and its leaf
  appended, or an empty field where the parent doesn't resolve.
- `paths` (later `resolved`) holds the `_REAL` result, so for
  `$HOME/lnk/../x.inc` it watches the physical `out/x.inc`. A path with `..`
  that doesn't fully resolve is refused (`loose`) with a `PermissionBreach`.
- At the end, `links` drops `/` and anything in `resolved`, and is deduped.
- `guard_git_dir` plain-packs `links + paths` and stores
  `parents = _sh(_REAL, *links)`. `_differs` reports a link whose own target
  text or `_REAL` parent changed. `restore_git_dir` never writes a link, so a
  moved link gives "NOT all put back".

So for `$HOME/lnk/../x.inc`, `normpath` gives `$HOME/x.inc`, `_LINKS` never
tests `$HOME/lnk`, and a retarget of it goes unseen. The same goes for a link
*after* a `..`, in the physical directory the kernel reaches
(`$HOME/lnk/../l3/x.inc` with `out/l3 -> out/c`): `_LINKS` tests `$HOME/l3`,
which is not where the kernel goes.

## Threat model

The agent runs as the engineer's own user and has bash. It can delete and
create symlinks, directories and files anywhere that user can write,
including `$HOME` and the work tree. It can't change the packs or the saved
`links`/`parents`, which live in SSSF's process memory, and it can't change
the engineer's config from before the run.

The trigger is a setup the engineer made: a watched path (an `include.path`,
`includeIf.*.path`, `core.excludesFile` or `core.attributesFile` value, or
the `.git`/`core.hooksPath` logical paths) written with a symlink followed
later by `..`. The agent only retargets that symlink, then writes the file
the kernel now reaches.

Because the agent is the engineer's own user, this guard can't stop, at its
level:

- killing the chain before the check runs;
- a process left behind that retargets the link, or writes, after the check;
- a race between the check and the next git command;
- writes to paths the guard doesn't watch.

`--sandbox` and `--container` are the isolation for those. This change only
widens which symlinks `guard["links"]` records.

## Bar

Against `4293881`, the change must:

- record in `guard["links"]`, for every watched or logical path with a `..`
  component, each component the kernel finds to be a symlink when it resolves
  that path as written, named as its physical parent plus its own name. For
  the reproduction that is `$HOME/lnk`; for `$HOME/lnk/../l3/x.inc` it is
  `$HOME/lnk` and `<physical out>/l3`. A name already in `resolved` is left
  out, as today.
- so a retarget of such a link, as in the reproduction, makes
  `git_dir_changed(guard)` true and `restore_git_dir` raise
  `PermissionBreach` with "NOT all put back" naming the link, for
  `include.path`, `includeIf.*.path`, `core.excludesFile` and
  `core.attributesFile`, in the repo config and in `~/.gitconfig`.
- keep every link the baseline records. The new links are added to today's
  `_LINKS` output and never replace it.
- leave every other behaviour as at `4293881`:
  - a path with no `..` component gets exactly today's links, and when no
    watched or logical path has a `..` the guard makes exactly the shell calls
    it makes today (`_LINKS` once, no extra `_REAL`);
  - `guard["paths"]`, `guard["kinds"]`, the `loose` refusal and its message,
    the packs, the restore, the scripts `_LINKS`, `_REAL`, `_PACK` and
    `_UNPACK`, and every breach text are unchanged;
  - the new step writes, creates, chmods or deletes nothing. It only runs the
    two existing read-only scripts.

Where the baseline doesn't, the change must never keep an agent file, run
agent code, or delete or overwrite an engineer file. A link is never written
back, as today. The breach says "NOT all put back" and the engineer checks it
by hand.

The request accepts one cost: if the engineer changes such a link
themselves during an agent call, the run now stops. A change to any other
link on the way stops it today.

## Out of scope

Old holes this change leaves open. A reviewer should file them as old:

- **Links inside a symlink's target.** `$HOME/lnk -> m/a` with `$HOME/m ->
  out`: the kernel passes through `$HOME/m` while following `lnk`, and a
  retarget of `$HOME/m` moves the file git reads. `[ -L ]` on written
  prefixes never reads a link's target, so `$HOME/m` is not recorded. Paths
  with no `..` have the same hole (`$HOME/d/x.inc`, `$HOME/d -> m/a`), and the
  request keeps their behaviour as today, so this change doesn't close it
  for `..` paths alone either.
- A link with `..` in its *target* (`$HOME/lnk -> sub/../a`), for the same
  reason.
- A prefix that the kernel can't resolve. `[ -L ]` is false there, so no link
  past that point is recorded. Such a path doesn't fully resolve, so `loose`
  already refuses the run. The exception is `core.hooksPath`: git normalizes
  it lexically for `paths`, and its `logical` entry keeps today's links plus
  whatever the new step finds.
- A directory name ending in a newline. Command substitution in `_REAL`
  strips it, as it does today. Paths with `\n` are already dropped from
  `paths`.
- Everything the Threat model lists as beyond this guard.

## Design

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. In `_watched`, replace

   ```python
   logical = ["/" + posixpath.normpath(p).lstrip("/")
              for p in dict.fromkeys(logical + paths) if p.startswith("/")]
   links = [q for q in _sh(_LINKS, *logical).stdout.split("\0") if q]
   ```

   with

   ```python
   written = [p for p in dict.fromkeys(logical + paths) if p.startswith("/")]
   logical = ["/" + posixpath.normpath(p).lstrip("/") for p in written]
   links = [q for q in _sh(_LINKS, *logical).stdout.split("\0") if q]
   # normpath drops lnk/.. before _LINKS sees lnk, but the kernel follows lnk
   # first. So a path with `..` is walked again as written: [ -L ] on each
   # written prefix is the kernel's own answer, and _REAL names each link it
   # finds by its physical parent, so lnk/../l3 is recorded as <out>/l3.
   dotted = [p for p in written if ".." in p.split("/")]
   found = [q for q in _sh(_LINKS, *dotted).stdout.split("\0") if q] if dotted else []
   if found:
       links += [r for r in _sh(_REAL, *found).stdout.split("\0") if r.startswith("/")]
   ```

   Why this gives the Bar's set: `_LINKS` on `$HOME//./lnk/../l3/x.inc` tests
   `…/lnk/../l3/x.inc`, `…/lnk/../l3`, `…/lnk/..`, `…/lnk`, `…/.`, `…/`,
   then `$HOME` and its parents. The kernel resolves each one. A prefix ending
   in `..` or `.` is a directory, so it's never printed. `_REAL` maps
   `…/lnk/../l3` to `<physical out>/l3`, and `…/lnk` to
   `<physical $HOME>/lnk`. A printed prefix's parent always resolves, because
   `[ -L ]` succeeded, so filtering on `startswith("/")` drops only the
   trailing empty field. (A scratch check in the spec review gave `out/l3`
   and `h/lnk` for `h//./lnk/../l3/x.inc`.)

   Keep the existing
   `links = [q for q in dict.fromkeys(links) if q not in resolved and q != "/"]`
   at the end of `_watched`. It dedupes and filters the union, and it drops
   the leaf itself when the leaf is a link already in `resolved`. Change
   nothing else in `_watched`, and add no shell constant.

2. `_watched` docstring: after the paragraph on the third value, add this
   paragraph: "The first value is every symlink on the way to a watched path,
   as `_LINKS` finds it on the normalized path and, for a path with `..`, on
   the path as written, named by its physical parent."

Don't touch `_LINKS`, `_REAL`, `_PACK`, `_UNPACK`, `_pack`, `_members`,
`_differs`, `guard_git_dir`, `restore_git_dir`, `_shared`, `GIT_WATCHED`, the
`loose` refusal or any breach text.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section between the `# ── a ".." in a path that doesn't resolve ──`
section and `# ── a git config git can't read ──`:
`# ── a link a later ".." cancels ──`. Use the existing `home` fixture and
`repo`, `git`, `shipped(repo)` (which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`) and `FSMONITOR`. `home` sets
HOME, so `git(repo, "config", "--global", …)` writes the test's own
`~/.gitconfig`. Paths in assertions are `str(...)` of `tmp_path`-based paths,
as in the neighbouring tests; use `.resolve()` where the plan says physical.

1. `test_a_link_before_dotdot_in_the_gitconfig_is_recorded_and_seen` covers
   the reproduction. `out = tmp_path / "out"`,
   `(out / "a").mkdir(parents=True)`, `(out / "x.inc").write_text("")`,
   `(home / "lnk").symlink_to(out / "a")`, then
   `git(repo, "config", "--global", "--add", "include.path",
   f"{home}/lnk/../x.inc")`. Set `run = shipped(repo)` and
   `guard = permissions.guard_git_dir(run)`. Assert
   `str(home / "lnk") in guard["links"]` and
   `str((out / "x.inc").resolve()) in guard["paths"]`.

   Then do what the agent would: `evil = tmp_path / "evil"`,
   `(evil / "a").mkdir(parents=True)`, `(evil / "x.inc").write_text(FSMONITOR)`,
   `(home / "lnk").unlink()`, `(home / "lnk").symlink_to(evil / "a")`. To
   prove the shape is real, check that
   `git(repo, "config", "--show-origin", "--get", "core.fsmonitor")` contains
   `pwned`. Assert `permissions.git_dir_changed(guard)`.
   `restore_git_dir(run, guard)` raises `PermissionBreach` matching
   "NOT all put back", and the message contains `str(home / "lnk")`.
   Afterwards, `os.readlink(home / "lnk") == str(evil / "a")` (a link is never
   written) and `(out / "x.inc").read_text() == ""`.

2. `test_a_link_before_dotdot_is_recorded_for_every_key`: parametrize over
   `"include.path"`, `"includeIf.onbranch:main.path"`, `"core.excludesFile"`
   and `"core.attributesFile"`. Use the same `out` setup, but put the link in
   the repo (`(repo / "lnk").symlink_to(out / "a")`) and set the value
   `f"{repo}/lnk/../x.inc"` in the repo config with
   `git(repo, "config", key, value)`. Guard, then assert
   `str(repo / "lnk") in guard["links"]`. Retarget `repo / "lnk"` to
   `evil / "a"`, with `evil / "x.inc"` written. `restore_git_dir` raises
   "NOT all put back".

3. `test_a_link_after_dotdot_is_recorded_where_the_kernel_goes`: parametrize
   over which link is retargeted, `"lnk"` or `"l3"`. Make the `out/a` and
   `out/c` dirs and an empty `out/c/x.inc`, then
   `(home / "lnk").symlink_to(out / "a")`,
   `(out / "l3").symlink_to(out / "c")`, and the global include
   `f"{home}/lnk/../l3/x.inc"`. Guard, then assert that `str(home / "lnk")`
   and `str(out.resolve() / "l3")` are both in `guard["links"]`, and that
   `str((out / "c" / "x.inc").resolve()) in guard["paths"]`.

   Retarget the parametrized link to an `evil` dir laid out so git then loads
   an `x.inc` holding `FSMONITOR`:
   - for `"lnk"`: `evil/a`, `evil/l3 -> evil/c` and `evil/c/x.inc`, with
     `home/lnk -> evil/a`;
   - for `"l3"`: `evil/c/x.inc`, with `out/l3 -> evil/c`.

   Check `pwned` with `git config --show-origin --get core.fsmonitor`.
   `restore_git_dir` raises "NOT all put back".

4. `test_a_dotdot_path_keeps_the_baseline_links_and_adds_the_kernels`. Set up
   `home/lnk -> out/a`, `out/l3 -> out/c` with an empty `out/c/x.inc`, and
   `(home / "l3").symlink_to(out / "y")` (a link that only today's lexical
   walk of `{home}/l3/x.inc` meets; `out/y` need not exist). Add the global
   include written as `f"{home}//./lnk/../l3/x.inc"`. Guard, then assert that
   `str(home / "lnk")`, `str(out.resolve() / "l3")` and `str(home / "l3")`
   are all in `guard["links"]`, and that
   `restore_git_dir(run, guard) == []`.

5. `test_a_path_without_dotdot_makes_no_extra_shell_call`. Wrap
   `permissions._sh` with a counting spy, as
   `test_a_readable_repo_runs_no_extra_command` does, recording each
   `script`. Set up `(home / "lnk").symlink_to(out / "a")`, an empty
   `out/a/x.inc` and the global include `f"{home}/lnk/x.inc"`, then guard.
   Assert `scripts.count(permissions._LINKS) == 1`. `_REAL` is called the same
   number of times as in the baseline, so assert
   `scripts.count(permissions._REAL)` equals that count, taken from the same
   spy run with the change's `dotted` branch unused. Simplest: assert it
   equals 3 (the `paths` call, the `{root}/.` tree call, and the `parents`
   call in `guard_git_dir`), and confirm 3 against `4293881` before writing
   the assertion. Also assert `str(home / "lnk") in guard["links"]` and
   `restore_git_dir(run, guard) == []`. Then, in the same test or a
   parametrized twin with the include `f"{home}/lnk/../x.inc"` and
   `out/x.inc`, assert `scripts.count(permissions._LINKS) == 2`, so the spy
   is shown to see the new call.

6. Module docstring: after the line "A watched path with `..` that doesn't
   fully resolve stops the run first.", add "A symlink a later `..` cancels on
   a watched path is recorded like any other link on the way."

Every existing test must pass unchanged. For example,
`test_an_include_through_a_symlinked_parent_is_watched`
(`.git/lnk/../evil.inc`) now also records `.git/lnk`. It still raises naming
`evil.inc` and puts `evil.inc` back, because that link doesn't move.

### `.claude/skills/sssf/references/config.md`

The paragraph that begins "After the agent, and before any gate runs git"
has the sentence "SSSF also notices when a symlink on the way to one of
these paths, such as a symlinked `~/.config`, `HOME` or `.git`, now points
somewhere else." Add this sentence after it: "That includes a symlink a later
`..` in the path cancels, such as `lnk` in an include path `~/lnk/../x.inc`:
git follows `lnk` before it takes the parent."

## Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: the sentence above.
- The `_watched` docstring (Design step 2) and the new comment in `_watched`.
- The module docstring of `.claude/skills/sssf/tests/test_git_dir_guard.py`
  (test step 6).
- No README usage, help text or CHANGELOG covers this.
- For the documenter: `specs/2d444669_refuse-unresolved-dotdot.md` says under
  Out of scope that "a `..` path that resolves today, where the agent later
  swaps a symlink on the way" is covered by `_LINKS`/`parents`. That wasn't
  true for a link the `..` cancels. That spec is a record and stays as it is.
  No `app_docs/` write-up of the git guard needs flagging.
- `specs/6d75d21b_record-link-before-dotdot.md` is the first version of this
  plan (with `_WALK`). It stays as the record.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, including the new ones:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite (the hook tests skip without gitleaks):
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 4293881 -- .claude/skills/sssf/templates/` lists only
   `adws/adw_modules/permissions.py`. Read the diff to check that it changes
   only the `written`/`logical`/`links`/`dotted`/`found` lines with their
   comment and the docstring paragraph, and that no `_… = """` shell
   constant is added or changed.
4. The reproduction by hand, outside pytest, in a scratch repo with a scratch
   `HOME`, as in test 1. Before the change, `restore_git_dir` returns `[]`.
   After it, it raises "NOT all put back" naming `$HOME/lnk`. Test 1
   automates this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`. It needs no new tool or dependency.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `4293881`. Until then, the hub's own runs keep the `4293881` behaviour,
  and this hole stays open there.

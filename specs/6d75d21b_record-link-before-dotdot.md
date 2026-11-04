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

Fix: for each watched path with `..`, also collect links by walking it the way
the kernel does, component by component as written, following each symlink
before a later `..`. A path with no `..`, and the paths the guard watches,
keep exactly today's behaviour.

## Baseline

`main` at `4293881` ("Stamp the dotdot and unreadable-config guards into the
hub"). Every finding is judged against that commit. At `4293881`, in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`:

- `_watched` builds `logical` = `{root}/.git`, the absolute `core.hooksPath`
  value, and every entry of `paths`, each passed through
  `"/" + posixpath.normpath(p).lstrip("/")`, then
  `links = [q for q in _sh(_LINKS, *logical).stdout.split("\0") if q]`.
- `_LINKS` prints every lexical prefix of each path (the path itself and each
  parent, by stripping `/…` from the end) that `[ -L ]` says is a symlink.
- `_REAL` resolves each path's parent with `cd -P`, so `paths` (later
  `resolved`) holds the physical `out/x.inc` for `$HOME/lnk/../x.inc`. A path
  with `..` that doesn't fully resolve is refused (`loose`) before any of the
  link logic matters.
- At the end, `links` drops `/` and anything in `resolved`, and is deduped.
- `guard_git_dir` plain-packs `links + paths`, stores `parents =
  _sh(_REAL, *links)`. `_differs` reports a link whose plain-pack member
  (its own target text) or `_REAL` parent changed. `restore_git_dir` never
  writes a link, so a moved link gives "NOT all put back".

So for `$HOME/lnk/../x.inc`, `normpath` gives `$HOME/x.inc`, `_LINKS` never
tests `$HOME/lnk`, and a retarget of it goes unseen. The same holds for a
link that sits *after* a `..` in the physical directory the kernel reaches
(`$HOME/lnk/../l3/x.inc`, `out/l3 -> out/c`): `_LINKS` tests `$HOME/l3`,
which is not where the kernel goes.

## Threat model

The agent runs as the engineer's own user and has bash. It can delete and
create symlinks, directories and files anywhere that user can write,
including `$HOME` and the work tree. It can't change the packs or the saved
`links`/`parents`, which live in SSSF's process memory, and it can't change
the engineer's config from before the run.

The trigger is a setup the engineer made: a watched path (an `include.path`,
`includeIf.*.path`, `core.excludesFile`, `core.attributesFile` value, or the
`.git`/`core.hooksPath` logical paths) written with a symlink followed later
by `..`. The agent only retargets that symlink, then writes the file the
kernel now reaches.

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

- record in `guard["links"]` every symlink the kernel tests while walking a
  watched path with a `..` component, as written: each component that is not
  `..` is appended to the current prefix and recorded if `[ -L ]`; each `..`
  replaces the prefix with the physical directory `cd -P "<prefix>/.."` gives.
  For the reproduction that records `$HOME/lnk`; for `$HOME/lnk/../l3/x.inc`
  it records `$HOME/lnk` and `<physical out>/l3`.
- so a retarget of such a link, as in the reproduction, makes
  `git_dir_changed(guard)` true and `restore_git_dir` raise
  `PermissionBreach` with "NOT all put back" naming the link, for
  `include.path`, `includeIf.*.path`, `core.excludesFile` and
  `core.attributesFile`, in the repo config and in `~/.gitconfig`.
- keep every link the baseline records: the new links are added to today's
  `_LINKS` output, never replace it.
- leave every other behaviour as at `4293881`:
  - a path with no `..` component gets exactly today's links, and the new
    walk is never run when no watched or logical path has a `..` (no extra
    shell call: `test_a_readable_repo_runs_no_extra_command` and the new
    spy test hold);
  - `guard["paths"]`, `guard["kinds"]`, the `loose` refusal and its message,
    the packs, the restore, `_REAL`, `_LINKS`, `_PACK`, `_UNPACK` and every
    breach text are unchanged;
  - the new walk writes, creates, chmods or deletes nothing; it only runs
    `[ -L ]` and `cd -P … && pwd -P` in a subshell.

Where the baseline doesn't, the change must never keep an agent file, run
agent code, or delete or overwrite an engineer file. A link is never written
back (as today): the breach says "NOT all put back" and the engineer checks
it by hand.

Cost the request accepts: an engineer who changes such a link themselves
during an agent call now stops the run, as they would for any link on the way
today.

## Out of scope

Old holes this change leaves open. A reviewer should file them as old:

- **Links inside a symlink's target.** `$HOME/lnk -> m/a` with `$HOME/m ->
  out`: the kernel passes through `$HOME/m` while following `lnk`, and a
  retarget of `$HOME/m` moves the file git reads. Neither today's walk nor the
  new one reads link targets, so `$HOME/m` is not recorded. This hole exists
  for paths with no `..` too (`$HOME/d/x.inc`, `$HOME/d -> m/a`), where the
  request fixes today's behaviour, so it isn't closed for `..` paths alone.
- A link with `..` in its *target* (`$HOME/lnk -> sub/../a`): same reason.
- A walk cut short: where `cd -P "<prefix>/.."` fails, the walk stops for that
  path. Such a path doesn't fully resolve, so `loose` already refuses the run
  (`core.hooksPath` aside: git normalizes it lexically for `paths`; its
  `logical` entry keeps today's links plus whatever the walk got).
- A directory name ending in a newline: `$(…)` strips it, as in `_REAL`.
  Paths with `\n` are already dropped from `paths`.
- Everything the Threat model lists as beyond this guard.

## Design

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. Add a constant `_WALK` right after `_LINKS`, with this comment and body
   (keep `\\0` doubled inside the Python string, as `_LINKS` does):

   ```python
   # Prints, NUL-separated, every symlink the kernel tests on the way along each
   # path, walking it as written: a `..` takes the physical parent of where the
   # walk is, so a link it cancels lexically (lnk in lnk/..) is still printed.
   # Only paths with `..` need it; _LINKS covers the rest. A `..` that can't be
   # resolved ends that path's walk: _watched refuses such a path anyway.
   _WALK = """for p in "$@"; do
     q=; rest=${p#/}
     while [ -n "$rest" ]; do
       c=${rest%%/*}
       case $rest in */*) rest=${rest#*/} ;; *) rest= ;; esac
       case $c in
         ''|.) ;;
         ..) q=$(cd -P "$q/.." 2>/dev/null && pwd -P) || break; q=${q%/} ;;
         *) q=$q/$c; if [ -L "$q" ]; then printf "%s\\0" "$q"; fi ;;
       esac
     done
   done
   """
   ```

   `q` empty means `/`; `"$q/.."` is then `/..`, and `pwd -P`'s `/` becomes
   empty again via `${q%/}`. Every printed name is absolute, has no `..`, and
   (after the first `..`) no symlinked parent, so tar and `_REAL` handle it
   like any other recorded link. (Checked by hand: for
   `$d/h/lnk/../x.inc` it prints `$d/h/lnk`; for `$d/h/lnk/../l3/x.inc` it
   prints `$d/h/lnk` and `$d/out/l3`.)

2. In `_watched`, replace

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
   # first, so a path with `..` is walked again as written.
   dotted = [p for p in written if ".." in p.split("/")]
   if dotted:
       links += [q for q in _sh(_WALK, *dotted).stdout.split("\0") if q]
   ```

   The existing `links = [q for q in dict.fromkeys(links) if q not in resolved
   and q != "/"]` at the end of `_watched` dedupes and filters the union; keep
   it. Change nothing else in `_watched`.

3. `_watched` docstring: after the paragraph on the third value, add one
   sentence-paragraph: "The first value is every symlink on the way to a
   watched path, as `_LINKS` finds it on the normalized path and, for a path
   with `..`, as `_WALK` finds it walking the path as written."

Don't touch `_LINKS`, `_REAL`, `_PACK`, `_UNPACK`, `_pack`, `_members`,
`_differs`, `guard_git_dir`, `restore_git_dir`, `_shared`, `GIT_WATCHED`, the
`loose` refusal or any breach text.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section after the `# ── a ".." in a path that doesn't resolve ──`
section (before `# ── a git config git can't read ──`):
`# ── a link a later ".." cancels ──`. Use the existing `home` fixture,
`repo`, `git`, `shipped(repo)` (which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`), `FSMONITOR`. Paths in
assertions use `str(...)` of `tmp_path`-based paths, like neighbouring tests.

1. `test_a_link_before_dotdot_in_the_gitconfig_is_recorded_and_seen` — the
   reproduction. `out = tmp_path / "out"`, `(out / "a").mkdir(parents=True)`,
   `(out / "x.inc").write_text("")`, `(home / "lnk").symlink_to(out / "a")`;
   `git(repo, "config", "--global", "--add", "include.path",
   f"{home}/lnk/../x.inc")`. `run = shipped(repo)`,
   `guard = permissions.guard_git_dir(run)`. Assert
   `str(home / "lnk") in guard["links"]` and
   `str((out / "x.inc").resolve()) in guard["paths"]`. Then, as the agent:
   `evil = tmp_path / "evil"`, `(evil / "a").mkdir(parents=True)`,
   `(evil / "x.inc").write_text(FSMONITOR)`, `(home / "lnk").unlink()`,
   `(home / "lnk").symlink_to(evil / "a")`. Prove the shape is real:
   `git(repo, "config", "--show-origin", "--get", "core.fsmonitor")` contains
   `pwned`. Assert `permissions.git_dir_changed(guard)`. `restore_git_dir(run,
   guard)` raises `PermissionBreach` matching "NOT all put back", and the
   message contains `str(home / "lnk")`. Afterwards
   `os.readlink(home / "lnk") == str(evil / "a")` (a link is never written)
   and `(out / "x.inc").read_text() == ""`.

2. `test_a_link_before_dotdot_is_recorded_for_every_key`, parametrized over
   `"include.path"`, `"includeIf.onbranch:main.path"`, `"core.excludesFile"`,
   `"core.attributesFile"`: same `out`/`lnk` setup but the link in the repo
   (`(repo / "lnk").symlink_to(out / "a")`) and the value
   `f"{repo}/lnk/../x.inc"` set in the repo config with
   `git(repo, "config", key, value)`. Guard; assert `str(repo / "lnk") in
   guard["links"]`; retarget `repo / "lnk"` to `evil / "a"` (with
   `evil / "x.inc"` written); `restore_git_dir` raises "NOT all put back".

3. `test_a_link_after_dotdot_is_recorded_where_the_kernel_goes`, parametrized
   over which link is retargeted, `"lnk"` or `"l3"`: `out/a`, `out/c` dirs,
   `out/c/x.inc` empty, `(home / "lnk").symlink_to(out / "a")`,
   `(out / "l3").symlink_to(out / "c")`, global include
   `f"{home}/lnk/../l3/x.inc"`. Guard; assert both `str(home / "lnk")` and
   `str(out.resolve() / "l3")` are in `guard["links"]`, and
   `str((out / "c" / "x.inc").resolve()) in guard["paths"]`. Retarget the
   parametrized link to an `evil` dir laid out so git would then load an
   `x.inc` with `FSMONITOR` (for `"lnk"`: `evil/a`, `evil/l3 -> evil/c`,
   `evil/c/x.inc`; for `"l3"`: `out/l3 -> evil/c`, `evil/c/x.inc`); check
   `pwned` via `git config --show-origin --get core.fsmonitor`;
   `restore_git_dir` raises "NOT all put back".

4. `test_the_walk_prints_links_as_the_kernel_meets_them`: call the script
   directly. With `home/lnk -> out/a`, `out/l3 -> out/c`, and `(home /
   "x.inc").symlink_to(out / "y")` (a link only the lexical walk meets):
   - `permissions._sh(permissions._WALK, f"{home}/lnk/../x.inc").stdout ==
     f"{home}/lnk\0"`;
   - `f"{home}//./lnk/../l3/x.inc"` gives `f"{home}/lnk\0{out.resolve()}/l3\0"`;
   - `f"{home}/missing/../x.inc"` gives `""` (ends at the unresolvable `..`).
   Then guard with a global include `f"{home}/lnk/../x.inc"` (and `out/x.inc`
   created) and assert `str(home / "x.inc") in guard["links"]` and
   `str(home / "lnk") in guard["links"]`: the baseline's link is kept.

5. `test_a_path_without_dotdot_never_runs_the_walk`: wrap `permissions._sh`
   with a counting spy as `test_a_readable_repo_runs_no_extra_command` does.
   With `(home / "lnk").symlink_to(out / "a")`, `out/a/x.inc` empty and a
   global include `f"{home}/lnk/x.inc"`, guard; assert
   `permissions._WALK not in scripts` and `str(home / "lnk") in
   guard["links"]`, and `restore_git_dir(run, guard) == []`.

6. Module docstring: after the line "A watched path with `..` that doesn't
   fully resolve stops the run first." add "A symlink a later `..` cancels on
   a watched path is recorded like any other link on the way."

Every existing test must pass unchanged. In particular
`test_an_include_through_a_symlinked_parent_is_watched` (`.git/lnk/../evil.inc`)
now also records `.git/lnk`; it still raises naming `evil.inc` and puts
`evil.inc` back, since that link doesn't move.

### `.claude/skills/sssf/references/config.md`

In the paragraph that begins "After the agent, and before any gate runs
git", after the sentence "SSSF also notices when a symlink on the way to one
of these paths, such as a symlinked `~/.config`, `HOME` or `.git`, now points
somewhere else.", add: "That includes a symlink a later `..` in the path
cancels, such as `lnk` in an include path `~/lnk/../x.inc`: git follows `lnk`
before it takes the parent."

## Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: the sentence above.
- The `_watched` docstring (Design step 3) and the new `_WALK` comment.
- The module docstring of `.claude/skills/sssf/tests/test_git_dir_guard.py`
  (test step 6).
- No README usage, help text or CHANGELOG covers this.
- For the documenter: `specs/2d444669_refuse-unresolved-dotdot.md` says under
  Out of scope that "a `..` path that resolves today, where the agent later
  swaps a symlink on the way" is covered by `_LINKS`/`parents`; it wasn't for
  a link the `..` cancels. That spec is a record and stays as it is. There is
  no `app_docs/` write-up of the git guard to flag.

## Verification

Run each from the repo root. Each must exit 0.

1. The guard tests, including the new ones:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite (the hook tests skip without gitleaks):
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 4293881 -- .claude/skills/sssf/templates/` lists only
   `adws/adw_modules/permissions.py`, and that diff adds `_WALK` with its
   comment, the `written`/`dotted` lines in `_watched`, and the docstring
   sentence, and removes only the two replaced lines. Judge by reading it.
4. The reproduction by hand, outside pytest, in a scratch repo with a scratch
   `HOME`, as in test 1: before the change `restore_git_dir` returns `[]`;
   after it, it raises "NOT all put back" naming `$HOME/lnk`. Test 1
   automates this.

The repo's `test` quality block runs the skill suite, so this plan adds no
extra `checks`. It needs no new tool or dependency.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `4293881`. Until then, the hub's own runs keep the `4293881` behaviour,
  with this hole open.

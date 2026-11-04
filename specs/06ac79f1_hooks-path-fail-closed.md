# Stop the run when git can't name the hooks directory

## Summary

`_watched` in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
(line ~391) asks git where the hooks directory is:

```python
hooks = _git(["rev-parse", "--path-format=absolute", "--git-path", "hooks"], root).strip()
if hooks.startswith("/"):
    paths.append(hooks)
```

`_git` returns `""` on any non-zero exit, so a failure here silently drops the
hooks directory from the watched paths. Reproduced on git 2.43.0: with
`core.hooksPath = <base>/A/B/hooks`, where `A` and `B` are relative symlinks
whose physical path is longer than PATH_MAX, the first `rev-parse`
(`--git-common-dir --git-dir`) and `git config --list --show-origin` both exit
0, but `rev-parse --git-path hooks` exits 128 with
`fatal: Invalid path '<the 4000+ character physical path>'`. The
`core.hooksPath` value only goes into `logical` (the prefix walk), never into
`paths`, so nothing under it is packed, and an agent's executable
`hooks/pre-commit` there survives the restore and runs on the engineer's next
commit.

The fix: run that `rev-parse` directly (not through `_git`), and when it exits
non-zero raise `PermissionBreach` with git's error, before the agent runs, the
same way `_unreadable` handles a config git can't read. A successful call keeps
exactly today's code path.

## Baseline

`main` at `e479d34` ("Stamp the prefix-gone and pwd-fail guards into the hub").
Every finding is judged against it. On the baseline, a failing
`rev-parse --git-path hooks` leaves the hooks directory unwatched and the agent
runs.

## Threat model

The agent runs as the engineer's own user, with bash and write. It can write
anywhere that user can, including the directory `core.hooksPath` names, and it
can create symlinks and deep directory trees. It can also change git config,
but a change there is already caught by the config packs after the agent.

What this change covers: a `core.hooksPath` that is in place *before* the
agent runs (set by the engineer, an earlier tool, or anything else) and that git
can't turn into an absolute path. That's the only state in which this guard
sees the failure; it checks before each agent.

What this guard can't stop at its level, because the agent is the engineer's
own user: killing the SSSF process before the restore runs; leaving a
background process that writes a hook after the check; changing where the
symlinks on the path point between the guard's checks in ways the existing
prefix walk already reports but can't undo. `--sandbox` / `--container`
isolate; this guard catches mistakes and casual attempts.

## Bar

Against the baseline, the change must:

- Stop the run before the agent, with a `PermissionBreach`, whenever
  `git rev-parse --path-format=absolute --git-path hooks` exits non-zero in a
  repo `_watched` has already found (that is, after the first `rev-parse` gave
  git directories). No agent is sent a prompt in that state, so no agent file
  is kept and no agent code runs.
- Say in the breach that git couldn't name the hooks directory, give git's
  error (each line made printable and cut at 300 characters, at most 20 lines,
  as `_unreadable` already does; the reproduced line is over 4000 characters),
  and say to point `core.hooksPath` at a shorter path, then run again. It must
  say the agent did not run.
- Write nothing: no file under the git directory, the `core.hooksPath` target
  or `HOME` is created, deleted or overwritten by the new branch.

It must never:

- Change anything when that `rev-parse` call exits 0: the same paths are
  watched, in the same order, with the same `startswith("/")` filter, and no
  extra command runs (`test_a_readable_repo_runs_no_extra_command` and every
  existing guard test still pass unchanged).
- Change the not-a-repo path: `_watched` still returns `([], [], {})` before
  reaching this call when the first `rev-parse` says not-a-repo.
- Delete or overwrite an engineer file, or keep an agent file, where the
  baseline does otherwise.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- A `core.hooksPath` that git *can* name but that the agent makes un-nameable
  (or retargets) during its own call. The config packs and the prefix walk
  (`_moved`) cover the config and the links; this change only checks before
  the agent.
- Other `_git(...)` calls in `_watched` that fall back to `""` on failure:
  `git config core.hooksPath` (only feeds the prefix walk) and
  `git config --list --show-origin -z` (its exit code isn't checked; a failure
  there is caught by the first `rev-parse` in the cases found so far). Also
  `snapshot`/`head`, which use `_git` too.
- Hooks git would run from anywhere other than `--git-path hooks`
  (for example `hook.<name>.command` config in git ≥ 2.36 pointing at a script
  in the work tree or elsewhere). The config packs catch a *new* such key, not
  a script it already names.
- A submodule's git directory, the index's `--skip-worktree` flag, a
  hard-linked `~/.gitconfig`, and a config that turns unreadable partway
  through the read (already listed in `.claude/skills/sssf/references/config.md`).

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

a. Factor the git-error formatting out of `_unreadable` so both breaches share
   it, with `_unreadable`'s output byte-for-byte unchanged:

```python
def _said(err: str, code: int) -> str:
    """git's error as a breach quotes it: at most 20 lines of 300 characters,
    non-printables as ?."""
    lines = [l for l in err.strip().splitlines() if l.strip()][:20]
    return "\n".join("  " + "".join(c if c.isprintable() else "?" for c in l)[:300]
                     for l in lines) or f"  (git printed no error; exit code {code})"


def _unreadable(err: str, code: int) -> str:
    """The breach for a repo whose git config git can't read."""
    return ("git couldn't read the repo's git config, so SSSF can't tell which git "
            "files to watch. The agent did not run, and no run will until git can "
            "read it. git said:\n" + _said(err, code) + "\n\n"
            "Fix the config file or include path git names above, for example a "
            "symlink loop or a directory git can't enter, then run again.")


def _no_hooks_dir(err: str, code: int) -> str:
    """The breach for a repo where git can't name its hooks directory."""
    return ("git couldn't name the hooks directory, so SSSF can't watch the hooks "
            "git will run. The agent did not run, and no run will until git can "
            "name it. git said:\n" + _said(err, code) + "\n\n"
            "Point core.hooksPath at a shorter path, one whose real path, with its "
            "symlinks resolved, is under the system's path length limit, then run again.")
```

   Place `_said` and `_no_hooks_dir` next to `_unreadable` (around line 607).

b. In `_watched`, replace the two lines at ~391–393 with a direct call that
   keeps stderr and the exit code:

```python
    named_hooks = sandbox.current().run(
        _gitargv(["rev-parse", "--path-format=absolute", "--git-path", "hooks"]),
        cwd=root, text=False)
    if named_hooks.returncode != 0:
        err = named_hooks.stderr
        err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
        raise PermissionBreach(_no_hooks_dir(err, named_hooks.returncode))
    hooks = named_hooks.stdout
    hooks = (hooks.decode("utf-8", "surrogateescape") if isinstance(hooks, bytes)
             else (hooks or "")).strip()
    if hooks.startswith("/"):
        paths.append(hooks)
```

   The argv is exactly what `_git` ran before (`_gitargv(args)`, no `env
   LC_ALL=C`: the code only checks the exit status, never git's words), so the
   success path runs the same command and gets the same string. Don't
   reorder anything else in `_watched`.

c. Extend the `_watched` docstring's last paragraph with one sentence:
   "A `rev-parse --git-path hooks` that fails, for example because
   `core.hooksPath` resolves to a path longer than the system limit, raises
   `PermissionBreach` with git's error, since no hooks would be watched."

d. Extend the `guard_git_dir` docstring: "...a repo whose config git can't
   read, or whose hooks directory git can't name, raises PermissionBreach."

### 2. `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section after the `# ── a directory pwd -P can't name ──` tests (the
`unnameable` fixture and `Unnameable` class are defined there; reuse them, do
not call `u.lock()` — git fails on the length alone). All tests use
`shipped(repo)`, which loads the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`.

Add `A git hooks directory git can't name stops the run first, with git's error.`
to the module docstring's list.

```python
# ── a hooks directory git can't name ──


def _hooks_unnameable(repo: Path) -> None:
    done = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-path", "hooks"],
                          cwd=repo, capture_output=True)
    if done.returncode == 0:
        pytest.skip("git can still name this hooks path here")
```

Tests to add:

1. `test_a_hooks_path_git_cannot_name_stops_the_run_first(repo, home, unnameable)`
   - `hooks = f"{unnameable.base}/A/B/hooks"`; `git(repo, "config", "core.hooksPath", hooks)`;
     `_hooks_unnameable(repo)`.
   - `before = state(repo)`, `saved = (home / ".gitconfig").read_text()`.
   - `pytest.raises(permissions.PermissionBreach)` around
     `permissions.guard_git_dir(shipped(repo))`.
   - The message contains each of: `"couldn't name the hooks directory"`,
     `"Invalid path"`, `"core.hooksPath"`, `"shorter path"`, `"did not run"`,
     `"run again"`.
   - Every line of the message is at most 302 characters (`len(l) <= 302`).
   - `state(repo) == before`, `.gitconfig` unchanged, and
     `not os.path.lexists(f"{unnameable.physical.rsplit('/', 1)[0]}/hooks")`
     (nothing was made at the long path).
2. `test_a_hooks_path_git_can_name_again_is_watched(repo, unnameable)`
   - Same setup and skip, assert the breach, then
     `git(repo, "config", "core.hooksPath", f"{unnameable.base}/A")`;
     `guard = permissions.guard_git_dir(shipped(repo))`; assert the resolved
     `os.path.realpath(f"{unnameable.base}/A")` is in `guard["paths"]` and
     `permissions.restore_git_dir(SimpleNamespace(repo_root=repo), guard) == []`.
3. `test_a_failed_hooks_rev_parse_stops_the_run(repo, monkeypatch, err)`,
   parametrized over `err` in `[b"fatal: boom\n", b""]`. Works as root and on
   any filesystem. Spy on `permissions.sandbox.current().run` (the pattern in
   `test_outside_a_repo_in_another_locale_is_still_nothing_to_guard`): when the
   command contains both `"--git-path"` and `"hooks"`, return
   `subprocess.CompletedProcess(command, 128, b"", err)`; otherwise call the
   original. Assert `PermissionBreach` whose text contains
   `"couldn't name the hooks directory"` and either `"fatal: boom"` or
   `"(git printed no error; exit code 128)"`. Assert `state(repo)` unchanged.
4. `test_unreadable_keeps_its_message()`: assert
   `permissions._unreadable("fatal: x\n", 128)` equals the exact string the
   baseline `_unreadable` produced (copy the baseline text literally into the
   test) — guards the refactor.

The existing `test_a_readable_repo_runs_no_extra_command`,
`test_an_existing_hooks_path_directory_is_watched`,
`test_a_hooks_path_at_the_root_refuses_the_run` and
`test_a_retargeted_hooks_path_link_is_seen` cover "success keeps today's
behaviour"; they must pass unchanged.

### 3. `.claude/skills/sssf/references/config.md`

In the paragraph beginning "If git can't read the repo's git config at all"
(~line 250), add after it a short paragraph:

"If git can't name the hooks directory, for example because `core.hooksPath`
leads through symlinks to a real path longer than the system limit, SSSF can't
watch the hooks git will run. So the run stops before the next agent with a
breach that gives git's error. Point `core.hooksPath` at a shorter path, then
run again. Before, SSSF ran the agent without watching any hooks directory."

No README, CHANGELOG (the repo keeps none) or CLI help text mentions this
behaviour. `app_docs/` is not touched; no earlier write-up there describes the
hooks lookup's failure path, so nothing for the documenter to flag beyond
noting this change.

## Verification

From `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q
```

Both exit 0. The new tests 1 and 2 skip under root (the `unnameable`
fixture) or where git can name the path; test 3 always runs. Check by hand
that `git diff e479d34 -- .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
touches only `_watched`'s hooks lookup, its docstring, `guard_git_dir`'s
docstring, and the `_said` / `_unreadable` / `_no_hooks_dir` helpers.

## For the engineer

The hub copy is protected, so the builder must not touch it. After the change
passes review, stamp it into the hub as in `e479d34`:

- copy `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` to
  `adws/adw_modules/permissions.py`;
- update `adws/adw_sssf_config/stamp.json`.

# Stop the run when the git guard can't read the repo's git config

## Summary

`_watched` in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
finds the repo's git directories with
`git rev-parse --path-format=absolute --git-common-dir --git-dir`, through
`_git`, which returns `""` on any failure. An empty result makes `_watched`
return `([], [], {})` and `guard_git_dir` return `None`, which is the result
meant for "outside a git repo". So when git can't read the config (an include
path that hits ELOOP or EACCES), the agent runs with no git-directory guard.

The change: run that one `rev-parse` so its exit code and stderr are kept.
On failure, return the empty result only when git's error is its
not-a-repo error **and** the repo root has no `.git` entry. Every other
failure raises `PermissionBreach` before the agent runs. The breach says git
couldn't read the repo's git config, gives git's error, and says to fix the
config before running again.

## Baseline

`guard-include-dotdot` at `3d8341f` ("Refuse every watched path with a .. that
doesn't resolve"). The current branch `guard-unreadable-config` sits on the same
commit. Judge every finding against it.

## Threat model

The agent runs as the engineer's own user, with bash. It can read and write
anything the engineer can, including `.git/`, `~/.gitconfig`, every included
config file, and the permissions of every directory on the way to one. The
guard packs the watched paths before the agent and compares and restores them
after it. That only works if `_watched` knows the paths, and `_watched` gets
them from git.

The engineer's own setup can leave git unable to read the config before the
run starts. Both of these are present on `main`:

- `include.path = <repo>/loop/x.inc`, where `<repo>/loop` is a symlink to
  itself. Every git command dies with `fatal: unable to access
  '<repo>/loop/x.inc': Too many levels of symbolic links` (exit 128). At the
  baseline the guard returns `None`, and the agent plants
  `.git/hooks/pre-commit`, removes the loop, and keeps the hook.
- `include.path = <repo>/lnk/../x.inc`, where `lnk` points at a directory
  without search permission. git dies with `Permission denied`. At the
  baseline the agent makes the directory searchable, writes
  `[core] fsmonitor = …` into `x.inc`, and keeps it.

What the guard can't stop at its level: an agent that kills the chain before
the check, leaves a process behind that writes later, or deletes `.git` itself
so the next guard sees a root that isn't a repo (see Out of scope). The guard
catches mistakes and casual attempts. `--sandbox` / `--container` isolate.

## Bar

Against the baseline, the change must:

- Stop the run with `PermissionBreach` from `guard_git_dir`, before the agent
  is sent anything, whenever the guard's `rev-parse` fails and either the repo
  root has a `.git` entry (directory, file, or symlink, dangling or not) or
  git's error isn't its not-a-repo error. This covers both shapes in the
  threat model. No agent file kept, no agent code run.
- Put in the breach: that git couldn't read the repo's git config, git's own
  error text, that the agent did not run, and to fix the config and run again.
- Keep today's behaviour (`guard_git_dir` returns `None`) for a repo root
  with no `.git` entry where git says `fatal: not a git repository`.
- Keep today's behaviour for every repo git can read. Same watched paths, same
  messages, and no extra git or shell call on that path. The classifying call
  is the existing `rev-parse`.

It must never:

- Delete, overwrite, chmod or create any engineer file or directory. The
  refusal reads only, like the `..` refusal does.
- Run the agent, or any git command that could run a hook or fsmonitor. Keep
  `_gitargv` (`-c core.fsmonitor=`) for the probe.
- Depend on the operator's locale to classify the error. Run the probe with
  `LC_ALL=C` so git's not-a-repo message is the English one. The breach shows
  that same C-locale text.

Decision, stated so the reviewer doesn't file it as a finding: a root with no
`.git` whose `rev-parse` fails for another reason, such as a broken
`~/.gitconfig` outside any repo, now stops with this breach. git couldn't tell
us it isn't a repo, so the guard fails closed. SSSF can't run a chain outside a
git repo anyway, because `snapshot` and the commit phase need one.

## Out of scope

Old holes this change leaves open, to be filed as old:

- An agent that deletes or renames `.git` (or replaces it with something git
  calls not-a-repo) so that a *later* guard returns `None`. The current
  agent's guard still sees `.git` gone as a change to watched paths and stops
  the run. Only a root that starts a call with no `.git` and git's not-a-repo
  error goes unguarded, as at the baseline.
- Other git calls in `_watched` that still go through `_git` and fall back
  silently: `git config --list --show-origin` (its stdout is used even on
  failure), `rev-parse --git-path hooks`, and `git config core.hooksPath`.
  Once the first `rev-parse` has read the same config they almost always
  succeed. A race where the config turns unreadable between calls is not
  handled.
- `snapshot` and `head` also use `_git` and return empty on failure. This
  change doesn't touch them. In the failing case the new breach fires before
  the agent anyway.
- The `GIT_DIR` / `GIT_WORK_TREE` environment the operator may have set. It
  is treated as today.
- Every limit already listed in `.claude/skills/sssf/references/config.md`
  (process left behind, killing the chain, submodule git dirs,
  `--skip-worktree`, hard-linked `~/.gitconfig`, and so on).

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

1. Add a module-level constant beside `GIT_WATCHED` / `PACK_CAP`:

   ```python
   # git's own words when a directory isn't in any repo, in the C locale.
   NOT_A_REPO = "fatal: not a git repository"
   ```

   This is a prefix match. It covers the plain message and the
   `GIT_DISCOVERY_ACROSS_FILESYSTEM` variant
   (`fatal: not a git repository (or any parent up to mount point /)`).

2. In `_watched`, replace

   ```python
   out = _git(["rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"], root)
   dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
   if not dirs:
       return [], [], {}
   ```

   with a direct run that keeps the exit code and stderr:

   ```python
   found = sandbox.current().run(
       ["env", "LC_ALL=C", *_gitargv(["rev-parse", "--path-format=absolute",
                                      "--git-common-dir", "--git-dir"])],
       cwd=root, text=False)
   out, err = found.stdout, found.stderr
   out = out.decode("utf-8", "surrogateescape") if isinstance(out, bytes) else (out or "")
   err = err.decode("utf-8", "surrogateescape") if isinstance(err, bytes) else (err or "")
   dirs = list(dict.fromkeys(line.strip() for line in out.splitlines() if line.strip()))
   if found.returncode != 0 or not dirs:
       dot_git = _sh('[ -e "$1" ] || [ -L "$1" ]', f"{root}/.git").returncode == 0
       if not dot_git and err.lstrip().startswith(NOT_A_REPO):
           return [], [], {}
       raise PermissionBreach(_unreadable(err, found.returncode))
   ```

   - The `.git` check goes through `_sh` and not `os.path`, so it works under
     every sandbox provider. `-L` counts a dangling or looping `.git` symlink
     as present.
   - `env LC_ALL=C` only changes the language of git's messages. stdout (the
     paths) is the same.

3. Add a helper next to `_shown_key`. Python resolves the name when
   `_watched` runs, so it may sit below it:

   ```python
   def _unreadable(err: str, code: int) -> str:
       """The breach for a repo whose git config git can't read."""
       lines = [l for l in err.strip().splitlines() if l.strip()][:20]
       said = "\n".join("  " + "".join(c if c.isprintable() else "?" for c in l)[:300]
                        for l in lines) or f"  (git printed no error; exit code {code})"
       return ("git couldn't read the repo's git config, so SSSF can't tell which git "
               "files to watch. The agent did not run, and no run will until git can "
               "read it. git said:\n" + said + "\n\n"
               "Fix the config file or include path git names above, for example a "
               "symlink loop or a directory git can't enter, then run again.")
   ```

   Cap it at 20 lines of 300 characters, with non-printables shown as `?`,
   the same way `_shown_key` handles keys.

4. Update the docstrings and comments this makes wrong:
   - `guard_git_dir`: `"""Pack the watched paths, before an agent runs. None outside a git repo."""`
     becomes something like `"""Pack the watched paths, before an agent runs.
     None only when the repo root has no .git and git says it isn't a repo; a
     repo whose config git can't read raises PermissionBreach."""`
   - `_watched` docstring: add a paragraph saying a failed `rev-parse` in a
     repo, or one that fails with any error but not-a-repo, raises
     `PermissionBreach` with git's error, and that the empty result means
     only "not a git repo".

Nothing else in the module changes. `guard_git_dir` still returns `None` when
`paths` is empty, which now happens only in the not-a-repo case.

### `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section at the end, `# ── a git config git can't read ──`. Every new
test builds its run with the existing `shipped(path)` helper, which loads the
`sssf.config.yaml` SSSF ships, under `.claude/skills/sssf/templates/`, and sets
`repo_root=path`. That includes the tests whose root isn't the `repo` fixture:
they pass their own directory, as in `shipped(root)`. No new test uses a bare
`SimpleNamespace(repo_root=...)`. Add these tests:

1. `test_a_symlink_loop_in_an_include_stops_the_run_first(repo, home)`
   - `git(repo, "config", "--add", "include.path", f"{repo}/loop/x.inc")`
     first, then `(repo / "loop").symlink_to("loop")`. Writing the include
     before the loop exists keeps the test's own `git` call working.
   - Record `before = state(repo)` and the saved `home/.gitconfig` text. Read
     `state` before making the loop (it only reads `.git/`, so it works
     either way).
   - `pytest.raises(PermissionBreach)` around
     `permissions.guard_git_dir(shipped(repo))`. Assert the message contains
     `"couldn't read the repo's git config"`, `"Too many levels of symbolic
     links"`, `f"{repo}/loop/x.inc"`, `"did not run"` and `"run again"`.
   - Assert `state(repo) == before`, `.gitconfig` unchanged, and that the
     loop symlink is still there (`os.path.islink`).
   - Then `(repo / "loop").unlink()` and assert
     `guard_git_dir(shipped(repo))` returns a dict whose `paths` contains
     `f"{repo}/.git/hooks"`. Once git can read the config, the guard is on.

2. `test_an_unsearchable_dir_before_dotdot_stops_the_run_first(repo, tmp_path)`,
   `@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores search permission")`
   - `out = _dotdot_setup(repo, tmp_path)`, then
     `git config --add include.path f"{repo}/lnk/../x.inc"`, then
     `(out / "x.inc").write_text("")`, then `os.chmod(out / "a", 0o600)`
     inside `try/finally` that restores `0o755`.
   - Around `permissions.guard_git_dir(shipped(repo))`, assert the breach
     contains `"couldn't read the repo's git config"` and `"Permission
     denied"`, that `stat.S_IMODE((out / "a").stat().st_mode) == 0o600` (the
     guard opened nothing up), and that `x.inc` is still empty.

3. `test_a_dot_git_git_cannot_read_stops_the_run(tmp_path)`
   - `root = tmp_path / "r"; root.mkdir()`, and
     `(root / ".git").write_text("gitdir: /nonexistent/sssf-missing\n")`.
     `os.environ.pop("GIT_DIR", None)`.
   - Assert `permissions.guard_git_dir(shipped(root))` raises
     `PermissionBreach` with `"couldn't read the repo's git config"`. git's
     error here is a not-a-repo one, and the `.git` entry is what makes it a
     breach.

4. `test_a_broken_global_config_outside_a_repo_stops_the_run(tmp_path, home)`
   - Write `home/.gitconfig` with `[include]\n\tpath = {tmp_path}/gloop/x.inc\n`,
     then `(tmp_path / "gloop").symlink_to("gloop")`. `nr = tmp_path / "nr"; nr.mkdir()`.
   - Assert `permissions.guard_git_dir(shipped(nr))` raises with
     `"Too many levels of symbolic links"`. This is the fail-closed decision
     in `## Bar`.

5. `test_outside_a_repo_in_another_locale_is_still_nothing_to_guard(tmp_path, monkeypatch)`
   - `plain = tmp_path / "plain"; plain.mkdir()`. Set
     `monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")` and
     `monkeypatch.setenv("LANGUAGE", "fr")`, and pop `GIT_DIR`.
   - Spy on the sandbox's `run`, so the test checks something even where the
     French locale isn't installed. `box = permissions.sandbox.current()`
     returns the same instance on every call, so record each argv with
     `monkeypatch.setattr(box, "run", spy)`, where `spy` appends `command`
     to a list and then calls the original `run` with the same arguments.
   - Assert `permissions.guard_git_dir(shipped(plain))` is `None`. Also
     assert that the recorded argv with `"--git-common-dir"` in it starts
     with `["env", "LC_ALL=C", "git"]`.

6. `test_a_readable_repo_runs_no_extra_command(repo, monkeypatch)`
   - Wrap `permissions._sh` with a counter through `monkeypatch`, call
     `guard_git_dir(shipped(repo))`, and assert no `_sh` call's script is
     the `.git` probe (`'[ -e "$1" ] || [ -L "$1" ]'`). The probe runs only
     on failure.

Keep `test_outside_a_repo_there_is_nothing_to_guard` as it is: it is the
existing not-a-repo baseline test, and this change doesn't add it.

Also update these, since they now describe old behaviour:
- The comment in `test_a_dotdot_after_an_unsearchable_dir_does_not_resolve`:
  `# git itself fails on config --list once an include can't be read, so such
  an include never reaches _watched; the shipped script is checked directly.`
  becomes: git fails on `rev-parse` once an include can't be read, which now
  stops the run (see the section below), so `_REAL` is checked directly here.
- The module docstring at the top of the test file: add one sentence, "A
  repo whose git config git can't read stops the run first, with git's
  error."

### `.claude/skills/sssf/references/config.md`

After the paragraph that begins "An include path, or another git path SSSF
watches, that has `..` in it…" (around line 197), add a paragraph:

> If git can't read the repo's git config at all, for example because an
> include path runs into a symlink loop or a directory git can't enter, SSSF
> can't ask git which files to watch. So the run stops before the next agent
> with a breach that gives git's error. Fix the config file or path git names,
> then run again. This applies whenever the repo root has a `.git`, and
> whenever git fails for any reason other than "not a git repository", which
> includes a broken `~/.gitconfig`. Before, SSSF treated such a repo as no
> repo and ran the agent with no git-directory check.

Also, in the "known limits" paragraph ("The check catches mistakes and casual
attempts…"), add after the submodule / `--skip-worktree` sentence: "It reads
the config once before each agent, so a config that turns unreadable partway
through that read isn't caught."

No CHANGELOG exists in the repo. There's no README usage or CLI help text for
this guard to update.

## Verification

Run from `.claude/skills/sssf/`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
```

Both must exit 0. The six new tests must pass, and every existing test must
pass unchanged apart from the comment and docstring edits above.

Do this check by hand in a scratch dir, with a throwaway `HOME`. Make a repo
with `include.path = <repo>/loop/x.inc` and `loop -> loop`. In Python, call
`permissions.guard_git_dir` on that repo, building the run the way `shipped`
does. It must raise with git's ELOOP error. After `rm loop` it must return a
guard.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's stamped copy and is
  protected. After this lands, stamp the new
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` into it, and
  bump `adws/adw_sssf_config/stamp.json`, the way `176e885` did for the
  include-refuse guard.
- For the documenter: no `app_docs/` write-up describes this guard's
  outside-a-repo behaviour today, so nothing there goes stale. The note in
  `176e885`'s commit message ("the guard turns itself off when git cannot read
  the config") is the gap this change closes.

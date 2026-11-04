# Fail closed when the git-directory pack hits its cap

## Request

This branch (`guard-private-names`) adds `sssf-private-names` to `GIT_WATCHED`.
A bypass review found it opens a bypass against `main`. The guard keeps two
packs of the watched paths: a plain one, which records symlinks as links, and
a follow one (`tar -h`), which reads through them so a write through a linked
`~/.gitconfig` shows. The follow pack is cut at `PACK_CAP` (16 MB) with
`head -c`, and `_members` compares a cut pack only as far as it reads. So when
`.git/sssf-private-names` is a symlink to a 17 MB file, the follow pack ends
inside that file, and every path packed after it is missing from both the
before and after packs. That includes `~/.gitconfig`. An agent then appends
`[core]` / `fsmonitor = touch /nonexistent/pwned` to `~/dotfiles/gitconfig`,
which `~/.gitconfig` links to, and the guard doesn't see it. On `main` the
same write stops the run. A `.git/hooks` link to a large tree masks the same
way already. The comment by `PACK_CAP` accepts that.

The fix: a follow pack cut at the cap fails closed. Before the agent, the run
stops with a git-guard breach (`PermissionBreach`) saying the watched paths
are too large to check and what to do. After the agent, a follow pack over the
cap is a change, and the run stops. A partial follow pack is never compared
as if it were whole. Everything the branch does for the names file stays.

## Baseline

`main` at `034b4cb`. Every finding is judged against that commit. At
`034b4cb`:

- `GIT_WATCHED` has no `sssf-private-names`. This branch's `583d776` adds it.
- `_PACK`'s follow branch runs `tar -chf - "$@" 2>/dev/null | head -c $PACK_CAP | base64`.
  `_pack(..., follow=True)` never checks the size. `guard_git_dir` stores
  `_pack(paths, follow=True) or ""`. `_differs` compares `_members` of the
  before and after follow packs per path. A cut tar adds a
  `"(cut off at the cap)"` key that sits under no watched path, so it changes
  nothing.
- So on `main`, a `.git/hooks` symlink to a tree over 16 MB, present before
  the agent runs, hides every later watched path from the follow comparison.
  `hooks` is first in `GIT_WATCHED`. That includes a linked `~/.gitconfig`.
  The names-file trigger is new on this branch. The `hooks` one is old.
- The plain pack already fails closed. If it goes over the cap, `_pack`
  returns `None` and `guard_git_dir` raises "Could not save the git directory
  and git config before the agent ran, so it did not run."

## Threat model

The agent runs as the engineer's own user and has bash. It can write any file
that user can write. That covers `.git/*`, `~/.gitconfig`, and any file a
dotfiles symlink points at. It can create, retarget and grow symlinks, and it
can leave processes behind. It cannot change the packs, which live in SSSF's
process memory, and it cannot change the engineer's setup from before the
run starts.

The trigger here is a setup the engineer made: a watched path that is a
symlink to something large. The agent doesn't build that. It only uses it to
write a config line the guard misses. The agent can also make a watched link
large during its call, for example by growing the file
`.git/sssf-private-names` links to, or by linking `.git/hooks/zz` to a big
tree.

What this guard can't stop at its level, since the agent is the engineer's
user: killing the chain before the check runs, a process left behind that
writes after the check, writes to paths the guard doesn't watch, and a race
between the check and the next git command. `--sandbox` and `--container`
are the isolation. This change only makes the follow pack's size limit fail
closed.

## Bar

Against `034b4cb`, the change must:

- stop the run before the first agent runs when the watched paths, read
  through their symlinks, are more than `PACK_CAP`. The breach says they are
  too large to check, says a symlink among them points at a large file or
  tree, and says what to do. This covers the reproduction (a names-file link
  to a 17 MB file) and the old `.git/hooks` link to a big tree.
- treat a follow pack that goes over the cap during an agent call as a
  change: `git_dir_changed` returns True and `restore_git_dir` raises
  `PermissionBreach`. It never compares a partial follow pack as whole.
- keep everything the branch already does for `sssf-private-names`: put
  back an emptied, edited, deleted, chmodded or symlink-swapped file, remove
  a created one, leave an untouched one alone, guard the worktree's shared
  copy, and never put the file's contents in a message.

It must never, where `034b4cb` doesn't:

- keep an agent file. Any watched path that a pack of normal size shows
  changed is still put back.
- run agent code. The early stop runs nothing new. The after-call path runs
  only the guard's own `tar`/`base64`/`sh`, as now.
- delete or overwrite an engineer file. The early stop writes nothing. After
  a call, only paths whose plain pack changed are unpacked, exactly as now.
  A follow pack over the cap adds a reported marker and never adds a path to
  unpack. Nothing is written through or to a link.

A known cost, to be documented: a setup whose watched paths really are over
16 MB through their links now stops every run until the engineer fixes the
link.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- When the follow pack goes over the cap *during* a call, SSSF can't tell
  which linked file changed behind the large one. The breach reports the
  too-large marker and "NOT all put back", but a write the agent made through
  another link (say to `~/dotfiles/gitconfig`) is named only if the restore's
  second comparison can read it, i.e. once the large link was itself put back.
  As on `main`, nothing is ever written back through a link.
- The plain pack's size check (`len(stdout) * 3 // 4 > PACK_CAP + 3`)
  counts base64 line breaks, so it refuses packs a little under 16 MB. That
  fails closed. It stays as it is.
- A follow-pack `tar` that fails mid-stream for some reason other than size
  still compares by what it read (`_members`' `"(cut off at the cap)"` key).
  The size check below catches every cut that `head -c` makes.
- The other gaps `references/config.md` already lists: submodule git
  directories, `--skip-worktree`, a hard-linked `~/.gitconfig`, a process left
  behind, killing the chain.
- Time and memory: the guard still reads up to 16 MB, plus one byte, through
  links before and after each agent.

## Changes

Builder edits only these three files:

- `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
- `.claude/skills/sssf/tests/test_git_dir_guard.py`
- `.claude/skills/sssf/references/config.md`

Nothing in `adws/` (protected), `specs/` or `app_docs/`.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

a. `_PACK`, follow branch: read one byte past the cap, as the plain branch
   already does, so a cut can be seen:

   ```sh
   tar -chf - "$@" 2>/dev/null | head -c $((PACK_CAP + 1)) | base64
   ```

   Rewrite the comment above `_PACK` to match: the follow pack is only
   compared, never unpacked, and a dangling link in it is not an error. It
   still stops at the cap, and one that reaches the cap can't be checked.

b. The comment above `PACK_CAP`. Replace "a follow pack is compared as far as
   it goes" with the new rule: a pack of either kind that is over the cap
   can't be checked. Before the agent, that stops the run before the agent
   runs. After it, it counts as a change. Keep the first sentence (a
   symlink to `/` in hooks would have the follow pack read the whole disk).

c. `_pack`: for `follow=True`, return `None` when the decoded pack is longer
   than `PACK_CAP`. Leave the plain check as it is:

   ```python
   def _pack(paths: list[str], *, follow: bool) -> str | None:
       done = _sh(f"PACK_CAP={PACK_CAP}\n" + _PACK, "h" if follow else "-", *paths)
       if done.returncode != 0 or "tar failed" in done.stderr:
           return None
       if not follow and len(done.stdout) * 3 // 4 > PACK_CAP + 3:
           return None
       if follow:
           import base64
           if len(base64.b64decode(done.stdout)) > PACK_CAP:   # cut at the cap
               return None
       return done.stdout
   ```

   (`b64decode` drops the line breaks `base64` adds. Read `PACK_CAP` at call
   time as now, so the tests' `monkeypatch.setattr(permissions, "PACK_CAP", ...)`
   still works.)

d. `guard_git_dir`: pack the follow copy before building the dict. If it is
   `None`, raise:

   ```python
   followed = _pack(paths, follow=True)
   if followed is None:
       raise PermissionBreach(
           "The git paths SSSF watches are too large to check: read through their "
           f"symlinks they come to more than {PACK_CAP / 1048576:g} MB, or could not be "
           "read, so the agent did not run, and no run will until that changes. A "
           "symlink among them, such as .git/hooks, .git/sssf-private-names, a linked "
           "~/.gitconfig or an included config file, points at a large file or tree. "
           "Point it at something smaller, or replace it with the file it stands for, "
           "then run again.")
   ```

   Store `followed` in the returned dict (drop the old `or ""`). The plain
   check above stays first.

e. A module constant beside `SEEN`, which tests can match against:

   ```python
   TOO_LARGE = ("(the watched paths, read through their symlinks, now too large to "
                "check: a link among them points at a large file or tree)")
   ```

   It must be defined at module level before `_differs` uses it, or after it,
   since Python resolves the name when the function runs. Put it next to
   `SEEN` for readability, or just above `_differs`.

f. `_differs`: when the current follow pack is `None`, compare the plain
   packs only and add `TOO_LARGE`. The follow comparison against an empty
   pack must not mark every path changed, because that would unpack every
   watched path:

   ```python
   now = (_members(plain),) + (() if followed is None else (_members(followed),))
   then = (_members(guard["plain"]), _members(guard["followed"]))
   ...
   found = moved + [p for p in guard["paths"]
                    if any(under(a, p) != under(b, p) for a, b in zip(now, then))]
   return found + ([TOO_LARGE] if followed is None else [])
   ```

   `zip` stops at the shorter tuple, so only the plain packs are compared then.
   Update the docstring: "The watched paths that differ from the saved packs,
   plus TOO_LARGE if the follow pack is now over the cap, or None if the
   plain pack failed."

g. `restore_git_dir`: after the unpack, report any path the second comparison
   still finds that the first didn't. That is how a write through a link,
   which the large link hid until it was put back, gets named:

   ```python
   left = _differs(guard)
   restored = left == []
   changed += [p for p in (left or []) if p not in changed]
   ```

   This replaces `restored = _differs(guard) == []`. Change nothing else in
   `restore_git_dir`. `TOO_LARGE` is not in `guard["paths"]`, so it is never
   unpacked. With no real path to unpack, the existing
   `changed and not paths and not sentinel` branch gives "NOT all put back".
   Compute `sentinel` before this, as now, from the first `changed`.

Add no `global` statement. Add no new module-level name except `TOO_LARGE`.

### 2. `.claude/skills/sssf/tests/test_git_dir_guard.py`

Add a section `# ── the pack cap ──` at the end. Every new test that builds a
`run` with a config loads the shipped one:
`agents.load_config(str(SKILL / "templates" / "sssf.config.yaml"))`. Import
`agents` from `adw_modules` beside `git_helper, permissions`. Never build a
test-only config. Add a helper:

```python
def shipped(repo: Path) -> SimpleNamespace:
    return SimpleNamespace(cfg=agents.load_config(str(SKILL / "templates" / "sssf.config.yaml")),
                           repo_root=repo)
```

and a helper `linked_gitconfig(home, tmp_path) -> Path`. It moves
`~/.gitconfig` to `tmp_path/"dotfiles"/"gitconfig"`, symlinks `~/.gitconfig`
to it, and returns the dotfiles path. Copy the setup from
`test_a_write_through_a_symlinked_gitconfig_is_caught`.

Tests:

1. `test_a_names_link_over_the_cap_stops_the_run_first(repo, home, tmp_path)`.
   This is the reproduction, at the real 16 MB cap with no monkeypatch. Make
   `tmp_path/"big"` a 17 MB sparse file (`f.truncate(17 * 1024 * 1024)`) and
   symlink `.git/sssf-private-names` to it. Link `~/.gitconfig` with the
   helper. Then `pytest.raises(PermissionBreach, match="too large to check")`
   on `permissions.guard_git_dir(shipped(repo))`. Assert the message also
   contains `"did not run"` and `"smaller"`. Assert nothing was touched:
   `.git/sssf-private-names` is still a symlink to `big`, `big` is still
   17 MB, and the dotfiles gitconfig still has its original text. Then, to
   show the stop covers the attack, append `"[core]\n\tfsmonitor = touch
   /nonexistent/pwned\n"` to the dotfiles gitconfig and assert
   `guard_git_dir` still raises. A run never gets an agent past this point.

2. `test_a_hooks_link_to_a_big_tree_stops_the_run_first(repo, tmp_path, monkeypatch)`.
   Set `PACK_CAP` to 64 KB. Make a `big/` dir holding a 1 MB `os.urandom`
   blob, and symlink `.git/hooks/zz` to it *before* the guard. `guard_git_dir`
   raises `PermissionBreach` matching `"too large to check"`, and
   `.git/hooks/zz` is still a link.

3. `test_a_names_link_grown_past_the_cap_is_a_breach(repo, home, tmp_path, monkeypatch)`.
   Set `PACK_CAP` to 64 KB. Link `.git/sssf-private-names` to a small file
   (`PRIVATE`) and link `~/.gitconfig` with the helper. Take the guard. Then
   play the agent: grow the linked file to 1 MB and append the fsmonitor lines
   to the dotfiles gitconfig. Assert `permissions.git_dir_changed(guard)`. Then
   `pytest.raises(PermissionBreach, match="NOT all put back")` on
   `restore_git_dir`, with `permissions.TOO_LARGE` in the message. Assert
   nothing was written through the links: `.git/sssf-private-names` is still
   a symlink, and the dotfiles gitconfig still holds the agent's line, since
   the guard reports a change through a link and never writes it back.

4. `test_a_names_file_swapped_for_a_big_link_names_the_gitconfig(repo, home, tmp_path, monkeypatch)`.
   Set `PACK_CAP` to 64 KB. Write a plain names file (`write_private`) and
   link `~/.gitconfig`. Take the guard. As the agent, replace
   `.git/sssf-private-names` with a symlink to a 1 MB file and append the
   fsmonitor lines to the dotfiles gitconfig. `restore_git_dir` raises
   `PermissionBreach`. The message contains `.git/sssf-private-names`,
   `.gitconfig` and `"NOT all put back"`, and not `"secret-ledger"`. The
   names file is a plain file again with `PRIVATE` as its contents.

5. `test_a_link_under_the_cap_is_still_checked(repo, home, tmp_path, monkeypatch)`.
   Set `PACK_CAP` to 64 KB. Link `.git/sssf-private-names` to a 4 KB file (small: the hook samples already take part of the 64 KB)
   and link `~/.gitconfig`. `guard_git_dir` succeeds. With no change,
   `restore_git_dir` returns `[]`. Take a fresh guard, append the fsmonitor
   lines through the linked gitconfig, and `restore_git_dir` raises with
   `.gitconfig` in the message. `TOO_LARGE` is not in the message.

Leave `test_a_symlink_to_a_big_tree_is_cut_off` as it is. It must still pass:
the plain pack sees the new `hooks/zz`, so `hooks` is put back, and after that
the follow pack is under the cap again. Add `TOO_LARGE`-free assertions
nowhere else. In the module docstring, add one sentence: a pack over the cap
can't be checked, so it stops the run.

### 3. `.claude/skills/sssf/references/config.md`

In the git-directory guard section, after the paragraph that ends "No agent
has a reason to write there: SSSF does the committing.", add a paragraph.
Keep the existing prose style, short sentences:

> SSSF reads the watched paths through their symlinks too, so a write through
> a linked `~/.gitconfig` shows, and it reads at most 16 MB. If they come to
> more, it can't check them, so the run stops before the first agent with a
> breach saying they're too large to check. That happens when a link among
> them, such as `.git/hooks`, `.git/sssf-private-names` or a linked git config
> file, points at a large file or tree. Point it at something smaller, or
> replace it with the file it stands for. If they pass 16 MB during an agent
> call, that's a change, and the run stops with "NOT all put back".

In the "Two costs." paragraph, nothing changes. The new paragraph covers the
cost.

### Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: updated in step 3.
- The comments by `_PACK` and `PACK_CAP` and the `_differs` docstring in
  `permissions.py`: updated in step 1.
- The module docstring of `test_git_dir_guard.py`: step 2.
- There is no CHANGELOG, and `app_docs/` doesn't exist in this repo, so the
  documenter has no older write-up to flag. `specs/cdd093fe_watch-private-names_v2.md`
  is a record and stays as written.
- `README` and help text don't mention the cap. Nothing to change.

## Verification

Run from the repo root. Each must exit 0.

1. The guard tests, the new ones included:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The rest of the skill suite still passes. The hook tests skip without
   gitleaks.
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 583d776 -- .claude/skills/sssf/templates/adws/adw_modules/`
   shows only `permissions.py`. Its diff adds no `global`, and `TOO_LARGE` is
   the only new module-level name.
4. `grep -n 'head -c \$((PACK_CAP + 1))' .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
   shows two lines, the plain branch and the follow branch.
5. By hand, the reproduction outside pytest. In a scratch repo, with a
   scratch `HOME` whose `.gitconfig` links to `dotfiles/gitconfig`, and
   `.git/sssf-private-names` linked to a 17 MB `truncate`d file,
   `permissions.guard_git_dir` raises the "too large to check" breach. Test 1
   is this check, automated.

The repo's own `test` quality block runs the skill suite, so `checks` is
empty.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. It already differs from the template on this branch. Once the
  build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it, or
  use the skill's update path. Until then, the hub's own runs keep `main`'s
  behaviour.
- If this hub's own `.git/hooks`, `.git/sssf-private-names` or git config
  links reach more than 16 MB, every run will stop before its first agent once
  the copy above is made. Check with
  `tar -chf - <paths> | wc -c` before syncing, if in doubt.

# Name the keys of a user-level git config change the guard puts back

## Request

The git-directory guard saves `~/.gitconfig`, `~/.config/git/config`, the
global ignore files and any included config file before each agent, and puts
back any change after it. These files are shared with everything else the
engineer runs, so a change made during a run by something else (`gh auth
setup-git` in another terminal, another Claude session) is put back too. The
breach blames the agent and doesn't say what was lost.

The guard can't tell who wrote the file, so the change is still put back and
the run still stops. What changes is the breach message. For each user-level
git config file the guard puts back, it names the file, says the change may
have come from outside the agent, and lists the config keys added, removed or
given a new value, such as `+ credential.https://github.com.helper`. It never
shows a value. A global ignore or attributes file is named with how many
lines were added and removed, and no line is shown. The listing is capped per
file and overall, and says when keys were left out. The repo's own `.git`
paths keep today's message. The guard writes no new file. Git reads the keys
(`git config --file - --list -z`, with a time limit). Python never parses the
config.

Reproduction: while an agent runs, a process that isn't the agent runs
`git config --global credential.https://github.com.helper '!gh auth git-credential'`.
Today the line is gone after the run, and the breach shows only
`- /home/<user>/.gitconfig`.

## Baseline

`main` at `165f82f` ("Give the skill's test command in the update steps").
The branch `guard-gitconfig-keys` is at the same commit. Judge every finding
against `165f82f`. At that commit the hub's installed
`adws/adw_modules/permissions.py` is byte-identical to the template
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`.

What the baseline does, in `permissions.py`:

- `_watched(run)` returns `(links, resolved)`: every watched physical path,
  including git config sources from `git config --list --show-origin`,
  include targets, `core.excludesFile` / `core.attributesFile` targets,
  `~/.gitconfig`, `$XDG/git/config`, `$XDG/git/attributes` and
  `$XDG/git/ignore`.
- `guard_git_dir` keeps `plain`, a base64 tar of all of them, in memory.
- `restore_git_dir` finds the changed paths (`_differs`), writes `plain` to a
  `mktemp` file `held`, runs `_UNPACK`, which deletes `held` when it finishes,
  re-checks, and raises
  `PermissionBreach("The agent changed the git directory or the git config, … N path(s), put back as they were:\n  - <path>…")`.

## Threat model

The agent runs as the engineer's own user and has bash. It can write any file
the engineer can, including `~/.gitconfig`, every included config file and the
global ignore files, and it can leave a process running. It cannot read or
change memory in the SSSF process, so it can't touch the saved `plain` pack.

This change adds no new power to restore or stop a run. It adds a message,
built from content the agent may have written, so the threats are about that
message and the work done to build it:

- **Agent code running while the keys are read.** A planted
  `core.fsmonitor`, `include.path` or `core.pager` must not run. `git config
  --file - --list` runs no hook, pager or fsmonitor, doesn't follow includes
  with `--file`, and `-c core.fsmonitor=` is passed as on every other SSSF git
  call. The script `cd`s to `/`, so no repo is found.
- **Stalling the restore.** An agent's long line, a 16 MB file, or a FIFO in
  place of `~/.gitconfig` must not hang the run or skip the put-back. The key
  listing has a watchdog in the script, the sandbox call has a timeout, the
  type of each version is checked from the tar header before git reads it,
  and any error in naming keys is caught before the unpack runs.
- **Leaking a secret into the breach.** Values never appear. A key name can
  carry a credential in a URL subsection
  (`url.https://x-access-token:TOKEN@github.com/.insteadof`), so the userinfo
  before the last `@` after `://` is shown as `***`.
- **Flooding or garbling the terminal.** Caps per file and overall, a width
  limit per key, and non-printable characters shown as `?`.
- **Misleading the engineer.** The agent can add a key that looks like one the
  engineer would set. The message tells the engineer to make *their* change
  again with the command they used, never to copy anything back from the
  listing, and the change is put back regardless.

What this guard can't stop at its level, since the agent is the engineer's own
user: it can't tell whether the agent or the engineer wrote a file; a process
the agent left behind can write after the check; an agent that kills the chain
first skips the check. `--sandbox` and `--container` isolate.

## Bar

Against `165f82f`, the change must:

- put back exactly the paths the baseline puts back, and raise
  `PermissionBreach` in exactly the cases the baseline raises it, with the same
  outcome wording (`put back as they were`, `changed back since`, `NOT all put
  back…`);
- for each watched config file outside the repo's git directories that it
  puts back, name the file, say the change may have come from outside the
  agent, and list keys as `+ key` (added), `- key` (removed) or `~ key`
  (present before and after, with a value added, removed or changed). The list
  is capped at `KEYS_PER_FILE = 10` per file and `KEYS_IN_ALL = 30` in all,
  with a `(N more key(s) left out)` line whenever a file has more than it
  shows;
- for each global ignore or attributes file it puts back, name it the same
  way, with `N line(s) added, M removed`;
- leave the message byte-for-byte as in the baseline when no such file is
  among the paths put back (the repo's `.git` paths, `SEEN`, `TOO_LARGE`, a
  change through a symlink, a failed compare);
- still put back every path when listing keys fails, times out or raises.

It must never:

- show a config value or an ignore/attributes line;
- write a new file. The only file is the baseline's `held` temp file, which
  `_UNPACK` still deletes;
- run agent code: no fsmonitor, hook, pager, include or credential helper
  from the files being read;
- keep an agent file, or delete or overwrite an engineer file, where the
  baseline doesn't;
- parse git config in Python, or use a regex on file content.

## Out of scope

Old holes, left open on purpose:

- The engineer's own change to a shared file during a run is still put back.
  The breach now names its keys, but the engineer has to make it again.
- A failed agent (`restore_git_dir(..., breach=False)`) still emits the
  `git_dir_restored` trace event with paths only. No keys are listed there.
- A shared file changed through a symlink, which the guard reports but never
  writes back, keeps today's bare line. So does a hooks directory outside the
  repo (`core.hooksPath`), which is not a config file.
- Secrets in key names other than URL userinfo (for example a token used as
  an alias name) are shown as written.
- A change undone before the restore (`SEEN`) and a follow pack over the cap
  (`TOO_LARGE`) keep today's message.
- Everything already listed under "The check catches mistakes and casual
  attempts" in `.claude/skills/sssf/references/config.md`: killing the chain,
  a leftover writer, submodules, `--skip-worktree`, hard links.

## Changes

All code changes are in
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py`. Tests go in
`.claude/skills/sssf/tests/test_git_dir_guard.py`. Docs go in
`.claude/skills/sssf/references/config.md`. Nothing else in `adw_modules/`
changes.

### 1. `_sh` takes a timeout

`_sh(script, *args, timeout=None)` passes `timeout=timeout` to
`sandbox.current().run(...)`. Existing callers are unchanged.

### 2. `_watched` also returns which watched paths are shared files

Change the return to `tuple[list[str], list[str], dict[str, str]]`:
`links, resolved, kinds`. `kinds` maps a **resolved** path to `"config"` or
`"lines"`, and only for paths outside the repo's git directories. Update the
docstring.

While collecting `paths`, record a kind per unresolved path in a local dict
`kind`. `"config"` wins over `"lines"` if a path gets both:

- each `file:` origin `source` → `"config"`
- an `include.path` / `includeIf.*.path` value → `"config"`
- a `core.excludesFile` / `core.attributesFile` value → `"lines"`
- `{home[0]}/.gitconfig`, `{home[1]}/git/config` → `"config"`;
  `{home[1]}/git/attributes`, `{home[1]}/git/ignore` → `"lines"`
- the hooks path, `{root}/.git` and the `GIT_WATCHED` paths get no kind.

In the resolve loop (`for p, r in zip(paths, real)`), after `r` is final, set
`kinds[r] = kind[p]` when `p in kind` and neither `p` nor `r` equals or sits
under (`startswith(d.rstrip("/") + "/")`) any `d` in `dirs`. That keeps the
repo's own `.git/config` and `.git/config.worktree` out. Return
`links, resolved, kinds`. Before returning, drop any kind whose path isn't in
the final `resolved` list.

`guard_git_dir` unpacks the three and adds `"kinds": kinds` to the dict it
returns. Every new read uses `guard.get("kinds", {})`, so a guard dict built
by hand in a test still works.

### 3. New module-level names, after `TOO_LARGE`

```python
# A git config file outside the repo's git directory is shared with everything
# else the engineer runs, so a change there may be theirs, made in another
# terminal while the agent ran. The guard can't tell, so it still puts the file
# back and stops the run, but it names the keys that changed, never a value,
# so the engineer can make their own change again. Git reads the keys, under a
# time limit; Python never parses the file.
KEYS_PER_FILE = 10
KEYS_IN_ALL = 30
KEYS_SECONDS = 10
KEY_WIDTH = 120
SHARED = ("(outside the repo and shared with everything you run, so the change "
          "may have come from outside the agent)")
UNLISTED = f"(its keys could not be listed: git could not read a version of it as config within {KEYS_SECONDS} s)"

# Lists the entries in one version of a watched config file, "key\nvalue"
# NUL-separated: the saved version from the pack file $1, or the live one when
# $1 is "-". Nothing is written. A watchdog kills git after $3 seconds; its own
# output goes to /dev/null, so it doesn't hold the pipe open.
_KEYS = """f=$1; rel=${2#/}; t=$3
cd / || exit 1
{ if [ "$f" = - ]; then tar -cf - "$rel"; else base64 -d < "$f"; fi; } 2>/dev/null |
  tar -xOf - "$rel" 2>/dev/null |
  git -c core.fsmonitor= config --file - --list -z &
pid=$!
( sleep "$t"; kill "$pid" ) >/dev/null 2>&1 &
w=$!
wait "$pid"; s=$?
kill "$w" 2>/dev/null
exit $s
"""
```

This script was tried during planning under `sh` with git 2.43. It lists a
saved and a live file, and on a FIFO it prints nothing and exits 0. That's
why step 4 checks types first.

### 4. New helpers, after `_KEYS`

```python
def _entries(source: str, path: str):
    """Counter of (key, value) git reads in one version of `path`, or None if
    git couldn't read it as config in time."""
    import subprocess
    from collections import Counter
    try:
        done = _sh(_KEYS, source, path, str(KEYS_SECONDS), timeout=KEYS_SECONDS + 5)
    except subprocess.TimeoutExpired:
        return None
    if done.returncode != 0:
        return None
    return Counter(tuple(e.partition("\n")[::2]) for e in done.stdout.split("\0") if e)


def _shown_key(key: str) -> str:
    """A key as the breach shows it: URL userinfo masked, non-printables as ?,
    at most KEY_WIDTH characters."""
    scheme, sep, rest = key.partition("://")
    if sep and "@" in rest:
        key = f"{scheme}://***@{rest.rpartition('@')[2]}"
    key = "".join(c if c.isprintable() else "?" for c in key)
    return key if len(key) <= KEY_WIDTH else key[:KEY_WIDTH - 1] + "…"


def _shared(guard: dict, paths: list[str], held: str) -> dict[str, list[str]]:
    """Detail lines for each shared file in `paths`, which are about to be put
    back from the pack file `held`."""
```

`_shared` does this:

- `kinds = guard.get("kinds", {})`, `shared = [p for p in paths if p in kinds]`.
  If `shared` is empty, return `{}`.
- `then = _members(guard["plain"])`. `fresh = _pack(shared, follow=False)`.
  If `fresh is None`, return `{p: [UNLISTED] for p in shared}`. Otherwise
  `now = _members(fresh)`.
- `budget = KEYS_IN_ALL`. For each `p` in `shared`, with `rel = p.lstrip("/")`,
  `b = then.get(rel)`, `a = now.get(rel)`:
  - If `b` or `a` is present and its type (`m[0]`) is not `tarfile.REGTYPE`
    or `tarfile.AREGTYPE`: `["(not a regular file before or after, so nothing in it is listed)"]`.
  - kind `"lines"`: build `Counter(data.splitlines())` from the bytes of each
    version (`b[3]`/`a[3]`, `b""` when absent). The line is
    `f"{sum((new - old).values())} line(s) added, {sum((old - new).values())} removed"`.
  - kind `"config"`: `old = Counter() if b is None else _entries(held, p)`,
    `new = Counter() if a is None else _entries("-", p)`. If either is
    `None`: `[UNLISTED]`. Otherwise
    `added = {k for k, _ in new - old}`, `removed = {k for k, _ in old - new}`,
    `marks = sorted([("~" if k in removed else "+", k) for k in added] + [("-", k) for k in removed - added], key=lambda m: m[1])`.
    If `marks` is empty: `["(no key added, removed or given a new value: a comment, layout or order change)"]`.
    Otherwise take `shown = marks[:max(0, min(KEYS_PER_FILE, budget))]`,
    `budget -= len(shown)`. The lines are `f"{m} {_shown_key(k)}"` for each in
    `shown`, plus `f"({len(marks) - len(shown)} more key(s) left out)"` when
    any were left out.
- Return the dict of `p → lines`.

### 5. `restore_git_dir`

- Before `if paths:`, set `details: dict[str, list[str]] = {}`.
- In the branch `if made.returncode == 0 and held.startswith("/")`, after
  `box.write_text(held, guard["plain"])` and **before** `_sh(_UNPACK, held, *paths)`:

  ```python
  try:
      details = _shared(guard, paths, held)
  except Exception:      # naming keys must never stop the put-back
      details = {p: [UNLISTED] for p in paths if p in guard.get("kinds", {})}
  ```

  No details are built when `held` couldn't be made, since nothing is put
  back then.
- After `left = _differs(guard)`, drop details for anything not put back:
  `details = {} if left is None else {p: d for p, d in details.items() if p not in left}`.
- Build the message. When `details` is empty, the string must be exactly
  today's. Otherwise:
  - Lead: if every entry of `changed` is in `details`, use
    `"A git config or ignore file outside the repo changed while the agent ran, and no agent may write one: config there runs code on later git commands. "`.
    Otherwise use today's lead (`"The agent changed the git directory or the git config, …"`).
    Both contain `git config`.
  - `f"{len(changed)} path(s), {outcome}:\n"` as today.
  - For each `p, s` in `zip(changed, shown)`: `f"  - {s}"`. When `p in details`,
    append `f" {SHARED}"` to that line, then one line per detail, indented
    six spaces (`f"      {d}"`).
  - Append a closing paragraph, separated by a blank line:
    `"Files marked as shared are used by everything else you run, so another terminal or session, such as `gh auth setup-git`, may have made the change, not the agent. SSSF can't tell which, so it put them back and stopped the run either way: an agent's line there would run on your next git command. Keys are marked + added, - removed or ~ given a new value; no value or line is ever shown. If a change is yours, make it again with the command you used."`
- The return value (`shown`, plain path strings) does not change.
- Update the docstring. Say that for a shared config file the breach lists the
  keys that changed, and never a value.

### 6. Comments

In the `# ── the git directory and git config ──` block comment, add one
sentence after "A change stops the run.": "A change to a shared config file
outside the repo also stops it. The breach lists the changed keys, since the
change may be the engineer's own."

### 7. Tests, in `.claude/skills/sssf/tests/test_git_dir_guard.py`

Use the existing `home` and `repo` fixtures and the `git` helper. No config
file is needed. Each test calls `guard_git_dir`, makes the change, then
expects `PermissionBreach` from `restore_git_dir`, and checks `str(err.value)`:

1. `test_a_shared_config_change_names_its_keys`: the reproduction.
   `git(repo, "config", "--global", "credential.https://github.com.helper", "!gh auth git-credential")`.
   The message contains `"+ credential.https://github.com.helper"`,
   `"may have come from outside the agent"`, `".gitconfig"` and `"gh auth setup-git"`.
   It doesn't contain `"git-credential"`. `~/.gitconfig` is back to its
   saved bytes.
2. `test_removed_and_revalued_keys_are_named_without_values`: before the
   guard, `.gitconfig` holds `user.name = t` and `core.hooksPath = /before-path`.
   After, unset `user.name`, set `core.hooksPath` to `/after-path`, add
   `core.fsmonitor = touch PLANTED`. The message has `"- user.name"`,
   `"~ core.hookspath"` and `"+ core.fsmonitor"`, and none of
   `before-path`, `after-path`, `PLANTED`.
3. `test_keys_are_capped_per_file`: add `alias.k00`…`alias.k39` by writing the
   file. Exactly 10 lines start with `"      + alias.k"`, and
   `"(30 more key(s) left out)"` is in the message.
4. `test_keys_are_capped_in_all`: `monkeypatch.setattr(permissions, "KEYS_IN_ALL", 12)`.
   Add 10 keys to `~/.gitconfig` and 10 to `~/.config/git/config`, a new
   file. Exactly 12 `"      + "` lines appear, and a `left out` line names
   the remaining 8.
5. `test_a_shared_ignore_change_counts_lines`: `~/.config/git/ignore` holds
   `"keep\nold\n"`, then `"keep\nadws/evil.py\nnew2\n"`. The message has
   `"2 line(s) added, 1 removed"` and the path, and not `"evil"`. The file is
   restored.
6. `test_a_repo_config_change_keeps_todays_message`: add `core.fsmonitor` to
   `repo/.git/config`. The message starts with `"The agent changed the git
   directory"`, and contains neither `"outside the agent"` nor `"+ core"`.
7. `test_a_token_in_a_key_name_is_masked`:
   `git config --global url.https://x-access-token:SECRETTOKEN@github.com/.insteadOf https://github.com/`.
   `"SECRETTOKEN"` is not in the message, and `"url.https://***@github.com/.insteadof"` is.
8. `test_an_unreadable_config_still_restores`: overwrite `.gitconfig` with
   `"[bad\n"`. The message has `"could not be listed"`, and the file is
   restored.
9. `test_a_stalled_listing_still_restores`: set `permissions._KEYS` to
   `"sleep 30"`, `KEYS_SECONDS` to `1`, change `.gitconfig`. The restore
   returns in under 15 s (time it), the message has `"could not be listed"`,
   and the file is restored.
10. `test_a_comment_only_change_says_so`: append `"# note\n"`. The message has
    `"comment, layout or order change"`.
11. `test_a_long_key_is_cut`: subsection of 500 `x`. No message line is
    longer than `KEY_WIDTH + 10`, and the key line ends with `"…"`.
12. `test_naming_keys_writes_no_file`: `TMPDIR` set to an empty `tmp_path / "t"`
    (monkeypatch). Make the reproduction change. After the breach, that
    directory is empty and `home`'s file list is unchanged.

Existing tests must pass unchanged. `test_the_global_git_config_is_put_back`
matches `"git config"`, which both leads contain, and the
`"put back as they were"` matches are unaffected.

Update the module docstring of the test file. Add a sentence saying a
shared config file's breach lists the changed keys, never a value.

### 8. Docs: `.claude/skills/sssf/references/config.md`

In the "Two costs." paragraph, after "edit them between runs.", add:

> A git config or ignore file outside the repo is shared with everything you
> run, so the breach marks it as maybe not the agent's change. For a config
> file it lists the keys added (`+`), removed (`-`) or given a new value
> (`~`), at most 10 per file and 30 in all, and never a value. For an ignore
> or attributes file it says how many lines were added and removed. If the
> change is yours, make it again with the command you used, such as
> `gh auth setup-git`. The run stopped either way.

### Docs this makes out of date

- `.claude/skills/sssf/references/config.md`: step 8.
- The `restore_git_dir` and `_watched` docstrings and the guard block comment
  in `permissions.py`: steps 2, 5 and 6.
- The module docstring of `test_git_dir_guard.py`: step 7.
- There is no CHANGELOG, and `app_docs/` doesn't exist in this repo, so the
  documenter has no older write-up to flag. The earlier specs in `specs/` are
  records and stay as written.
- `README.md` doesn't describe the breach message. Nothing to change.

## Verification

Run from the repo root. Each must exit 0.

1. The guard tests, the new ones included:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q -p no:cacheprovider`
2. The whole skill suite. The hook tests skip without gitleaks:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
3. `git diff --stat 165f82f -- .claude/skills/sssf/templates/` shows only
   `adws/adw_modules/permissions.py`. Its diff adds no `import re` and no
   `re.` call.
4. By hand, the reproduction outside pytest. Use a scratch repo and a scratch
   `HOME`. Call `permissions.guard_git_dir`, run the `git config --global
   credential…` command above, then call `permissions.restore_git_dir`. The
   breach lists `+ credential.https://github.com.helper`, doesn't show
   `gh auth git-credential`, and `~/.gitconfig` is back as it was. Test 1
   automates this.

The repo's `test` quality block runs the skill suite, so the plan adds no
extra `checks`.

## For the engineer

- `adws/adw_modules/permissions.py` is the hub's installed copy, and it is
  protected. Once the build lands, copy
  `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` over it (or
  run `install.py --update`), and update `adws/adw_sssf_config/stamp.json` as
  in `1c47666`. Until then the hub's own runs keep the baseline message.

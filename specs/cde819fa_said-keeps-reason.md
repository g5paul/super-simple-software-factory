# Keep git's reason when the breach clips a long error line

## Summary

`_said` in `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`
quotes git's error in two breaches (`_unreadable`, `_no_hooks_dir`): at most 20
non-blank lines, each cut to its first 300 characters, with non-printables shown as `?`.
Git puts the path first and the reason last
(`fatal: unable to access '<path>': Too many levels of symbolic links`), so
with a long path the cut drops the reason. The run still stops; only the message
is incomplete.

Change: a line longer than 300 characters keeps its start and its end with a
`[…]` marker between them, 300 characters in total. Lines of 300 or fewer
characters are quoted exactly as today. The 20-line limit and the `?` stay as
they are.

Reproduced on `main` (git 2.43, TMPDIR of 291 characters): git's line for the
symlink-loop test is 388 characters, and exactly these three tests in
`.claude/skills/sssf/tests/test_git_dir_guard.py` fail, while the other 170 pass:
`test_a_symlink_loop_in_an_include_stops_the_run_first`,
`test_an_unsearchable_dir_before_dotdot_stops_the_run_first`,
`test_a_broken_global_config_outside_a_repo_stops_the_run`.

## Baseline

`main` at `b7d16ea` ("Give the guard chain a third spec review when the second
narrows"). Every finding is judged against it. At the baseline, `_said` is at
`.claude/skills/sssf/templates/adws/adw_modules/permissions.py:620`, and the
hub copy `adws/adw_modules/permissions.py` matches it byte for byte.

## Threat model

The agent runs as the engineer's own user. It can write any file that user can
write, including `~/.gitconfig`, the repo's `.git/config` and include files, and
it can make symlink loops or directories git can't enter. `_said` handles
only the text of git's stderr after git failed to read config or name the hooks
directory. The guard has already decided to stop the run by then. That text is
partly controlled by the agent, because paths and include names are the agent's to choose.

What `_said` must keep true: the quoted text has a fixed size (≤ 20 lines ×
300 characters plus the 2-space indent), and no control character reaches the
engineer's terminal (every non-printable becomes `?`). Showing the end of a line
as well as its start shows no more characters than before, and nothing in it is run,
kept or written.

What this guard can't stop at its level: an agent running as the user can kill
the chain, edit files between the guard's reads, or write a misleading but
printable path into a config. The breach quotes git, and the guard can't
check that what git says is true. None of that changes here.

## Bar

The change must:

- Quote a line of more than 300 characters (counted after the `?` substitution)
  as its first 149 characters, then `[…]`, then its last 148 characters: 300
  characters in total, so git's reason at the end survives.
- Quote a line of 300 characters or fewer exactly as today.
- Keep the 20-line limit, the blank-line filter, the 2-space indent, the `?` for
  every non-printable (at both ends of a clipped line), and the
  `(git printed no error; exit code N)` fallback.
- Leave every breach decision unchanged. The same inputs stop the run, and
  nothing new passes.
- Make the three named tests pass with TMPDIR of about 290 characters, and keep
  the whole suite passing with the normal TMPDIR.

It must never, where the baseline does otherwise:

- keep an agent file, run agent code, or delete or overwrite an engineer file;
- let a control character or more than 20 × 300 characters of git's text into a breach;
- change `_unreadable`'s or `_no_hooks_dir`'s fixed wording
  (`test_unreadable_keeps_its_message` must pass unchanged).

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:

- A reason longer than 148 characters, or one git puts in the middle of
  a line, can still be cut.
- An error of more than 20 lines still drops everything after line 20, which
  may include the reason.
- A printable but misleading path in git's message (for example one that
  contains `': Permission denied`) is quoted as written. The guard can't
  check what git says.
- `_shown_key` (keys in the shared-config breach) still cuts to its first
  `KEY_WIDTH` characters. This request covers only git's error text.
- Under a long TMPDIR the other 170 guard tests already pass, and other test
  files under long paths are not covered here.
- Stamping the change into the hub copy (see "For the engineer").

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/permissions.py`

Replace `_said` (lines 620–625). Name the limits as module constants next to it,
following the existing `KEY_WIDTH` style:

```python
SAID_LINES = 20
SAID_WIDTH = 300
SAID_CUT = "[…]"


def _said_line(line: str) -> str:
    """One line of git's error as a breach quotes it: non-printables as ?, and
    a line over SAID_WIDTH characters cut in the middle, so the path git names
    first and the reason it gives last both survive."""
    line = "".join(c if c.isprintable() else "?" for c in line)
    if len(line) <= SAID_WIDTH:
        return line
    room = SAID_WIDTH - len(SAID_CUT)
    head = room - room // 2
    return line[:head] + SAID_CUT + line[len(line) - room // 2:]


def _said(err: str, code: int) -> str:
    """git's error as a breach quotes it: at most SAID_LINES lines of at most
    SAID_WIDTH characters, non-printables as ?. A longer line keeps its start
    and its end with SAID_CUT between them."""
    lines = [l for l in err.strip().splitlines() if l.strip()][:SAID_LINES]
    return "\n".join("  " + _said_line(l) for l in lines) or f"  (git printed no error; exit code {code})"
```

With `SAID_WIDTH = 300` and a 3-character cut, `room = 297`, head = 149, tail = 148.
Use `line[len(line) - room // 2:]`, not `line[-(room // 2):]`, so the slice
stays right if `room // 2` is ever 0. Leave every caller and every other function unchanged.

### 2. `.claude/skills/sssf/tests/test_git_dir_guard.py`

a. `test_a_symlink_loop_in_an_include_stops_the_run_first` (line ~1986): the
   full include path can't fit in a 300-character line under a long TMPDIR (it
   is ~370 characters there), so check both ends of it, and the whole path
   only when it fits. Replace the `f"{repo}/loop/x.inc"` entry in the `for part in (...)`
   tuple with `include[:100]` and `include[-30:]`, where
   `include = f"{repo}/loop/x.inc"` is assigned at the top of the test, used in
   the `git config --add include.path` call too, and followed after the loop by:

   ```python
   if len(include) <= 200:
       assert include in text
   ```

   Leave the rest of the test as it is. The other two named tests need no edit.
   They pass once `_said` keeps the tail.

b. Add unit tests for `_said` next to `test_unreadable_keeps_its_message`.
   They need no git:

   - `test_said_quotes_a_line_within_the_limit_as_is`:
     `permissions._said("f" * 300, 1) == "  " + "f" * 300`.
   - `test_said_keeps_both_ends_of_a_long_line`: with
     `err = "fatal: unable to access '/" + "d" * 400 + "': Too many levels of symbolic links\n"`,
     `out = permissions._said(err, 128)` satisfies
     `out == "  " + err.strip()[:149] + "[…]" + err.strip()[-148:]`, and
     `len(out) == 2 + 300`.
   - `test_said_shows_non_printables_at_both_ends_of_a_cut_line`:
     `permissions._said("\x1b" + "a" * 400 + "\x07", 1) == "  ?" + "a" * 148 + "[…]" + "a" * 147 + "?"`.
     (`strip()` keeps `\x1b` and `\x07`. They are not whitespace.)
   - `test_said_keeps_at_most_twenty_lines`:
     `permissions._said("\n".join(f"line {i}" for i in range(25)), 1).splitlines()`
     has 20 entries, the last `"  line 19"`.

c. In the module docstring, add one line after "A git hooks directory git can't name stops the run first, with git's error.":
   `A long line of git's error keeps its start and its end, so the reason survives.`

### Docs this change makes out of date

- `_said`'s docstring: rewritten in change 1.
- `.claude/skills/sssf/references/config.md` lines 250–263 say only that the
  breach "gives git's error", which stays true. No edit.
- No README usage, help text or CHANGELOG covers this. The repo keeps no
  CHANGELOG.
- No `app_docs/` exists in this repo. For the documenter: an earlier write-up
  of the unreadable-config or hooks-path breach (runs `8fabb40a`, `06ac79f1`)
  that says "each line cut to its first 300 characters" now describes the
  old behaviour.

## Verification

From `.claude/skills/sssf`, each of these must exit 0:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q
```

From the repo root, with TMPDIR of 291 characters (the reproduction). On
`b7d16ea` exactly the three named tests fail here. After the change all of
`tests/test_git_dir_guard.py` passes:

```
d="$(mktemp -d)/$(printf '%0135d' 0)/$(printf '%0135d' 0)" && mkdir -p "$d" && cd .claude/skills/sssf && TMPDIR="$d" uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_git_dir_guard.py -q
```

The unsearchable-dir test skips under root. Run as a normal user.

Check by hand that
`git diff b7d16ea -- .claude/skills/sssf/templates/adws/adw_modules/permissions.py`
touches only `_said`, the new `_said_line` and the three `SAID_*` constants.

## For the engineer

The hub copy is protected, so the builder must not touch it. After the change
passes review, stamp it into the hub as in `4c332d0`:

- copy `.claude/skills/sssf/templates/adws/adw_modules/permissions.py` to
  `adws/adw_modules/permissions.py`;
- update its `sha256` in `adws/adw_sssf_config/stamp.json`.

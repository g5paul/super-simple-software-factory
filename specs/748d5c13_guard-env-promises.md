# Close the two broken promises in the secret-file guard

Run 13b4d30c built `specs/tasks/guard-env-read.md` on this branch (bb1b840).
Two of that spec's promises break in normal use:

1. A chain started through a `just` recipe has every `.env` key exported
   before Python starts (`set dotenv-load`, `.claude/skills/sssf/templates/justfile:7`).
   `utils.DOTENV_KEYS` is computed as "keys `load_dotenv` added", so it is
   empty and `agent_env()` strips nothing.
2. `agent_cc._command` passes `Read(/<abs path>)` with the raw path. Claude Code
   reads the rule as a gitignore-style pattern, so `[`, `]`, `*`, `?`, `!`, a
   backslash or a space in the path can deny a different file and leave the real
   one readable.

Fix both, and nothing else. Everything else run 13b4d30c built stays as it is,
and so do its bar and out-of-scope list.

## Threat model

Agents (Claude Code and pi) run as the engineer's own Unix user on the host,
with bash, Read/Grep (Claude Code) or read/grep/find/ls (pi). An agent doing
ordinary work may print its environment (`env`, `printenv`) or open `.env` with
a read tool; the secret then lands in a commit, a log or the trace. That
accidental exposure is what this guard stops.

What the guard can't stop at its level: an agent set on a secret. Running as
the engineer's user, it can read `/proc/<pid>/environ` of the ADW, build the
path at runtime, follow a symlink or hard link, or use any interpreter. No
in-process check bounds that; the container and the VMs are the boundary,
since `.env` never enters them. `references/config.md` already says so, and
this change keeps that wording.

## Baseline

`main` at `e3bcd88`. Every finding is judged against it. At `e3bcd88` every
`.env` key reached every agent and no `.env` file was denied to any read tool.
The branch commit `bb1b840` is the change being repaired, not the baseline.

## Bar

The change must:

- Strip from an agent's environment every key that `.env` names, whether or not
  it was already in the environment when the ADW started (a `just` recipe, a
  shell export with the same name, direnv). Exceptions stay as built:
  `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, pi's provider keys for pi agents,
  and `agent_env` keys.
- Let a shell export whose name `.env` does not set pass, as today.
- Keep the full environment for quality checks, `worktree_setup` and git.
- Deny, for each protected file, a Claude Code rule that matches exactly that
  file, with `[ ] * ? ! \` and space escaped by a backslash. One rule per file.

The change must never, against the baseline:

- keep an agent's file (no agent output kept that the baseline would not keep);
- run agent code the baseline would not run;
- delete or overwrite an engineer file the baseline leaves alone;
- change which `.env` file is loaded or what lands in `os.environ` (the ADW's
  own process and quality checks see exactly what they see at `e3bcd88`).

Accepted by the engineer on 2026-11-06: a shell export whose name `.env` also
sets is stripped from agents too, since that name is a secret the repo keeps in
`.env`.

## Out of scope

Old holes, carried from run 13b4d30c, for a reviewer to file as old:

- Case variants of a protected name on a case-insensitive filesystem (`.ENV`).
- `.env` behind a symlinked folder.
- A grep over a whole folder that holds a protected file.
- Obfuscated names (`cat .e''nv`, a path built at runtime).
- Hard links to a protected file.

Also left as is by this change:

- Characters other than the seven escaped (`[ ] * ? ! \` and space) in a deny
  path, such as `(`, `)` or `#`. Only these seven were checked by hand on
  claude 2.1.319.
- A secret that lives only in the engineer's shell and that `.env` does not
  name (unchanged: it passes).
- A `.env` other than the one `load_dotenv()` finds today (nested `.env`
  files' keys are not loaded into the environment, so they are not in it).

## Files to change

All guard code lives in `.claude/skills/sssf/templates/adws/adw_modules/`.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/utils.py`

Today (lines 14–19):

```python
from dotenv import load_dotenv

_SHELL_KEYS = frozenset(os.environ)
load_dotenv()
# Keys .env added. load_dotenv never overrides, so a key the shell exported is the shell's.
DOTENV_KEYS = frozenset(os.environ) - _SHELL_KEYS
```

Change to read the key names from the file itself:

```python
from dotenv import dotenv_values, find_dotenv, load_dotenv

# The same file load_dotenv() finds on its own: find_dotenv walks up from this
# module's caller, which is this module either way.
_DOTENV = find_dotenv()
load_dotenv(_DOTENV)
# Every key .env names, read from the file as dotenv_values does, not from what
# load_dotenv changed: a `just` recipe (`set dotenv-load`) or a shell export of
# the same name has already put the key in os.environ, and it is still .env's.
DOTENV_KEYS = frozenset(dotenv_values(_DOTENV)) if _DOTENV else frozenset()
```

- Drop `_SHELL_KEYS`; nothing else uses it (check with a grep).
- `load_dotenv(_DOTENV)` must load the same file as today's bare
  `load_dotenv()`. Both call `find_dotenv()` from this module's frame, so the
  result is the same; keep the call at module level in `utils.py`, never move
  it into a helper in another module (that changes the frame `find_dotenv`
  walks from).
- `dotenv_values` includes a key written with no `=` (value `None`); keep it in
  `DOTENV_KEYS` — `.env` names it.
- Update the `agent_env` docstring: "env without the keys `.env` names (however
  they reached the environment), except …". Keep its logic unchanged.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- Add a module-level helper next to `_command`:

  ```python
  # Claude Code reads a Read(...) rule as a gitignore-style pattern. Checked by
  # hand on claude 2.1.319 (bypassPermissions, --safe-mode) in `weird [x] dir`:
  # the raw path let the agent read .env; each of these escaped denied it.
  _RULE_SPECIAL = re.compile(r"([\[\]*?!\\ ])")

  def deny_rule(path: str) -> str:
      """The Read deny for one absolute path, in Claude Code's //<abs> form,
      with gitignore pattern characters escaped so it matches only that file."""
      return "Read(/" + _RULE_SPECIAL.sub(r"\\\1", path) + ")"
  ```

  (Import `re` if the module doesn't already.) One regex pass escapes a
  backslash and the other characters alike, so nothing is escaped twice.
- In `_command` (around line 209–211) replace
  `cmd += ["--disallowedTools", f"Read(/{path})"]` with
  `cmd += ["--disallowedTools", deny_rule(path)]`. Still one rule per file.
- Update the module docstring (lines ~20–21) to say the path's pattern
  characters are escaped.

No change to `secret_files.py`, `agent_pi.py`, `agents.py`, `sssf_rules.ts`,
`sssf_refuse.py`, `repo_settings.py` or `data_types.py`. pi's
`sssf_rules.ts` compares paths as strings, not patterns, so it is unaffected.

### 3. Tests: `.claude/skills/sssf/tests/test_secret_files.py`

Settings and the roster come from the files SSSF ships under
`.claude/skills/sssf/templates/` (`repo.yaml`, `sssf.config.yaml`), never a
test-only config. Keep every existing test. Add:

**a. `.env` names a key the environment already had (as `just` leaves it).**
In-process tests can't do this, since `utils` is imported once and conftest's
autouse `no_dotenv_keys` sets `DOTENV_KEYS` empty. So run a fresh process:

- In `tmp_path`, `git init -q`, then stamp with
  `python .claude/skills/sssf/scripts/install.py` run with `cwd=tmp_path`
  (same call as `test_update.py`'s `install()`, default `pi` roster, which
  stamps the shipped `sssf.config.yaml`). Write a helper `stamped_repo(tmp)` so
  test b reuses it.
- Write `tmp/.env` with `APP_SECRET=from-dotenv`, `SSSF_X=1` and
  `DATABASE_URL=db`.
- Write a driver script `tmp/probe.py` (a file, so `__main__` has `__file__`
  and `find_dotenv` walks up from the stamped `adws/adw_modules/utils.py` to
  `tmp/.env`, never to this repo's own `.env`):
  `sys.path.insert(0, "adws")`, import `utils`, `agent_cc`, `agent_pi`, and
  print JSON: `sorted(utils.DOTENV_KEYS)`, the key sets of
  `agent_cc.child_env()` and `agent_pi.child_env()`, and whether `APP_SECRET`
  is in `utils.operator_env()`.
- Run it with `sys.executable`, `cwd=tmp`, and an env of `os.environ` plus
  `APP_SECRET=from-shell` (already set before `utils` loads) and
  `SHELL_ONLY=1`, minus any `APP_SECRET`/`SSSF_X`/`DATABASE_URL` inherited.
- Assert: `APP_SECRET` in `DOTENV_KEYS`; `APP_SECRET` not in either agent env;
  `DATABASE_URL` not in either (no `agent_env` in the stamped `repo.yaml`);
  `SSSF_X` and `SHELL_ONLY` in both; `APP_SECRET` in `operator_env()` (what
  quality checks get, `quality.py` uses `operator_env()`).

**b. A launch through the stamped justfile's own recipe, with real just.**
`@pytest.mark.skipif(not (shutil.which("just") and shutil.which("uv")), ...)`.

- Reuse `stamped_repo`. `.env` sets `APP_SECRET=from-dotenv`; the launching
  env must NOT contain `APP_SECRET`, so `just`'s `set dotenv-load` is its only
  source.
- A stand-in `pi` script in `tmp_path/bin` (outside the repo), pointed to with
  `PI_PATH` in the launch env (not in `.env`): on `--list-models` print a
  header line and `google gemini-3.6-flash 1.0M` (the shipped default model;
  read `agent_pi._pi_catalog` for the format); on an agent call (argv has
  `-p`), write `json.dumps(sorted(os.environ))` to `$FAKE_PI_ENV` and exit 1.
  If another pre-spawn step in `agent_pi` needs answering, answer it in the
  stand-in; never change guard code for the test.
- Run `just prompt "say hi"` with `cwd=tmp`, `timeout=300`. Ignore its exit
  status (the stand-in fails the call on purpose).
- Assert the dump exists, holds `SSSF_ADW_ID` (it is the agent's env, not the
  model-list call's), and has no `APP_SECRET`.

**c. Deny-rule escaping.** Pure unit tests on `agent_cc.deny_rule` and
`_command`:

- `deny_rule("/abs/weird [x] dir/.env") == r"Read(//abs/weird\ \[x\]\ dir/.env)"`.
- `deny_rule("/a/*?!\\b/.env") == r"Read(//a/\*\?\!\\b/.env)"` — each of `*`,
  `?`, `!` and the backslash escaped once.
- An ordinary path is unchanged: `deny_rule("/home/u/repo/sub/.env.local") ==
  "Read(//home/u/repo/sub/.env.local)"`.
- `_command(request_with([p1, p2]))` with one weird and one ordinary path has
  exactly two `--disallowedTools` values, equal to `[deny_rule(p1),
  deny_rule(p2)]`.

Update `test_claude_code_denies_read_of_each_file_once_with_the_double_slash_form`
to compare against `deny_rule(f)` instead of `f"Read(/{f})"`, keeping its other
asserts.

## Docs the change makes out of date

- `.claude/skills/sssf/references/config.md`, `## Secret files` section
  (around lines 411–427):
  - The Claude Code bullet: one `--disallowedTools "Read(//<abs>)"` per file,
    with `[ ] * ? ! \` and space in the path escaped by a backslash, since
    Claude Code reads the rule as a gitignore-style pattern.
  - The `.env` keys paragraph: a key counts as from `.env` when `.env` names
    it, however it reached the environment, so a `just` recipe's
    `set dotenv-load` changes nothing. A shell export (direnv included) whose
    name `.env` doesn't set passes; one whose name `.env` also sets is held
    back too, since that name is a secret the repo keeps in `.env`. Quality
    checks, `worktree_setup` and git keep the full environment.
  - The "Also left open" sentence: drop "keys exported before the ADW starts";
    say instead "secrets exported in your shell that `.env` doesn't name".
  - Line ~507 (Auth): with `SSSF_CLAUDE_AUTH=api_key`, export
    `ANTHROPIC_API_KEY` in your shell and keep it out of `.env`, or list it
    under `agent_env`.
- `.claude/skills/sssf/templates/env.sample`: the `ANTHROPIC_API_KEY` comment
  ("export … in your shell or list it under `agent_env`") becomes "export it in
  your shell and leave it out of this file, or list it under `agent_env`".
- `.claude/skills/sssf/templates/repo.yaml`: the `agent_env` comment "Your
  shell's own exports and quality checks are not affected" becomes "Quality
  checks are not affected. A shell export passes unless .env names the same
  key."
- Docstrings in `utils.py` (`agent_env`, the `DOTENV_KEYS` comment) and
  `agent_cc.py` (module docstring, `deny_rule`) as above.
- No CHANGELOG in this repo. `docs/guide/customize.md`'s `agent_env` row still
  reads right; leave it. No `app_docs/` write-up describes this guard yet.

## Steps

1. Edit `utils.py` as in §1.
2. Edit `agent_cc.py` as in §2.
3. Add and update the tests in §3.
4. Update the docs listed above.
5. Run the verification below.

## Verification

From `.claude/skills/sssf`:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
```

Both exit 0. Test b runs (not skipped) on this machine, where `just` and `uv`
are on PATH; confirm with `-rs` that it isn't in the skip list. `grep -n
_SHELL_KEYS` over `.claude/skills/sssf/templates/adws/adw_modules/` finds
nothing.

Dependencies: test b needs `just` and `uv` on PATH (skips without them) and
`uv` able to resolve `adw_prompt.py`'s script dependencies from its cache or
the network.

## For the engineer

- This repo's own copy under `adws/adw_modules/` is protected and is not
  touched by this change (nor was it by bb1b840). After merge, run
  `install.py --update` here if you want the repo's own ADWs to carry the guard.

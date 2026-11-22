# Let an agent in a worktree load `adw_modules.utils`, and nobody else past an unreadable `.env`

## Problem

`.claude/skills/sssf/templates/adws/adw_modules/utils.py` runs this at import:

```python
load_dotenv()
DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
```

`find_dotenv()` walks up from the folder of `utils.py`. A run's worktree (`adws/adw_data/worktrees/<id>/`) has no `.env`, so the walk ends at the main checkout's `.env`. Under agentsh every open of a file named `.env` is denied, so the import stops with `PermissionError: [Errno 13] Permission denied`, and pytest stops at collection for every test file that imports `utils` (`tests/conftest.py` does). Runs 4e70661f and cd521f37 hit it, and so did this planner (see "Planner's findings").

Run 5e3cc5db fixed it by catching `PermissionError` in every process. That was a bypass: an agent under agentsh can make the main checkout's `.env` unreadable with an ACL set through `os.setxattr`, which agentsh doesn't trace, and the next orchestrator started without `just` then ran with an empty `DOTENV_KEYS` and handed agents every `.env`-named key its shell exported. That run was set aside. This plan uses its findings and none of its code.

## The decision

**The sign is `SSSF_AGENTSH=1` in the process's environment, read before any `.env` is loaded.** Only a process that carries it gets past a `.env` it can't open. It then loads nothing from the file and `DOTENV_KEYS` is `frozenset()`. Every other process re-raises the `PermissionError`, as on the baseline.

The walk does not change. No path is passed to `load_dotenv`, and the walk is not stopped at the worktree's root. So every process that can read the `.env` the walk finds reads the same file with the same python-dotenv calls as on the baseline and gets the same `DOTENV_KEYS` and the same `os.environ`, with or without the sign. That is item 2 of the request, met by not touching the readable path.

### Why `SSSF_AGENTSH` and not `SSSF_AGENT`

- `agentsh_guard.py` sets `SSSF_AGENTSH=1` on the wrapped command only, beside `JUST_NO_DOTENV=true`, after the orchestrator has built the agent's environment. It marks exactly the processes for which the denied open is expected and is agentsh's doing.
- `SSSF_AGENT` is on every agent, `local_guard: text` and `none` included. There nothing denies the open, so an unreadable `.env` is never the guard's doing: somebody took the file's permissions away. Those agents keep the baseline's stop. That keeps the change as small as the problem.
- `SSSF_AGENT` is also documented for a repo's own scripts (`config.md`, "A repo's own scripts can refuse a command"), so engineers export or test with it. `SSSF_AGENTSH` has one writer.
- A stronger sign was looked at and dropped: "the file's owner is this user and its mode grants the owner read, yet the open is denied", which no `chmod` or ACL can fake. The request's bar asks that a process with the sign import past a `.env` at mode `000`, so the sign can't depend on the mode.

### Why an agent can't put the sign on a process outside its wrap

The sign is an environment variable, so the question is who writes the environment of the next orchestrator, of the host's quality checks, and of the engineer's scripts.

1. **Through the `.env` that `utils` loads.** The sign is read into a module constant before `dotenv_values` and `load_dotenv` run, so no `.env` can set it for the process that loads it. And a `.env` that set anything was opened, so the walk's `.env` was readable and the sign was never consulted.
2. **Through `just`'s `set dotenv-load`.** `just` reads the `.env` beside the justfile before any recipe starts. If that file is the unreadable one, `just` stops with `I/O error at .env: Permission denied` (`docs/designs/agentsh-local-sandbox.md`) and no ADW starts. For `just` to hand the sign on while `utils` then meets an unreadable file, there must be two files: a readable `<root>/.env` naming `SSSF_AGENTSH=1`, and an unreadable `.env` earlier on the walk (`<root>/adws/.env` or `<root>/adws/adw_modules/.env`). An agent that can place the earlier file could place an empty readable one there instead and get the same empty `DOTENV_KEYS` on the baseline, with no sign at all. That is the open P1 row "A chain that starts in a run's worktree loads the `.env` the run's branch committed" in `docs/open.md`. The sign opens nothing that row doesn't already hold. An agent under agentsh can't create a file named `.env` anywhere (see findings), a VM agent's commits under `adws/` are refused, and an unreadable mode can't be committed.
3. **Through the justfile.** `justfile`, `sssf.just` and `local.just` are `protected_files`; a run that changes one is stopped. An agent that could rewrite a recipe runs code on the host without needing the sign.
4. **Through the 5e3cc5db bypass.** `os.setxattr` changes the main checkout's `.env`, not anybody's environment. The next orchestrator started with `uv run adws/adw_<chain>.py` takes its environment from the engineer's shell, which doesn't carry `SSSF_AGENTSH`, so the import stops with `PermissionError` as on the baseline. Started with `just`, it stops in `just` (item 2).
5. **Through the engineer's shell** (`~/.bashrc`, a `.envrc` the engineer allowed). Writing there is outside the worktree, which agentsh's file rules deny, and an agent that can write the engineer's shell profile already runs code as the engineer. This guard can't stop it at its level (see "Threat model").
6. **Through a child of the wrap.** A process an agent starts inherits the sign. It is inside the wrap and is the agent's own process, so it is meant to carry it.

An agent's process that drops the sign (`env -u SSSF_AGENTSH python ...`) stops at import with `PermissionError`. That is the baseline, and safe.

### Why the empty `DOTENV_KEYS` is safe in an agent's process

`DOTENV_KEYS` is read only by `agent_env`, which drops those names from an environment handed to an agent. An empty set drops nothing. That is safe when the environment holds no `.env` key to drop:

- The agent's environment was built by the host orchestrator with `agent_env(operator_env())` and the full `DOTENV_KEYS`, before the wrap added the sign. Every name `.env` sets is already gone, however it got into the orchestrator's environment, except `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, the names under `agent_env:` and pi's provider keys for a pi agent.
- Nothing in the wrap can put them back. The file rule that denies `utils` the open denies every other reader, whatever its name. `load_dotenv` sets nothing before it raises. `just` under the wrap runs with `JUST_NO_DOTENV=true`, so `set dotenv-load` loads no file; an agent that unsets it gets `just`'s own stop, `I/O error at .env: Permission denied`, before any recipe runs.
- So an agent this process starts (a nested ADW, a `just` recipe, a test calling `agent_cc.child_env()`) gets at most what its parent holds. The one difference from a full set is a kept key: a parent holding `DATABASE_URL` through `agent_env:`, or a pi agent's provider key, passes it to a child that a full set would have stripped. The parent holds that key already and can hand it over by any means, so nothing new is exposed.

A process with the sign and a `.env` it *can* open (the sign exported by hand on the host, or a wrap whose policy lets one through) takes the readable path and gets the full `DOTENV_KEYS`. The empty set exists only where the open was denied.

## Planner's findings

Checked in this worktree, under agentsh, on 2026-11-22:

- The planner's environment carries `SSSF_AGENTSH=1`, `SSSF_AGENT=planner` and `JUST_NO_DOTENV=true`.
- From `.claude/skills/sssf`, `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py -q -p no:cacheprovider` exits 2 at collection with `PermissionError: [Errno 13] Permission denied: '<main checkout>/.env'`. The `docs/open.md` row still holds.
- Creating a file named `.env` in a fresh temp folder is denied (`.env: Permission denied`). So a test that writes a `.env` can't pass under agentsh and carries `@pytest.mark.host_only`, as its neighbours do. The request's "it may run" does not hold for these tests; verify, on the host, is the first to run them.
- On a temp file named `x.env`, `os.setxattr(path, "system.posix_acl_access", acl)` with the three-entry ACL in step 2 went through, left the file at mode `0o0`, and the open then raised `PermissionError` (errno 13). The test's ACL bytes are those.
- `guard_builder`'s `writes` in `adws/adw_sssf_config/sssf.config.yaml` names `**` and `.claude/skills/sssf/`, so every file in the steps is the builder's to write.

## Threat model

The agent runs as the engineer's own user.

- **What the agent can do.** Under agentsh it runs any program, imports `utils` from its worktree, starts an ADW or calls `agent_env` itself, and sets or unsets any variable in its own processes. It can take away the permissions of a file it may not `chmod`, the main checkout's `.env` included, with `os.setxattr(path, "system.posix_acl_access", ...)`, because agentsh doesn't trace the xattr calls (`docs/open.md`). It can commit files outside the protected paths. Under `local_guard: text` or `none` only command text is checked, so a script can open, `chmod`, empty or replace the main checkout's `.env`.
- **What this change gives it.** An import that succeeds in its own wrapped processes where it failed. It gets no key: the file stays unreadable to it and its environment was stripped before the wrap started.
- **What it must not get.** A process outside its wrap that imports past an unreadable `.env`. That was run 5e3cc5db's hole, whose threat model gave `chmod` on the main checkout's `.env` only to `text` and `none` agents.
- **What this guard can't stop at its level.** The sign is an environment variable and `DOTENV_KEYS` is a list of names from a file the agent's user owns. An agent that writes the engineer's shell profile, or changes which `.env` the walk finds or what it says, decides both. That is true on the baseline (an empty `.env` earlier on the walk) and stays true. Making the `.env` unreadable still stops the next run at import: a denial of service, on the baseline too. `agent_env` also can't strip a key from a process that already holds it.

## Baseline

`main` at `3865035` ("Set guard run 5e3cc5db aside, record the xattr gap, and tighten its brief"). Every finding is judged against that commit. On it:

- Importing `adw_modules.utils` with an unreadable `.env` on the walk raises `PermissionError` in every process, with or without `SSSF_AGENTSH`.
- With a readable `.env`, from the checkout or from a worktree folder under it with no `.env` of its own, `DOTENV_KEYS` names every key in the file and `os.environ` gets each one it didn't already have.
- The first `.env` on the walk shadows the main checkout's for any process started below it.

## Bar

Must:

1. **Unreadable `.env`, agent's process.** In a subprocess with `SSSF_AGENTSH=1`, a temp repo's `.env` at mode `000`, the test not run as root: importing that repo's `adw_modules.utils` exits 0, `DOTENV_KEYS` is empty, and no key from the file is in `os.environ`. On the baseline the same import raises `PermissionError`.
2. **Unreadable `.env`, any other process.** The same import without the sign raises `PermissionError`, with the `.env` at mode `000` and with its permissions taken away by an ACL set through `os.setxattr` and no `chmod`. The same holds with `SSSF_AGENT` set and `SSSF_AGENTSH` unset, with `SSSF_AGENTSH` set to anything but `1`, and with a readable `.env` at the root naming `SSSF_AGENTSH=1` behind an unreadable `adws/.env`. With a `.env`-named key in the environment, the process builds no agent environment at all: it prints none and exits non-zero.
3. **Readable `.env`, unchanged.** In the same temp repo with the `.env` readable, from the checkout and from a worktree folder under it with no `.env` of its own, with and without the sign: `DOTENV_KEYS` names every key in the file and `os.environ` has them.
4. **Agents still lose `.env`'s keys.** Every test in `.claude/skills/sssf/tests/test_secret_files.py` passes, and with the readable `.env`, `agent_cc.child_env()` and `agent_pi.child_env()` drop every name the file sets except the ones kept today.
5. **Nothing else changes.** Every existing test passes on the host.

Must never, against the baseline:

- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't. This change adds no file write, no subprocess and no new path lookup.
- Let a process without `SSSF_AGENTSH=1` in its starting environment import past a `.env` it can't open.
- Read the sign after a `.env` has been loaded.
- Change `DOTENV_KEYS` or `os.environ` for a process that can read the `.env` the walk finds.
- Leave the file's keys in `os.environ` while `DOTENV_KEYS` doesn't name them. The names are read first, so a file that turns unreadable between the two reads leaves names known and values unloaded.
- Catch more than `PermissionError`. A malformed `.env` or any other error fails the import as on the baseline.
- Copy `.env` into a worktree, change agentsh's policy on `.env`, or change how `agent_env` picks the keys it keeps.

## Steps for the builder

### 1. `.claude/skills/sssf/templates/adws/adw_modules/utils.py`

Replace the two import-time lines and the comment above `DOTENV_KEYS` with this block. Change nothing else in the file; `agent_env` stays as it is.

```python
# True inside the agentsh wrap, which sets SSSF_AGENTSH=1 (agentsh_guard.py). Read here,
# before any .env loads, so no .env can set it for this process.
_IN_AGENTSH_WRAP = os.environ.get("SSSF_AGENTSH") == "1"

# Every key .env names, read from the file as dotenv_values does, not from what
# load_dotenv changed: a `just` recipe (`set dotenv-load`) or a shell export of
# the same name has already put the key in os.environ, and it is still .env's.
#
# agentsh denies an agent's processes the open of .env. Only there, an unreadable
# .env loads nothing and names no key: the agent's environment was stripped by
# agent_env before the wrap started, and nothing in the wrap can read the file to
# put a key back. Every other process stops on it, because an agent can take the
# file's permissions away, and an orchestrator with no names here would hand
# agents every .env key its shell exports. The names are read first, so .env's
# keys are never in os.environ without being in DOTENV_KEYS.
DOTENV_KEYS: frozenset[str] = frozenset()
try:
    DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
    load_dotenv()
except PermissionError:
    if not _IN_AGENTSH_WRAP:
        raise
```

Notes:

- `find_dotenv()` and `load_dotenv()` stay bare calls at module level with no arguments. `find_dotenv` picks its start folder from the calling frame, so don't move them into a helper in another file.
- The order swap (`dotenv_values` before `load_dotenv`) changes nothing for a readable file: `dotenv_values` doesn't write `os.environ`.
- The test is `== "1"`, the exact value the wrap sets. Not truthiness.

### 2. `.claude/skills/sssf/tests/test_secret_files.py`

Add the tests below after `test_a_key_the_environment_already_had_is_still_held_back_from_agents`. Change no existing test, and don't touch `.claude/skills/sssf/tests/conftest.py`. Every new test writes a `.env`, so each carries `@pytest.mark.host_only` under the comment line its neighbours use (`# host_only, the error names the sandbox: writes or links a file that agentsh denies.`). All paths stay under `tmp_path`. They build the repo with the existing `stamped_repo`, which installs the files SSSF ships; no test-only config.

Two helpers:

- `dotenv_repo(tmp_path)`: `stamped_repo(tmp_path)`, write `.env` with `APP_SECRET=from-dotenv\nSSSF_X=1\nDATABASE_URL=db\n`, write `probe.py` with `PROBE`, return `tmp_path`.
- `run_probe(cwd, sign=None, **extra)`: copy `os.environ` without `APP_SECRET`, `SSSF_X`, `DATABASE_URL`, `SSSF_AGENTSH` and `SSSF_AGENT` (the suite may itself run inside an agent), add `SHELL_ONLY=1`, add `SSSF_AGENTSH=sign` when `sign` is not `None`, add `extra`, run `[sys.executable, "probe.py"]` in `cwd` with `capture_output=True, text=True, check=False`, return the result. A `got(done)` one-liner parses the last stdout line as JSON, as the neighbour does.
- `no_acl(path)`: sets the ACL that grants nothing, with no `chmod`:
  ```python
  acl = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", tag, 0, 0xFFFFFFFF) for tag in (0x01, 0x04, 0x20))
  os.setxattr(path, "system.posix_acl_access", acl)
  ```
  (version 2, then `USER_OBJ`, `GROUP_OBJ`, `OTHER`, each with no permission). Call `pytest.skip` when `os` has no `setxattr`, when the call raises `OSError` (a filesystem without ACLs), or when the file still opens afterwards. Add `import struct` to the file's imports.

Every test that takes permissions away is also `@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")` and restores `0o600` in a `finally`, so pytest can clear `tmp_path`.

Tests:

1. **`test_an_agents_process_under_agentsh_imports_utils_past_an_unreadable_env_file`**. `.env` at `chmod(0o000)`, `run_probe(repo, sign="1")`. Assert return code 0 (message `done.stderr`), `got["dotenv"] == []`, `APP_SECRET` and `DATABASE_URL` in none of `operator`, `cc`, `pi`, and `SHELL_ONLY` in `cc` and `pi` (the probe built real agent environments). Fails on the baseline with `PermissionError` in stderr.

2. **`test_any_other_process_stops_on_an_unreadable_env_file`**, parametrized over how the file is made unreadable (`"chmod"`: `chmod(0o000)`; `"acl"`: `no_acl`) and over the environment: no sign; `sign="0"`; `sign=""`; `sign="true"`; no sign with `SSSF_AGENT="builder"`. Each run also passes `APP_SECRET="from-shell"`. Assert the return code is not 0, `"PermissionError" in done.stderr`, and `done.stdout.strip() == ""`: no agent environment was built, so none holds `APP_SECRET`. The no-sign rows pass on the baseline; they pin what 5e3cc5db broke.

3. **`test_the_acl_bypass_gets_past_only_with_the_sign`** is not needed as its own test: add the `"acl"` way to test 1 by parametrizing it too (`"chmod"`, `"acl"`), so the ACL case is shown to be the same failure the sign lets through and nothing else.

4. **`test_an_env_file_cannot_give_a_process_the_sign`**. Keep the root `.env` readable and append `SSSF_AGENTSH=1\n` to it; write `adws/.env` with `X=1\n` and `chmod(0o000)` it (restore in `finally`). `run_probe(repo)` with no sign: return code not 0, `PermissionError` in stderr, empty stdout.

5. **`test_a_readable_env_file_gives_the_same_keys_from_the_checkout_and_a_worktree_folder`**, parametrized over `sign` in (`None`, `"1"`). Make `w1 = tmp_path / "adws/adw_data/worktrees/w1"`, `shutil.copytree` the stamped repo's `adws/adw_modules` to `w1/adws/adw_modules`, write `probe.py` into `w1`; it has no `.env` (a plain folder is enough: the walk reads folders, not git). Run the probe with `cwd=tmp_path` and `cwd=w1`. For each: return code 0, `got["dotenv"] == ["APP_SECRET", "DATABASE_URL", "SSSF_X"]`, all three names in `operator`, and for `cc` and `pi`: `APP_SECRET` and `DATABASE_URL` absent, `SSSF_X` and `SHELL_ONLY` present. Passes on the baseline too; it pins item 2 of the request.

If `copytree` runs before `chmod`, nothing unreadable is copied. Keep that order.

### 3. `.claude/skills/sssf/references/config.md`

In the paragraph that starts "`.env` keys don't reach agents." (near line 955), after the sentence ending "so a `just` recipe's `set dotenv-load` changes nothing.", add:

> A `.env` that can't be read stops SSSF at the start with `PermissionError`, since it could then name no key to hold back. The one exception is an agent's own process under `local_guard: agentsh`, such as its test run: agentsh denies it the file, its environment was stripped before it started, so it loads nothing and goes on.

Keep the page's voice. Touch no other section.

### 4. `docs/open.md`

Delete the whole table row that starts "Under agentsh, an agent in a worktree can't run a test file that imports `adw_modules.utils`." (near line 58). Leave the rows "agentsh doesn't trace the xattr calls" and "A chain that starts in a run's worktree loads the `.env` the run's branch committed" as they are: both stay open, and the first one's "On main this is a denial of service ... the next run stops at import" stays true after this change.

### 5. Docs this change makes out of date

- The comment in `utils.py` (step 1) and `config.md` (step 3) are the only texts that describe the load. `agent_env`'s and `mark_agent`'s docstrings stay true.
- The repo keeps no CHANGELOG. README and the cookbooks don't describe the load.
- `docs/designs/agentsh-local-sandbox.md` records earlier runs' probes that hit the denied `.env`. It is a record; leave it.
- No `app_docs/` write-up describes the import. Nothing for the documenter to flag.

## Verification

The builder runs under agentsh. Before step 1, no test file that imports `utils` collects in the worktree. After step 1 it does, because the builder's own processes carry `SSSF_AGENTSH=1`; that is the first proof. Don't copy a `.env` into the worktree and don't patch `find_dotenv` in a test run.

Builder, from `.claude/skills/sssf`, after steps 1 and 2:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

Exit status 0 means the file collects and the tests an agent can run pass. The new tests are `host_only` and deselected; write in the build's notes that the builder could not run them and that verify is the first to. Don't run the whole suite under agentsh (`docs/open.md`: the after-call event read takes many minutes).

Host (SSSF's check after the build, and verify), from the repo root:

```
cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py -q -p no:cacheprovider
```

Exit 0 means bar items 1 to 4 hold. The ACL rows skip on a filesystem without POSIX ACLs; the reviewer checks they ran, not skipped (`-rs`). Verify's whole-suite run from `.claude/skills/sssf` (same `uv run` line, `pytest tests`) covers item 5.

Baseline proof for the reviewer: with `utils.py` from `3865035`, new test 1 fails with `PermissionError` in the probe's stderr, and tests 2, 4 and 5 pass. With run 5e3cc5db's `utils.py`, tests 2 and 4 fail.

## Out of scope

Holes this change leaves open. A reviewer files them as old holes.

- **An earlier `.env` on the walk shadows the main checkout's.** A chain started in a run's worktree takes `DOTENV_KEYS` from the first `.env` the walk finds, and an empty one gives an empty `DOTENV_KEYS` on the baseline. With this change, a readable `.env` beside the justfile naming `SSSF_AGENTSH=1` plus an unreadable one earlier on the walk gives the same result through `just`; whoever can place the second file can place an empty one. Open in `docs/open.md` (P1, needs decision).
- **The xattr gap itself.** An agent under agentsh can still make the main checkout's `.env` unreadable, and the next run stops at import or in `just`. A denial of service, as on the baseline. The fix is in agentsh (`docs/open.md`).
- **A sign put in the engineer's own shell.** An engineer who exports `SSSF_AGENTSH=1`, or an agent that can write the engineer's shell profile, gets an orchestrator that imports past an unreadable `.env`. The first is the engineer's choice; the second already runs code as the engineer.
- **A parent agent passes its kept keys to a child it starts.** `agent_env:` names and pi's provider keys in a wrapped agent's environment reach an agent that process starts. The parent holds them already.
- **`local_guard: text` and `none`.** A script can open, empty or replace `.env` there. Their agents get no exception from this change.
- **`find_dotenv` walking past the main checkout** when a folder on the way can't be read. Unchanged.
- agentsh's policy on `.env` and other secret files, the VM modes (`openshell`, `vercel`, `e2b`), and which keys `agent_env` keeps.

## For the engineer

After the merge:

1. Update the hub's own install with `install.py --update` and commit it. `adws/adw_modules/utils.py` is under `adws/`, which no agent may write, so the hub's installed copy keeps the old import until then.
2. Proof: the next guard run's planner, under agentsh in its worktree, collects `tests/test_git_dir_guard.py` (pytest exits 0 or 1, not 2 with a collection error).

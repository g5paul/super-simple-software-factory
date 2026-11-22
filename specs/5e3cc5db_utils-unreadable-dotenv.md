# Let an agent in a worktree load `adw_modules.utils`

## Problem

`.claude/skills/sssf/templates/adws/adw_modules/utils.py` runs this at import:

```python
load_dotenv()
DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
```

`find_dotenv()` walks up from the folder of `utils.py`. A run's worktree (`adws/adw_data/worktrees/<id>/`) has no `.env`, so the walk ends at the main checkout's `.env`. Under agentsh the agent may `stat` that file but not open it, so the import stops with `PermissionError: [Errno 13] Permission denied`. `tests/conftest.py` imports `utils`, so pytest stops at collection for most of the skill suite. Runs 4e70661f and cd521f37 hit it, and so did this planner (see "Planner's findings").

## The decision

Importing `utils` never fails because the `.env` the walk finds can't be opened. In that case the import loads nothing from it and `DOTENV_KEYS` is `frozenset()`.

The walk itself does not change. No path is passed to `load_dotenv`, and the walk is not stopped at the worktree's root. So every process that can read the `.env` runs the same two python-dotenv calls on the same file as on the baseline, and gets the same `DOTENV_KEYS` and the same `os.environ`. That is item 2 of the request, met by not touching the readable path. (The `docs/open.md` row suggests keeping the search inside the worktree. This plan does not do that, because it would take the main checkout's keys away from an orchestrator and from quality checks started in a worktree on the host.)

### Which processes get the empty `DOTENV_KEYS`

1. **An agent's own processes under agentsh** (`local_guard: agentsh`), when they import the worktree's or the checkout's `adw_modules.utils`: pytest through `tests/conftest.py`, a probe script, or an ADW the agent starts itself. agentsh's file rules deny the open of `.env` for every process in the wrap, whatever its name.
2. **Any host process whose user can't open the `.env`**: a `.env` with mode `000`, one owned by another user, or a `.env` in the worktree that is a symlink to a file the user can't open. On the baseline every one of these stops at import with `PermissionError`.

The VM modes are not in this list. A VM's clone has no `.env`, so the walk finds none and the import already works.

### Why the empty set is safe for an agent's process under agentsh

`DOTENV_KEYS` is only read by `agent_env`, which drops those names from the environment handed to an agent. An empty set drops nothing. That is safe when the environment holds no `.env` key to drop, and under agentsh it doesn't:

- The agent's environment was built by the host orchestrator with `agent_env(operator_env())` and the full `DOTENV_KEYS`. Every name `.env` sets is already gone, however it got into the orchestrator's environment (`load_dotenv`, `just`'s `set dotenv-load`, a shell export), except `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, the names under `agent_env:` and pi's provider keys for a pi agent.
- Nothing inside the wrap can put them back. The same file rule that makes `DOTENV_KEYS` empty denies every other reader. `load_dotenv` reads nothing: the planner checked that python-dotenv raises `PermissionError` at the open and sets no variable first. `just` with `set dotenv-load` fails at the file: `docs/designs/agentsh-local-sandbox.md` records `I/O error at .env: Permission denied` for `just` and for a renamed copy of it, and with `--no-dotenv` the recipe runs with no keys.
- So an agent that the wrapped process starts (a nested ADW, or a test that calls `agent_cc.child_env()`) gets at most what its parent already holds. The one difference from a full `DOTENV_KEYS` is a kept key: a parent that keeps `DATABASE_URL` through `agent_env:`, or a pi agent's provider key, passes it to a child that a full set would have stripped. The parent already holds that key and can hand it to the child by any other means, so nothing new is exposed.

### What a host process with an unreadable `.env` gets

It loads nothing, so no key comes from the file. A name the engineer also exported in the shell stays in `os.environ`, and with `DOTENV_KEYS` empty an agent started by that process gets it. On the baseline that process could not start at all. This is the one place the change is weaker than the baseline, and it is named under "Out of scope" with the reason it is accepted: the baseline has the same hole with a readable file (a worktree `.env` that names no keys gives a chain started there an empty `DOTENV_KEYS`, `docs/open.md`, "A chain that starts in a run's worktree loads the `.env` the run's branch committed").

`just` still fails loudly on an unreadable `.env` under `set dotenv-load`, so a launch through a `just` recipe stops before any ADW runs, as on the baseline.

## Planner's findings

Checked in this worktree, under agentsh, on 2026-11-21:

- `uv run ... pytest tests/test_secret_files.py` from `.claude/skills/sssf` exits 2 with `PermissionError: [Errno 13] Permission denied` at collection. The row in `docs/open.md` still holds.
- agentsh denies creating a file named `.env` in a temp folder too (`cp: cannot create regular file '.env': Permission denied`). So a test that writes a `.env` can't pass under agentsh and must carry `@pytest.mark.host_only`, as the two tests beside it already do. The request's "it may run" does not hold for these tests.
- python-dotenv, on a file with mode `000` (named `x.env`, since `.env` can't be made here): `dotenv_values(path)` and `load_dotenv(path)` both raise `PermissionError`, and `load_dotenv` sets nothing in `os.environ` before it raises.
- `guard_builder`'s `writes` in `adws/adw_sssf_config/sssf.config.yaml` names `**` and `.claude/skills/sssf/`, so every file below is the builder's to write.

## Threat model

The agent runs as the engineer's own user. What matters here:

- **What the agent can do.** Under agentsh it can run any program, import `utils` from its worktree, and start an ADW or call `agent_env` itself. It can commit any file outside `adws/`, a `.env` or a symlink named `.env` included; that file sits in the run's worktree, where a later host chain's walk finds it first. Under `local_guard: text` or `none` it can also open, `chmod` or replace the main checkout's `.env` from a script, since only command text is checked.
- **What this change gives it.** An import that succeeds where it failed. It gets no key: the file is still unreadable to it, and its environment was stripped before it started.
- **What this guard can't stop at its level.** `DOTENV_KEYS` is a list of names read from a file the agent's user owns. An agent that can change which `.env` the walk finds, or what it says, decides the list. That is true on the baseline (a committed worktree `.env`) and stays true. `agent_env` also can't strip a key from a process that already holds it, so a parent agent can always pass its own keys to a child.
- **Who is not an attacker here.** The engineer who makes their own `.env` unreadable. They get an orchestrator that runs with no `.env` keys instead of one that stops.

## Baseline

`main` at `926d63d` ("Add brief for letting a worktree agent load adw_modules.utils"). Every finding is judged against that commit. On it:

- Importing `adw_modules.utils` with an unreadable `.env` on the walk raises `PermissionError`.
- With a readable `.env`, from the checkout or from a worktree folder under it with no `.env` of its own, `DOTENV_KEYS` names every key in the file and `os.environ` gets each one it didn't already have.
- A worktree `.env` shadows the main checkout's for any process started in that worktree.

## Bar

Must:

1. **Unreadable `.env`.** In a subprocess, with a temp repo's `.env` at mode `000` and the test not run as root, importing that repo's `adw_modules.utils` exits 0, `DOTENV_KEYS` is empty, and no key from the file is in `os.environ`. On the baseline the same import raises `PermissionError`.
2. **Readable `.env`, unchanged.** In the same temp repo with the `.env` readable, from the checkout and from a worktree folder under it with no `.env` of its own, `DOTENV_KEYS` names every key in the file and `os.environ` has them.
3. **Agents still lose `.env`'s keys.** Every test in `.claude/skills/sssf/tests/test_secret_files.py` passes. With the readable `.env`, `agent_cc.child_env()` and `agent_pi.child_env()` drop every name the file sets except the ones kept today (`SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`, `allowed`, pi's provider keys), from the checkout and from the worktree folder.
4. **Nothing else changes.** Every existing test passes on the host.

Must never, against the baseline:

- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't. This change adds no file write, no subprocess and no new path lookup.
- Change `DOTENV_KEYS` or `os.environ` for a process that can read the `.env` the walk finds.
- Leave a state where the file's keys are in `os.environ` and `DOTENV_KEYS` doesn't name them. The names are read before the values are loaded (see step 1), so a file that turns unreadable between the two reads leaves the names known and the values unloaded, never the reverse.
- Catch more than `PermissionError`. A malformed `.env`, a missing `dotenv` package or any other error fails the import as on the baseline.
- Copy `.env` into a worktree, change agentsh's policy on `.env`, or change which keys `agent_env` keeps.

## Steps for the builder

### 1. `.claude/skills/sssf/templates/adws/adw_modules/utils.py`

Replace the two import-time lines (and the comment above `DOTENV_KEYS`) with one guarded block. Keep the existing comment's point about `just` and shell exports, and add why the block may be skipped:

```python
# Every key .env names, read from the file as dotenv_values does, not from what
# load_dotenv changed: a `just` recipe (`set dotenv-load`) or a shell export of
# the same name has already put the key in os.environ, and it is still .env's.
#
# A .env this process can't open loads nothing and names no key. That is an
# agent's own process under agentsh, which denies the open: its environment was
# already stripped by agent_env when the agent started, and nothing in the wrap
# can read the file to put a key back. The names are read first, so the file's
# keys are never in os.environ without being in DOTENV_KEYS.
DOTENV_KEYS: frozenset[str] = frozenset()
try:
    DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
    load_dotenv()
except PermissionError:
    pass
```

Notes:

- `find_dotenv()` and `load_dotenv()` stay bare calls at module level, with no arguments. `find_dotenv` picks its start folder from the calling frame, so don't move them into a helper in another file.
- The order swap (`dotenv_values` before `load_dotenv`) changes nothing for a readable file: `dotenv_values` doesn't write `os.environ`, and the key names don't depend on it.
- Change nothing else in the file. `agent_env` stays as it is.

### 2. `.claude/skills/sssf/tests/test_secret_files.py`

Add two tests after `test_a_key_the_environment_already_had_is_still_held_back_from_agents`. Both reuse `stamped_repo` and `PROBE`, write a `.env`, and so carry `@pytest.mark.host_only` with the comment line the neighbours use (`# host_only, the error names the sandbox: writes or links a file that agentsh denies.`). All paths stay under `tmp_path`.

Shared setup for both: `stamped_repo(tmp_path)`, `.env` holding `APP_SECRET=from-dotenv\nSSSF_X=1\nDATABASE_URL=db\n`, `probe.py` holding `PROBE`, and a subprocess `env` copied from `os.environ` without `APP_SECRET`, `SSSF_X` and `DATABASE_URL`, plus `SHELL_ONLY=1`. Run `[sys.executable, "probe.py"]` and parse the last stdout line as JSON, as the neighbour does. Factor the run into a small helper if it reads better.

**`test_utils_imports_where_the_env_file_cannot_be_read`**, also `@pytest.mark.skipif(os.geteuid() == 0, reason="root can read a mode-000 file")`:

- `chmod(0o000)` the `.env`; restore `0o600` in a `finally` so pytest can clear `tmp_path`.
- Assert return code 0 (with `done.stderr` as the message), `got["dotenv"] == []`, and that `APP_SECRET` and `DATABASE_URL` are in none of `got["operator"]`, `got["cc"]`, `got["pi"]`.
- Assert `SHELL_ONLY` is in `got["cc"]` and `got["pi"]`, so the probe is known to have built real agent environments.

On the baseline this test fails at the return code with `PermissionError` in stderr. The builder can't run it (it is `host_only`), so say so in the build's notes.

**`test_a_worktree_folder_with_no_env_file_gets_the_checkouts_keys`**:

- Make the worktree folder `tmp_path / "adws/adw_data/worktrees/w1"`, copy the stamped repo's `adws/adw_modules` to `w1/adws/adw_modules` with `shutil.copytree`, and write `probe.py` into `w1`. It has no `.env`. (A plain folder is enough: the walk reads folders, not git.)
- Run the probe twice, `cwd=tmp_path` and `cwd=w1`. For each: `got["dotenv"] == ["APP_SECRET", "DATABASE_URL", "SSSF_X"]`; all three names in `got["operator"]`; for `cc` and `pi`, `APP_SECRET` and `DATABASE_URL` absent, `SSSF_X` and `SHELL_ONLY` present.

This test passes on the baseline too. It pins item 2 of the request.

Don't change any existing test in the file, and don't change `.claude/skills/sssf/tests/conftest.py`: its `no_dotenv_keys` fixture iterates `utils.DOTENV_KEYS`, which stays a `frozenset`.

### 3. `.claude/skills/sssf/references/config.md`

In the paragraph that starts "`.env` keys don't reach agents." (near line 955), add after the sentence ending "so a `just` recipe's `set dotenv-load` changes nothing.":

> A process that can't open the `.env` loads nothing from it and counts no key as from `.env`. That is an agent's own process under agentsh, for example its test run: the agent's environment was stripped when it started, and nothing in the wrap can read the file. An orchestrator on this machine whose user can't open the `.env` also runs, with no key from the file, and passes agents any same-named key your shell exports.

Keep the wording plain, in the page's voice. Don't touch other sections.

### 4. `docs/open.md`

Delete the whole table row that starts "Under agentsh, an agent in a worktree can't run a test file that imports `adw_modules.utils`." (near line 58). Leave the row "A chain that starts in a run's worktree loads the `.env` the run's branch committed" as it is; this change doesn't close it.

### 5. Docs this change makes out of date

- The comment in `utils.py` (step 1) and `config.md` (step 3) are the only texts that describe the load. `agent_env`'s docstring stays true.
- The repo keeps no CHANGELOG. README and the cookbooks don't mention the load.
- `docs/designs/agentsh-local-sandbox.md` mentions a reviewer's probe that hit the denied `.env` read. It is a record of that run; leave it.
- No `app_docs/` write-up describes the import. Nothing for the documenter to flag.

## Verification

The builder runs under agentsh. Until this change is in the worktree's `utils.py`, no test file that imports `utils` collects there; after step 1 it does, which is itself the first proof. Don't copy a `.env` into the worktree and don't patch `find_dotenv` in a test run.

Builder, from `.claude/skills/sssf`, after step 1:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py -m 'not pushes and not host_only'
```

It must collect (exit status 0, not 2). The two new tests are `host_only` and are deselected here; write in the build's notes that verify is the first to run them. Don't run the whole suite under agentsh (`docs/open.md`: the after-call event read takes many minutes).

Host (SSSF's check after the build, and verify), from the repo root:

```
cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_secret_files.py -q
```

Exit 0 means bar items 1 to 3 hold. Verify's whole-suite run from `.claude/skills/sssf` (same `uv run` line, `pytest tests`) covers item 4.

Baseline proof for the reviewer: with `utils.py` from `926d63d`, `test_utils_imports_where_the_env_file_cannot_be_read` fails with `PermissionError` in the probe's stderr, and `test_a_worktree_folder_with_no_env_file_gets_the_checkouts_keys` passes.

## Out of scope

Holes this change leaves open. A reviewer files them as old holes.

- **A worktree `.env` shadows the main checkout's.** A chain started in a run's worktree takes `DOTENV_KEYS` and its loaded keys from the `.env` the run's branch committed. An empty committed `.env` already gives that chain an empty `DOTENV_KEYS` on the baseline. Open in `docs/open.md` (P1, needs decision).
- **A host orchestrator with an unreadable `.env` and a same-named shell export.** On the baseline it stops at import. Now it runs, and an agent it starts gets the exported key. Reaching it needs the engineer's own `.env` to be unreadable to them, or a committed `.env` symlink to a file they can't open plus a chain started in that worktree without `just`; the row above gives the same result with a plain empty file. Accepted, and named in `config.md` by step 3. A fix that refuses to start agents when the `.env` was unreadable would change `agent_env` or the launch, which the request rules out.
- **A parent agent passes its kept keys to a child it starts.** `agent_env:` names and pi's provider keys in a wrapped agent's environment reach an agent that process starts. The parent holds them already.
- **`find_dotenv` walking past the main checkout.** If a `.env` can't be `stat`ed, the walk goes on up and may load one from a parent folder. Unchanged from the baseline.
- **`local_guard: text` and `none`.** A script can open `.env` there; only command text is checked (`config.md`).
- agentsh's policy on `.env` and other secret files, the VM modes (`openshell`, `vercel`, `e2b`), and which keys `agent_env` keeps.

## For the engineer

After the merge:

1. Update the hub's own install with `install.py --update` and commit it. `adws/adw_modules/utils.py` is under `adws/`, which no agent may write, so the hub's installed copy keeps the old import until then.
2. Proof: the next guard run's planner, under agentsh in its worktree, collects `tests/test_git_dir_guard.py` (exit status 0 or 1 from pytest, not a collection error).

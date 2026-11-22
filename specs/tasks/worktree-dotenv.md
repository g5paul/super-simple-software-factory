# Let an agent in a worktree load adw_modules.utils

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `templates/adws/adw_modules/utils.py`, which isn't on the guard list. It still goes through the chain, because `utils.DOTENV_KEYS` is the list `agent_env` uses to keep `.env`'s keys out of every agent's environment, and that behavior's tests are in `tests/test_secret_files.py`, which only `guard_builder` may write.

## What broke

`utils.py` runs two things at import:

```python
load_dotenv()
DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
```

`find_dotenv()` walks up from the folder of the file that called it. In a run's worktree, `adws/adw_data/worktrees/<id>/` has no `.env` (it's gitignored), so the walk goes on up to the main checkout's `.env`. Under agentsh, an agent can't read that file, which is right, since it holds keys. So any agent process that imports the worktree's `adw_modules.utils` stops with `PermissionError: [Errno 13] Permission denied` on the main checkout's `.env`, and pytest stops at collection. `tests/conftest.py` imports `utils`, so this hits most of the skill suite.

Runs 4e70661f and cd521f37 (2026-11-21) both hit it. In cd521f37 the planner hit it 13 times and the bypass reviewer 4 times, so neither could run the tests it was judging, and the host's verify was the first to run them. `docs/open.md` has the row ("Under agentsh, an agent in a worktree can't run a test file that imports `adw_modules.utils`").

## The change

1. **Importing `utils` works where `.env` can't be read.** A process that can't read the `.env` the walk finds imports `utils` without an error, loads nothing from it, and gets a `DOTENV_KEYS` the spec defines. Say in the spec which processes this is (agent processes under agentsh, and anything else you find), and why what they get is safe: an agent's environment was already stripped by `agent_env` when it started, but check that a process with an empty `DOTENV_KEYS` can't hand `.env` keys to an agent it starts, for example through a `just` recipe with `set dotenv-load`.
2. **Every process that reads `.env` today gets the same keys.** The orchestrator, from the main checkout or from a worktree on the host, and the host's quality checks in a worktree, keep the `DOTENV_KEYS` and the `os.environ` they get on `main`. A fix that stops the walk at the worktree's root changes that for a worktree on the host, so if the spec picks one, it says what each of those processes gets instead and why that is still complete.
3. **Docs.** Delete the `docs/open.md` row named above. If `references/config.md` says how `.env`'s keys are kept from agents, add a sentence on a `.env` that can't be read.

Out of reach: no `.env` copy into the worktree, no change to agentsh's policy on `.env`, and no change to how `agent_env` picks the keys it keeps.

## The bar

- **Unreadable `.env`.** In a subprocess, with a temp repo's `.env` made unreadable (`chmod 000`, the test run not as root), importing that repo's `adw_modules.utils` succeeds, and `DOTENV_KEYS` is what the spec says. On `main` the same import raises `PermissionError`.
- **Readable `.env`, unchanged.** In the same temp repo with the `.env` readable, both from the checkout and from a worktree folder under it with no `.env` of its own, `DOTENV_KEYS` names every key in the `.env` and `os.environ` gets them, as on `main`.
- **Agents still lose `.env`'s keys.** Every test in `tests/test_secret_files.py` passes, and `agent_env` with the readable `.env` drops every key it names except the ones it keeps today.
- **Nothing else changes.** Every existing test passes on the host.

## After the merge, the engineer's proof

Update the hub's own install (`install.py --update`) and commit it. The next guard run's planner, under agentsh in its worktree, can collect `tests/test_git_dir_guard.py`.

## Running the tests

The planner and the builder run under agentsh, and until this lands they can't collect a test file that imports `adw_modules.utils` from the worktree. Don't work around that (no `.env` copy, no patched `find_dotenv` in the test run); say so in the build's notes, and verify, which runs on the host, judges the tests. A new test that imports `utils` in a subprocess from a temp repo doesn't walk up to the main checkout's `.env`, so it may run. Otherwise run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`).

## Out of scope

- agentsh's policy on `.env` and other secret files.
- The VM modes (`openshell`, `vercel`, `e2b`).
- Which keys `agent_env` keeps (`SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN`).

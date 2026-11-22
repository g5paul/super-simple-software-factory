# Let an agent in a worktree load adw_modules.utils

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `templates/adws/adw_modules/utils.py`, which isn't on the guard list. It still goes through the chain, because `utils.DOTENV_KEYS` is the list `agent_env` uses to keep `.env`'s keys out of every agent's environment, and that behavior's tests are in `tests/test_secret_files.py`, which only `guard_builder` may write.

A first run, 5e3cc5db (2026-11-22), passed the chain and was set aside unmerged, because the bypass review found a bypass. Its fix caught `PermissionError` in every process: a `.env` nobody could read loaded nothing and gave an empty `DOTENV_KEYS`. Under agentsh, an agent can make the main checkout's `.env` unreadable without `chmod`, which agentsh refuses, by setting an ACL that grants nothing with `os.setxattr(path, "system.posix_acl_access", ...)`. agentsh doesn't trace the xattr calls; the engineer confirmed it under the hub's policy on 2026-11-22, on a decoy `.env` (see `docs/open.md`, "agentsh doesn't trace the xattr calls"). The next orchestrator started without `just` (`uv run adws/adw_<chain>.py`) then got an empty `DOTENV_KEYS` and handed agents every key in its environment that `.env` names, for example keys direnv exported. On `main` that import stops with `PermissionError` and no agent starts. Its spec is `specs/5e3cc5db_utils-unreadable-dotenv.md`. Its threat model gave `chmod` on the main checkout's `.env` only to `text` and `none` agents, which is the gap. The chain's own review passed it; the bypass came from the separate review before the merge. Use the spec for what it found, but don't start from its code.

## What broke

`utils.py` runs two things at import:

```python
load_dotenv()
DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))
```

`find_dotenv()` walks up from the folder of the file that called it. In a run's worktree, `adws/adw_data/worktrees/<id>/` has no `.env` (it's gitignored), so the walk goes on up to the main checkout's `.env`. Under agentsh, an agent can't read that file, which is right, since it holds keys. So any agent process that imports the worktree's `adw_modules.utils` stops with `PermissionError: [Errno 13] Permission denied` on the main checkout's `.env`, and pytest stops at collection. `tests/conftest.py` imports `utils`, so this hits most of the skill suite.

Runs 4e70661f and cd521f37 (2026-11-21) both hit it. In cd521f37 the planner hit it 13 times and the bypass reviewer 4 times, so neither could run the tests it was judging, and the host's verify was the first to run them. `docs/open.md` has the row ("Under agentsh, an agent in a worktree can't run a test file that imports `adw_modules.utils`").

## The change

1. **Only an agent's own process gets past an unreadable `.env`.** A process inside an agent's wrap that can't read the `.env` the walk finds imports `utils` without an error, loads nothing from it, and gets a `DOTENV_KEYS` the spec defines. Every other process, the orchestrator, the host's quality checks and any script the engineer runs, stops on an unreadable `.env` as on `main`. The spec says how `utils` tells an agent's process apart (SSSF sets `SSSF_AGENT` on every agent, and `SSSF_AGENTSH=1` inside the agentsh wrap; pick one, or something better, and say why), and why an agent can't make a process outside its wrap carry that sign, for example through a `.env` or a justfile the next run loads. An agent's process that drops the sign stops at import, which is safe. Also say why an agent's process with an empty `DOTENV_KEYS` can't hand `.env` keys to an agent it starts, for example through a `just` recipe with `set dotenv-load`.
2. **Every process that reads `.env` today gets the same keys.** The orchestrator, from the main checkout or from a worktree on the host, and the host's quality checks in a worktree, keep the `DOTENV_KEYS` and the `os.environ` they get on `main`. A fix that stops the walk at the worktree's root changes that for a worktree on the host, so if the spec picks one, it says what each of those processes gets instead and why that is still complete.
3. **Docs.** Delete the `docs/open.md` row named above. If `references/config.md` says how `.env`'s keys are kept from agents, add a sentence on a `.env` that can't be read.

Out of reach: no `.env` copy into the worktree, no change to agentsh's policy on `.env`, and no change to how `agent_env` picks the keys it keeps.

## The bar

- **Unreadable `.env`, agent's process.** In a subprocess carrying the spec's sign, with a temp repo's `.env` made unreadable (`chmod 000`, the test run not as root), importing that repo's `adw_modules.utils` succeeds, and `DOTENV_KEYS` is what the spec says. On `main` the same import raises `PermissionError`.
- **Unreadable `.env`, any other process.** The same import without the sign raises `PermissionError`, as on `main`, both with the `.env` at mode 000 and with its permissions taken away by an ACL set through `os.setxattr` with no `chmod` (the 5e3cc5db bypass). With a key of a `.env` name in the environment, no agent environment built by `agent_env` can come out holding it.
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

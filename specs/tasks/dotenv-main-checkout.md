# Load only the main checkout's .env, and refuse an agent's commit of one

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `utils.py`, the `justfile` template, and `permissions.py`, which is guard code, so it goes through the chain.

The engineer decided on 2026-11-25: one `.env`, the main checkout's, for every SSSF process wherever it starts (option A), and the host refuses an agent's commit that adds or changes a secret file (option B). Both, in one run.

## What broke

`docs/open.md` has the P1 row ("A chain that starts in a run's worktree loads the `.env` the run's branch committed"). What the code at `main` shows, checked on 2026-11-25:

- **The walk.** `utils.py` loads `.env` at import: `DOTENV_KEYS = frozenset(dotenv_values(find_dotenv()))`, then `load_dotenv()`. `find_dotenv()` with no arguments starts from the calling file's folder (`adws/adw_modules/` of whichever copy was imported) and walks up to the first `.env`. For a process that imports a worktree's `utils.py` (`adws/adw_data/worktrees/<adw_id>/adws/adw_modules/`), the walk passes the worktree's `adws/`, the worktree's root, `adws/adw_data/worktrees/`, `adws/adw_data/` and `adws/` before it reaches the main checkout's `.env`. Check this against python-dotenv's source; if the walk starts from the working folder instead, say so, since that changes who's reached.
- **Who starts there.** The orchestrator imports `utils` in the main checkout, then moves into the worktree (`worktree.py:311`, `os.chdir(path)`; a resume at `:282`). Find every process SSSF starts afterwards that imports `adw_modules.utils` or runs `just` with the worktree as its working folder or its `utils` copy, and list them in the spec with source lines. Add what the engineer starts by hand in a worktree, such as `just` from there.
- **What a found `.env` does.** A `.env` the run's branch committed (a VM agent under `host_agents: false` can add one) sets its keys in that process and its children: `LD_PRELOAD`, `BASH_ENV`, `PYTHONPATH`, `NODE_OPTIONS`, `GIT_CONFIG_GLOBAL` or `GIT_DIR` would run agent files on the host. An empty `.env` found first gives an empty `DOTENV_KEYS`, so `agent_env` holds back nothing and every key already in the environment reaches agents. `adws/adw_data/` is gitignored and sits above every worktree, so a `.env` there hides the real one from every worktree process. Not reproduced.
- **`just`.** The `justfile` template has `set dotenv-load` (line 12), which loads the first `.env` in the justfile's folder or a folder above it, before any recipe line runs (check this against `just` 1.59.0). In a worktree that's the branch's, or an empty one in `adws/adw_data/`. Every shell `just` starts then carries those keys, so a refusal inside a recipe comes too late. `config` reads `SSSF_CONFIG` through `env_var_or_default` (line 19), so a key in `.env` can choose the roster today. A repo that loads `sssf.just` with `mod sssf` has its own justfile and its own settings.
- **What exists to build on.** `worktree.main_checkout()` finds the main checkout through `--git-common-dir` with git's location variables removed (spec d0200ae6), so a `GIT_DIR` in `.env` can't move it. `permissions.judge_commits` judges every commit of a VM run before the host lands it (`sandbox_remote.py`, the `bundle unbundle` and `--ff-only` merge), and refuses the whole bundle on any refused path. `secret_files.py` names the repo's secret files: `.env` and `.env.*` except `.env.sample` and `.env.example`, plus `repo.yaml`'s `secret_files`.

## The change

1. **One `.env`.** `utils.py` reads `DOTENV_KEYS` and loads keys from the main checkout's `.env` only, found the way `main_checkout()` finds the main checkout, from any working folder and any copy of `utils.py`. A `.env` anywhere else (a worktree's root, its `adws/`, `adws/adw_data/`) is never read by it. Outside a git repo, say what happens and keep today's behaviour if nothing reaches it. Keep the `PermissionError` handling and its agentsh-only sign exactly as they are; the bar's existing tests for them still pass.
2. **`just` loads no worktree `.env`.** A `just` recipe from the `justfile` template, run in a run's worktree, starts no process with a key from the worktree's `.env`. Say in the spec how, from `just` 1.59.0's documentation or source (this machine's version): drop `set dotenv-load` and have the recipes get `.env`'s keys from SSSF's Python, point `dotenv-path` at the main checkout if `just` can compute it, or something smaller. Say what changes for a repo whose `.env` sets `SSSF_CONFIG` or other keys the recipes read, and keep that working from the main checkout. A repo's own justfile is the repo's; `config.md` says what its `set dotenv-load` does in a worktree.
3. **No agent commits a secret file.** `permissions.judge_commits`, and the after-call check for a local agent if it doesn't already, refuse a commit that adds, changes, renames to, or deletes a path whose name `secret_files` matches, anywhere in the tree, for every agent. Say whether `protected_files` or the existing secret-file rules already cover any of this, and add only what's missing. A repo that tracks a `.env` on purpose keeps it; only agents can't change it, and `config.md` says so.
4. **Who relied on a worktree `.env`.** Say whether anything SSSF ships writes a `.env` into a worktree (`worktree_setup` examples, `install.py`, the docs), and what a repo that does it in its own `worktree_setup` loses: SSSF's processes stop loading it, while the repo's own tools still read it.
5. **Docs.** Delete the `docs/open.md` row named above, or cut it to what's left. `config.md`'s `host_agents` section and its `.env` notes say which `.env` SSSF loads, what `just` loads, and that an agent's commit of a secret file is refused.

## The bar

- **The committed `.env`.** In a temp repo with a worktree like a run's: a `.env` at the worktree's root setting `SSSF_PROBE=worktree`, a process that imports the worktree's `utils.py` from the worktree gets the main checkout's keys and no `SSSF_PROBE`, and its `DOTENV_KEYS` are the main checkout's names. On `main` it gets `SSSF_PROBE=worktree`.
- **The empty `.env`.** The same with an empty `.env` in the worktree's `adws/`, and again in the main checkout's `adws/adw_data/`: `DOTENV_KEYS` is still the main checkout's names, and `agent_env` still holds back a key the main checkout's `.env` names. On `main` it holds back nothing.
- **`GIT_DIR` in the environment.** With `GIT_DIR` set to another repo, the `.env` loaded is still the main checkout's.
- **`just`.** In the same temp repo with the template's `justfile`, a recipe that prints the environment, run with `just` from the worktree, shows no key from the worktree's `.env`. Mark it to skip where `just` isn't installed. On `main` it shows the key.
- **The commit.** `judge_commits` refuses a VM-style commit that adds `.env`, one that adds `sub/.env.local`, one that renames a file to `.env`, and one that changes a tracked `.env`; it passes one that adds `.env.example`. A local agent's commit of `.env` is refused by the after-call check.
- **Nothing else changes.** Every existing test passes on the host, `test_secret_files.py`, `test_worktree.py` and the `utils` import tests among them.

## After the merge, the engineer's proof

Update the hub's install and commit it. In a scratch repo with SSSF installed and a decoy `.env` holding `SSSF_DECOY=main`: start a worktree run, put `SSSF_DECOY=worktree` in a `.env` at its root by hand, and run one SSSF command from the worktree and one `just` recipe from there. Both see `main`, or no key, never `worktree`. Then run one VM run (`--sandbox openshell`) whose agent is told to commit a `.env`, and see the bundle refused.

## Running the tests

The planner and the builder run under agentsh. Run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Every `.env` a test writes is in a temp repo and holds made-up keys. Never open, copy or print the hub's own `.env`; check it by key name only, if at all.

## Out of scope

- The xattr gap that makes the main checkout's `.env` unreadable (its own row).
- A repo's own justfile and its own `.env` loading, past what `config.md` says.
- Other host processes a VM run's commits can reach, past `.env`: each is its own row.
- The text checks' own gaps. The text guard is frozen.

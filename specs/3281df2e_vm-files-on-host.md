# Keep a VM run's committed files from running on the host

Third of three runs that merge together (9f9522bd `host_agents`, d0200ae6 main-checkout lookup, this one). It closes H1 and H2 from the second review: two host steps that read a VM run's worktree as project config and run what it names.

All module paths below are under `.claude/skills/sssf/`. The builder is `guard_builder`, whose `writes` names every protected path there.

## Baseline

`sssf/d0200ae6` at `0e09a03` ("Find the main checkout without git's location variables and fail closed when git can't answer"). Every finding and every "unchanged" claim is judged against that commit. This run's branch is cut from it.

## Threat model

**What the agent can do.** A VM agent commits any file outside `adws/` to the run's branch. `commits_out` (`templates/adws/adw_modules/sandbox_remote.py`) lands those commits in the run's worktree on the host, `<main checkout>/<data_dir>/worktrees/<adw_id>`. So the agent chooses the content of every tracked file there: `.python-version`, `.python-versions`, `uv.toml`, `pyproject.toml`, `.env`, `.venv/` (with `git add -f`), `.gitattributes`, `.githooks/`, `.npmrc`, wheels, scripts, and a folder shaped like a bare git repo (`x/HEAD`, `x/config`, `x/objects/`, `x/refs/`).

**What the agent cannot do from a VM.** Write the host's `.git` (the worktree's `.git` is a file git refuses to track, and its config lives in the main checkout's `.git`), write the main checkout, set a host environment variable, or write outside the worktree.

**What this guard can't stop at its level.**
- A host run's agent (`host_agents: true`) runs as the engineer's own user. It can write any of these files anywhere. Nothing here is a control for it.
- Anything the engineer starts inside the worktree (`just quality`, `just rebase`, `uv run ...`, `just ...` with `set dotenv-load`) reads the worktree's config. This change documents those, it doesn't refuse them.
- After `just merge`, the agent's files are the main checkout's. Review before merge is the control.
- The engineer's own git config and environment. A `GIT_CONFIG_*` variable or a global `filter.*`, `core.sshCommand` or `diff.external` is theirs. The host git calls in the worktree get two `-c` overrides, not a clean config.

## What I checked on the baseline

| Step | Where | Runs in the worktree? | Reads config an agent's commit can set? |
|---|---|---|---|
| `uv python find` | `Remote.open` → `_host_python(self.tree)`, `sandbox_remote.py` ~733, 422. `self.tree` is `Path.cwd()` (line 679) | **Yes**, on a new run and every resume or join, all providers | **Yes: H1.** `.python-version`, `.python-versions`, `.venv/`, `uv.toml`, `pyproject.toml` |
| `uv python find` | `image_python` in `sandbox_openshell.py` (callers at ~139, ~220, ~443) | **No.** `repo_root(path)` is `--git-common-dir`'s parent with `git_location_env()`, so it is the main checkout from a worktree too. The docstring is right | No. Its environment is not scrubbed, which step 1 fixes in `_host_python` |
| `uv run --with <sdk>` re-exec | `require_sdk`, `sandbox_remote.py` ~353, reached from `session._open_sandbox` after `worktree.enter` | **Yes** on a no-flag resume, E2B and Vercel (OpenShell's `require_sdk` never re-execs) | **Yes: H2.** `uv.toml`, `.python-version`, then the worktree's `adw_modules` and, through `find_dotenv` walking up from that `utils.py`, the worktree's `.env` |
| `git merge -q --ff-only` | `commits_out` → `_host_git`, ~1173 | Yes, after every VM commit | Hooks: `post-merge` runs from the worktree's own folder when the engineer set a relative `core.hooksPath` (`.githooks`, which SSSF's installer suggests). fsmonitor: only from config or `GIT_CONFIG_*` |
| `git bundle create`, `config user.name/email`, `bundle unbundle`, `rev-list` | `_host_git`, ~816, 817, 1148, 1175 | Yes | No hook, no index refresh. Config only |
| `git apply --numstat` | ~892, already `permissions._REF_READ` | Yes | Already hardened |
| `permissions.judge_commits` | ~1157 | Yes | Already hardened (`_SAFE`, `_NO_HOOKS`) |
| `git status --porcelain` | `worktree.dirty`, `worktree.uncommitted` | On a VM run: in `merge`, `discard`, `rebase` (engineer starts them, SSSF runs the git). `session.ensure`'s `dirty` call is host runs only | fsmonitor, only from config or `GIT_CONFIG_*` |
| `git rev-parse`, `git ls-tree` | `sandbox.extensions_in_clone`, `cwd=root` | No, main checkout | No |
| `git config user.name`, `git rev-parse --show-toplevel` | `utils.engineer_name`, `utils._recipe_prefix` | Yes, read only | No program |
| `worktree_setup`, quality checks, agents, `git_helper` | through `sandbox.current()` | No: in the VM for a VM run | Not a host step |
| `worktree_setup` and `uv run adws/adw_quality.py` | `worktree._rebase`, `_recheck`, `cwd=path` | Yes, on the host | **Yes**, all of uv's and npm's config. Engineer starts it: document |
| `git rebase`, `git reset --hard` | `worktree._rebase` | Yes | Hooks under a relative `core.hooksPath`. Engineer starts it: document |
| `git merge --ff` in the main checkout | `worktree._merge` | No, but it checks the agent's files out into the main checkout, and `post-merge` then runs from there under a relative `core.hooksPath` | Engineer starts it: document |

**Git config an agent's commit can set: none directly.** No tracked file is git config. A committed bare-repo-shaped folder is found only by a git call whose working folder is inside it, and every host call above uses the tree's root. `.gitattributes` can name a filter or merge driver, but the program comes from config. So the routes are a worktree `.env` setting `GIT_CONFIG_*` (closed with H2), and a relative `core.hooksPath` (closed for SSSF's own calls in step 3).

**Files uv reads to pick a Python.** From uv's docs (`concepts/python-versions`, `concepts/configuration-files`, `reference/environment`), to be confirmed by the builder against those pages for the installed uv before writing the docstring:
- From the working folder and its parents: `.python-version`, `.python-versions`, `.venv/`, `uv.toml`, `pyproject.toml` (`requires-python`, `[tool.uv]`).
- From the user and system: `~/.config/uv/uv.toml`, `~/.config/uv/.python-version`, `/etc/uv/uv.toml`.
- From the environment: `UV_PYTHON`, `UV_PYTHON_PREFERENCE`, `UV_MANAGED_PYTHON`, `UV_NO_MANAGED_PYTHON`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_DOWNLOADS`, `UV_SYSTEM_PYTHON`, `UV_CONFIG_FILE`, `UV_NO_CONFIG`, `UV_PROJECT`, `UV_WORKING_DIRECTORY`, `UV_PROJECT_ENVIRONMENT`, `VIRTUAL_ENV`, `CONDA_PREFIX`, `PATH`.

With the main checkout as the working folder, every file in the first group is the main checkout's or a parent's, and a run's worktree is below it, never above. The second group is the engineer's. In the third, the only value that differs per run is `UV_PROJECT_ENVIRONMENT`, which `worktree._own_environment` sets to `<shared>-<adw_id>`; a host `just rebase` builds that environment from the worktree's files. Step 1 drops it for this one call. The rest stay as the engineer set them, as on the baseline.

## Decisions

1. **H1: ask uv in the main checkout.** Not "find the version without uv": the main checkout's config is the engineer's, and asking uv there keeps the answer a new run gets today.
2. **H2: `sandbox.prepare` runs before `worktree.enter` on a no-flag resume**, as it does for a new run with the flag. Why this and not a re-exec from the main checkout with an absolute path:
   - The re-exec then happens before any `chdir`, so the working folder, the relative `sys.argv[0]` and every relative argument (a prompt file path) mean what they meant at launch. A forced `chdir` to the main checkout would change them for a launch from another folder.
   - `require_sdk`'s `execvp` line doesn't change, so a new run with the flag is byte-for-byte the baseline.
   - It needs no path arithmetic on `__file__` or `sys.argv[0]`.
   - Its gap is a chain launched with a run's worktree as its working folder. `require_sdk` refuses that case instead of fixing it: see step 2.
3. **Git: two `-c` overrides on SSSF's own host calls in the worktree**, the same values `permissions.py` uses: `core.fsmonitor=` (empty; `false` runs the program `false` before git 2.36) and `core.hooksPath=/dev/null`. `-c` wins over `GIT_CONFIG_COUNT`. `GIT_CONFIG_*` is not scrubbed: `utils.py` keeps it on purpose so a container's `safe.directory` still works.

## Steps

### 1. `templates/adws/adw_modules/sandbox_remote.py`: the Python lookup

- In `_host_python`, pass `env=` to the `uv python find` call: a copy of `os.environ` without `UV_PROJECT_ENVIRONMENT`. Add a module constant for the dropped names with a one-line comment saying why. `os.environ` itself is not changed. The interpreter call keeps its arguments.
- In `Remote.open`, replace `_host_python(self.tree, problems)` with `_host_python(worktree.main_checkout(), problems)` (import `worktree` inside the method, as `_repo_names` does). A `WorktreeError` there is a `SandboxFailed` naming the cause.
- Update `_host_python`'s docstring: it is always called with the main checkout, list the files and variables above, and say none comes from a run's worktree.
- `templates/adws/adw_modules/sandbox_openshell.py`: no code change to `image_python` or `repo_root`. Add one sentence to `image_python`'s docstring: `repo_root` is the main checkout from a worktree too, and `_host_python` drops the run's own `UV_PROJECT_ENVIRONMENT`.

### 2. `templates/adws/adw_modules/session.py` and `sandbox_remote.py`: the SDK re-exec

- In `session.ensure`, after `adw_id = adw_id or new_id(8)` and before `worktree.enter`: when `sandbox_name` is empty, read `worktree.recorded(_db_path(cfg.observability.db), adw_id)`; if it names a `sandbox`, call `sandbox.prepare(<that provider>, cfg)`. Reuse `_vm_resume`'s lookup so the trace is read once.
- In `_open_sandbox`, delete the `if not asked: sandbox.prepare(provider, run.cfg)` lines. Keep the `asked != provider` refusal. Fix the comment.
- In `sandbox_remote.require_sdk`, before `os.execvp`: refuse when the working folder is in a run's worktree. Add `worktree.run_worktree_here() -> bool` in `templates/adws/adw_modules/worktree.py`: true when `git rev-parse --show-toplevel` (with `git_location_env()`) is not `main_checkout()`, is inside it, and its parent folder is named `worktrees` (the shape `path_for` makes). The refusal is a `SystemExit` in one line: it names the worktree, says SSSF won't fetch `<requirement>` from inside a run's worktree because uv would read that folder's config, and says to launch from `<main checkout>`. When git can't answer inside a checkout (`checkout_here()`), refuse too.
- Update the module docstrings of `session.py` and `require_sdk` to say where the re-exec happens.

### 3. Git calls in a VM run's worktree

- `templates/adws/adw_modules/utils.py`: add `GIT_NO_PROGRAMS = ("-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null")` with the comment from decision 3.
- `sandbox_remote._host_git`: run `["git", *GIT_NO_PROGRAMS, *args]`. The failure message keeps naming `git <args>` only.
- `worktree.dirty` and `worktree.uncommitted`: pass `*GIT_NO_PROGRAMS` before `status`. `worktree.git`'s own error text may then show the options; keep it readable.
- Leave every other `worktree.git` call alone: `worktree add`, `merge`, `rebase`, `reset`, `branch` are the baseline's, and the engineer's hooks may be wanted there.

### 4. The refusal

- `templates/adws/adw_modules/worktree.py`: add `checkout_folder() -> Path | None`, the first folder at or above the working folder holding `.git`; `checkout_here()` returns `checkout_folder() is not None`.
- `templates/adws/adw_modules/budget.py`, `_root()`: the message names the working folder and the checkout folder, keeps git's own error, and gives the fix: `git config --global --add safe.directory <checkout folder>` when git says the folder's ownership is dubious, else the `GIT_CONFIG_*` line it has now. Still a `WorktreeError`, still raised only inside a checkout.
- `templates/adws/adw_tickets.py`, about line 94: wrap `session.choose_sandbox(...)` so a `worktree.WorktreeError` becomes `SystemExit(f"WorktreeError: {error}")`, the form `ensure` uses.
- **The operator container.** Nothing under `templates/container/` or `scripts/` sets `safe.directory`. The builder reads `templates/container/Containerfile` and `run.sh` and answers in `config.md`: the container launches when the repo mount's owner is the container's user, and stops with the new message when it isn't. Do not add `safe.directory` to the image in this run; if the mount's owner differs, that line is the engineer's (see "For the engineer").

### 5. Tests: new file `tests/test_vm_files_on_host.py`

Build on `tests/test_main_checkout_lookup.py`'s `Rig` and `tests/test_host_python.py`'s `host` and `open_box` helpers (import them, or copy the few lines needed). Config comes from the shipped `templates/adws/adw_sssf_config/sssf.config.yaml`. No real VM, agent or SDK. Each test that plants a program has it write a marker file and asserts the marker is absent.

1. **H1, resume.** A VM run's worktree with a committed `.python-version` holding `./tools/python3` and an executable `tools/python3`. A resume with `--adw-id` and no flag reaches `Remote.open`. Real uv, in a subprocess. The marker is never written.
2. **H1, join.** The same for a second chain joining the run's `adw_id`.
3. **H1, the call.** With uv faked as `test_host_python.py` fakes it, the working folder uv got is the main checkout, and its environment has no `UV_PROJECT_ENVIRONMENT` when the process has one.
4. **The Python is unchanged.** For a repo with `.python-version` in its main checkout and for one without, a new run and a resume give the VM the same `UV_PYTHON`, equal to what `_host_python(<main checkout>)` returns on the baseline. Real uv in a subprocess, skipped when the host has no uv.
5. **H2, resume.** `sandbox: e2b`, the `e2b` import made to fail, `os.execvp` replaced by a recorder. The worktree holds a committed `uv.toml`, `.python-version`, `.env` and its own `adws/adw_modules`. A no-flag resume: `execvp` is asked once, with the working folder equal to the launch folder (the main checkout), `argv` equal to the baseline's list, and `worktree.enter` not yet called.
6. **H2, from inside the worktree.** The same launch with the worktree as the working folder: `SystemExit` with the one-line message, `execvp` never asked.
7. **H2, new run.** A new run with `--sandbox e2b` asks `execvp` exactly what the baseline asks.
8. **Git.** With `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=core.fsmonitor`, `GIT_CONFIG_VALUE_0=<marker script>` in the environment, and again with a relative `core.hooksPath` plus a committed `post-merge` hook, and again with a committed bare-repo-shaped folder whose `config` sets `core.fsmonitor`: `_host_git("merge", "--ff-only", ...)`, `worktree.dirty` and `worktree.uncommitted` in the worktree write no marker. First assert the plant works: plain `git status` with that environment writes the marker.
9. **The refusal.** A checkout git rejects (the rig's failing-git case): `budget._root()`'s message holds the working folder, the checkout folder and `safe.directory`. `adw_tickets.main` in a subprocess exits non-zero with that message and no `Traceback` in stderr.

Tests 1, 2, 3, 5, 6, 8 (hook and `GIT_CONFIG_*` cases) and 9 must fail on `0e09a03`. Say so in each docstring.

**One existing test changes:** `test_open_refuses_a_bad_answer_in_the_worktree_alone` in `tests/test_host_python.py` pins H1 itself, a bad answer planted in the worktree alone stopping `open`. Rewrite it to assert the worktree's plant is never asked and `open` goes on. Change no other existing test; if another fails, the code is wrong.

### 6. Docs

- `references/config.md`, "Keeping every agent in the VM, `host_agents`" (about line 735), next to the text the first two runs added:
  - Closed: the host's Python lookup and the SDK fetch no longer read a run's worktree; SSSF's own git calls there run no hook and no fsmonitor.
  - Still open, the engineer's steps: `just rebase` runs `worktree_setup` and the quality checks on the host in the worktree, `just quality` run there does too, and `just merge` checks the run's files out into the main checkout. Each reads the run's `uv.toml`, `.python-version`, `.npmrc`, `.env` (through a justfile's `set dotenv-load`) and, under a relative `core.hooksPath`, its hooks. Read the diff first.
  - The `safe.directory` message and the operator container answer from step 4.
- `docs/open.md`, two rows in their place in the sort, in the table's existing columns:
  - Bug, Low: a tool call named `worktree` shadows the run's record. `worktree.recorded` matches `name='worktree'` with no `type` filter, so a resume is refused or crashes with `KeyError: 'worktree'`. A denial of service.
  - Bug: an agent can commit `.env`, `uv.toml` or `.python-version`. They load on the host when the engineer runs a recipe inside the worktree, on `just rebase`, and after `just merge`. Refusing them by name at the commit check is the only fix left for those steps.
- `app_docs/`: not edited. For the documenter: the write-ups for 9f9522bd and d0200ae6 describe a resume whose `sandbox.prepare` ran after `worktree.enter`, and a `_host_python` that ran in the run's tree.

## Bar

Against `0e09a03`. Must:
- H1: a committed `.python-version` naming a program is never run on a resume or a join. No agent code run.
- H2: no SDK re-exec runs with a run's worktree as its working folder, reads its `uv.toml`, `.python-version` or `.env`, or imports its modules. Launched from inside one, it refuses.
- No hook and no fsmonitor program runs from SSSF's own host git calls in a VM run's worktree, whatever `GIT_CONFIG_*` or a relative `core.hooksPath` says.
- A checkout git rejects stops with the folder and the fix, and `adw_tickets` prints it without a traceback.

Must never:
- Change the `UV_PYTHON` a new run or a resume passes, for a repo with or without a main-checkout `.python-version`.
- Change what `execvp` is asked on a new run with the flag.
- Change a new VM run, a resume from the main checkout, a host run under `host_agents: true`, `merge` or `discard` in any other way. Known and intended: a `post-merge` hook no longer runs when a VM's commits land in the worktree.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't.
- Fail an existing test, the one named in step 5 aside.

## Out of scope

Old holes, to be filed as such:
- `just quality` and `just rebase` on the host in a run's worktree, and what `just merge` brings into the main checkout. Documented, not refused.
- An agent's commit of `.env`, `uv.toml` or `.python-version` is not refused by name. New `docs/open.md` row.
- The `worktree` tool-call name shadowing the run's record. New `docs/open.md` row.
- Program-naming git config other than hooks and fsmonitor (`filter.*`, `diff.external`, `core.sshCommand`, `submodule.recurse`) from the engineer's own config or environment.
- A chain the engineer launches from inside a run's worktree with the SDK already installed: it runs the worktree's copy of `adws/`, as on the baseline.
- The P2 row on committed secret files, and every other open row.
- Host runs: the agent is the engineer's user.

## Verification

From `.claude/skills/sssf`, with the runner and packages the repo's own test command uses (`adws/adw_modules/quality.py`):

1. `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -p no:cacheprovider tests/test_vm_files_on_host.py tests/test_host_python.py tests/test_main_checkout_lookup.py tests/test_host_agents.py tests/test_sandbox_remote.py tests/test_worktree.py tests/test_tickets.py tests/test_budget.py -m 'not pushes and not host_only' -q` passes.
2. Prove the new tests bite: with the four module changes reverted in the working tree (`git stash push -u -m 3281df2e-bite` on those files only, or a temporary copy of the baseline modules on `PYTHONPATH`), tests 1, 2, 3, 5, 6, 8 and 9 fail. Restore, and report which failed.
3. Leave the whole suite to the verify step. Keep runs light: the agentsh event store slows under load (`docs/open.md`).
4. Read `config.md`'s section and both `docs/open.md` rows against the tables in this plan.

## For the engineer

- `adws/` in this repo is the installed copy of the templates and is protected. After the three runs merge, bring it up to date the usual way (`just sssf update` or the installer), so the hub's own launches get the fix.
- If the operator container's repo mount is owned by another user than the container's, add `git config --global --add safe.directory <mount path>` to the container's start, or the launch stops with the new message. The builder's `config.md` text says which case the shipped `run.sh` is.
- Merge order: 9f9522bd, then d0200ae6, then this run.

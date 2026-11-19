# Find the main checkout without trusting git's location variables

## Problem

`host_agents: false` is read from the main checkout's `repo.yaml`. The main checkout is found by `worktree.main_checkout()`, and `budget._root()` wraps it. Both run `git rev-parse --path-format=absolute --git-common-dir` with the process's whole environment, and git obeys `GIT_DIR` there.

A VM agent commits a `.env` holding `GIT_DIR=sub/gd` and a bare repo at `sub/gd`. A chain process that starts in the run's worktree runs the worktree's copy of `adws/adw_modules/utils.py`. Its `load_dotenv()` walks up from that file's folder, finds the worktree's `.env`, and sets `GIT_DIR`. The lookup then answers `<worktree>/sub`, which has no `repo.yaml` and no trace, so `host_agents` reads `true` and the VM run reads as a host run.

Reproduced while planning, in a throwaway repo with git 2.43.0:

| Call, from the worktree | `--git-common-dir` answers |
|---|---|
| plain | `<main>/.git` |
| `GIT_DIR=sub/gd` | `<main>/wt/sub/gd` |
| `GIT_DIR=sub/gd`, removed from the child's environment | `<main>/.git` |
| `GIT_CONFIG_COUNT=x` | exit 128, `bogus count in GIT_CONFIG_COUNT` |
| `GIT_CONFIG_GLOBAL=<file with a bad line>` | exit 128, `bad config line 1` |

The last two rows matter. `budget._root()` catches every exception and falls back to the working folder. So a `.env` line that only makes git fail still moves `_root()` to the worktree: its copy of `repo.yaml`, and a trace database that isn't there. Removing the location variables alone doesn't close that. The fallback has to fail closed inside a checkout.

## Threat model

The agent works in a VM and can commit any path that `permissions.judge_commits` passes. That includes a `.env` and any file under a folder such as `sub/`. Its commits land on the run's branch, and the run's host worktree checks them out. The agent can't change the main checkout's files, the worktree's `.git` file, or `<main>/.git/worktrees/<id>/commondir`. `protected_files` covers `adws/`, so it can't change the worktree's copy of `adws/` or of `repo.yaml` either.

The agent's lever is the environment of a chain process that starts in the worktree. It gets there through the `sandbox_remote.require_sdk` re-exec after `worktree.enter` has moved the process, through `just`'s `set dotenv-load`, or through an engineer launching from the worktree. The same variables can also come from the engineer's own shell.

What this guard can't stop at its level:

- **The `.env` is still loaded.** This change makes SSSF's lookup ignore part of the environment. It doesn't stop an agent-written `.env` from reaching `os.environ`. See "Where the setting comes from" for what that leaves open.
- **A host agent is the engineer's user.** Once a host run is allowed, the agent can set any variable for its own children. This change only protects the decision made before any agent starts, and the settings read during the run.
- **Git must work.** If git can't answer inside a checkout, SSSF can only refuse. It can't find the main checkout another way.

## Baseline

`sssf/9f9522bd` at `d4ceb15` ("Add host_agents repo setting that refuses any launch whose agents would run on this machine"). Every finding is judged against that commit. The redirect also exists on `main` for the spend caps, the sandbox default and the trace database.

## The change

### 1. One scrubbed environment for the lookup, in `.claude/skills/sssf/templates/adws/adw_modules/utils.py`

Add a constant and a helper. `utils.py` is the right home because every module already imports it, and `worktree.py` imports it, so nothing becomes circular.

- `GIT_LOCATION_VARS`: a frozenset of the names below.
- `git_location_env() -> dict[str, str]`: a copy of `os.environ` without those names. It never changes `os.environ`.

The names, each with its source. The builder checks each against `git help git` (ENVIRONMENT) and `git rev-parse --local-env-vars` on the machine, and adds any location variable a newer git lists.

| Variable | What it moves | Source |
|---|---|---|
| `GIT_DIR` | the git dir, and with it the common dir | `git help git`; local-env-vars |
| `GIT_COMMON_DIR` | the common dir directly | same |
| `GIT_WORK_TREE` | `--show-toplevel` | same |
| `GIT_INDEX_FILE` | which index is read | same |
| `GIT_OBJECT_DIRECTORY` | which object store is read | same |
| `GIT_ALTERNATE_OBJECT_DIRECTORIES` | extra object stores | same |
| `GIT_NAMESPACE` | which refs are seen | `git help git` |
| `GIT_CEILING_DIRECTORIES` | where the upward search stops | `git help git` |
| `GIT_DISCOVERY_ACROSS_FILESYSTEM` | whether the search crosses a mount | `git help git` |
| `GIT_IMPLICIT_WORK_TREE` | whether a work tree is assumed | local-env-vars |
| `GIT_PREFIX` | the subfolder git thinks it was called from | local-env-vars |
| `GIT_SHALLOW_FILE`, `GIT_GRAFT_FILE`, `GIT_REPLACE_REF_BASE`, `GIT_NO_REPLACE_OBJECTS` | how history is read | local-env-vars |

Left in on purpose: `GIT_CONFIG`, `GIT_CONFIG_COUNT`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM`, `GIT_CONFIG_NOSYSTEM`. No config key changes `--git-common-dir`. An engineer in the operator container may need `safe.directory` from one of them for git to answer at all. They can make git fail, and step 3 handles that.

One behaviour changes by request: an engineer who exports `GIT_CEILING_DIRECTORIES` to hide an outer repo will have SSSF's lookup find that outer repo. The docs step says so.

### 2. The lookups use it, in `.claude/skills/sssf/templates/adws/adw_modules/worktree.py`

- `git()` gains an optional `env` parameter, passed to `subprocess.run`. Default `None`, so every other call is unchanged.
- Add `common_dir() -> Path`: `rev-parse --path-format=absolute --git-common-dir` with `env=git_location_env()`.
- Add `in_repo() -> bool`: `rev-parse --git-dir` with `check=False` and the same env.
- `main_checkout()` returns `common_dir().parent.resolve()`.
- `locked()` uses `common_dir()`.
- `launching()` and `enter()` use `in_repo()`.
- `in_run_worktree()` runs its `--show-toplevel` with the same env.
- Add `checkout_here() -> bool`: true when the working folder or any parent holds a `.git` entry. It walks paths in Python, with no git call and no environment, for step 3.
- Update `main_checkout()`'s docstring to say it ignores git's location variables and why.

### 3. `budget._root()` fails closed, in `.claude/skills/sssf/templates/adws/adw_modules/budget.py`

When `worktree.main_checkout()` fails:

- `worktree.checkout_here()` is false: return `Path.cwd()`, as now. A chain in a folder that isn't a repo works as before.
- it is true: raise `worktree.WorktreeError`. The message says git couldn't find this repo's main checkout from the working folder, quotes git's own error, and says to fix the git environment (a `GIT_CONFIG_*` variable, or a config file git can't read) and launch again.

In `.claude/skills/sssf/templates/adws/adw_modules/session.py`, `ensure()` turns that error from its first two calls (`choose_sandbox`, `host_agents`) into `SystemExit(f"WorktreeError: {error}")`, the same form its later handler uses. Update the module docstring and `host_agents`'s docstring: the main checkout is found without git's location variables. `adw_tickets.py` calls `choose_sandbox` itself and gets a traceback with the same message, which is fine: it exits non-zero either way.

### 4. Every other place SSSF works out the main checkout or the repo root

| Site | Now | After |
|---|---|---|
| `worktree.main_checkout()`, `locked()`, `launching()`, `enter()`, `in_run_worktree()` | own `rev-parse`, full environment | step 2 |
| `budget._root()`; `session.choose_sandbox`, `_db_path`, `host_agents` | through `main_checkout()` | fixed lookup, fail closed |
| `trial.py` (six calls), `sandbox_remote.py:755`, `session.py:174,256,258`, `budget.py:88`, `adw_kill.py`, `adw_sandbox_usage.py`, `adw_trial.py`, `adw_worktree.py` | through `main_checkout()` | fixed lookup, no edit |
| `agentsh_guard.py:460,471` | `worktree.git("rev-parse", ...)` | use `worktree.in_repo()` and `worktree.common_dir()` |
| `agentsh_guard.py:754` `check_root` | `_run([... "--show-toplevel"])` | give `_run` an optional `env` and pass `git_location_env()` on this call |
| `sandbox_openshell.repo_root()` | own `subprocess.run` | add `env=git_location_env()` |
| `utils._recipe_prefix()` | own `subprocess.run` | add `env=git_location_env()`; it only picks `just` or `just sssf` in messages |
| `adw_export.py:95` | `--show-toplevel`, then that folder's `repo.yaml` for `export_checks` | that one call runs with `git_location_env()`. `export_checks` are commands run on the host, so `GIT_WORK_TREE` must not pick the `repo.yaml` |
| `git_helper.repo_root()`, `is_repo()` | through `sandbox.current().run` | no edit. In a VM run the call runs in the VM, with the VM's environment. In a host run it says where a host agent works, after `host_agents` has allowed it. See Out of scope |
| `templates/justfile` (`agentsh` install recipe), `githooks/pre-push`, `container/*.sh`, `scripts/install.py`, the visualizer's `factory.ts` | shell or script, typed by the engineer | no edit. None reads a setting that gates an agent, and none imports `utils.py`. `just`'s `set dotenv-load` can still hand them a worktree's `.env`. See Out of scope |

`agentsh_guard.py` and `sandbox_openshell.py` are protected and under `.claude/skills/sssf/`, so they are builder steps. Run their test files after the edit.

### 5. Where the setting comes from

- **Should `load_dotenv()` stop loading `GIT_*` names from `.env`? No.** It wouldn't cover a shell export, which the bar requires. It would change what the repo's own tools see for an engineer who keeps `GIT_SSH_COMMAND` or an author name in `.env`. And the names that matter aren't all `GIT_` names.
- **Should a worktree's `.env` be read at all when a chain starts in a worktree? No, but not in this change.** Steps 1 to 4 close the redirect on their own, so this change stops there, as the request prefers. The `.env` door stays open for other payloads. Reasoned from git's and the loader's documented behaviour, not reproduced here:
  - `GIT_DIR=sub/gd` still reaches SSSF's other host git calls. Those then read the agent's `sub/gd/config` and `sub/gd/hooks`, which can name commands to run.
  - `LD_PRELOAD`, `BASH_ENV`, `PYTHONPATH`, `NODE_OPTIONS` or `GIT_CONFIG_GLOBAL` in the `.env` can run an agent's file in the chain's first child process.

  Either one runs agent code on the host under `host_agents: false`. The fix is to choose which `.env` a chain started in a worktree loads. That changes the environment the repo's commands get, which this request rules out, so it needs the engineer's decision. Step 7 files it.

### 6. Docs, in `.claude/skills/sssf/references/config.md`

In "Keeping every agent in the VM, `host_agents`":

- After "SSSF reads the main checkout's `repo.yaml`...", add: SSSF finds the main checkout without git's location variables (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_CEILING_DIRECTORIES` and the rest), whether a `.env` or the shell set them. If git can't answer inside a checkout, the launch stops.
- Beside the `just quality` hole, name three more, one sentence each on what to do:
  - **A nested repo or submodule.** Launched from inside one, SSSF reads that repo's `repo.yaml`, which is missing. Launch from the main checkout.
  - **`just rebase`.** It runs `worktree_setup` and the repo's checks on this machine, on the agent's rebased code. Review the run's diff before rebasing, or rebase in a VM.
  - **An ADW that claims to start no agent.** An engineer-written ADW that calls `session.ensure(..., agentless=True)` and then starts an agent skips the check. Pass `agentless=True` only from a chain that never calls one.

No `README.md` text covers this lookup, and the repo keeps no CHANGELOG. There is no `app_docs/` folder, so no older write-up goes stale.

### 7. `docs/open.md`

Add two rows, each in its place in the sort:

- **Bug, High, Needs decision:** `just rebase` and `just quality` run agent-written code on the host under `host_agents: false`. Link the `host_agents` section of `config.md`.
- **Bug, P1, High, Needs decision:** a chain that starts in a run's worktree loads the `.env` the run's branch committed, and `just`'s `set dotenv-load` does the same. Names such as `LD_PRELOAD`, `BASH_ENV` or `GIT_DIR` then run agent files on the host. Include the note from step 5 that this is reasoned, not reproduced. This row also covers refusing an agent's commit of `.env`.

## Tests

New file `.claude/skills/sssf/tests/test_main_checkout_lookup.py`. It isn't a protected path.

**The rig.** A main checkout in `tmp_path`:

- `adws/` copied from `templates/adws` (skip `adw_data` and `__pycache__`);
- `sssf.config.yaml` copied from `templates/sssf.config.yaml`, never a test-only config;
- `adws/adw_sssf_config/repo.yaml` holding `sandbox: vercel`, `host_agents: false` and `max_live_cost: 7`;
- `.gitignore` with `adws/adw_data/`; all committed.

A run worktree at `adws/adw_data/worktrees/<id>` on `sssf/<id>`. Its branch commits `.env` (`GIT_DIR=sub/gd`) and a bare repo at `sub/gd`, with a keep file under `objects/` and `refs/` so the committed form is still a git dir. The main trace holds the run as a VM run: a `sessions` row and a `worktree` event whose payload has `sandbox: vercel`, written with `Tracer` the way `session._start` writes them.

A folder first on `PATH` holds fake `claude` and `pi` scripts. Each writes a marker file and exits 1. No VM is made.

**Every launch is a subprocess**: `[sys.executable, "adws/<chain>.py", ...]` with `cwd` the worktree, with the arguments the template `justfile` gives that chain and `--config` naming the copied config. No test imports a chain in-process.

| Test | Does | Asserts |
|---|---|---|
| the plant works | fresh process in the worktree imports `adw_modules.utils`, prints `os.environ.get("GIT_DIR")`; plain `git rev-parse --git-common-dir` with that value | prints `sub/gd`; raw git answers `<worktree>/sub/gd`. So the refusals below aren't from an inert plant |
| the bypass refuses | `adws/adw_plan.py` fresh in the worktree | non-zero exit; stderr has `host_agents: false` and `adws/adw_sssf_config/repo.yaml`; no marker; main trace row counts unchanged; `<worktree>/sub/adws` doesn't exist |
| the shell export refuses | same, no `.env`, `GIT_DIR=sub/gd` in the child's env; then once per other name in `GIT_LOCATION_VARS` that can point at `sub/gd` or `sub` (`GIT_COMMON_DIR`, `GIT_WORK_TREE`) | same |
| the resume stays a VM run | no-flag `adws/adw_build.py` resume of the VM run's id, fresh in the worktree, with `SSSF_SANDBOX_SDK=1`, which is what `require_sdk` sets before its re-exec | no marker; the main trace has a new `processes` row for the id; nothing new under `<worktree>/sub`; when the Vercel SDK can't be imported, stderr has `require_sdk`'s `--sandbox vercel needs` message |
| every setting | fresh process in the worktree prints `budget._root()`, `budget.max_live_cost()` and `session.choose_sandbox(None, db, None)` | the real main checkout, `7.0`, `vercel` |
| a failing git refuses | `.env` holds `GIT_CONFIG_COUNT=x`; `adws/adw_plan.py` fresh in the worktree | non-zero exit; stderr names the main checkout lookup; no marker; `<worktree>/adws/adw_data` has no database |
| the environment is left alone | in-process, `monkeypatch.setenv` each name, call `worktree.main_checkout()` | `os.environ` still holds each name; `utils.operator_env()` does too |
| same answers as before | `main_checkout()` from the main checkout, a subfolder, and the worktree, with no variable set; `budget._root()` in a folder that isn't a repo | the main checkout three times; the working folder |

**The fake fails like the real one.** Write the test file first and run it against the baseline modules, before any step above. "The bypass refuses", "the shell export refuses", "the resume stays a VM run", "every setting" and "a failing git refuses" must each fail there, with the launch going through: the marker written, or a database under `<worktree>/sub`. Put that failing output in the build report. If one passes at baseline, fix the rig before the code.

## Bar

Against `d4ceb15`, the change must:

- refuse the planted-`.env` launch and the shell-export launch, in a fresh process, with the `host_agents` message;
- keep a no-flag resume of a VM run a VM run, with its record found in the real trace;
- return the real main checkout from `budget._root()` inside the planted worktree, so the caps and the sandbox default come from the real `repo.yaml`;
- refuse, inside a checkout, when git can't answer.

It must never:

- change `os.environ`, or the environment of an agent, a quality check, `worktree_setup` or any other repo command;
- change which main checkout a plain run, a worktree run or a VM run finds when no location variable is set;
- break a chain started in a folder that isn't a repo;
- keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't.

Every existing test passes.

## Out of scope

- **The worktree's `.env` is still loaded** (step 5). It can still run agent files on the host through loader, shell and git variables. Filed in `docs/open.md`.
- **SSSF's other git calls still obey `GIT_DIR`.** Only the lookups in step 4 ignore it. A host run's `git_helper.repo_root()` still follows `GIT_WORK_TREE`.
- **Shell recipes and scripts** (`templates/justfile`, `githooks/pre-push`, `container/*.sh`, `scripts/install.py`, the visualizer) run git with the caller's environment.
- **`just quality` and `just rebase`** run agent-written code on the host. Documented and filed, not refused.
- **A nested repo or submodule** and **an ADW that passes `agentless=True` and then starts an agent**. Documented.
- **An agent's commit of `.env`, or of a git repo as plain files**, is still accepted.
- The P2 row on committed secret files, and the other open rows.

## Verification

From `.claude/skills/sssf`, with the runner the test files name:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_main_checkout_lookup.py tests/test_host_agents.py -m 'not pushes and not host_only' -q
```

Then the files for the other modules touched, one run, same flags: `tests/test_agentsh_guard.py`, `tests/test_sandbox_openshell.py`, and the worktree, budget and export test files the builder finds with a search for `main_checkout`, `budget._root` and `adw_export` under `tests/`. Leave the whole suite to the verify step, and keep runs light: the agentsh event store slows under load (`docs/open.md`).

By reading:

- `rg "git-common-dir|show-toplevel" .claude/skills/sssf/templates/adws` shows each remaining call either passes `git_location_env()` or is a row in step 4 marked "no edit".
- `config.md` names the three new holes, and `docs/open.md` has the two new rows.

## For the engineer

Nothing in this plan needs a protected path outside `.claude/skills/sssf/`. Two things need a decision, not a build:

- The repo's own `adws/` copy gets the fix only when you run the update that copies the templates in. Until then, runs of this repo use the baseline lookup.
- Which `.env` a chain started in a worktree should load (step 5, and the P1 row). Until that's decided, `host_agents: false` still doesn't hold against an agent that commits a `.env`.

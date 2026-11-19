# Keep a VM run's committed files from running on the host

Run it with `--worktree`, from a branch cut from `sssf/d0200ae6`'s tip, with `--base sssf/d0200ae6`. It's the third of three runs that merge together:
- 9f9522bd added `host_agents` to `repo.yaml`;
- d0200ae6 made the main-checkout lookup ignore git's location variables;
- this one closes two holes the second review found, where files a VM agent commits run on the host.

`sandbox_remote.py`, `sandbox_openshell.py`, `session.py` and `worktree.py` decide what runs where, so they're guard code, and the change goes through the chain.

## What broke

A VM run's agents work in the VM. Their commits come back to the host as a bundle, which SSSF merges into the run's worktree on the host (`commits_out`). The commit check refuses only paths under `adws/`, so a builder can commit any other file there. Two host steps then read files from that worktree as project config and run what they name, with the host's full environment, including every key `load_dotenv` loaded from the main checkout's `.env`.

**H1: `.python-version` runs on every resume or join, on every provider.**
- On a resume, `Remote.__init__` (`sandbox_remote.py`, about line 733) calls `_host_python(self.tree, ...)`, where `self.tree` is `Path.cwd()`, the run's worktree.
- `_host_python` (about line 422) runs `uv python find` with `cwd=tree`. uv reads the worktree's `.python-version`, and when that names a path, uv executes it to ask its version.
- The agent commits `.python-version` holding `./tools/python3`, and an executable `tools/python3`. A resume with `--adw-id` and no flag, or another chain joining the run, runs the script on the host before the VM exists.
- The second review reproduced it on OpenShell with `host_agents: false`, and with the setting unset. The hub session reproduced the uv step with uv 0.10.3: `uv python find` ran a script named in `.python-version`.
- `image_python` in `sandbox_openshell.py` calls `_host_python(repo_root(path))`. Its docstring says "in its main checkout", but `repo_root` of a worktree path may be the worktree. The spec checks it.

**H2: the SDK re-exec runs from inside the worktree, on E2B and Vercel.**
- On a resume without `--sandbox`, `_open_sandbox` in `session.py` calls `sandbox.prepare` after `worktree.enter` has moved the process into the worktree.
- When the provider's SDK isn't importable, `require_sdk` (`sandbox_remote.py`, about line 353) runs `os.execvp("uv", ["uv", "run", "--quiet", "--with", requirement, sys.argv[0], ...])`, and `sys.argv[0]` is relative.
- `uv run` in the worktree obeys the worktree's `uv.toml` and `.python-version`. The review made a committed `uv.toml` (`no-index = true`, `find-links = ["wheels"]`) and a fake `e2b-2.62.0` wheel run the agent's code at import.
- The re-exec'd process also imports the worktree's `adw_modules/utils.py`, and its `load_dotenv()` finds the worktree's `.env` first. That `.env` can set `GIT_CONFIG_*`, `LD_PRELOAD`, `BASH_ENV` or `GIT_DIR`, and the chain's own report reproduced a `core.fsmonitor` script running through a later host `git status`.

**Two changes d0200ae6 made to the default behavior:**
- Inside a checkout where git fails, `budget._root()` now refuses instead of falling back to the working folder. That's intended. But a container without `safe.directory` that used to launch now stops, and the operator container (decision 14) may be one.
- `adw_tickets` calls `session.choose_sandbox` outside `ensure`'s `try`, so a `WorktreeError` there prints a traceback instead of a message.

## The change

1. **The host's Python lookup reads only the main checkout's config.**
   - `_host_python` and `image_python` run `uv python find` with the main checkout (`worktree.main_checkout()`) as the working folder, never a run's worktree, and with a scrubbed environment for uv's own config: no `UV_*` from the run's worktree. Alternatively, find the version without letting uv execute a path from project config.
   - The spec lists every file uv reads to pick a Python (`.python-version`, `.python-versions`, `pyproject.toml`'s `requires-python`, `uv.toml`, `UV_PYTHON` and the like) from uv's docs, and shows that none comes from a worktree.
   - The value the VM gets (`UV_PYTHON`) keeps coming from the main checkout, as it does for a new run.
2. **No SSSF process starts from inside a run's worktree to fetch an SDK.**
   - Either `sandbox.prepare` runs before `worktree.enter` on a no-flag resume, as it does for a new run with the flag, or the re-exec runs from the main checkout with an absolute path to the main checkout's chain script. The spec picks one and says why.
   - The re-exec'd process must not load a worktree's `.env`, `uv.toml` or `.python-version`, nor import the worktree's `adw_modules`.
3. **The spec finds every other host step that runs a tool inside a run's worktree** after the VM's commits have landed there: `git status` in `worktree.dirty` / `uncommitted`, `worktree_setup`, the quality checks on rebase, and anything else. For each one, it says whether the tool reads project config that can name a program (git's `core.fsmonitor`, `core.hooksPath`, `include.path`; uv's config; npm's `.npmrc`) and whether an agent's commit can set it.
   - Fix the ones SSSF runs on its own during a launch or resume, with no engineer step: pass git `-c core.fsmonitor=false -c core.hooksPath=/dev/null` or the like, and keep uv out of the worktree.
   - Document the ones the engineer starts (`just quality`, `just rebase`), next to the text the first two runs added to `config.md`.
4. **`budget._root()`'s refusal is clear.** Its message names the folder and says how to fix it, for example `git config --global --add safe.directory <path>`. `adw_tickets` prints the message, not a traceback. The spec says whether the operator container still launches, and if not, what the engineer changes.
5. **Docs.** `config.md`'s `host_agents` text says what this run closed and what's still open. Add `docs/open.md` rows for:
   - a tool call named `worktree` shadowing the run's record. `worktree.recorded` matches `name='worktree'` events with no type filter, so a resume is refused or crashes with `KeyError: 'worktree'`. It's Low, a denial of service.
   - the `.env` an agent could commit, if the spec leaves a path for it to load on the host.

## The bar

- **H1 refuses.** Through the existing test rig, a VM run whose worktree holds a committed `.python-version` naming `./tools/python3`, with that script writing a marker, resumes with `--adw-id` and no flag, and the marker is never written. The same holds for another chain joining the run. The test fails on `sssf/d0200ae6`.
- **H2 refuses.** Under `sandbox: e2b` with the SDK missing, a no-flag resume's re-exec never runs with the worktree as its working folder, never reads the worktree's `uv.toml`, `.python-version` or `.env`, and never imports the worktree's modules. The test records what `execvp` was asked and fails on `sssf/d0200ae6`.
- **Git config can't name a program.** A committed `.git`-like folder or `GIT_CONFIG_*` setting `core.fsmonitor` doesn't run during a launch or a resume's host steps.
- **The Python is unchanged.** A new run and a resume pass the VM the same `UV_PYTHON` as on `sssf/d0200ae6`, for a repo with a `.python-version` in its main checkout and for one without.
- **The refusal is readable.** A checkout git rejects stops with a message naming the folder and the fix. `adw_tickets` shows it without a traceback.
- **Nothing else changes.** Every existing test passes. A new VM run, a resume, a host run under `host_agents: true`, `merge` and `discard` behave as on `sssf/d0200ae6`.

## Running the tests

Run the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. No test starts a real VM or a real agent: use the existing rigs and fakes, and a fake SDK. A test that needs uv to run a planted interpreter starts a subprocess. Keep runs light, since the agentsh event store slows under heavy load (`docs/open.md`).

## Out of scope

- Refusing `just quality` and `just rebase`, or running them in a VM. Document them.
- Refusing an agent's commit of `.env`, `uv.toml` or `.python-version` by name. If the spec finds that's the only fix for a step, say so and add an open.md row instead.
- The P2 row on committed secret files, and the other open rows.

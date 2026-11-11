# Refuse a wrapped agent whose workspace shares files with the engineer's

The hardlink leftover from run d48bd070's spec, `specs/tasks/guard-agentsh-pin-and-root.md`, and two findings from that run's review. Start from `main`, which has run d48bd070 merged as 7ab1149. The hub session ran the probes below on 2026-11-10, on build 05b3777f, under the hub's installed policy.

## The problem

agentsh judges a write by its path. A hardlink gives one file two paths. So a file that has one link in the workspace and another outside it can be changed through the workspace link, though the policy denies the outside path. Agents can't make such a link: a hardlink's two ends are both judged, and the outside end is denied. Tools outside the wrap make them:

| Tree | Files with another link | The other link is in |
|---|---|---|
| sssf-sandbox's `.venv` | 492 of 642 | `~/.cache/uv`, uv's default link mode |
| the dashboard's `node_modules`, `.claude/skills/sssf/apps/visualizer/`, in the hub and sssf-sandbox | 5,176 of 5,176, and 5,176 of 5,184 | `~/.bun/install/cache`, bun's default on Linux |
| the hub's `.git` | 1,565 of 2,517, and 0 since the engineer re-cloned the install clone with `git clone --no-local` on 2026-11-10 | `~/github/sssf-install/.git`, the install clone, made by a plain `git clone` of the hub, which hardlinks objects |

The probe: a decoy file in `~/.cache/uv`, hardlinked into the hub as uv links one. Under the wrap, a direct append to the cache path was refused, and an append to the workspace link went through and changed the cache copy.

What that reaches:

- **The engineer's caches.** uv and bun link a cached file into every tree they build. A file an agent changed in the cache then runs, outside the wrap, in the engineer's other projects and in any tree SSSF builds outside the wrap.
- **The install clone.** Agents may write the hub's git directory. Rewriting a loose object in place changes the same object in `~/github/sssf-install`. Git doesn't check an object's hash when it reads it. The engineer runs `agentsh_install.py` from that clone and then its sudo lines, so the agent's code would print the diff the engineer reads before them.
- **Trees SSSF builds itself.** `worktree_setup` and the quality checks run outside the wrap with `operator_env()`, the engineer's environment, so a `uv sync` or `bun install` there links new files to the engineer's caches mid-run.

### Two findings from run d48bd070's review

- **A broken promise.** `check_machine` refuses when `BINARY` or `CONFIG` changed after the service started, but `os.stat` follows a link, so a link re-pointed after the start passes on its target's older change time.
- **Constants outside the pin line.** The rendered config's pin line names `BINARY` and `PINNED_SHA256`. A surviving edit to `SERVER`, `CONFIG` or `POLICY_DIR` in the checkout's `agentsh_guard.py` isn't caught: an agent's `SERVER` reaches the wrap as `AGENTSH_SERVER`, and an agent's `CONFIG` makes the pin line agree with itself after a restart. `config.md` says only an edit rewriting the check itself gets past "differs", which overstates it.

## What should change

For runs under `local_guard: agentsh` only:

1. **Each wrapped agent's launch walks its workspace and the git common dir.** A regular file with more links than the walk found inside those trees has a link elsewhere, and the launch refuses before the wrap starts. The message names a few such files, their trees, and how to rebuild each:
   - a uv venv: `UV_LINK_MODE=copy uv sync --reinstall`;
   - `node_modules`: bun's copy backend;
   - a git clone made with hardlinks: re-clone it with `git clone --no-local`.

   The walk follows no link and skips nothing else. It took 0.02 seconds on each of the hub and sssf-sandbox. The spec says what the walk costs on a tree with 100,000 files.
2. **Trees SSSF builds stay unlinked.** `cache_env` and the environment of `worktree_setup`, the quality checks, and `just rebase`'s setup set the copy mode for uv, `UV_LINK_MODE=copy`, and for bun and pnpm. The spec finds bun's and pnpm's settings in their docs and cites them. If one has no environment setting, it says so and names the limit. The commands outside the wrap keep the engineer's cache folders. They only stop linking from them, so they don't read the agents' cache.
3. **SSSF's own `bun install` in the justfile's dashboard recipes** uses bun's copy backend, so the dashboard's `node_modules` doesn't link to `~/.bun` again after the engineer rebuilds it.
4. **`config.md`, in "Running local agents under agentsh"**, replaces the hardlink limit run d48bd070 added. It says the launch refuses while a workspace file has a link outside it, and why. It gives the rebuild commands and the one-time steps for the hub and sssf-sandbox. It says agents may still make hardlinks between workspace files, which the check allows.
5. **`install.md`** says to make the install clone with `git clone --no-local`, and why.
6. **`check_machine` refuses when `BINARY` or `CONFIG` is a link.** Both are root-owned files the install writes, never links.
7. **The pin line names every path constant** `check_machine` and `start()` use: `BINARY`, `CONFIG`, `POLICY_DIR`, `SERVER` and `API` with `PINNED_SHA256`. `config.md` says which constants it covers, and that a surviving edit to other `adws/` code can still change what the next run does. The `writes` check puts such an edit back after each phase.

## The bar

The earlier bars stand. Under `local_guard: agentsh`:

- A wrapped agent never starts while a regular file in its workspace or the git common dir has a link outside them. That covers a plain run, a worktree run, and links made between two agent starts by a check or a setup command.
- A tree built by `worktree_setup`, a quality check or the dashboard recipe shares no inode with the engineer's uv or bun cache.
- After the engineer's one-time rebuild, a plain run and a worktree run in sssf-sandbox start their agents, and so does a guard run in the hub.
- `check_machine` refuses a link at `BINARY` or `CONFIG`, and an edit to any constant the pin line names refuses as "differs".
- Runs under `local_guard: text` don't change.

A refusal of a tree with no outside link is a false positive.

Any change to the guard beyond the seven above is a finding.

## Testing

Unit tests make temp trees: a file hardlinked to a temp "cache" outside the tree refuses; two links inside the tree pass; a link reached only through a symlink isn't followed; and the git common dir is walked on a worktree layout. A test checks the copy settings in `cache_env` and in the environments of `worktree_setup` and the checks. Tests for items 6 and 7 use temp files and monkeypatched constants, as run d48bd070's did.

The tests never touch the real caches, `.git` or `~/github/sssf-install`.

## Before and after the merge

This run is under agentsh, and the walk only reaches the runner after the merge, so the run isn't stopped by today's linked trees. The builder runs the suite from a copy outside the repo, as run d48bd070's did.

After the merge, the engineer:
1. rebuilds sssf-sandbox's `.venv` and both repos' dashboard `node_modules` with copies;
2. reinstalls both repos' policies and restarts the service.

The hub session then checks that each launch starts, and that a decoy hardlink to a temp file outside the workspace refuses.

## Out of scope

The fork's code: an engine check of a file's link count on open would be a fork patch. pi's policy. Runs under `local_guard: text`. Hardlinks made outside SSSF between runs, beyond refusing the next launch.

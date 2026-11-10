# Fix npm and uv's managed Pythons under agentsh, and record folder listing

Item 12 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`. The design doc's section "The tool-config run under agentsh, 130ca6e0" has the probes, from the hub and sssf-sandbox on 2026-11-10, under the installed policies of skill 9ccf50d, with the variables `cache_env` sets.

## The problem

1. **npm fails under the wrap.** npm opens its user config, `~/.npmrc`, before anything else. It doesn't exist on this machine, so outside the wrap npm gets ENOENT and goes on. Under the wrap the policy refuses the open, npm gets EACCES, and every npm command stops with "error loading user config". `npm init` also reads `~/.npm-init.js` and is refused the same way. `main` before 9ccf50d had no rule for either file, so npm failed there too. In the probes, with `npm_config_userconfig` pointing at a file in the agent cache, `npm install` worked; `npm init -y` worked with `npm_config_init_module` moved there as well.
2. **`uv run --with` fails on a venv built on uv's managed Python.** uv picks the project `.venv`'s interpreter when a repo has no `.python-version`. In sssf-sandbox the engineer built that venv outside the wrap on uv's managed 3.13, under `~/.local/share/uv/python`. `uv run --no-project --with pyyaml` makes a throwaway environment in the agent cache whose `python` links to that interpreter, and the policy refuses the link: `sssf-system-link` allows targets under `/usr/bin` and `/bin` only. Agents may already read `~/.local/share/uv/python`, and may not write it. In the hub, whose venv is on `/usr/bin/python3`, the same command works, and so does adding `--python /usr/bin/python3` in sssf-sandbox.
3. **The folders above the workspace can be listed.** In ptrace mode agentsh judges opening a folder as `open`, and reading its entries isn't a separate check. So the `open` that `sssf-config-folders` allows lets `ls` list `/`, `/home`, the home folder, `~/github` and the folders between there and the workspace. `/`, `/home` and the home folder were listable before 9ccf50d. `~/github` wasn't: an agent can now read the names of the engineer's other repos, though nothing inside them. Run 130ca6e0's spec promised `list` stays denied on those folders, and that doesn't hold on the engine.

## What should change

1. **`cache_env` moves npm's user config and init module into the agent cache**, with `npm_config_userconfig` and `npm_config_init_module`, for both harnesses. The spec checks npm's docs for any other file npm reads from the home folder at start, and moves it too or says why not. It says what an agent loses: the engineer's `~/.npmrc` on a machine that has one, private registry settings and tokens included, and that a repo needing those has to say so in its own `.npmrc`, which npm still reads from the project.
2. **`SYSTEM_LINK` adds uv's managed Pythons**, `${HOME}/.local/share/uv/python/**`, as allowed link targets. The spec shows an agent still can't write anywhere under that folder, so a link there can't be used to change an interpreter the engineer runs. It says whether `UV_PYTHON_INSTALL_DIR`'s folder in the agent cache needs the same, and why.
3. **`config.md` records the listing as a limit** in "Running local agents under agentsh": under agentsh an agent can list every folder from `/` down to the workspace, so it can see the names of the engineer's other repos and of the sessions and worktrees in `adws/adw_data`, but not read inside them. It says what would close it: a fork patch that judges a folder opened for its entries as `list`. The spec also fixes run 130ca6e0's line that promised otherwise, wherever it is.

## The bar

The earlier bars stand. Under the wrap, `npm install`, `npm init -y`, `npx` and `uv run --with` on a venv built on a managed Python work. An agent still can't write the engineer's `~/.npmrc`, `~/.npm`, `~/.cache/uv`, `~/.local/bin` or anything under `~/.local/share/uv`, and still can't read a file in a sibling repo. A link target outside the three trees above stays refused. Any change to the guard beyond the three above is a finding, and so is a `config.md` line that promises more than the probes show.

## Testing

Unit tests: `cache_env` has both npm variables pointing into the folder, for both harnesses, and the rendered policy's `sssf-system-link` names the new tree and nothing else new.

After the merge and the engineer's reinstall, the hub session reruns the 2026-11-10 probes in the hub and sssf-sandbox: the npm commands above, `uv run --no-project --with pyyaml` in sssf-sandbox, the refused writes, a link to `/etc/shadow`, and a Claude Code agent under each policy.

## This run is under agentsh

The hub is on `local_guard: agentsh`, and run 130ca6e0 passed under it. The builder runs the suite the way that run's did, from a copy outside the repo, since `adws/tests` and the skill suite read the hub's `.env` at import.

## Out of scope

The fork patch for listing. pnpm, which fails on this machine without the wrap. The three items left by run 130ca6e0 (Next item 11). pi's policy.

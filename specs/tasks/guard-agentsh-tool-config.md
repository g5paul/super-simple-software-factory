# Let tools read their config above the workspace, and give agents their own caches

Item 4 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`, once the ninth fork patch, which lets agentsh judge relative link targets, is installed. The design doc's section "Tool config and caches, 2026-11-09" has the evidence and the engineer's decisions, so don't reopen them.

## The problem

The facts below come from uv 0.10.3, npm 11.6.2, bun 1.4.4 and Node 24.11.1. Each tool ran under a policy that allows every file, and the fork's engine then judged each path it touched against the hub's installed policy.

1. **Config lookups above the workspace fail.** uv reads `uv.toml` and `pyproject.toml` in every folder from its working folder up to `/`. bun and Corepack, which runs pnpm and yarn, read `package.json` in each, and bun also reads `~/.bunfig.toml`. uv and bun also open the folders above the workspace themselves. A denied file answers `EACCES` even when it doesn't exist. uv and Corepack stop on that, where `ENOENT` would let them go on. bun goes on. `CONFIG_SEARCH` in `agentsh_guard.py` allows uv's two names at `/`, `/home` and `$HOME` only. So:
   - on a plain run of a repo under `~/github`, every uv command fails with `error: failed to open file ~/github/uv.toml: Permission denied`. sssf-sandbox fails this way;
   - on a `--worktree` run, uv fails on `<main>/adws/adw_data/worktrees/uv.toml` first, and every folder from there up is denied too;
   - under the hub's policy, pnpm fails on `<main>/adws/adw_data/package.json`. Corepack's pnpm is broken on this machine outside the wrap too, so pnpm can't be tested here past that point.
2. **Agents write the engineer's own caches.** `TOOL_WRITE` lets an agent write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm`, `~/.cargo/registry` and `~/.cargo/git`. The engineer's own tools, run outside the wrap, reuse what's there. uv links unpacked wheels from its cache into new environments without checking them again. `~/.local/share/uv` holds the Pythons and tools uv installs, which are programs. cargo builds extracted crate sources. So an agent that edits a cached package gets its code run on the host, outside SSSF, in any repo the engineer works in later. Quality checks also run outside the wrap, by design, but they only run this run's workspace. A cache reaches the engineer's later work everywhere.
3. **Some caches are missing.** bun's `~/.bun/install/cache` and Corepack's `~/.cache/node/corepack` aren't allowed, and `mkdir ~/.npm` is denied. npm and bun carry on without them. Corepack doesn't.

## What should change

1. **An agent can read the config files tools look for above its workspace.** For every folder above the workspace, on a plain run and on a `--worktree` run, allow the read operations on the names these tools look for. Also allow what opening the folder itself needs. Take the names from each tool's docs or source, cite the source in the spec, and cover at least `uv.toml`, `pyproject.toml`, `package.json` and bun's config names. The spec lists every path the rendered policy allows, for the hub's own layout and for a repo at `~/github/<repo>`. Only the folders above the workspace count. A sibling repo's files stay denied, as does every file in those folders that isn't on the list. Also say what such a file can hold that an agent then reads, such as a `uv.toml` index URL carrying a token, and record it in `config.md` as a known limit.
2. **Agents get caches of their own.** The engineer decided this on 2026-11-09. Under `local_guard: agentsh`, the wrap points each tool's cache and install folders at a folder only wrapped agents of this repo use, through the tools' environment variables. One folder per repo means one repo's agent can't plant a package for another's. The variables include `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_TOOL_DIR`, `npm_config_cache`, `BUN_INSTALL_CACHE_DIR`, `COREPACK_HOME`, pnpm's store, and cargo's registry and git folders. The spec finds the full list from each tool's docs, and says for each what moving it changes, for example which of `~/.cargo`'s config the agent still reads. The policy removes the write allow on the engineer's own caches and tool folders and allows the agent folder instead. The spec decides whether agents may still read the engineer's caches, and says why that's safe. Both harnesses get the same variables.
3. **`config.md`** replaces its gap line with what works now, says where agents' caches live and that a first install in a repo downloads again, and records the limit from item 1.

## Testing

The chain's agents run under agentsh themselves and can't install a policy. Test the rendered policy's paths with the Python emulation earlier runs used. Before the sudo install, the hub session checks the rendered policy on the fork's engine (`LoadFromFile`, `CheckFile`) against the paths in the spec. The check under the service is the engineer's, after the install.

## The bar

The earlier bars stand.
- **Bypass:** a read of any file above the workspace that isn't on the config list, or any write above the workspace outside the agent cache folder.
- **Harm:** a write by a wrapped agent that reaches a file the engineer's own tools later run outside the wrap, whether it sits in a cache, a tool folder or anywhere else the change touches.
- **Broken promise:** a lookup of a listed name above the workspace that still gets `EACCES`, or a tool that still writes the engineer's cache under the wrap.
- **Not findings:** the config list is a record of what tools read, not a list of exceptions. A name on it without a cited source is a finding.

## Out of scope

Relative link targets, which the ninth fork patch covers before this run. Answering `ENOENT` for a denied file that doesn't exist, which would need a fork change. pi's own policy, the network rules, sudo, and turning agentsh on in another repo.

# Close the pin check's holes and the engine's raw `${PROJECT_ROOT}`

Items 1, 10 and 11 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Start from `main`. The hub session ran the probes below on 2026-11-10, on build 05b3777f, under the hub's installed policy and on the fork's policy engine at that commit.

## The problem

1. **An agent's edit to the project's `agentsh_guard.py` can repoint the pin.** `check_machine` hashes `BINARY` and compares it with `PINNED_SHA256`, and both constants come from the checkout's own copy of the module. The "differs" checks cover the installed config and policies, not these two constants. So an edit that sets `BINARY` to a file the agent wrote, and `PINNED_SHA256` to its hash, passes the check, and the next run starts that file as the wrap, outside any policy. The `writes` check puts back a change to `adws/adw_modules/` after each phase, and this run's process has already imported the module, so the edit acts only on a later run, and only if it survives.
2. **A service still running an older binary passes.** `check_machine` hashes the file on disk. After an install that replaces `/usr/bin/agentsh` without a restart, the service runs the old build and the check passes. A user can't see the service's running binary: `/proc/<pid>/exe` is denied. But `systemctl show agentsh -p ExecMainStartTimestamp --timestamp=unix` gives the start time (`@1794357427` on 2026-11-10), and `os.stat(BINARY).st_ctime` gives when the file last changed (`1794352487.9`). The same holds for `/etc/agentsh/sssf.yaml`: config.md lists "a service still running on an older config" as a limit, since only a restart loads it.
3. **The engine expands `${PROJECT_ROOT}` as raw text.** `ExpandVariables` in the fork's `internal/policy/vars.go` substitutes the value with no glob escaping, and the rule then compiles with gobwas/glob, where `*`, `?`, `[`, `]`, `{`, `}` and `\` are special. On the engine, with rules `${PROJECT_ROOT}/**` (allow all) and `${PROJECT_ROOT}/.git/config` (deny write):

   | Root | Write in the root | Write elsewhere |
   |---|---|---|
   | `/h/github/plain` | allowed; `.git/config` denied | `/h/github/other/f.txt` denied |
   | `/h/github/repo[old]` | denied by default-deny | `/h/github/repoo/f.txt` **allowed** |
   | `/h/github/re*po` | allowed | `/h/github/rexyzpo/f.txt` **allowed** |
   | `/h/github/a{b,c}` | denied | `/h/github/ab/f.txt` **allowed** |
   | `/h/github/q?x` | allowed | `/h/github/qzx/f.txt` **allowed** |
   | `/h/github/back\slash` | denied | `/h/github/backslash/f.txt` **allowed** |
   | `/h/github/d${HOME}x` | denied | |

   So a checkout or worktree whose path holds one of those characters gives an agent every operation on sibling folders the pattern matches. SSSF's own literal paths go through `glob_escape`, and on the engine each escaped root matched itself and none of the siblings above. `${HOME}` in the rendered rules is expanded the same raw way.
4. **The agent-cache folder.** run 130ca6e0 left this open, and config.md says "SSSF makes `adws/adw_data/agent-cache` the same way", meaning it replaces a link in place of a folder. `start()` makes it with `Path.mkdir(parents=True, exist_ok=True)`, which follows a link. On build 05b3777f under the installed policy, the swap the run's review described is closed: a link from the cache to `~/.local` is refused, absolute or relative. On a worktree run the agent can't move the cache. On a plain run it can move the cache away and put a link to a folder in its workspace in its place, and SSSF's `mkdir` then follows that link.
5. **uv's and bun's hardlinks.** A tree built outside the wrap links its files to the engineer's caches. In sssf-sandbox, 492 of the `.venv`'s 642 files share an inode with `~/.cache/uv`. In both repos, all 5,176 files of the dashboard's `node_modules` share one with `~/.bun/install/cache`. `worktree_setup` runs outside the wrap with the engineer's environment, so the trees it builds do the same. Under the hub's policy, a direct write to a decoy file in `~/.cache/uv` was refused, and an append through the decoy's hardlink in the workspace changed the cache copy. An agent can't make a new hardlink to a cache file, since a hardlink's two ends are both judged. config.md says agents no longer write `~/.cache/uv`, which isn't so for these trees. A check for this is a separate run; this run records it.
6. **`config.md`'s example pi pattern**, `(^|[;&|]\s*)pi\b`, misses pi after a wrapper word, such as `env pi`, `timeout 60 pi` or `bash -c 'pi'`. Run 6e904be8's review found it.

## What should change

1. **The rendered config names the pin.** `render_config` ends the config with a comment line that names `BINARY` and `PINNED_SHA256`, so the byte comparison `check_machine` already makes refuses an edit to either constant with the "differs" message. A comment, since agentsh reads the YAML and the line must not change what it loads. The install script and the check share `render_config`, so both render the same text.
2. **The service must have started after its binary and config last changed.** `check_machine` refuses, with a message that says to restart the service, when `BINARY`'s or `CONFIG`'s `st_ctime` is later than the service's `ExecMainStartTimestamp`, or when that timestamp can't be read. The message gives the restart command.
3. **No root, and no home folder, with a character the engine treats as glob syntax.** Under `local_guard: agentsh`, the launch refuses when the guarded root or `$HOME` holds any of `*?[]{}\$`, and says why: agentsh expands those paths into its rules as raw text. The check sits where `check_root` and `start()` already check the root, so it covers plain and worktree runs.
4. **`start()` makes the agent cache without following a link.** A link at the agent cache's path is replaced by a real folder, the way SSSF already makes the session folders, or the launch refuses. Either way, nothing SSSF does outside the wrap follows a link an agent put there.
5. **`config.md`, in "Running local agents under agentsh"**:
   - says the pin and the binary path are in the rendered config, so an edit to either refuses as "differs", and that the check refuses until the service restarts after an install. It drops "a service still running on an older config" from the limits. It keeps an accurate line on what an agent's edit to other `adws/` code can do: the `writes` check puts it back after each phase, and the running process doesn't reread it;
   - names the glob characters a checkout path may not hold under agentsh;
   - fixes the agent-cache line to match the code;
   - adds a limit for hardlinks: a tree built outside the wrap, by the engineer or by `worktree_setup`, can share files with the engineer's uv and bun caches, and an agent's write to its copy changes the cache copy. It says how to rebuild a tree with copies, `UV_LINK_MODE=copy uv sync --reinstall` for a uv venv and bun's `--backend=copyfile` for `node_modules`, and it removes the claim that agents no longer write `~/.cache/uv`;
   - lists the example pi pattern's miss after a wrapper word as a text-guard limit, and points to `deny_exec`, which refuses the exec under agentsh whatever comes before it.
6. **`install.md`** says to restart the service after each install, if it doesn't already.

## The bar

The earlier bars stand. Under the rendered policy and the new checks:

- An edit to the checkout's `PINNED_SHA256` or `BINARY` makes `check_machine` refuse as "differs".
- `check_machine` refuses when `BINARY` or `CONFIG` changed after the service started, and passes on this machine after a restart.
- A root or `$HOME` holding any of `*?[]{}\$` refuses before an agent starts, on a plain run and a worktree run. A root without them works as before.
- With a link at the agent cache's path, no SSSF code outside the wrap creates, writes or reads through it.
- Every config.md line promises no more than the probes show.

Any change to the guard beyond the six above is a finding.

## Testing

Unit tests:
- `render_config` ends with the pin line;
- `check_machine` refuses on an edited constant and on a changed time, using monkeypatched constants and a fake `systemctl`;
- the root check refuses each character in a temp checkout named with it;
- `start()` doesn't follow a link at the agent cache's path.

The tests use temp folders, never the real `/usr/bin/agentsh` or `/etc/agentsh`, and they don't name the pin's value.

After the merge, the engineer reinstalls both repos' policies and restarts the service. The hub session then checks that `check_machine` passes in both repos after the restart, and reruns the agent-cache probes.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The runner checks its own checkout's module, so the worktree's change doesn't stop this run. The builder runs the suite the way run cea6fe8d's did, from a copy outside the repo, since `adws/tests` and the skill suite read the hub's `.env` at import.

## Out of scope

The hardlink check itself, which is the next run. The fork's code. pi's policy. pnpm's `npm_config_*` warning. `/proc/<pid>/cwd` and `cmdline`.

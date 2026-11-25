# Close Node's compile cache for every pi SSSF starts

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `agent_pi.py` and the other modules that start pi, `sandbox_remote.py` and `container/pi_token_broker.py` among them. `agent_pi.py` is guard code, so it goes through the chain.

## What broke

`docs/open.md` has the P1 row ("Node's compile cache lets an agent leave compiled code for every later pi"). Spec review 1 of guard run 31eae620 found it, from source. What the code at `main` shows, checked on 2026-11-25:

- **pi turns the cache on.** pi 1.4.0's bin is `dist/bundle/cli.js`, whose first lines import `enableCompileCache` from `node:module` and call it. Node then keeps V8 code cache for every module pi loads in `$TMPDIR/node-compile-cache/<node version>-<arch>-<hash>-<uid>/`. On this machine that's `/tmp/node-compile-cache/v24.11.1-x64-ef5a0af0-1000/`, with 1093 entries, written by pi and any other Node program that turns the cache on.
- **Any agent can write there.** agentsh's pi and Claude policies allow `/tmp/**` (`sssf-temp`), and under `local_guard: none` nothing stops it. The files are mode 600, owned by the engineer, which is the user every local agent runs as.
- **Every later pi reads it.** Agents' pis, starter children, and the pi SSSF starts unwrapped on the host: `_pi_catalog`, `_host_token`, `trial.pi_problems`, `pi_token_broker.mint`. The host's pi runs as the engineer with `operator_env()`, which keeps `.env`'s keys. So a rewritten entry that runs code other than its source would run it outside every wrap.
- **What Node and V8 check.** Node accepts an entry when the source's hash matches, then hands the code cache to V8, which checks its version, flags and source hash. None of these is a secret. Whether a crafted entry can run code other than the source was not tried.
- **The switch.** On this machine's Node 24.11.1, `module.enableCompileCache()` returns status 3 (`DISABLED`) when `NODE_DISABLE_COMPILE_CACHE` is set to any value, `0` included, and status 1 (`ENABLED`) without it. `NODE_COMPILE_CACHE=<dir>` moves the folder instead.
- **What 31eae620 did for jiti.** `agent_pi.NO_EXTENSION_CACHE` (`JITI_FS_CACHE=false`) is set last on every pi agent's environment (`agent_pi.py:859`), `sandbox_remote.PI_NO_CACHE` repeats it for a VM, and `subagents.ts` sets it on the child. The host's pi starts don't get it, since they load no extension.

## The change

1. **No shared compile cache.** Every pi SSSF starts reads no compile cache another process could have written. That covers agents on the host, under agentsh, in the container and in a VM, starter children, and each host start in the list above, plus any other the spec finds (scripts included). Say in the spec how, and why an agent can't undo it for a later pi. The candidates are `NODE_DISABLE_COMPILE_CACHE=1`, or `NODE_COMPILE_CACHE` pointed at a folder SSSF makes per call. Pick the smallest. If it's an environment setting, put it beside `NO_EXTENSION_CACHE` so one place names both, and set it last as that is.
2. **The child.** Say whether a starter child inherits the setting from its parent pi's environment (`{...process.env, ...}` in `subagents.ts`) or needs it set explicitly, as `JITI_FS_CACHE` is.
3. **The cost.** Measure a real `pi --list-models --offline` start with and without the setting, using an empty scratch agent dir and a scratch `TMPDIR`, and put the number in `config.md`.
4. **What else trusts it.** Say whether anything else SSSF starts on the host enables Node's compile cache: Claude Code, `bun`, `npx`, the dashboard. Fix only pi here, and file any other as a new `docs/open.md` row.
5. **Docs.** Delete the `docs/open.md` row named above, or cut it to what's left. `references/config.md`'s "Discovery is off" bullet, or wherever 31eae620 put the jiti cache, says both caches are off for every pi SSSF starts and why.

## The bar

- **Every start.** A test with a stand-in `pi` that records its environment: every agent call (on the host, in the container, in each VM mode) and every host start in the list carries the spec's setting, and an operator value can't override it. On `main` none does.
- **The real switch.** A test that runs `node` the way pi's bin does (`enableCompileCache()` from an ES module) with the spec's environment sees status `DISABLED`, or sees the folder the spec picked, and no new entry under the test's scratch `TMPDIR/node-compile-cache`. Without the setting it sees `ENABLED`. Mark it to skip where `node` is missing.
- **The child.** A starter child's environment carries the setting, under SSSF and in interactive pi if the spec sets it explicitly.
- **Nothing else changes.** Every existing test passes on the host, `test_agent_pi.py`, `test_pi_extension_cache.py`, `test_subagents_extension.py` and `test_sandbox_remote.py` among them.

## After the merge, the engineer's proof

Update the hub's install and commit it. Run one ChatGPT pi call through `adw_prompt.py` whose agent prints `$NODE_DISABLE_COMPILE_CACHE` (or the spec's setting), and one starter-roster scout whose subagent does the same. Then run the host's model list the way `_pi_catalog` does and see no new entry in `/tmp/node-compile-cache` (count before and after).

## Running the tests

The planner and the builder run under agentsh. Run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Don't run a real pi against `~/.pi/agent`, don't write anything under `~/.pi`, and don't read, change or delete the entries in `/tmp/node-compile-cache` or `/tmp/jiti`; give every real `node` or `pi` a scratch `TMPDIR`.

## Out of scope

- Crafting a code cache entry that runs other code. The fix doesn't depend on whether it can be done.
- The engineer's own interactive pi and other Node tools that read `/tmp/node-compile-cache`. The spec adds them to the P2 row on an interactive pi's `/tmp/jiti`, or files a row of their own.
- The `/skill:` race and the three Low rows on the starter subagents.
- The text checks' own gaps. The text guard is frozen.

# Close Node's compile cache for every pi SSSF starts

Guard change. Run with `--worktree --base main` on the hub's own config. Builder: `guard_builder`.

## Problem

pi 1.4.0's bin (`dist/bundle/cli.js`) calls `enableCompileCache()` from `node:module` on its second line. Node then reads and writes V8 code cache in `$TMPDIR/node-compile-cache/<version>-<arch>-<hash>-<uid>/`. Any agent can write that folder (`/tmp/**` is allowed under agentsh, and nothing guards it under `local_guard: none`), and every later pi reads it, including the pi SSSF starts unwrapped on the host with the operator's environment. Whether a crafted entry can run code other than its source was not tried, and the fix does not depend on it.

## Threat model

- An agent runs as the engineer's own user. It can create, rewrite and delete any file under `/tmp`, so it can write `/tmp/node-compile-cache/**` whatever SSSF does. This change does not stop the write. It stops SSSF's pis from reading the folder.
- An agent can change the environment of its own process and of the processes it starts. It cannot change the environment SSSF builds, in the orchestrator's process, for a later pi start. The setting's value comes from a constant in protected guard code, set after every other step.
- An agent can start a pi of its own with the variable removed (`env -u NODE_DISABLE_COMPILE_CACHE pi ...`). That pi reads the shared cache inside the agent's own wrap and gains nothing the agent lacks. It also writes entries there, which is the same write as above.
- What this guard can't stop at its level: a Node program SSSF doesn't start (the engineer's interactive pi, `npm`, `npx`, vite) still reads the folder. See Out of scope.

## Baseline

`main` at `95b1d48`. Every finding is judged against that commit. At the baseline no pi SSSF starts carries `NODE_DISABLE_COMPILE_CACHE`, so every one reads `$TMPDIR/node-compile-cache`.

## What the planner checked (2026-11-25, Node v24.11.1, pi 1.4.0, bun 1.4.4)

All run with a scratch `TMPDIR`, never against `/tmp/node-compile-cache`.

| Environment | `enableCompileCache()` returns | Folder made under scratch `TMPDIR` |
| --- | --- | --- |
| neither variable | status 1 (`ENABLED`), `directory: <TMPDIR>/node-compile-cache` | yes, one entry per module |
| `NODE_DISABLE_COMPILE_CACHE=1` | status 3 (`DISABLED`), message `Disabled by NODE_DISABLE_COMPILE_CACHE` | none |
| `NODE_DISABLE_COMPILE_CACHE=` (empty) | status 3 | none |
| `NODE_DISABLE_COMPILE_CACHE=1` and `NODE_COMPILE_CACHE=<dir>` | status 3 | none, `<dir>` not made |
| `NODE_DISABLE_COMPILE_CACHE=1` and `enableCompileCache(<dir>)` | status 3 | none |

- `module.constants.compileCacheStatus` is `{FAILED: 0, ENABLED: 1, ALREADY_ENABLED: 2, DISABLED: 3}`.
- pi's `cli.js` names neither variable, so pi does not override the switch.
- Cost, `pi --list-models --offline` with `HOST_FLAGS` and `--no-approve`, empty scratch agent dir, scratch `TMPDIR`, cwd `/`, 7 runs each, under agentsh: warm cache median 0.35 s, `NODE_DISABLE_COMPILE_CACHE=1` median 0.40 s (cold fill 0.44 s). The model list wrote 20 cache entries. An agent's `-p` start loads more of pi, so it loses more; that was not measured because it needs a model call.
- From memory, not checked here: Node's docs give `NODE_DISABLE_COMPILE_CACHE` as added in v22.8.0 with `module.enableCompileCache`. pi's `engines` minimum is 22.19.0 (`sandbox_remote.TOOLS`), so any Node that runs pi knows the switch. The real-switch test below proves it on the machine's Node.

## Decisions

1. **The setting is `NODE_DISABLE_COMPILE_CACHE=1`.** It is the smaller candidate: one fixed variable, no folder to make, own or clean up per call, and it wins over `NODE_COMPILE_CACHE` and over a directory argument. With it pi neither reads nor writes a compile cache.
2. **Why an agent can't undo it for a later pi.** SSSF sets it in the environment it builds for each start, last, from a constant in `agent_pi.py` (protected). Any value disables the cache, `0` and empty included, so an operator or `.env` value can only keep it off. `NODE_COMPILE_CACHE` in the operator's environment is left alone because the disable wins over it.
3. **A new constant, not a wider one.** `agent_pi.NO_EXTENSION_CACHE` and `sandbox_remote.PI_NO_CACHE` stay `{"JITI_FS_CACHE": "false"}`: `test_agent_pi.py` pins both to that value, `test_pi_extension_cache.py` uses the first as the jiti setting alone, and the host's pi must not gain `JITI_FS_CACHE` (`test_the_hosts_pi_catalog_gets_no_jiti_setting`). The new constant sits on the next line.
4. **The child inherits it. `subagents.ts` is not changed.** The child's environment is `{ ...process.env, JITI_FS_CACHE: "false" }`, and under SSSF the parent pi's `process.env` carries `NODE_DISABLE_COMPILE_CACHE=1` because `agent_pi.run` sets it last. A command the parent's model runs can't change the parent pi's own `process.env`. Inheriting also covers a repo whose copy of `subagents.ts` under `<data_dir>/harness_engineering/` differs from the template. An interactive pi's child has the setting only when the engineer's shell does; its parent reads the shared cache already, so setting it on the child alone would close nothing. A test pins the inheritance.
5. **Image builds are left alone.** `pi --version` in `templates/container/Containerfile`, in `sandbox_openshell.py`'s image lines and in `scripts/e2b_template.py` runs in a fresh build layer before any agent exists, so no other process could have written a cache there.

## Bar

Against the baseline, the change must:

- Put `NODE_DISABLE_COMPILE_CACHE=1` on every pi SSSF starts: agent calls on the host (`local_guard: none`), under agentsh, in the operator container and in every VM provider (`Remote.start`, which E2B, Vercel and OpenShell all inherit), the VM's `pi --version` tool check, and the host starts `_pi_catalog`, `_host_token` (both forms), `trial.pi_problems` and `pi_token_broker.mint`.
- Keep it there whatever the operator's environment holds for `NODE_DISABLE_COMPILE_CACHE` or `NODE_COMPILE_CACHE`.
- Leave a starter child's environment carrying it under SSSF.
- Make a real `node` that calls `enableCompileCache()` from an ES module with that environment report `DISABLED` and write nothing under its `TMPDIR`.

It must never:

- Change `NO_EXTENSION_CACHE`, `PI_NO_CACHE`, pi's argv, its cwd, or any other variable of any start.
- Give the host's pi `JITI_FS_CACHE`, or a Claude Code start in a VM either cache variable.
- Read, write or delete anything under `/tmp/node-compile-cache`, `/tmp/jiti` or `~/.pi`, in code or in a test.
- Fail any existing test.

Usual guard bar: no agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. Here that means no pi SSSF starts loads a compile cache entry an agent wrote.

## Builder steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Directly under `NO_EXTENSION_CACHE` (line 86) add:

  ```python
  # Set last on every pi SSSF starts, the host's included: pi's bin calls enableCompileCache(), and
  # Node would read V8 code cache from $TMPDIR/node-compile-cache, which any agent can write. Any
  # value turns the cache off, so an operator's value can't turn it on. references/config.md.
  NO_COMPILE_CACHE = {"NODE_DISABLE_COMPILE_CACHE": "1"}
  ```

- `run` (line 859): after `env.update(NO_EXTENSION_CACHE)` add `env.update(NO_COMPILE_CACHE)`. Both stay after `_in_container`. Keep the "last" comment true for both.
- `_pi_catalog` (line 102): after `env = _in_container(operator_env(), sandbox.LOCAL)` add `env.update(NO_COMPILE_CACHE)`.
- `_host_token` (line 398): `env={**operator_env(), **NO_COMPILE_CACHE}`.
- Docstrings: `run` (line 811) gains one sentence that the environment also carries `NODE_DISABLE_COMPILE_CACHE=1` so pi reads no compile cache; `_pi_catalog` and `_host_token` each say the same in a clause.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/trial.py`

- `pi_problems` (line 138): `env={**agent_pi.child_env(), **agent_pi.NO_COMPILE_CACHE}`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

- Under `PI_NO_CACHE` (line 64) add `PI_NO_COMPILE_CACHE = {"NODE_DISABLE_COMPILE_CACHE": "1"}   # must match agent_pi.NO_COMPILE_CACHE (a test pins it)`. `_env` passes only `SSSF_*` and `OBS_*`, so the host's value never reaches the VM on its own.
- `start` (line 992): after `agent_env.update(PI_NO_CACHE)` add `agent_env.update(PI_NO_COMPILE_CACHE)`, in the pi branch only.
- `TOOLS["pi"]["command"]` (line 115): `"NODE_DISABLE_COMPILE_CACHE=1 pi --version"`. `_check_tools` runs the line through `sh -c`, so the prefix applies to that one command. `test_sandbox_openshell.py:366` checks for the substring `pi --version`, which stays.

### 4. `.claude/skills/sssf/templates/container/pi_token_broker.py`

- Stdlib only and run standalone, so it can't import `agent_pi`. Add a module constant `NO_COMPILE_CACHE = {"NODE_DISABLE_COMPILE_CACHE": "1"}   # must match agent_pi.NO_COMPILE_CACHE (a test pins it)`.
- `mint`: pass `env={**os.environ, **NO_COMPILE_CACHE}` to `subprocess.run`.
- Module docstring: one line saying pi starts with Node's compile cache off.

### 5. Tests

Every test that runs a real `node` or `pi` builds its environment from `PATH` only plus a `TMPDIR` under `tmp_path`, as `test_pi_extension_cache.py`'s `_env` does. A "without the setting" case must remove `NODE_DISABLE_COMPILE_CACHE` and `NODE_COMPILE_CACHE` itself: after this change the builder's own shell carries the first one. Guard tests that build a config use the shipped `.claude/skills/sssf/templates/sssf.config.yaml`.

**New file `.claude/skills/sssf/tests/test_pi_compile_cache.py`** with a fixture `.claude/skills/sssf/tests/fixtures/compile_cache/probe.mjs` (an ES module that imports `enableCompileCache` and `constants` from `node:module`, calls `enableCompileCache()` as pi's bin does, imports a sibling `mod.mjs` so there is something to cache, and prints the result and `constants.compileCacheStatus` as JSON; add `mod.mjs` beside it):

- `test_the_three_modules_name_the_same_setting`: `agent_pi.NO_COMPILE_CACHE == sandbox_remote.PI_NO_COMPILE_CACHE == pi_token_broker.NO_COMPILE_CACHE == {"NODE_DISABLE_COMPILE_CACHE": "1"}` (load the broker by path, as `test_pi_token_broker.py` reaches it), and `NO_EXTENSION_CACHE` is still `{"JITI_FS_CACHE": "false"}`.
- The real switch, `skipif(shutil.which("node") is None)`:
  - with `agent_pi.NO_COMPILE_CACHE`: status equals `DISABLED` from the printed constants, and `<TMPDIR>/node-compile-cache` does not exist;
  - the same with `NODE_COMPILE_CACHE=<tmp_path>/other` added: still `DISABLED`, and neither folder exists;
  - without the setting: status `ENABLED`, `directory` is under the scratch `TMPDIR`, and the folder has an entry.
- Host starts, each with a stand-in `pi` script that writes `env` to a file, and each run twice: once clean, once with `NODE_DISABLE_COMPILE_CACHE=0` and `NODE_COMPILE_CACHE=/somewhere` in the operator's environment (`monkeypatch.setenv`). Each asserts the line `NODE_DISABLE_COMPILE_CACHE=1` is recorded:
  - `_pi_catalog` (clear its `lru_cache` before and after, as `test_the_hosts_pi_catalog_gets_no_jiti_setting` does; also assert no `JITI_FS_CACHE=` line);
  - `_host_token("openai")` and `_host_token("google", check=True)`;
  - `trial.pi_problems("google")` with `SSSF_PI_TOKEN_SOCKET` unset (the stand-in prints `{"status": "ready", "authType": "api_key"}`);
  - `pi_token_broker.mint()` with the module's `PI` pointed at the stand-in.
- The cost, `skipif` without `pi`: the model list as `test_the_cost_on_a_real_pi` runs it but with no `-e`, an empty scratch agent dir, scratch `HOME` and `TMPDIR`. One run to fill the cache, the median of 5 warm runs, the median of 5 with `NO_COMPILE_CACHE` in a second scratch `TMPDIR`. Assert the second `TMPDIR` holds no `node-compile-cache`, print both medians, and assert only `off < warm + 2`.

**`.claude/skills/sssf/tests/test_agent_pi.py`** (beside the jiti tests near line 1203; add, change none):

- `_env_the_agent_gets(..., sandbox.LOCAL)["NODE_DISABLE_COMPILE_CACHE"] == "1"`, clean and with the operator's `NODE_DISABLE_COMPILE_CACHE=0` and `NODE_COMPILE_CACHE` set.
- An agentsh call (`own` fixture): `own.seen[-1]["env"]["NODE_DISABLE_COMPILE_CACHE"] == "1"` with the operator's value set to `0`.
- A real stand-in pi run through `agent_pi.run` on the host records `NODE_DISABLE_COMPILE_CACHE=1` (as `test_a_pi_the_agent_runs_records_the_cache_setting`).
- In the container: with the `broker` fixture (`SSSF_PI_TOKEN_SOCKET` set) the environment the agent gets carries it, next to the broker's `OPENAI_API_KEY`.

**`.claude/skills/sssf/tests/test_sandbox_remote.py`** (near line 1598):

- A pi start (`SSSF_PI_TOKEN` in env) sees `NODE_DISABLE_COMPILE_CACHE == "1"`, also when the host's environment has it as `0`.
- A Claude start does not see it, also when the host's environment has it.
- The tool-check line built by `_check_tools` for a pi roster contains `NODE_DISABLE_COMPILE_CACHE=1 pi --version`.
- `E2B.start`, `Vercel.start` and `OpenShell.start` are `Remote.start` (compare `__dict__` membership or the function objects; skip a provider whose module can't import without its SDK, as the existing tests for it do).

**`.claude/skills/sssf/tests/test_sandbox_openshell.py`** (line 421 and 429): the pi start's values hold `NODE_DISABLE_COMPILE_CACHE == "1"`; the Claude start's hold no such key. Add as new assertions or new tests; keep the existing ones.

**`.claude/skills/sssf/tests/test_subagents_extension.py`** (near line 131):

- `run.go(NODE_DISABLE_COMPILE_CACHE="1")`: the child's recorded env has `"1"`. This is the SSSF case, where `agent_pi.run` put it on the parent.
- `run.go_plain(NODE_DISABLE_COMPILE_CACHE=None)`: the child's env has no such key. This records that an interactive pi's child gets it only from the shell. `env()` drops a key passed as `None`.

Do not change `.claude/skills/sssf/templates/harness_engineering/subagents.ts`.

### 6. Docs

- `.claude/skills/sssf/references/config.md`
  - "Discovery is off" bullet (line 847): replace the last sentence ("Still open: Node's own compile cache ...") with: every pi SSSF starts, the host's four and the VM's version check included, also runs with `NODE_DISABLE_COMPILE_CACHE=1`, because pi's bin turns on Node's compile cache and Node would read V8 code cache from `$TMPDIR/node-compile-cache`, which any agent can write; any value of the variable keeps the cache off, so nothing in your environment turns it back on; the cost for the host's model list, with the medians the cost test printed on the builder's run (the planner saw 0.35 s warm and 0.40 s off, under agentsh) and a note that an agent's start loads more of pi and was not measured. Keep the jiti sentences, so the bullet says both caches are off for every pi SSSF starts and why. Say the host's pi still gets no `JITI_FS_CACHE`, since it loads no extension.
  - Subagents paragraph (line 980): add that the child inherits `NODE_DISABLE_COMPILE_CACHE=1` from its parent under SSSF, and that an interactive pi and its child read Node's compile cache unless your shell sets the variable (`docs/open.md`).
  - "Known limit" (line 994): after the `/tmp/jiti` sentence, one clause that no pi SSSF starts reads `/tmp/node-compile-cache` either.
- `docs/open.md`
  - Delete the P1 row "Node's compile cache lets an agent leave compiled code for every later pi" (line 62).
  - Widen the P2 row "A pi that SSSF doesn't start still reads compiled extensions from `/tmp/jiti`" (line 63): the same pi also reads `/tmp/node-compile-cache`, since only SSSF's starts set `NODE_DISABLE_COMPILE_CACHE`; the engineer can export it in their own shell. Keep its priority and date it 2026-11-25.
  - Add one new row, Bug, P2, Medium, Ready, 2026-11-25: other Node tools on the host read `/tmp/node-compile-cache`, which any agent can write. What the planner of guard run a3c73f0f found, from the installed files: npm's `lib/cli.js` calls `enableCompileCache()`, so `npm` and `npx` read it wherever a repo's checks or `worktree_setup` run them on the host; vite 7.3.6's `bin/vite.js` calls `module.enableCompileCache?.()`, and the dashboard recipes run `bunx vite build`; bun 1.4.4 implements the call for a program that makes it (its own folder name, `v1.4.4-x86_64-<hash>-<uid>`) but the dashboard's server code does not call it; Claude Code 2.1.332 is a native binary that contains the names `NODE_COMPILE_CACHE` and `NODE_DISABLE_COMPILE_CACHE`, and whether it enables the cache was not determined. Not run as an attack. A fix could set `NODE_DISABLE_COMPILE_CACHE=1` in `operator_env()` for every host command, or point `NODE_COMPILE_CACHE` at a per-user folder agents can't write.
- No CHANGELOG in this repo. No `app_docs/` write-up mentions the compile cache, so the documenter has nothing older to correct; the documenter writes this change up after the build.

## Verification

Run under agentsh, from the repo root, the touched test files only:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_pi_compile_cache.py tests/test_agent_pi.py tests/test_sandbox_remote.py tests/test_sandbox_openshell.py tests/test_pi_token_broker.py tests/test_subagents_extension.py tests/test_trial.py tests/test_pi_extension_cache.py -q -p no:cacheprovider -m 'not pushes and not host_only'
```

- Exit status 0. Judge by the exit status, not by words in the output.
- Run `tests/test_pi_compile_cache.py` once more with `-s` to read the two medians, and put them in `config.md`.
- Before the code change, the new "every start" tests fail on every start and the "without the setting" switch test passes; say so in the build report after trying one.
- Leave the whole suite to SSSF's own verify step. Do not run it under agentsh.
- Never run a real pi against `~/.pi/agent`, never write under `~/.pi`, and never read, change or delete entries in `/tmp/node-compile-cache` or `/tmp/jiti`. Every real `node` or `pi` gets a scratch `TMPDIR`.
- Write test files and fixtures with the file tool, not a shell heredoc.

## Out of scope

Holes this change leaves open. A reviewer files them as old holes.

- Crafting a code cache entry that runs other code than its source.
- The engineer's interactive pi, and an operator's own pi in the container, reading `/tmp/node-compile-cache` and `/tmp/jiti` (the widened P2 row).
- `npm`, `npx`, vite and any other Node tool on the host reading `/tmp/node-compile-cache` (the new P2 row). Claude Code's use of the cache is undetermined.
- An agent still writes `/tmp/node-compile-cache`, directly or through a pi it starts with the variable removed. No pi SSSF starts reads it.
- An interactive pi's starter child does not get the setting unless the shell has it.
- `pi --version` in image builds (decision 5).
- The `/skill:` race and the three Low rows on the starter subagents.
- The text checks' own gaps. The text guard is frozen.
- bun's own transpiler cache, which `agentsh_guard.cache_env` already moves for wrapped agents.

## For the engineer

Nothing in the build waits on these. They follow the merge.

- Update the hub's own install (`adws/adw_modules/agent_pi.py`, `adws/adw_modules/sandbox_remote.py`, `adws/adw_modules/trial.py` and the container's broker copy) from the merged skill, and commit it. `adws/` is protected and not in the builder's `writes`, so the builder leaves it alone.
- The proof: one ChatGPT pi call through `adw_prompt.py` whose agent prints `$NODE_DISABLE_COMPILE_CACHE` (expect `1`); one starter-roster scout whose subagent prints the same (expect `1`, inherited); then the host's model list the way `_pi_catalog` runs it, counting the entries in `/tmp/node-compile-cache` before and after (expect no new entry).
- The 1093 entries already in `/tmp/node-compile-cache/v24.11.1-x64-ef5a0af0-1000/` stay where they are. No pi SSSF starts reads them after the update; your interactive pi and `npm` still do. Deleting the folder by hand, and exporting `NODE_DISABLE_COMPILE_CACHE=1` in your shell, closes that for your own tools.

# Close pi's extension cache and a subagent's prompt templates

## Summary

Two rows in `docs/open.md`, both from the bypass review of guard run f91f5f0e:

1. pi loads every `-e` extension through jiti, and jiti reuses a compiled copy from `/tmp/jiti/` after checking only its last line. An agent that rewrites the body replaces `sssf_rules.ts` (or a roster extension) in every later pi, without editing the source.
2. A starter subagent's child pi starts without `--no-prompt-templates`, so a task `/x` puts `prompts/x.md` (a link to `.env`, say) in the child's prompt.

The change: every pi SSSF starts for an agent, and every starter child, gets `JITI_FS_CACHE=false` in its environment, so jiti neither reads nor writes a cache file. The child's argv gains `--no-prompt-templates --no-themes`. No change to the host's own pi.

## Baseline

`main` at `b752435` ("Record the proof of guard run f91f5f0e"). Every finding is judged against it. On that commit:

- `agent_pi.run` sets no `JITI_*` variable. `sandbox_remote.Remote._env` passes only `SSSF_*` and `OBS_*` names into a VM (`PASSED`), so nothing named `JITI_*` reaches a VM pi either.
- `subagents.ts` starts the child with `--no-extensions` only, and `env: { ...process.env }`.

## What the source says (pi 1.4.0, jiti 2.7.0, read from pi's install folder)

- `dist/core/extensions/loader.js:480`: `createJitiImpl(import.meta.url, { moduleCache: false, ...resolutionOptions })`, then `jiti.import(extensionPath, { default: true })`. pi passes no `fsCache` or `cache` option.
- `jiti/dist/jiti.cjs`, `createJiti`: the defaults come from the environment (`fsCache: _booleanEnv("JITI_FS_CACHE", _booleanEnv("JITI_CACHE", true))`) and are merged as `{...envDefaults, ...callerOptions}`. Since pi passes neither option, the environment decides.
- `_booleanEnv` is `Boolean(JSON.parse(value))` when the name is in `process.env`, and the default when the value doesn't parse. So the value must be the text `false`. An empty value falls back to the default (cache on). When `JITI_FS_CACHE` is set and parses, `JITI_CACHE` is never the answer.
- `getCache` starts with `if (!e.opts.fsCache || !t.filename) return i();`. With `fsCache` false it compiles the source and returns, with no `existsSync`, no `readFileSync` and no `writeFileSync` on a cache file. `prepareCacheDir` makes no folder.
- With the cache on, the folder is `<dir of loader.js>/node_modules/.cache/jiti` if that `node_modules` exists (it doesn't in pi's install), else `os.tmpdir()/jiti`. The hit test is `cached.endsWith(" /* v9-<hash of source> */\n")`.

The planner ran this in a scratch folder with `TMPDIR` pointed inside it (nothing under `/tmp/jiti` or `~/.pi` touched), loading a small `.ts` the way pi does:

| Second load, after the cached body was changed and the last line kept | Runs |
|---|---|
| no setting (baseline) | the plant |
| `JITI_FS_CACHE=false` | the source |
| `JITI_FS_CACHE=false`, fresh `TMPDIR` | the source, and no `jiti` folder is made |
| `JITI_FS_CACHE=` (empty) | the plant |
| `JITI_FS_CACHE=false JITI_CACHE=true JITI_REBUILD_FS_CACHE=true` | the source |

### Why this setting and not a cache folder per call

- A per-call folder has to be somewhere the agent can't write. Under `local_guard: none` the agent is the engineer's user and can write any folder SSSF makes. And a starter child starts inside the parent's call, so it would share the parent's folder, which the parent's agent can write before it starts the child. That is the "within one call" hole again.
- jiti has no option to set the folder from the environment anyway (`fsCache` as a path is a caller option; the environment gives only a boolean, and `TMPDIR` moves every other temp file too).
- With the cache off there is no file to plant. This is stronger and smaller.

### Why an agent can't undo it for a later pi

- SSSF puts the variable in the environment of each pi it starts, last, after every other step that builds that environment. Nothing in the operator's shell or `.env` can leave another value there.
- An agent's `export JITI_FS_CACHE=true` changes its own shell and that shell's children. It doesn't change the pi it runs in, a later agent's pi, or a starter child: the child's environment is `process.env` of the parent pi, which the model's commands (child shells) can't change, and `subagents.ts` sets the variable again itself.
- pi reads no `.env` (no `dotenv` in `dist/`; the only `process.env` writes are `PI_OFFLINE`, `PI_SKIP_VERSION_CHECK`, `PI_CODING_AGENT`, `AI_AGENT`). jiti reads no config file for `fsCache`. `.env` keys never reach an agent except the names `utils.agent_env` allows, and `JITI_*` is not among them.
- A pi the agent starts itself, from its own shell with its own environment, is the agent's own process. It is out of scope, as on the baseline.

### Where it applies

| Where | How the setting gets there |
|---|---|
| Host, `local_guard: none` | `agent_pi.run` sets it; `tree.start` passes `env` as given. |
| Host, agentsh | The same; `agentsh_guard.start` copies `env` and removes only the cache names it replaces (uv, npm, bun, cargo). |
| Operator container | The tree is `sandbox.LOCAL`, so the same path as the host. |
| VMs (e2b, vercel, openshell) | `Remote._env` drops every name but `SSSF_*` and `OBS_*`, so `Remote.start` adds it for a pi start, beside `pi_env`. `OpenShell`, `E2B` and `Vercel` all use `Remote.start`. |
| A starter child, anywhere | Inherits it from the parent pi, and `subagents.ts` sets it in the child's `env`. |

### What it costs

Measured by the planner: a real `pi --list-models --offline -e <ext>` with discovery off, `HOME`, `PI_CODING_AGENT_DIR` and `TMPDIR` in a scratch folder, four runs each.

| Extension | Warm cache | `JITI_FS_CACHE=false` | No extension |
|---|---|---|---|
| a four-line `.ts` | 0.36–0.40 s | 0.43–0.48 s | 0.32–0.34 s |
| `sssf_rules.ts` | 0.37–0.40 s | 0.49–0.54 s | |

So about 0.15 s more per pi start for the refuse extension, the same as a cold-cache start has always cost (0.60 s on the first run, which also writes the file). A roster with more extensions pays for each one. A starter child loads `sssf_rules.ts` only. Against a model call this is nothing.

### The host's pi

It loads no extension through jiti, so nothing changes there.

- `_pi_catalog` runs `pi --list-models` with `--no-extensions` and no `-e`. `dist/core/resource-loader.js:409`: with `noExtensions`, the list is the `-e` paths only, which is empty. jiti is created only in `loader.js`'s per-path load (`loader.js:480`), which then never runs. The built-in extensions are factories (`main.js:454`), not files.
- `pi auth print-bearer-token` and `pi auth check` return at `main.js:460` (`runAuthCommand`), before the resource loader exists.

### `/skill:<name>`

A child task `/skill:<name>` needs no flag and nothing changes for it. pi expands it in `_expandSkillCommand` (`agent-session.js:1727`), separately from prompt templates, and `--no-prompt-templates` doesn't turn it off. Skills stay on for parent and child, as `AGENT_FLAGS` documents. What is left: the expansion reads the skill's file again at prompt time (`readFileSync(skill.filePath)`, links followed), after the load that needed a `description` in its frontmatter. A file swapped for a link to a secret between the two reads would land in the child's prompt. Read from source, not run; it goes in `docs/open.md` as a new Low row (step 6).

### Other caches in `/tmp`

None found. Every other `tmpdir()` use in pi's `dist/` (clipboard, output files, session share, external editor) writes a file with a random name and reads back only its own. No new `open.md` row for this.

## Threat model

The agent runs as the engineer's own user. It can write `/tmp/**` under every guard (agentsh's `sssf-temp` allows it), so it can plant a cache entry. It can write `.pi/prompts/` in the project and, under agentsh, any run's pi agent dir. It writes the task a starter child gets.

What this change can't stop at its level:

- A pi the agent starts from its own shell, with any environment and flags it likes. That pi is the agent's own code. Under `local_guard: none` only the text checks watch it.
- An agent that edits the installed pi or jiti under `~/.nvm` (`local_guard: none`; agentsh denies the write).
- An engineer's own roster extension that changes `process.env.JITI_FS_CACHE` in the parent pi. `subagents.ts` sets it again for the child.
- The skills and context files an agent can leave for a later pi (existing `open.md` row on the pi agent dir).

## Bar

Against `b752435`:

- **The cache.** Every agent call's environment carries `JITI_FS_CACHE=false`: on the host with `local_guard: none`, under agentsh, and in a pi start in each VM mode (e2b, vercel, openshell). So does a starter child's. On the baseline none does. An operator's own `JITI_FS_CACHE=true` or `JITI_CACHE=true` doesn't survive.
- **A planted cache entry is ignored.** A `.ts` extension loaded twice through jiti the way pi does: after a planted body with the right last line goes in the cache folder, the second load with the setting runs the source. Without the setting, the plant runs (the control).
- **The child's flags.** The child's argv carries `--no-prompt-templates` and `--no-themes` with `--no-extensions`, under SSSF and in interactive pi. Under SSSF the `-e` is still the last flag and `--` still comes before the task.
- **Never.** No agent file kept, no agent code run, and no engineer file deleted or overwritten where the baseline does otherwise. The host's pi argv, cwd and environment are unchanged. A Claude Code start in a VM gets no `JITI_FS_CACHE`. No test runs a real pi against `~/.pi/agent`, writes under `~/.pi`, or touches `/tmp/jiti`.
- **Nothing else changes.** Every existing test passes, except the one assertion that pins the interactive child's exact argv, which gains the two flags.

## Steps for the builder

All paths are from the repo root. Each is under `.claude/skills/sssf/`, which `guard_builder`'s `writes` names.

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Add a module constant after `AGENT_FLAGS`, with a comment of two or three lines saying what it does and where the reason is (`references/config.md`):

  ```python
  NO_EXTENSION_CACHE = {"JITI_FS_CACHE": "false"}
  ```

- In `run`, after `env = _in_container(env, tree)` and before `agentsh_guard.start`, add `env.update(NO_EXTENSION_CACHE)`. It is last so no earlier step and no operator value wins.
- Add one sentence to `run`'s docstring: the agent's environment carries `JITI_FS_CACHE=false`, so pi compiles each extension from its source and reads no cache file.
- Change nothing in `_pi_catalog`, `_host_token`, `HOST_FLAGS` or `AGENT_FLAGS`.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_remote.py`

- Add a module constant near `PASSED` and `HELD`: `PI_NO_CACHE = {"JITI_FS_CACHE": "false"}`, with a one-line comment that it must match `agent_pi.NO_EXTENSION_CACHE` (a test pins it; `sandbox_remote` doesn't import `agent_pi`).
- In `Remote.start`, in the pi branch (`if "OPENAI_API_KEY" in agent_env:`), after `agent_env.update(self.pi_env)`, add `agent_env.update(PI_NO_CACHE)`.
- Leave `_env`, `PASSED` and the Claude Code branch alone.

### 3. `.claude/skills/sssf/templates/harness_engineering/subagents.ts`

In `spawnAgent`'s `spawn("pi", [...])`:

- Add `"--no-prompt-templates", "--no-themes",` right after `"--no-extensions",`. Both modes get them. The rest of the argv keeps its order, so under SSSF it still ends `"-e", REFUSE_EXTENSION, "--", prompt`.
- Change `env: { ...process.env }` to `env: { ...process.env, JITI_FS_CACHE: "false" }`, with a short comment: jiti then reads no cached copy of the child's extension.
- In the header comment's "Under SSSF" list, add a line: the child compiles its extension with jiti's file cache off. After the list, note that in both modes the child starts with `--no-prompt-templates` and `--no-themes`, so a task starting with `/` loads no prompt template; `/skill:<name>` still works. Change "Without them (an interactive pi) nothing changes" to say nothing else changes.

### 4. Tests

Build each guard test's config from the `sssf.config.yaml` under `.claude/skills/sssf/templates/`, as the existing helpers do (`_shipped`, `Own`). No test-only config.

**`.claude/skills/sssf/tests/test_agent_pi.py`** (add, next to `_env_the_agent_gets`):

- With `sandbox.LOCAL`: `_env_the_agent_gets(...)["JITI_FS_CACHE"] == "false"`. Run it again with `monkeypatch.setenv("JITI_FS_CACHE", "true")` and `JITI_CACHE=true` and assert the same.
- Under agentsh, with the `own` fixture: after `own` runs a call, `own.seen[-1]["env"]["JITI_FS_CACHE"] == "false"`. Use the fixture as the neighbouring tests do.
- With the stand-in `pi` that records its environment (the `env > file` script pattern in `test_the_host_call_has_discovery_off_and_the_operators_environment`, used through `_fake_pi` and a real `agent_pi.run` on `sandbox.LOCAL` with `local_guard` `none`): the recorded environment has `JITI_FS_CACHE=false`. If `_fake_pi`'s script can't record it, write a small script in the test that does and prints one valid turn.
- The host's pi: in `test_the_host_call_has_discovery_off_and_the_operators_environment`'s pattern, a new test asserts the recorded environment of `_pi_catalog`'s pi has no `JITI_FS_CACHE` unless the operator's own environment has one (`monkeypatch.delenv` first, then assert it's absent).
- `agent_pi.NO_EXTENSION_CACHE == sandbox_remote.PI_NO_CACHE == {"JITI_FS_CACHE": "false"}`.

**`.claude/skills/sssf/tests/test_sandbox_remote.py`** (add, next to `test_a_claude_start_keeps_the_token_and_gets_no_pi_env`, using the `box` fixture, which runs for vercel and e2b):

- A pi start (`env={**os.environ, "SSSF_PI_TOKEN": "chatgpt-access"}`) has `JITI_FS_CACHE == "false"` in `_env_of(...)`, also when the caller's env has `JITI_FS_CACHE=true`.
- A Claude Code start has no `JITI_FS_CACHE`, also when the host's environment has one.

**`.claude/skills/sssf/tests/test_sandbox_openshell.py`**:

- In `test_a_pi_start_gets_the_chatgpt_token_and_pi_offline_not_the_claude_token`, or a new test beside it: `values["JITI_FS_CACHE"] == "false"`. In `test_a_claude_start_keeps_the_claude_token`: `"JITI_FS_CACHE" not in values`.

**`.claude/skills/sssf/tests/test_subagents_extension.py`**:

- `test_the_child_loads_the_refuse_extension_as_its_only_extension`: also assert `--no-prompt-templates` and `--no-themes` are in the argv, both before `-e`. Keep the existing assertions as they are (`-e` once, `--no-extensions` before it, the tail `["-e", REFUSE_EXT, "--", "look around"]`).
- `test_without_sssf_the_session_is_in_home_and_the_argv_is_mains`: the exact argv gains `"--no-prompt-templates", "--no-themes"` after `"--no-extensions"`. Rename it so it no longer says the argv is `main`'s, and say in a comment that the two flags are the only difference from `b752435`.
- New: under SSSF, `run.argv()["env"]["JITI_FS_CACHE"] == "false"` (the stand-in `fixtures/subagents/pi` already records `env`), also when the parent's environment has `JITI_FS_CACHE="true"` (pass it through `run.go(..., JITI_FS_CACHE="true")`). The same in a plain run (`go_plain`).
- New: a task `/x` under SSSF still starts a child (the flags handle it; it is not refused), and so does `/skill:demo`; each child's argv ends `["--", "<the task>"]`.

**`.claude/skills/sssf/tests/test_pi_extension_cache.py`** (new) and **`.claude/skills/sssf/tests/fixtures/jiti/probe.mjs`** (new):

- `probe.mjs` takes jiti's package folder and an extension path. It does what `loader.js:480` does: `createRequire` on `<jiti folder>/package.json`, `require` the folder for `createJiti`, `createJiti(import.meta.url, { moduleCache: false, alias: {} })`, `await jiti.import(extension, { default: true })`, call the result and write what it returns to stdout.
- The test finds jiti from the real pi without running it: `Path(shutil.which("pi")).resolve()` is `<package>/dist/cli.js` or similar; walk up to the folder holding `package.json` with `node_modules/jiti/package.json` under it. Skip the module (`pytest.mark.skipif` or `pytest.skip`) when `node`, `pi` or that folder is missing. Say in the skip reason that the planted-cache proof is then left for the engineer's proof.
- Every run sets `TMPDIR` to a folder under `tmp_path` and builds the environment from `PATH` only plus what the case sets, so the real `/tmp/jiti` is never read or written and an operator's `JITI_*` doesn't leak in.
- The extension, under `tmp_path`: a default-exported function that returns the string `"source"`, with one type annotation so it must be compiled.
- Test "a planted cache entry runs without the setting" (the control, the baseline's behaviour): load once; exactly one file is in `<TMPDIR>/jiti/`; its text ends with a `/* v9-<16 hex> */` line; rewrite it with `"source"` replaced by `"plant"` and the last line kept; load again; stdout is `plant`.
- Test "with the setting the source runs": the same plant, then load with `agent_pi.NO_EXTENSION_CACHE` added to the environment; stdout is `source`, and the planted file is unchanged on disk.
- Test "with the setting no cache is made": fresh `TMPDIR`, load with the setting; stdout is `source` and `<TMPDIR>/jiti` doesn't exist.
- Test "the cost, on a real pi": skipped without `pi`. With `_real_pi_env`'s pattern (`HOME` and `PI_CODING_AGENT_DIR` in temp folders, `PI_OFFLINE=1`) plus `TMPDIR` under `tmp_path`, run `pi --list-models *HOST_FLAGS --no-approve --offline -e <the real sssf_rules.ts>` in `cwd="/"`, once to warm, then three times without and three times with the setting. Assert each exits 0, that with the setting `<TMPDIR>/jiti` gains no file, and that the median with the setting is under the warm median plus 2 seconds (a loose bound so it never flakes; the measured difference is about 0.15 s). Print both medians. Also run it once with `HOST_FLAGS` and no `-e` in a fresh `TMPDIR` and assert no `jiti` folder appears, which is the host's pi loading nothing through jiti.
- Use the argv list form of `subprocess.run` with a timeout, and judge by the return code.

### 5. `.claude/skills/sssf/references/config.md`

- The "Discovery is off" bullet (about line 847): add that every pi agent, and every starter subagent, runs with `JITI_FS_CACHE=false`, so pi compiles each `-e` extension from its source on every start and never reads a compiled copy from `/tmp/jiti`, which any agent can write. Give the cost (about 0.15 s per start for `sssf_rules.ts`) and say the host's own pi loads no extension and is unchanged.
- The subagents paragraph (about line 980): the child starts with `--no-extensions`, `--no-prompt-templates` and `--no-themes`, the same three as its parent, so a task starting with `/` loads no prompt template. A `/skill:<name>` task still expands the skill, since skills stay on.
- The **Known limit** on an edit of `sssf_rules.ts` (about line 994): name the cache. Before this change, pi's compiled copy of an extension in `/tmp/jiti/` could be rewritten without touching the source, and the after-call check saw nothing; `JITI_FS_CACHE=false` closes it, since no pi SSSF starts reads that folder. What stays open is unchanged: an extension edited and loaded within one call, and a worktree run loading extensions from the launch checkout.

### 6. `docs/open.md`

- Delete the row that starts "pi's extension cache lets an agent replace `sssf_rules.ts` without editing it."
- Delete the row that starts "A starter subagent's task that starts with `/` loads a prompt template."
- Add one row, in the same column layout as its neighbours (Bug, P3, Low, Ready, 2026-11-25): a starter subagent's `/skill:<name>` task puts the skill's file in the child's prompt with no tool call. pi reads the file again at prompt time (`agent-session.js:1727`, links followed), after the load that needed a `description` in its frontmatter, so a skill file swapped for a link to a secret between the two reads would be read by no check. Under agentsh the secret-file deny stops the read. Read from source by the planner of guard run 31eae620; not run.

### 7. Other docs

- `docs/sssf.md` and `README.md`: search both for `--no-extensions` and `jiti`. Where one lists a starter child's flags or the agent's environment, bring it in line; if neither does, change nothing.
- No CHANGELOG in this repo.
- For the documenter: `app_docs/` write-ups of guard run f91f5f0e describe the child as started with `--no-extensions` only. After this change it also gets `--no-prompt-templates` and `--no-themes`. Note it in the new write-up; don't edit the old one.

## Verification

From the repo root, the touched test files only, as the request asks (the whole suite is left to verify):

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agent_pi.py tests/test_subagents_extension.py tests/test_pi_extension_cache.py tests/test_sandbox_remote.py tests/test_sandbox_openshell.py -m 'not pushes and not host_only' -q -p no:cacheprovider
```

It must exit 0. Then check by reading:

- The control test (plant runs without the setting) passes, so the planted-cache test proves something.
- `git diff b752435 -- .claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` shows the constant, the one `env.update` and the docstring line, and nothing in `_pi_catalog` or `_host_token`.
- `/tmp/jiti` holds the same three files with the same sizes and times as before the tests ran (`ls -la /tmp/jiti`), and `~/.pi` is untouched.

The repo's own `test` command runs all of `tests`, the new file included, so no extra check is declared.

## Out of scope

Left open by this change; a reviewer files each as an old hole:

- The three Low rows on the starter subagents: partial lines hiding an event, a child's cost, a child's own children.
- What else in `/tmp` a later process trusts. The planner found no other cache pi reads there.
- The text checks' own gaps. The text guard is frozen.
- A pi an agent starts itself, with its own environment and flags.
- An extension edited and loaded within one call, and a worktree run loading extensions from the launch checkout (`config.md`'s known limit, as it stands after step 5).
- The pi agent dir an agent can write under agentsh (skills, `AGENTS.md`, `settings.json` packages): its own `open.md` row.
- The `/skill:<name>` swap race, filed in step 6.
- Other `JITI_*` variables in the operator's own shell (`JITI_ALIAS`, `JITI_TRANSFORM_MODULES`). Only the engineer sets them; `.env` keys don't reach an agent.
- Interactive pi outside SSSF: the parent pi there still uses jiti's cache. Only the child's flags and environment change.

## For the engineer

Not builder steps. These paths are protected and outside `guard_builder`'s `writes`, or happen after the merge.

- **Protect the new files.** `.claude/skills/sssf/tests/test_pi_extension_cache.py` and `.claude/skills/sssf/tests/fixtures/jiti/` are a guard test and its fixture, unprotected until they are added to `protected_files` and to `guard_builder`'s `writes` in `adws/adw_sssf_config/sssf.config.yaml` (and the shipped template's list, if you want it there). `sandbox_remote.py` and `test_sandbox_remote.py` now carry part of this guard too and are in neither list.
- **Update the hub's install and commit it**: `adws/adw_modules/agent_pi.py`, `adws/adw_modules/sandbox_remote.py` and the installed `subagents.ts` under `adws/adw_data/harness_engineering/`.
- **Proof 1, the plant.** Make a scratch extension, load it once with a real pi so `/tmp/jiti` gets its entry, copy that entry aside, write a decoy body into the copy's place keeping the last line, then run one pi load with SSSF's environment (`JITI_FS_CACHE=false`) and see the source run, not the plant. Remove the scratch entry after. Don't change the three real entries.
- **Proof 2.** One starter-roster scout call whose subagent runs. In the child's recorded argv or `ps` output: `--no-prompt-templates`, `--no-themes`, and `JITI_FS_CACHE=false` in `/proc/<pid>/environ`.
- **Proof 3.** One ChatGPT pi call through `adw_prompt.py`.
- The whole suite on the host, outside agentsh, including the `host_only` and `pushes` tests.
- The stale `/tmp/jiti/*.mjs` files can be deleted by hand; nothing SSSF starts reads them after this change.

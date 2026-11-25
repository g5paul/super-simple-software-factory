# Close pi's extension cache and a subagent's prompt templates

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `agent_pi.py` and `templates/harness_engineering/subagents.ts`, both guard code, so it goes through the chain.

## What broke

`docs/open.md` has two rows from the bypass review of guard run f91f5f0e, on `main` and its head alike:

1. **"pi's extension cache lets an agent replace `sssf_rules.ts` without editing it."** pi 1.4.0 loads every `-e` extension through jiti 2.7.0 (`dist/core/extensions/loader.js:480`, `moduleCache: false`). jiti's file cache is on unless `JITI_FS_CACHE` or `JITI_CACHE` says otherwise (`_booleanEnv("JITI_FS_CACHE", _booleanEnv("JITI_CACHE", true))` in `jiti/dist/jiti.cjs`). `getCache` keeps the compiled module in `$TMPDIR/jiti/` (or `/tmp/jiti/`), named from the source's folder, name and a hash of its path, and reuses it when the cached file ends with ` /* v9-<hash of the source> */`; it never checks the body. `/tmp/jiti/adw_modules-sssf_rules.024e7654.mjs` is on this machine now. An agent that rewrites that body and keeps the last line replaces the refuse extension in every later pi that loads it, and `sssf_rules.ts` never changes, so the after-call check sees nothing. The same works on a roster extension's cache, `subagents.ts` among them. agentsh allows `/tmp/**` (`sssf-temp`), so a wrapped agent can write it; agentsh's own secret-file deny still holds. Under `local_guard: none` the text checks are all there is.
2. **"A starter subagent's task that starts with `/` loads a prompt template."** `AGENT_FLAGS` gives the parent pi `--no-prompt-templates` and `--no-themes`. `subagents.ts` starts the child with `--no-extensions` only. pi expands a `-p` message `/x` from `<agent dir>/prompts/x.md` or a trusted project's `.pi/prompts/x.md` (`dist/core/agent-session.js:1660`, `prompt-templates.js`), following links, so `x.md` linked to `.env` puts the file in the child's prompt, which no check reads.

## The change

1. **No shared cache.** Every pi SSSF starts for an agent, and every child a starter subagent starts, compiles its extensions without reading a cache another process could have written. Say in the spec how, from jiti's source: `JITI_FS_CACHE=false` in the agent's environment (children inherit it), a cache folder SSSF makes per call, or something better, and why an agent can't undo it for a later pi (an environment variable the agent sets in its own shell, a `.env`, a file jiti reads). Cover the host, agentsh, `local_guard: none`, the VMs and the container. Say what it costs a pi start, measured on a real `pi --list-models` load of one extension if the tests can do that without touching `~/.pi/agent`.
2. **The host's pi.** Say whether the pi SSSF starts on the host (`_pi_catalog`, `pi auth`) loads any extension through jiti. It runs with `--no-extensions` and in `/`; if it loads none, say so from source and change nothing there.
3. **The child's flags.** `subagents.ts` starts the child with `--no-prompt-templates` and `--no-themes` as well, under SSSF and in interactive pi, so the child's flags match `AGENT_FLAGS`. Say whether a `/skill:<name>` task needs anything, given skills stay on for parent and child.
4. **Docs.** Delete the two `docs/open.md` rows named above, or cut them to what's left. `references/config.md`'s known limit on an edit of `sssf_rules.ts` names the cache and says what closes it.

## The bar

- **The cache.** A test with a stand-in `pi` that records its environment: every agent call's environment, on the host and in each VM mode, carries the spec's setting, and so does a starter child's. On `main` none does.
- **A planted cache entry is ignored.** A test that loads a small `.ts` extension through jiti twice the way pi does, with the spec's setting: after a planted body with the right last line goes into the cache folder, the second load runs the source, not the plant. Without the setting, the plant runs. If jiti can't be loaded from the tests, say so and leave it for the engineer's proof.
- **The child's flags.** The child's argv carries `--no-prompt-templates` and `--no-themes`, with `--no-extensions`, the `-e` last among the flags, and the `--` before the task, as on `main` since f91f5f0e.
- **Nothing else changes.** Every existing test passes on the host, `test_agent_pi.py` and `test_subagents_extension.py` among them.

## After the merge, the engineer's proof

Update the hub's install and commit it. Plant a decoy body in a copy of the cache entry for a scratch extension under `/tmp/jiti`, run one pi load with SSSF's environment, and see the source run, not the plant. Run one starter-roster scout call whose subagent runs, and one ChatGPT pi call through `adw_prompt.py`.

## Running the tests

The planner and the builder run under agentsh. Run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Don't run a real pi against `~/.pi/agent`, don't write anything under `~/.pi`, and don't change the real entries in `/tmp/jiti`; read pi's and jiti's source from pi's install folder.

## Out of scope

- The three Low rows on the starter subagents (partial lines, a child's cost, a child's own children).
- What else in `/tmp` a later process trusts. The spec names any other cache it finds pi reading, as a new `open.md` row.
- The text checks' own gaps. The text guard is frozen.

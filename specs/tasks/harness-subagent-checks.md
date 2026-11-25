# Give the starter harness's pi subagents SSSF's checks

Run it with `--worktree` and `--base main`, on the hub's own config. The change is in `templates/harness_engineering/subagents.ts`, which starts pi agents that SSSF's checks must reach, and maybe in `agent_pi.py`, which is guard code, so it goes through the chain.

## What broke

`docs/open.md` has the row ("A pi subagent of the starter harness meets no text check"). The starter roster (`templates/sssf.config.yaml`) gives `planner` and `scout` the extension at `adws/adw_data/harness_engineering/subagents.ts`, with its four `subagent_*` tools. Each tool starts a child pi in `spawnAgent` (`subagents.ts:210-240`):

```ts
spawn("pi", ["--mode", "json", "-p", "--session", state.sessionFile, "--no-extensions",
             "--model", model, "--tools", "read,bash,grep,find,ls", "--thinking", thinking, prompt],
      { stdio: ["ignore", "pipe", "pipe"], env: { ...process.env } })
```

What the code at `main` (cf6879b) shows, checked on 2026-11-24:

- **No refuse extension.** `agent_pi.run` passes `-e <REFUSE_EXTENSION>` (`sssf_rules.ts`) last on every agent call (`agent_pi.py:821`). The child gets `--no-extensions` and no `-e`, so its `bash` meets no `refuse` rule and its `read`, `grep`, `find` and `ls` meet no secret-file check. The child's environment already carries `SSSF_REFUSE_RULES` and `SSSF_SECRET_FILES` (`_with_refusals`, `agent_pi.py:733`), since `env` copies the parent's. Only the extension's path is missing: nothing in the environment names it, and in a VM `tree.install(REFUSE_EXTENSION)` puts it somewhere else.
- **No stream check.** `rules.refusing` (`rules.py:316`) hands the extension only the `on_match: refuse` rules. Every other `forbid` rule, the default `kill` ones among them, is checked only in the stream SSSF reads from the agent's stdout (`agents.py`, `check`, about line 715). The child's events go to the extension's `processLine` and never to that stdout, so a subagent's command meets no `kill` rule even with the `-e`. `references/config.md` line 800 says a `forbid` entry is searched in "every shell call an agent makes: Claude Code's `Bash`, a subagent's included, and pi's `bash`".
- **The session file is in `~/.pi`.** `makeSessionFile` (`subagents.ts:122`) puts each child's session in `~/.pi/agent/sessions/subagents/`. Since guard run cd9eb718 a wrapped pi agent can write nothing in `~/.pi`, so under agentsh the child's `mkdirSync` or session write is refused and, from source, no subagent can start. Under `local_guard: none` the child writes into the engineer's own `~/.pi/agent`. The folder doesn't exist on this machine, so no starter-roster subagent has run here since then. Not run; check it.
- **Plain `pi`.** The child is `spawn("pi", ...)`, the first `pi` on `PATH`, not `PI_PATH`.
- **The file isn't protected.** The hub's `protected_files` lists the guard modules for `guard_builder` only, and `templates/harness_engineering/subagents.ts` isn't on it, so any run's builder can change it.

Under agentsh the wrap still covers the child: it's a process under the wrapped agent. Under `local_guard: none` the text checks are all there is, and a subagent's `cat .env` or forbidden command gets through.

## The change

1. **The refuse extension.** Each child pi loads `sssf_rules.ts`, last among its extensions, on the host and in a VM. Say in the spec how `subagents.ts` finds its path (an environment variable SSSF sets on every pi agent, for example), and why an agent can't point it at another file and get a child with no checks. A child pi that can't find the extension doesn't start, and the tool's result says why.
2. **The other `forbid` rules.** A child's shell command meets every `forbid` rule the parent meets, `kill` rules included, and a match stops the run as it would for the parent: SSSF records `rule_breach` and raises `RuleBreach`, or the spec gives a design that ends the same way. Say how the child's commands reach SSSF's check, or why they can't and what the spec does in its place, and pick the smallest design. If closing this needs a new mechanism the bar doesn't justify, stop at step 1, and `config.md` says plainly that a starter subagent's commands meet only the `refuse` rules.
3. **The session file.** A child's session goes inside the folder the parent pi agent may write: its own agent dir (`PI_CODING_AGENT_DIR`) or its session dir, whichever the spec shows is writable under every `local_guard` and in a VM. Interactive pi with no SSSF env keeps today's folder.
4. **The binary.** Say whether the child should start `PI_PATH` when it's set, and pick.
5. **Protection.** Add `templates/harness_engineering/subagents.ts` to the hub's `protected_files` and to `guard_builder`'s writes, as the comment above the guard list says a new guard file needs.
6. **Docs.** Delete the `docs/open.md` row named above, or cut it to what's left. `references/config.md`'s "Harness engineering" section says which checks reach a starter subagent, and line 800's promise matches what's true.

## The bar

- **The extension loads.** A test reads the argv `spawnAgent` builds (by running the extension's spawn under a stand-in `pi` that records argv and environment, or by the spec's better seam): `-e <the refuse extension>` is there, last, with the path SSSF set, and `--no-extensions` stays.
- **A refused read and a refused command.** With a stand-in or real pi, a child's `read .env` and a child's `bash` command that a `refuse` rule matches are both refused, under `local_guard: none`. On `main` both go through.
- **A `kill` rule.** A child's command that a `kill` rule matches stops the run with `RuleBreach`, or, if the spec stopped at step 1, `config.md` says so and a test pins that the child got the `refuse` rules.
- **The session file.** With `PI_CODING_AGENT_DIR` set to a temp folder, the child's `--session` path is inside the folder the spec picked, and nothing is made under the test's `$HOME/.pi`. With no SSSF env, the path is as on `main`.
- **Protection.** A test of the hub's config sees `subagents.ts` in `protected_files` and in `guard_builder`'s writes.
- **Nothing else changes.** Every existing test passes on the host, `test_sandbox_extensions.py` and `test_factory_files.py` among them.

## After the merge, the engineer's proof

Update the hub's install and commit it. In a scratch repo with the starter roster under agentsh, run a scout call that uses `subagent_create` to run `cat .env` on a decoy `.env`, and see the subagent start, its read refused, and its session file land in the folder the spec picked. Repeat with `local_guard: none`.

## Running the tests

The planner and the builder run under agentsh. Run only the touched test files from `.claude/skills/sssf` with `-m 'not pushes and not host_only'`, and leave the whole suite to verify. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). Don't run a real pi against `~/.pi/agent`, and don't write anything under `~/.pi`; read pi's source from its install folder.

## Out of scope

- The text checks' own gaps, such as a quoted command word. The text guard is frozen; a new spelling goes in `docs/designs/agentsh/corpus.sh`.
- What the starter subagent tools do apart from starting the child: widgets, `/sub` commands, themes.
- Claude Code's subagents, which already reach SSSF's stream.

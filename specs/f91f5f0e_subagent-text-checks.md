# Give the starter harness's pi subagents SSSF's checks

All skill paths below are under `.claude/skills/sssf/`; each file is named once from the repo root.

## Problem

`.claude/skills/sssf/templates/harness_engineering/subagents.ts` starts each subagent as a child `pi` with `--no-extensions` and no `-e`, keeps the child's events inside the extension, and puts the child's session in `~/.pi/agent/sessions/subagents/`. So a starter `planner` or `scout` subagent meets no `refuse` rule, no secret-file check and no `kill` rule, and under agentsh it can't start at all, because a wrapped pi may write nothing in `~/.pi` (`docs/open.md`, row "A pi subagent of the starter harness meets no text check").

## Baseline

`main` at `83ba4f1`. Every finding is judged against it. At that commit:

- `spawnAgent` (`subagents.ts:210-240`) builds `pi --mode json -p --session <file> --no-extensions --model <m> --tools read,bash,grep,find,ls --thinking <t> <prompt>` with `env: { ...process.env }`.
- `agent_pi.run` passes `-e <REFUSE_EXTENSION>` last and sets `SSSF_REFUSE_RULES` and `SSSF_SECRET_FILES`, but no variable names the extension's path or the session dir.
- A child's `bash`, `read`, `grep`, `find` and `ls` meet no check of any kind. A child's task that is `@.env` makes pi attach the file itself (pi 1.4.0 `dist/cli/args.js`: a positional that starts with `@` is a file argument, even after `--`).
- Read from pi 1.4.0's source, not run: in `--mode json` pi replaces `process.stdout.write` with a write to stderr (`dist/core/output-guard.js`, `takeOverStdout`) and writes its own events through the bound original.

## Threat model

The agent is the model driving a pi that runs as the engineer's own user. It chooses the `task`, `prompt`, `model` and `thinking` it hands to the `subagent_*` tools, and every command its own `bash` runs.

What this change stops: the starter tools themselves starting a child that has fewer text checks than its parent.

What it can't stop at this level, on `main` and after alike:

- The agent's own `bash` can start any `pi` with any flags, edit `adws/adw_modules/sssf_rules.ts` inside one call and then start a subagent that loads the edit, or put a `pi` earlier on a writable `PATH` folder. The after-call check catches the file edit only after the call. Under agentsh the wrap covers these processes; under `local_guard: none` only the text checks do, and they read the parent's command text.
- The text checks read text. Their own gaps (a quoted command word and the like) reach the child exactly as they reach the parent.
- A `kill` rule stops the run when the call is announced, so the command may have started. That is true of the parent too.

Why the agent can't aim the child at another extension: `subagents.ts` reads the path from the parent pi's own environment once, when the extension loads, before the model's first tool call. The model's commands run in child shells and can't change the parent process's environment, and no `subagent_*` parameter carries an environment variable or a flag: the prompt goes after `--`, so pi reads it as text, and the model and thinking values are each one argv after their flag.

## Design

### 1. The refuse extension (`SSSF_REFUSE_EXTENSION`)

`agent_pi.run` sets `SSSF_REFUSE_EXTENSION=str(REFUSE_EXTENSION)` on every pi agent, in `_with_refusals`, whether or not there are rules. The same value is right in a VM: `Remote.install` copies the file to the same absolute path (`sandbox_remote._path`), and the argv's own `-e` already uses it.

`subagents.ts` captures, at load, whether it runs under SSSF: any of `SSSF_ADW_ID`, `SSSF_AGENT`, `SSSF_REFUSE_EXTENSION`, `SSSF_SESSION_DIR`, `SSSF_REFUSE_RULES`, `SSSF_SECRET_FILES` is non-empty. Under SSSF:

- The child's argv ends `... "--thinking", thinking, "-e", <SSSF_REFUSE_EXTENSION>, "--", prompt`. `--no-extensions` stays, and the refuse extension is the child's only and so last extension.
- A child doesn't start, and the tool's result (or the `/sub` notice) says why, when: `SSSF_REFUSE_EXTENSION` is unset, not absolute, or not a regular file; `SSSF_SESSION_DIR` is unset or not absolute; or the prompt, after leading whitespace, starts with `@` ("SSSF doesn't start a subagent whose task begins with @: pi would attach that file unread by any check"). The check runs before any state or session file is made, at all four call sites (`subagent_create`, `subagent_continue`, `/sub`, `/subcont`), through one helper.

With no SSSF variable set, the argv and behaviour are byte for byte as on `main`.

### 2. The other `forbid` rules: forward the child's tool events

Smallest design, no Python change: under SSSF, `processLine` writes each child `tool_execution_start` and `tool_execution_end` event to the parent pi's real stdout as one JSON line, with `toolCallId` rewritten to `sub<id>:<toolCallId>` and a `subagent: <id>` field added. SSSF's stream check (`agents.py`, `check`) already reads both types for pi (`RULE_EVENT_TYPES`), so with no change there:

- a child command matching a `kill` rule raises `RuleBreach` at `tool_execution_start`, SSSF records `rule_breach` and `stop_tree` kills the parent, the child and its shell;
- a child command matching a `refuse` rule goes `pending`, and the child's blocked result (no `durationMs`, the refusal text) counts as a refusal toward `MAX_REFUSALS`, or raises `RuleBreach` if it ran;
- the child's calls are traced as `tool_call` rows of the parent agent.

No other child event is forwarded: `message_end` and `agent_end` carry usage and final text that `agent_pi.run` would read as the parent's. The id prefix keeps a child id from ever meeting a parent `pending` id.

The write must reach fd 1 although pi took `process.stdout.write` over. Use the stream's own method, `Object.getPrototypeOf(process.stdout).write.call(process.stdout, line)`: it joins the same Node stream queue as pi's own raw writes, so lines never interleave. Not `fs.writeSync(1, ...)`, which can split a line pi is mid-way through. If the write throws, the extension kills that child and marks it `error`: a child SSSF can't see doesn't keep running.

This depends on pi 1.4.0's takeover. A test pins it against the installed pi (below), so a pi that changes it fails the suite, not silently the guard.

### 3. The session file (`SSSF_SESSION_DIR`)

`agent_pi.run` sets `SSSF_SESSION_DIR` to `request.session_dir`, made absolute. Under SSSF the child's session is `<SSSF_SESSION_DIR>/subagents/subagent-<id>-<ms>.jsonl`. This is the folder the parent pi is told to write (`--session-dir`), so it is writable wherever the parent can run: every `local_guard`, the container, and a VM (same argv, same path). `PI_CODING_AGENT_DIR` is not picked: SSSF sets it only under agentsh on the host, and under `local_guard: none` it is the engineer's own folder. With no SSSF variable, `makeSessionFile` keeps `~/.pi/agent/sessions/subagents/`.

The builder confirms from `agentsh_sssf.yaml` and `agentsh_guard.py` that the wrap lets pi create a folder inside the session dir. If it doesn't, stop and report; don't widen the policy.

### 4. The binary

The child stays plain `pi` from `PATH`; it does not start `PI_PATH`. `PI_PATH` is the host SSSF process's setting: in a VM or the container it names a host path, and a relative one resolves against the wrong folder (`docs/open.md`). The parent's `PATH` is one the agent can't change. `config.md` says that with `PI_PATH` set, a subagent runs the first `pi` on `PATH`, which may be another version.

## Builder steps

1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`: in `_with_refusals` (or beside its call in `run`), set `SSSF_REFUSE_EXTENSION` and `SSSF_SESSION_DIR` as above. Update `run`'s docstring. Nothing else.
2. `.claude/skills/sssf/templates/harness_engineering/subagents.ts`: the load-time capture, the preflight helper, the argv tail, `makeSessionFile`, and the forward in `processLine`, as designed. Update the header comment. `require("child_process")` may become an `import` if the test seam needs it. Touch no widget, theme or command text beyond the refusal notice.
3. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`: header comment only, one clause: it is also loaded into each starter subagent.
4. New test file `.claude/skills/sssf/tests/test_subagents_extension.py`, with its stand-ins under `.claude/skills/sssf/tests/fixtures/subagents/`:
   - `driver.mjs`: loads the shipped `subagents.ts` unmodified (jiti from pi's install, or Node's type stripping with a resolve hook), with stub modules for the `@mariozechner/*` and typebox imports, a fake `ExtensionAPI` that captures `registerTool`, and calls a tool's `execute`. The real `spawn("pi", ...)` runs, with a temp `bin/` first on `PATH`.
   - `pi` (stand-in, in that `bin/`): records argv and environment to a file; loads each `-e` path from its argv; feeds it the `tool_call` events a fixture variable lists; prints pi-shaped `tool_execution_start` and `tool_execution_end` lines (blocked: `isError: true`, no `durationMs`, the block reason as text; ran: with `durationMs`).
   - The tests skip with a reason only when `node` is missing. Rules come from the shipped `templates/sssf.config.yaml` with a rule added, never a test-only config.
5. `.claude/skills/sssf/tests/test_agent_pi.py`: the agent's environment carries `SSSF_REFUSE_EXTENSION == str(REFUSE_EXTENSION)` and an absolute `SSSF_SESSION_DIR`, on the host with no rules, and on a stand-in remote tree.
6. Docs:
   - `.claude/skills/sssf/references/config.md`: "Harness engineering" says which checks reach a starter subagent (the `refuse` rules and secret-file check through `sssf_rules.ts`; every other `forbid` rule through the forwarded events), where its session goes, that a task starting with `@` is refused, that it runs `pi` from `PATH` and not `PI_PATH`, and that an interactive pi with no SSSF variable gets none of this. Line 800 becomes true as written: add "and a starter subagent's `bash`". Line 961 and the "Known limit" at line 990 get the same one-clause fix where they speak of subagents.
   - `docs/open.md`: cut the row to what's left, as P3 "Needs info": a starter subagent still running when its parent's call ends is no longer forwarded and meets only the `refuse` rules; not checked whether `pi -p` waits for it.
   - `.claude/skills/sssf/templates/sssf.config.yaml`: no change.
   - No CHANGELOG exists. Don't edit `app_docs/`; the documenter notes any older write-up there that says starter subagents run unchecked or keep sessions in `~/.pi`.

## Bar

Against `83ba4f1`, the change must:

- **Load the extension.** The recorded child argv has `-e <SSSF_REFUSE_EXTENSION>` as its only `-e`, after `--no-extensions`, followed only by `--` and the prompt, with the path SSSF set.
- **Refuse a read and a command.** Through the stand-in chain (recorded argv and environment, the real `sssf_rules.ts`), a child's `read .env` and a child's `bash` matching a `refuse` rule are blocked, with no guard wrap. On `main` neither is.
- **Stop on a `kill` rule.** The driver's stdout, fed to SSSF's real stream check, raises `RuleBreach` for a child command a `kill` rule matches, and counts a blocked `refuse` match as `rule_refused`. Forwarded lines carry the `sub<id>:` prefix and only the two event types.
- **Reach real stdout.** With the installed pi's `output-guard.js` `takeOverStdout()` called first, the forwarded line arrives on fd 1, not fd 2. Skipped only when pi isn't installed.
- **Fail closed.** Under SSSF with the extension variable missing, relative or naming no file, with no session dir, or with an `@` task, no `pi` starts and the tool result names the reason.
- **Place the session.** With `PI_CODING_AGENT_DIR` and `SSSF_SESSION_DIR` set to temp folders and `HOME` a temp folder, `--session` is inside `<SSSF_SESSION_DIR>/subagents/` and no `$HOME/.pi` exists afterwards. With no SSSF variable, the path is `$HOME/.pi/agent/sessions/subagents/...` and the argv equals `main`'s.

It must never, where the baseline does otherwise: keep an agent file, run agent code, or delete or overwrite an engineer file. It writes one new folder, `subagents/`, inside the run's own session dir.

## Verification

From `.claude/skills/sssf`, with the repo's own pytest invocation (`repo.yaml`'s test command), only the touched files, under agentsh:

- `tests/test_subagents_extension.py`, `tests/test_agent_pi.py`, `tests/test_sandbox_extensions.py`, `tests/test_factory_files.py`, each with `-m 'not pushes and not host_only'`.

Leave the whole suite to the verify step. Don't run a real pi against `~/.pi/agent`, and write nothing under `~/.pi`; read pi's source from its install folder.

## Out of scope

- The text checks' own gaps. The text guard is frozen; a new spelling goes in `docs/designs/agentsh/corpus.sh`.
- A subagent that outlives its parent's call (the cut `docs/open.md` row), and any process the agent detaches itself (existing row "An agent's background write lands after the after-call check").
- The agent starting its own `pi` from `bash`, or editing `sssf_rules.ts` and loading it within one call (existing "Known limit" in `config.md`).
- What the child pi loads besides extensions: it gets no `--no-prompt-templates` or `--no-themes`, as on `main`.
- Widgets, `/sub` command behaviour, themes; Claude Code's subagents.
- The interactive pi with no SSSF variable: unchanged and unchecked.

## For the engineer

The builder can't write these; the "Protection" bar item lands here, so no builder test covers it.

- `adws/adw_sssf_config/sssf.config.yaml`: add to `protected_files` and, identically, to `guard_builder`'s `writes`: `.claude/skills/sssf/templates/harness_engineering/subagents.ts`, `.claude/skills/sssf/tests/test_subagents_extension.py` and `.claude/skills/sssf/tests/fixtures/subagents/`.
- `adws/tests/test_guard_paths.py`: a test that the hub's config has `subagents.ts` in `protected_files` and in `guard_builder`'s `writes`.
- After the merge: update the hub's install (which rewrites `adws/adw_data/harness_engineering/subagents.ts`) and commit it. Then the proof from the brief: in a scratch repo with the starter roster under agentsh, a scout call uses `subagent_create` to run `cat .env` on a decoy; see the subagent start, the read refused, the session file under `<session dir>/subagents/`, and the child's calls in the trace. Repeat with `local_guard: none`. This is also the first real run of the stdout forward and of a nested `pi` under the wrap; neither was run here.

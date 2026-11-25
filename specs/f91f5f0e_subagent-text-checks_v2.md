# Give the starter harness's pi subagents SSSF's checks (v2)

Second plan of this session. It replaces `specs/f91f5f0e_subagent-text-checks.md` after spec review 1: a child's life is now bound to its parent's call, and the "every existing test passes" bar item is back. Each file is named from the repo root.

## Problem

`.claude/skills/sssf/templates/harness_engineering/subagents.ts` starts each subagent as a child `pi` with `--no-extensions` and no `-e`, keeps the child's events inside the extension, puts the child's session in `~/.pi/agent/sessions/subagents/`, and returns from the tool at once while the child runs on. So a starter `planner` or `scout` subagent meets no `refuse` rule, no secret-file check and no `kill` rule, can outlive the parent's call, and under agentsh can't start at all, because a wrapped pi may write nothing in `~/.pi` (`docs/open.md`, row "A pi subagent of the starter harness meets no text check").

## Baseline

`main` at `83ba4f1`. Every finding is judged against it. At that commit:

- `spawnAgent` (`subagents.ts:210-240`) builds `pi --mode json -p --session <file> --no-extensions --model <m> --tools read,bash,grep,find,ls --thinking <t> <prompt>` with `env: { ...process.env }`.
- `subagent_create` and `subagent_continue` call `spawnAgent` without awaiting it and return "Returns immediately"; the result arrives later through `pi.sendMessage(..., { deliverAs: "followUp", triggerTurn: true })`.
- pi 1.4.0's print mode (`dist/modes/print-mode.js:95-132`) returns once `session.prompt` resolves, with no wait for background work. `agent_pi.run` calls `stop_tree` only on an error. So a child still running when the parent's turn ends is orphaned: it runs on after the after-call check.
- `agent_pi.run` passes `-e <REFUSE_EXTENSION>` last and sets `SSSF_REFUSE_RULES` and `SSSF_SECRET_FILES`, but no variable names the extension's path or the session dir.
- A child's `bash`, `read`, `grep`, `find` and `ls` meet no check. A child's task that is `@.env` makes pi attach the file itself (pi 1.4.0 `dist/cli/args.js`: a positional starting with `@` is a file argument, even after `--`).
- In `--mode json`, pi replaces `process.stdout.write` with a write to stderr (`dist/core/output-guard.js`, `takeOverStdout`) and writes its own events through the bound original. Read from source, not run.

## Threat model

The agent is the model driving a pi that runs as the engineer's own user. It chooses the `task`, `prompt`, `model` and `thinking` it hands the `subagent_*` tools, and every command its own `bash` runs.

What this change stops: the starter tools themselves starting a child that has fewer text checks than its parent, or one that runs after the parent's call.

What it can't stop at this level, on `main` and after alike:

- The agent's own `bash` can start any `pi` with any flags, detach it, edit `adws/adw_modules/sssf_rules.ts` within one call and then start a subagent that loads the edit, or put a `pi` on a writable `PATH` folder. The after-call check catches the file edit only after the call. Under agentsh the wrap covers these processes; under `local_guard: none` only the text checks do, and they read the parent's command text.
- A command a child's `bash` detaches itself (`setsid ... &`) outlives the child, as one from the parent's `bash` does.
- The text checks read text. Their gaps reach the child as they reach the parent.
- A `kill` rule stops the run when the call is announced, so the command may have started. True of the parent too.

Why the agent can't aim the child at another extension: `subagents.ts` reads the path from the parent pi's own environment once, when the extension loads, before the model's first tool call. The model's commands run in child shells and can't change the parent process's environment. No `subagent_*` parameter carries a variable or a flag: the prompt goes after `--`, so pi reads it as text, and the model and thinking values are each one argv after their flag.

## Design

### 0. "Under SSSF"

`subagents.ts` captures at load: under SSSF means `SSSF_ADW_ID` or `SSSF_REFUSE_EXTENSION` is non-empty. `mark_agent` sets the first on every run's agents, and step 1 sets the second on every pi agent. With neither set (the engineer's interactive pi), the argv, the session folder and the tools' behaviour are as on `main`.

### 1. The refuse extension (`SSSF_REFUSE_EXTENSION`)

`agent_pi.run` sets `SSSF_REFUSE_EXTENSION=str(REFUSE_EXTENSION)` on every pi agent, in `_with_refusals`, rules or none. The value is right in a VM too: `Remote.install` copies the file to the same absolute path (`sandbox_remote._path`), and the argv's own `-e` already uses it.

Under SSSF:

- The child's argv ends `... "--thinking", thinking, "-e", <SSSF_REFUSE_EXTENSION>, "--", prompt`. `--no-extensions` stays, and the refuse extension is the child's only, so last, extension.
- No child starts, and the tool's result (or the `/sub` notice) says why, when: `SSSF_REFUSE_EXTENSION` is unset, not absolute, or not a regular file; `SSSF_SESSION_DIR` is unset or not absolute; or the prompt, after leading whitespace, starts with `@`. One helper runs this check before any state or session file is made, at all four call sites (`subagent_create`, `subagent_continue`, `/sub`, `/subcont`).

### 2. The other `forbid` rules: forward the child's tool events

No Python change. Under SSSF, `processLine` writes each child `tool_execution_start` and `tool_execution_end` event to the parent pi's real stdout as one JSON line, with `toolCallId` rewritten to `sub<id>:<toolCallId>` and a `subagent: <id>` field added. SSSF's stream check (`agents.py`, `check`) already reads both types for pi (`RULE_EVENT_TYPES`), so:

- a child command matching a `kill` rule raises `RuleBreach` at `tool_execution_start`; SSSF records `rule_breach`, and `stop_tree` kills the parent, the child and its shell;
- a child command matching a `refuse` rule goes `pending`; the child's blocked result (no `durationMs`, the refusal text) counts toward `MAX_REFUSALS`, and any other result raises `RuleBreach`;
- the child's calls are traced as `tool_call` rows of the parent agent, and keep the stream from looking silent while the parent waits (step 3).

No other child event is forwarded: `message_end` and `agent_end` carry usage and final text that `agent_pi.run` would read as the parent's. The prefix keeps a child id from meeting a parent `pending` id.

The write uses the stream's own method, `Object.getPrototypeOf(process.stdout).write.call(process.stdout, line, callback)`. It joins the Node stream queue pi's own raw writes use, so lines never interleave; `fs.writeSync(1, ...)` could split a line pi is mid-way through. A closed pipe doesn't throw, so fail closed on all three: a thrown write, an error passed to the callback, and an `error` event on `process.stdout` each kill every running child and mark it `error`. A child SSSF can't see doesn't keep running.

This depends on pi 1.4.0's takeover. A test pins it against the installed pi, so a pi that changes it fails the suite.

### 3. No child outlives its parent's call

Under SSSF, `subagent_create` and `subagent_continue` await `spawnAgent`'s promise and return the child's result as the tool's own result (the same text as today's follow-up, same 8000-character cut), and `spawnAgent` sends no follow-up message. A pending tool call keeps the parent's turn open, so `pi -p` can't return while a child runs. The tool descriptions registered under SSSF say the call waits for the result. Cost: subagents the model starts in one turn run as pi schedules tool calls, possibly one after another.

Backstop, for a parent that ends some other way: under SSSF the extension registers one `process.on("exit")` handler that sends `SIGKILL` to every child still running. When SSSF itself stops the parent, `stop_tree` already takes the whole tree.

`/sub` and `/subcont` are interactive commands and can't be called in `-p` mode; under SSSF they get the preflight and the backstop, nothing more.

### 4. The session file (`SSSF_SESSION_DIR`)

`agent_pi.run` sets `SSSF_SESSION_DIR` to `request.session_dir`, made absolute. Under SSSF the child's session is `<SSSF_SESSION_DIR>/subagents/subagent-<id>-<ms>.jsonl`. This is the folder the parent pi is told to write (`--session-dir`), so it is writable wherever the parent runs: every `local_guard`, the container, and a VM (same argv, same path). `PI_CODING_AGENT_DIR` is not picked: SSSF sets it only under agentsh on the host, and under `local_guard: none` it is the engineer's own folder.

The builder confirms from `agentsh_sssf.yaml` and `agentsh_guard.py` that the wrap lets pi create a folder inside the session dir. If it doesn't, stop and report; don't widen the policy.

### 5. The binary

The child stays plain `pi` from `PATH`; it does not start `PI_PATH`. `PI_PATH` is the host SSSF process's setting: in a VM or the container it names a host path, and a relative one resolves against the wrong folder (`docs/open.md`). The parent's `PATH` is one the model can't change. `config.md` says that with `PI_PATH` set, a subagent runs the first `pi` on `PATH`, which may be another version.

## Builder steps

1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`: in `_with_refusals` (or beside its call in `run`), set `SSSF_REFUSE_EXTENSION` and `SSSF_SESSION_DIR`. Update `run`'s docstring. Nothing else.
2. `.claude/skills/sssf/templates/harness_engineering/subagents.ts`: the load-time capture, the preflight helper, the argv tail, `makeSessionFile`, the forward and its fail-closed handling, the awaited tools, and the exit handler, as designed. Update the header comment. `require("child_process")` may become an `import` if the test seam needs it. Touch no widget or theme.
3. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`: header comment only, one clause: it is also loaded into each starter subagent.
4. New `.claude/skills/sssf/tests/test_subagents_extension.py`, with stand-ins under `.claude/skills/sssf/tests/fixtures/subagents/`:
   - `driver.mjs` loads the shipped `subagents.ts` unmodified (jiti from pi's install, or Node's type stripping with a resolve hook), with stub modules for the `@mariozechner/*` and typebox imports and a fake `ExtensionAPI` that captures `registerTool` and `sendMessage`, then calls a tool's `execute`. The real `spawn("pi", ...)` runs, with a temp `bin/` first on `PATH`.
   - `pi`, the stand-in in that `bin/`, records argv and environment to a file; loads each `-e` path from its argv; feeds it the `tool_call` events a fixture variable lists; prints pi-shaped `tool_execution_start` and `tool_execution_end` lines (blocked: `isError: true`, no `durationMs`, the block reason as text; ran: with `durationMs`); and can be told to sleep, then write a marker file as it exits.
   - The tests skip with a reason only when `node` is missing. Rules come from the shipped `.claude/skills/sssf/templates/sssf.config.yaml` with a rule added, never a test-only config.
5. `.claude/skills/sssf/tests/test_agent_pi.py`: the agent's environment carries `SSSF_REFUSE_EXTENSION == str(REFUSE_EXTENSION)` and an absolute `SSSF_SESSION_DIR`, on the host with no rules and on a stand-in remote tree.
6. Docs:
   - `.claude/skills/sssf/references/config.md`: the "Harness engineering" section (line 970) says which checks reach a starter subagent (the `refuse` rules and secret-file check through `sssf_rules.ts`; every other `forbid` rule through the forwarded events), that under SSSF the tool waits for the subagent and returns its result, where its session goes, that a task starting with `@` is refused, that it runs `pi` from `PATH` and not `PI_PATH`, and that an interactive pi outside a run gets none of this. Line 800 becomes true as written: add "and a starter subagent's `bash`". The "Known limit" at line 990 keeps its subagent clause, reworded to name the edit of `sssf_rules.ts` within one call.
   - `docs/open.md`: delete the row. Nothing of it is left open.
   - No CHANGELOG exists. Don't edit `app_docs/`; the documenter notes any older write-up there that says starter subagents run unchecked, return at once, or keep sessions in `~/.pi`.

## Bar

Against `83ba4f1`, the change must:

- **Load the extension.** The recorded child argv has `-e <SSSF_REFUSE_EXTENSION>` as its only `-e`, after `--no-extensions`, followed only by `--` and the prompt, with the path SSSF set.
- **Refuse a read and a command.** Through the stand-in chain (recorded argv and environment, the real `sssf_rules.ts`), a child's `read .env` and a child's `bash` matching a `refuse` rule are blocked, with no guard wrap. On `main` neither is.
- **Stop on a `kill` rule.** The driver's stdout, fed to SSSF's real stream check, raises `RuleBreach` for a child command a `kill` rule matches, and counts a blocked `refuse` match as `rule_refused`. Forwarded lines carry the `sub<id>:` prefix and only the two event types.
- **Reach real stdout.** With the installed pi's `output-guard.js` `takeOverStdout()` called first, the forwarded line arrives on fd 1, not fd 2. Skipped only when pi isn't installed. With stdout made to fail (thrown, callback error, `error` event), the running child is killed.
- **Bind the child to the call.** Under SSSF, `execute` resolves only after the stand-in `pi` has exited (its marker file exists), its result holds the child's text, and `sendMessage` was not called. With the driver exiting while a stand-in still sleeps, the stand-in's pid is gone shortly after. With no SSSF variable, `execute` returns before the child exits and the follow-up is sent, as on `main`.
- **Fail closed.** Under SSSF with the extension variable missing, relative or naming no file, with no session dir, or with an `@` task, no `pi` starts and the tool result names the reason.
- **Place the session.** With `PI_CODING_AGENT_DIR` and `SSSF_SESSION_DIR` set to temp folders and `HOME` a temp folder, `--session` is inside `<SSSF_SESSION_DIR>/subagents/` and no `$HOME/.pi` exists afterwards. With no SSSF variable, the path is `$HOME/.pi/agent/sessions/subagents/...` and the argv equals `main`'s.
- **Change nothing else.** Every existing test passes on the host, `.claude/skills/sssf/tests/test_sandbox_extensions.py` and `.claude/skills/sssf/tests/test_factory_files.py` among them, with no existing test edited except the additions to `test_agent_pi.py`.

It must never, where the baseline does otherwise: keep an agent file, run agent code, or delete or overwrite an engineer file. It writes one new folder, `subagents/`, inside the run's own session dir.

## Verification

From `.claude/skills/sssf`, with the repo's own pytest invocation, only the touched and named files, each with `-m 'not pushes and not host_only'`, under agentsh:

- `tests/test_subagents_extension.py`, `tests/test_agent_pi.py`, `tests/test_sandbox_extensions.py`, `tests/test_factory_files.py`.

The whole suite, which settles "Change nothing else", runs in the verify step on the host. Don't run a real pi against `~/.pi/agent`, and write nothing under `~/.pi`; read pi's source from its install folder.

## Out of scope

- The text checks' own gaps. The text guard is frozen; a new spelling goes in `docs/designs/agentsh/corpus.sh`.
- A process a child's or the parent's own `bash` detaches (existing row "An agent's background write lands after the after-call check").
- The agent starting its own `pi` from `bash`, or editing `sssf_rules.ts` and loading it within one call (existing "Known limit" in `config.md`).
- What the child pi loads besides extensions: it gets no `--no-prompt-templates` or `--no-themes`, as on `main`.
- Widgets, themes, and `/sub` command behaviour beyond the preflight; Claude Code's subagents.
- The interactive pi outside a run: unchanged and unchecked.

## For the engineer

The builder can't write these, so the brief's "Protection" bar item lands here and no builder test covers it.

- `adws/adw_sssf_config/sssf.config.yaml`: add to `protected_files` and, identically, to `guard_builder`'s `writes`: `.claude/skills/sssf/templates/harness_engineering/subagents.ts`, `.claude/skills/sssf/tests/test_subagents_extension.py` and `.claude/skills/sssf/tests/fixtures/subagents/`.
- `adws/tests/test_guard_paths.py`: a test that the hub's config has `subagents.ts` in `protected_files` and in `guard_builder`'s `writes`.
- After the merge: update the hub's install (it rewrites `adws/adw_data/harness_engineering/subagents.ts`) and commit it. Then the brief's proof: in a scratch repo with the starter roster under agentsh, a scout call uses `subagent_create` to run `cat .env` on a decoy; see the subagent start, the read refused, the tool wait for the result, the session file under `<session dir>/subagents/`, and the child's calls in the trace. Repeat with `local_guard: none`. This is the first real run of the stdout forward, of an awaited subagent tool, and of a nested `pi` under the wrap; none was run here.

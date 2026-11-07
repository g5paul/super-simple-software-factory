# Plan: count a pi refusal only when pi shows it never ran the call

## Summary

The stream check (`_event_forwarder` in `.claude/skills/sssf/templates/adws/adw_modules/agents.py`)
counts a refuse-rule call as refused when its result text reads as SSSF's refusal
(`rules.refused`). The agent writes that text. This change:

1. **pi:** counts the refusal only when the text reads as a refusal **and** pi's result shows it
   never ran the call (`rules.blocked`, ported from guard run 3e98eebd: `isError: true` and no
   `durationMs`). Anything else raises `RuleBreach`.
2. **Claude Code:** no change in behavior. A `_CLAUDE_REFUSED` result or a `permission_denied`
   for the call still counts. The docs say why it can't be closed there.
3. **Pending at exit:** when the agent's process ends (normal return, or a plain
   `RuntimeError` such as "pi exited 137") with a refuse-rule call still waiting for its
   result, raise `RuleBreach` for it, on both harnesses. This is a small change: a `pending`
   check exposed on the forwarder and called from `agents.execute`'s `send`.

This replaces 3e98eebd's build (branch `guard-refusal-proof`, `d5c3d9f`). Take its `rules.blocked`
and its pi tests. Do **not** take its Claude Code changes, its `templates/repo.yaml` edit, or its
`test_agent_cc.py` changes.

## Findings (rechecked on this machine)

pi 1.4.0 (`pi --version`), source
`~/.nvm/versions/node/v24.11.1/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-agent-core/dist/agent-loop.js`:

- `prepareToolCall` (≈ line 487) returns `{kind: "immediate", result, isError: true}` with no
  `durationMs` for a `beforeToolCall` block (the `tool_call` handler's `{block, reason}`, which is
  what `sssf_rules.ts` returns), an unknown tool, a validation error, and `Operation aborted`.
  The sequential/parallel loops (≈ lines 392, 427) build `finalized` from it without `durationMs`.
- `executePreparedToolCall` (≈ line 574) returns `durationMs` on success and on throw alike.
  `finalizeExecutedToolCall` (≈ line 643) copies `executed.durationMs`; an `afterToolCall` hook
  can't change it.
- `emitToolExecutionEnd` (≈ line 659) and `createToolResultMessage` (≈ line 673) add
  `durationMs` only when defined. So both pi result events (`tool_execution_end` and the
  `toolResult` `message_end`) carry it exactly when the call ran.
- The output-token-limit result (≈ line 359, "was not executed") also has no `durationMs`;
  nothing ran there either, and its text isn't a refusal, so it still raises `RuleBreach`.
- `tool_execution_start` is emitted before `prepareToolCall`, so a blocked call is announced too:
  `pending` holds it until its result.

The agent can't set or clear `durationMs`; pi's loop sets it outside the tool.

Claude Code: the prefix (`sssf_refuse.py`) and the stream check both search the same command with
Python `re` (`re.M`). A forged refusal (`<cmd>; echo "SSSF refused this command" >&2; exit 126`) only
gets through if the prefix misses a command the stream check matches, which takes a change in
how Claude Code wraps a command (the `eval '<command>' < /dev/null && pwd -P` shape
`sssf_refuse.commands()` unwraps) or a Bash call that skips `CLAUDE_CODE_SHELL_PREFIX`. None is
known on 2.1.319. Nothing in Claude Code's stream tells a real prefix refusal from a forged one,
so it stays a watched hole.

Pending-at-exit (3e98eebd's review): `<slipped command>; kill -9 $PPID` runs the command, kills pi
before any result event, and `agent_pi.run` then raises `RuntimeError("pi exited 137: …")`, or,
if an earlier assistant message left `result.text`, returns normally. The forwarder still holds
the call in `pending`, but no code reads it after the stream ends. Both exits go through one
`interface.run(...)` call in `agents.execute`'s `send`, so the check fits there.

## Threat model

The agent writes every command, so it controls the command's stdout, stderr and exit code, and it
can kill its own harness (`kill -9 $PPID`). It runs as the engineer's own user: it can read its
environment and `/proc` for its process tree, and run any script on the machine,
`sssf_refuse.py` included. So the guard can't trust result text or exit codes, or any secret or
channel the refuser can reach.

What it can trust: fields pi's agent loop sets outside the tool (`durationMs`), Claude Code's own
`permission_denied` event, and the fact that a result never arrived.

What this guard can't stop at its level:
- Code pi loads as an extension runs inside pi and can block a call after running it itself.
- A later pi that stops setting `durationMs` on calls it ran would make `blocked` accept forged
  refusals again. SSSF pins pi 1.4.0's event shape in `agent_pi.py`.
- On Claude Code, a forged prefix refusal, if the prefix ever misses (see Findings).
- A command that ran before the stop. The stop ends the run; it can't undo the command.

## Baseline

`main` at `9680496` ("Record the listed projects on skill 184dffe"). Every finding is judged
against it. On the baseline, `forward` counts a refuse-rule call as refused when
`call_id in denied` or `rules.refused(text)`, and nothing reads `pending` once the stream ends.

## Bar

Must:
- On pi, count a refuse-rule call as refused only when its result event is in `rules.blocked`
  (`isError is True`, no `durationMs`) and `rules.refused(text)` is true. Otherwise raise
  `RuleBreach` with the "may have run" message (`refusals == 0`).
- On Claude Code, behave exactly as the baseline: `_CLAUDE_REFUSED` text or `permission_denied`
  counts. Every existing Claude Code test passes unchanged.
- When `interface.run` returns, or raises an exception that isn't SSSF's own stop
  (`rules.RuleBreach`, `budget.BudgetStop`, `account.RateLimitStop` (which covers
  `agent_pi.UsageLimitStop`), `silence.SilentStop`), with a refuse-rule call still pending,
  raise `RuleBreach` for it (first pending call, "may have run" message, `builtin` set when the
  rule is the built-in one). It goes through the existing `except rules.RuleBreach` handler, so
  `rule_breach` is logged. On a normal return, its `partial` is the returned `AgentResult`, so
  that call's spend still counts.
- Keep counting where a refusal still counts: `rule_refused` log, console line, third counted
  refusal stops with `refusals: 3`. Built-in secret-file refusals stay uncounted on both
  harnesses, and the run goes on.
- Keep `REFUSED` and every refusal message the agent reads, byte for byte.

Must never, against the baseline:
- accept as refused any call the baseline would stop. The pi test is strictly narrower, and the
  Claude Code test is unchanged.
- keep an agent file, run agent code, or delete or overwrite an engineer file. The change only
  raises `RuleBreach` in more cases.
- change what `sssf_rules.ts` or `sssf_refuse.py` block.
- change `.claude/skills/sssf/templates/repo.yaml`.

## Out of scope

Holes this change leaves open, to be filed as old holes:
- Making Python and JavaScript read patterns the same. The differences `references/config.md`
  lists now decide only whether a pi run stops or the agent is refused.
- pi's file-tool refusals (`read`/`grep`/`find`/`ls`); they never reach the stream check.
- The Claude Code forged-refusal case (new `docs/open.md` `watch` row).
- A pi that drops `durationMs` from calls it ran; code pi loads as an extension.
- A pending call when SSSF itself stopped the agent (budget, rate limit, silence): the run stops
  anyway, so these paths are left as they are.
- A pending call when a remote-sandbox interface raises something other than a plain exception
  path covered above. Same `send` path, so it's covered unless the interface swallows the end.
- Background processes the slipped command left running after the agent died (existing
  leftovers handling).
- Quote-splitting (`j''ust`) and the other text-search limits.

## Files to change (builder)

All paths are from the repo root. None is in `protected_files` (the protected
`adws/adw_modules/` is the repo's installed copy; edit only the skill's templates).

### 1. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

- Add `blocked(event: dict) -> list[str]` after `denied`, as in d5c3d9f
  (`git show d5c3d9f -- .claude/skills/sssf/templates/adws/adw_modules/rules.py`):
  `tool_execution_end` event, or `message_end` whose `message.role == "toolResult"`; return
  `[str(body.get("toolCallId"))]` when `body.get("isError") is True and "durationMs" not in body`,
  else `[]`. Every Claude Code event gives `[]`. Docstring: the pi 1.4.0 source finding above
  (function names, not line numbers), that the agent can't set or clear it, and that Claude Code's
  stream has no such field, so the stream check reads Claude Code results by text alone.
- `refused(text)`: behavior unchanged. Docstring: it reads only the text, which the agent
  controls; on pi the stream check also needs `blocked`; on Claude Code the text alone counts
  (see `sssf_refuse.py`).
- Comment above `REFUSED`: on pi the stream check reads this text only together with `blocked`;
  on Claude Code it reads it after the exit line.
- Module docstring, the "The stream check stays as the backstop" sentence: on pi a matching call
  counts as refused only when pi's result shows it never ran the call and reads as the refusal; on
  Claude Code when its result is the prefix's refusal or Claude Code denied it itself
  (`permission_denied`). Anything else is a breach, and so is a matching call still waiting for
  its result when the agent ends.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

In `_event_forwarder`:
- In `forward`, before the results loop: `pi_blocked = set(rules.blocked(event))`.
- Replace the acceptance test with:
  ```python
  by_text = rules.refused(text) and (call_id in pi_blocked or not rules.pi_result(event))
  if not (by_claude or by_text):
  ```
  Implement the harness split without a new flag, by event shape: add to `rules.py` a small
  helper `pi_result(event) -> bool` that is true for `tool_execution_end` and for `message_end`
  with `message.role == "toolResult"` (the same two shapes `blocked` reads; have `blocked` use
  it). A Claude Code `user` result is not a pi result, so its text counts as today. Name it in the
  docstring.
- Add, next to `forward.tracker = tracker`:
  ```python
  def ended(partial=None) -> None:
      """The agent's process is gone. A refuse-rule call still waiting for its result
      may have run (`<command>; kill -9 $PPID`): raise RuleBreach for the first one."""
      if pending:
          rule, pattern, command = next(iter(pending.values()))
          breach = rules.RuleBreach(agent_name, rule, pattern, command, builtin=is_builtin(rule))
          breach.partial = partial
          raise breach
  forward.ended = ended
  ```
- Docstring: rewrite the "A refuse rule waits for the call's result instead" paragraph: on pi,
  SSSF's refusal counts when pi's result shows it never ran the call (`rules.blocked`) and the
  text is the refusal; on Claude Code, the prefix's refusal text (`_CLAUDE_REFUSED`) counts, as
  does Claude Code's own `permission_denied`, because Claude Code's stream can't tell a real
  prefix refusal from a forged one (see `sssf_refuse.py`). Add: a call still pending when the
  agent ends raises `RuleBreach` through `forward.ended`. In the `uncounted` paragraph, replace
  "whose result isn't the refusal, and that Claude Code didn't deny" with "that the stream check
  doesn't count as refused".

In `execute`'s `send` (≈ line 233), wrap only the `interface.run(...)` call in an inner `try`
inside the existing outer `try`, so a `RuleBreach` from `ended` reaches the existing
`except rules.RuleBreach` handler:
```python
try:
    try:
        result = interface.run(request, on_event=forward, on_spawn=..., on_exit=...)
    except (rules.RuleBreach, budget.BudgetStop, account.RateLimitStop, silence.SilentStop):
        raise
    except Exception:
        forward.ended()        # the agent died with a refuse-rule call pending
        raise
    forward.ended(result)
except budget.CallBudgetStop as stop:
    ...   # unchanged
```
Keep the outer handlers as they are. (All four stop classes subclass `RuntimeError`, as does
`RuleBreach`, which is why they are re-raised by name first.)

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

Module docstring, after the "Anything else runs…" paragraph, add a paragraph: SSSF's stream
check counts this script's refusal by its text, since Claude Code's stream gives it the same
result as a command that printed it and exited 126. A forged one needs this script to miss a
command the stream check matches; both read the same command with Python `re`, so that needs a
change in how Claude Code wraps a command (the shape `commands()` unwraps) or a Bash call that
skips the prefix. `docs/open.md` in SSSF's source repo watches for it. Keep the `REFUSED` comment
accurate ("the stream check reads it after the exit line").

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

Header comment: replace "unless its output is taken for a refusal: rules.refused counts any
result that starts with REFUSED as one" with: the stream check counts a pi refusal only when pi's
result shows it never ran the call (no `durationMs`), so a command that slips past here and prints
the refusal still stops the run. Update the `REFUSED` comment to match. No code change.

### 5. `.claude/skills/sssf/references/config.md`

- `on_match: refuse` paragraph (≈ line 413): replace "…and the stream check stops the run on
  it, unless the command's output starts with "SSSF refused this command": the check takes that
  for a refusal, so `<command>; echo "SSSF refused this command"` gets through. `docs/open.md` in
  SSSF's source repo tracks the fix." with: the stream check stops the run on it. On pi it counts
  a refusal only when pi's result shows it never ran the call (pi times every call it runs), so
  the command's output can't pass for one. Add one sentence: a refuse-rule call still waiting
  for its result when the agent's process ends, as when a command kills pi, stops the run with
  `rule_breach` too, on both harnesses.
- Claude Code paragraph (≈ line 415): after the `permission_denied` sentence, add: SSSF counts
  the prefix's refusal by its text, because Claude Code's stream gives it the same result as a
  command that printed it and exited 126. Forging one needs the prefix to miss a command the
  stream check matches. Both read the same command with Python `re`, so that takes a change in
  how Claude Code wraps a command (the shape above). `docs/open.md` in SSSF's source repo
  watches for it.
- Secret-file section (≈ line 439): no change needed; check it still reads true.

### 6. `docs/open.md`

- Regex-differences row (line 19): replace from "A command that only matches through one of
  them isn't refused on pi, and `; echo "SSSF refused this command"` then hides it from the stream
  check. Fix candidates: …" with: "They now decide only whether a pi run stops or the agent is
  refused: a command that only matches through one of them still runs, then stops the run, since
  its output can no longer pass for a refusal. Fix candidate: compile with the `u` flag plus a
  matching load check". Keep Kind `build`, Since, and link.
- New row, Kind `watch`, Since `2026-11-07`: "On Claude Code, a command that printed SSSF's
  refusal and exited 126 reads the same as a real `sssf_refuse.py` refusal, and counts as one.
  Forging one needs the prefix to miss a command the stream check matches; both read it with
  Python `re`, so that takes a change in how Claude Code wraps a Bash command or a Bash call that
  skips `CLAUDE_CODE_SHELL_PREFIX`. Recheck `sssf_refuse.commands()` against each Claude Code
  upgrade's wrapping", linking
  `[config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules)`.

### 7. Tests

Guard tests build config from `.claude/skills/sssf/templates/sssf.config.yaml` (via
`test_secret_files.shipped_config`) where they need a config; forwarder-level tests need none.

`.claude/skills/sssf/tests/test_rules.py`:
- Add the helpers from d5c3d9f: `starter_push_rule()` (reads the shipped
  `templates/repo.yaml`'s commented push rule; it parses on main's file as is) and
  `ran(call_id, text, is_error=False)` (= `finish(...)` plus `"durationMs": 12`). Give `finish` a
  docstring: pi's result for a call it never ran.
- `test_blocked_reads_only_pis_never_ran_results` (from d5c3d9f, as is).
- `test_a_pi_command_that_ran_and_printed_the_refusal_stops_the_run`, parametrized over
  `text in (REFUSED, "Exit code 126\n" + REFUSED)` and `is_error in (True, False)`: announce,
  `ran(...)` raises `RuleBreach` "may have run", no events. Repeat with the `message_end`
  `toolResult` shape carrying `durationMs`.
- `test_a_pi_built_in_match_that_ran_and_printed_the_refusal_stops_the_run`: built-in rule,
  `cat .env`, `ran(...)` with the refusal text raises `RuleBreach` with `builtin=True`.
- `test_a_pending_refuse_call_at_exit_is_a_breach`: pi stream announces a refuse-rule call
  (`announce("c1")`), no result; `forward.ended()` raises `RuleBreach` "may have run" with
  `refusals == 0`; `ended(partial=x)` sets `.partial is x`. A forwarder with no pending call
  (refused call already finished) returns `None` from `ended()`. Same for a Claude Code
  `tool_use` announce with no result.
- `test_execute_stops_when_the_agent_dies_with_a_refuse_call_pending`: model on
  `test_secret_files.test_execute_marks_only_the_built_in_rule_in_the_requests_refuse_rules`
  (import `shipped_config`, `make_repo` from `test_secret_files`; write the repo's `repo.yaml`
  from the shipped `templates/repo.yaml` with a push refuse rule). The fake interface's `run`
  calls `kw["on_event"](announce("c1", "git push"))`, then (param 1) raises
  `RuntimeError("pi exited 137: ")`, or (param 2) returns `AgentResult(text="done", cost=0.1)`.
  Assert `agents.execute` raises `rules.RuleBreach`, and the tracer saw an `error` event named
  `rule_breach` with `command == "git push"`. Use `agent_pi.ToolCallTracker`. Stub the same
  budget/permission/account calls that test stubs.
- Every existing test passes unchanged. `finish()` is already pi's never-ran shape, so the pi
  refusal tests (`test_a_refused_command_is_logged_and_the_run_goes_on`,
  `test_the_third_refusal_…`, `test_built_in_refusals_never_stop_the_run`, …) still count. If one
  does need a change, it may change only because it relied on a text-only pi refusal; say which
  in the report.
- `test_a_claude_code_refusal_is_read_past_its_exit_line` stays as is: it is the Claude Code
  keep-today's-behavior check.

`.claude/skills/sssf/tests/test_rules_multiline.py` (bun-driven, skip without bun), from d5c3d9f
(`git show d5c3d9f -- .claude/skills/sssf/tests/test_rules_multiline.py`):
- `_bun_handler` and `_pi_block` helpers.
- `test_a_real_pi_refusal_counts_and_the_third_stops`: also assert the two events are
  `rule_refused`.
- `test_a_command_that_slips_past_pi_and_prints_the_refusal_stops_the_run` (the `﻿` push).
- `test_a_real_pi_block_under_the_built_in_rule_is_uncounted_and_never_stops`.
- Import `announce, forwarder, ran, starter_push_rule` from `test_rules` and `secret_files` from
  `adw_modules`.

`.claude/skills/sssf/tests/test_agent_cc.py`: no change. Every existing refusal test there, the
built-in secret-file ones included (`test_a_bash_read_claude_code_denies_…`, `_refused_bash`,
`test_three_denials_…`), must pass unchanged.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md` (step 5).
- `docs/open.md` (step 6).
- Docstrings/comments in `rules.py`, `agents.py`, `sssf_refuse.py`, `sssf_rules.ts` (steps 1-4).
- No CHANGELOG in this repo. No README usage or help text changes. No `app_docs/` exists at the
  root; nothing for the documenter to flag.

## Verification

From the repo root:

1. `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest .claude/skills/sssf/tests/test_rules.py .claude/skills/sssf/tests/test_rules_multiline.py .claude/skills/sssf/tests/test_agent_cc.py .claude/skills/sssf/tests/test_secret_files.py -q`
   exits 0, with bun on PATH so the multiline bun tests run, not skip
   (`-rs` shows no skip for them).
2. The full skill suite (`cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests`) exits 0. The known flakes in
   `docs/open.md` (`test_a_forbidden_command_kills_a_pi_agent…`,
   `test_the_relay_refuses_bad_json…`) may need one rerun; report it if so.
3. `git diff --stat main -- .claude/skills/sssf/templates/repo.yaml .claude/skills/sssf/tests/test_agent_cc.py` is empty
   (a manual check, not a SSSF check).
4. Read `git diff` for `sssf_rules.ts` and `sssf_refuse.py`: comments/docstrings only.

## For the engineer

Nothing in `protected_files` is needed for this change. After it merges, the repo's installed
copy under `adws/adw_modules/` picks it up through the usual "Update SSSF to skill …" step.

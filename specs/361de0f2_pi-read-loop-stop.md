# Stop pi when its read loop fails, as Claude Code's does

## Summary

`agent_pi.run` can leave its read loop by an exception, and pi then runs on unwatched. The loop doesn't stop the tree, wait for pi, close the guard or call `on_exit`. Two causes:

- A JSON line that isn't an object (`null`, `[1]`, `5`, `"x"`), or a `message_end` whose `message` or `usage` isn't an object, raises `AttributeError` in the loop body.
- Any other exception in the loop that isn't from `on_event` has no handler, `KeyboardInterrupt` included.

The fix:

1. Skip non-object lines, and read `message` and `usage` only when they are dicts.
2. Give pi's loop the `try` / `except BaseException` / `finally` shape that `agent_cc.run` already has.

Only `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` and the tests change.

## Baseline

`main` at **`8ca7e6d`** ("Record run 955ca723 and write runs G and F's specs"), which holds run 955ca723 merged as `c7ac4e7`. Every finding is judged against it. The pi read loop at the baseline is `agent_pi.py:707-789`.

What the baseline does, by exit:

- **`on_event` raises `rules.RuleBreach`:** it stops the tree and sets `error.partial = result`. Then it waits, calls `agentsh_guard.close` and `on_exit`, and re-raises the breach.
- **`on_event` raises another `Exception`:** it stops the tree, waits, closes and calls `on_exit`. If `agentsh_guard.breach(process, agent)` returns a stop, that stop is raised `from` the error with `partial = result`. Otherwise the error is re-raised.
- **The loop body raises anything else** (`event.get` on a non-dict, `message.get` on a non-dict, `usage.get` on a non-dict, an error from `dog.touch`, from the raw log or from `_text_of`), or `on_event` raises a `BaseException` that isn't an `Exception`: nothing runs. pi runs on unwatched and is never waited for. `close` and `on_exit` are not called.
- **Clean end (stdout EOF):** it reads stderr, waits, closes and calls `on_exit`. Then come `check_stopped`, where a breach or `AgentshRefused` gets `partial = result`, and `dog.hung`, which raises `SilentStop` with `partial`. Then `leftovers_stopped`, the `UsageLimitStop`, the error-turn `RuntimeError`, and the non-zero exit with no text.

Claude Code's loop (`agent_cc.py:365-459`) already has the shape this change copies.

## Threat model

- **What the agent can do.** The agent runs as the engineer's own user, so any command it runs can write to pi's stdout through `/proc/<pi pid>/fd/1`. That injects whole lines into the stream that `agent_pi.run` reads.
  - Under `local_guard: agentsh`, agentsh would deny that write, but pi doesn't run under agentsh yet (Next item 7).
  - Under `local_guard: text`, nothing stops it.
- **What a forged line could do at the baseline.** One forged line that isn't an object ended the loop, and with it every later rule check, while pi kept running. Every forbid rule was then off for the rest of the call.
- **What this change stops.** Such a line is now skipped, and pi is still watched after it. Any exception that does leave the loop now stops pi's tree first.
- **What this guard can't stop at its level.** The agent can still:
  - end its own call by injecting a line that makes the loop or the forwarder raise, such as a `content`, `usage.cost` or `tool_execution_end` `result` that isn't the expected type. That is now fail-closed: pi is stopped and the call fails.
  - forge tool-call announcements. Those only stop the run sooner.
  - act through anything that never reaches pi's stdout.
- **Not in reach of this guard.** A process that leaves pi's process tree (for example `setsid` plus a double fork) outlives `stop_tree`. That is old and the same in both harnesses.

## Bar

Must, in every mode (`text` and `agentsh`, on the host and in a sandbox tree):

1. **Bad lines don't end the loop.** A line on pi's stdout that isn't a JSON object, or a `message_end` whose `message` or `usage` isn't an object, never ends the read loop, and pi is still watched after it. A forbidden tool call after such a line gives the same `RuleBreach` (same type, same `partial`) as it does at the baseline without that line.
2. **Any exception stops the tree.** Any exception that leaves pi's read loop stops pi's process tree before `run` returns or raises. That covers `Exception` and `BaseException`, from `on_event` or from anywhere in the loop body, the raw log's open included.
3. **The guard is closed exactly once.** `agentsh_guard.close` and `on_exit` each run exactly once per call: on a clean end, on a rule stop, on a three-denial stop, on any other exception and on a `KeyboardInterrupt`.
4. **Every baseline refusal and stop stays as it was,** with the same exception type and `partial`:
   - a `RuleBreach` from `on_event` carries `partial = result`;
   - a three-denial stop replaces any other `Exception` leaving the loop (not a `BaseException` that isn't an `Exception`, as in Claude Code), with `partial = result`, raised `from` the original;
   - on a clean end, `check_stopped`, `SilentStop`, `UsageLimitStop`, the error-turn `RuntimeError` and the non-zero-exit `RuntimeError` run as before.
5. **Claude Code's loop doesn't change.** `agent_cc.py` is not edited.

Must never, where the baseline does otherwise:

- keep an agent file, run agent code, or delete or overwrite an engineer file;
- drop from `on_event` an event the baseline delivered there. The one new skip is a `message_end` whose `message` is present and not a dict, and the baseline never delivered one of those because its loop raised first.

Any change beyond the three in the request is a finding. The three are the non-object skip, the loop shape, and the `config.md` decision below.

## Out of scope

These holes stay open, so a reviewer files them as old holes:

- **pi's policy under agentsh** (Next item 7). pi doesn't run under agentsh.
- **The drop line's type taken over by agent text before it.** This is old hole 2 of `adws/adw_data/sessions/955ca723/context_handoff/bypass_review_1.md`.
- **Claude Code's loop.**
- **Run F's retention.**
- **Errors before the `try`, the same in both harnesses.** An error in `silence.watch`'s own setup, in `on_spawn`, or in anything before the `try` still leaves without stopping the tree. Claude Code's loop has the same gap, and this change copies its shape.
- **Other malformed fields still end the call, now with pi stopped.** These are a `message.content` that isn't a list of dicts, a `usage.cost` that isn't a dict, a `usage` field that isn't a number, a `tool_execution_end` `result` that isn't a dict, and any other field the forwarder reads. Each ends the call through the new handler (fail closed). Making them skippable would change `rules.py` / `agents.py`, which this change doesn't touch.
- **A second interrupt during the `finally`'s wait,** the same as in Claude Code.

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Only `run` changes. Replace the block from `with silence.watch(...) as dog:` through the `on_exit` call after it (baseline lines 707-766) with the shape below. Leave everything from `try: agentsh_guard.check_stopped(...)` onward exactly as it is.

```python
    stderr = ""
    left_by = None
    with silence.watch(process, request.max_silence_seconds) as dog:
        try:
            with session_files.open_append(raw_path, request.data_root) as raw:
                assert process.stdout is not None
                for line in process.stdout:
                    dog.touch()
                    raw.write(line)
                    raw.flush()                      # events land on disk as they happen
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue                     # null, a list, a number or a string: no event, as in agent_cc
                    if event.get("type") == "agent_end":
                        dog.finished()
                    if event.get("type") == "message_end":
                        message = event.get("message", {})
                        if not isinstance(message, dict):
                            continue                 # announces nothing; the forwarder can't read it either
                        if message.get("role") == "assistant":
                            ... unchanged body, except the usage line:
                            usage = message.get("usage")
                            if not isinstance(usage, dict):
                                usage = {}
                            ... unchanged ...
                    # agentsh_ events come only from the guard (start/close); a stream copy is a command's forgery.
                    if on_event and not agentsh_guard.guard_event(event):
                        try:
                            on_event(event)
                        except rules.RuleBreach as error:
                            error.partial = result   # pi priced every turn so far
                            raise
        except BaseException as error:
            left_by = error
            # Leaving mid-call, by a stop, a forbidden command, a tracer failure, an error in this loop
            # or `just kill`: the agent must not run on unwatched. On the host the kill reaches it too,
            # but in a sandbox VM nothing else does, and the wait below would last until it finished.
            process.stop_tree()
            raise
        finally:
            if left_by is None:
                stderr = process.stderr.read() if process.stderr else ""
            result.returncode = process.wait()
            agentsh_guard.close(process, on_event)
            if on_exit:
                on_exit(process.pid)
            if isinstance(left_by, Exception) and not isinstance(left_by, rules.RuleBreach):
                # Three denials replace whatever other stop the call was leaving by.
                stop = agentsh_guard.breach(process, request.agent_name)
                if stop:
                    stop.partial = result           # pi priced every turn so far
                    raise stop from left_by
```

Then the existing post-loop code follows unchanged, starting at `try: agentsh_guard.check_stopped(...)`. Delete the old post-loop `stderr = ...`, `result.returncode = process.wait()`, `agentsh_guard.close(...)` and `on_exit` lines, since the `finally` now does them.

Points the builder must keep:

- **`usage` falls back the way the baseline's `or {}` did.** Read it as `usage = message.get("usage")` with a non-dict falling back to `{}`. A missing, `None`, `0` or `""` usage still gives `{}`, as `message.get("usage", {}) or {}` did. Only a truthy non-dict changes, from a crash to `{}`. The rest of the assistant-turn body, the `cost` line included, stays byte-for-byte.
- **`message` keeps the baseline's default.** It is read with `event.get("message", {})`, so a `message_end` without a `message` key is still forwarded, as at the baseline. Only a present non-dict (`None`, `5`, `"x"`, `[1]`, `true`) is skipped with `continue`, before `on_event`. The baseline's loop raised on each of those before `on_event`, so no event the baseline forwarded is lost. If such a line were forwarded, `rules.calls`, `rules.results` and `ToolCallTracker.observe` would raise on it, and that would end the loop.
- **The non-object `continue` comes after the raw-log write,** as now. The raw log keeps every line.
- **`stderr` is read only on a clean end.** Do not copy `agent_cc`'s unconditional read. On an exception path the baseline never read stderr, and a process that escaped the tree while holding stderr would make the read block. On a clean end the order stays as at the baseline: stderr, wait, close, `on_exit`.
- **The wait now happens inside `silence.watch`,** as in `agent_cc`, where at the baseline it came after the watch. On a clean end, stdout is closed by then and the watchdog has normally finished. Note it in the review as part of item 2's shape. Add no new behaviour for it.
- **`on_event` failures other than a breach are no longer caught inline.** The inner handler catches only `rules.RuleBreach`, to set `partial`. Every other error reaches the outer `except BaseException`, which stops the tree. The `finally` then gives the three-denial replacement that the inline code gave.
- **No other function in the file changes.** `ToolCallTracker`, `_text_of` and the env helpers stay as they are.

### `.claude/skills/sssf/tests/test_agentsh_guard.py`

Add the new tests here, after the `forged_run` tests. That way they reuse the `cfg`, `machine` and `service` fixtures, so the agentsh-mode config comes from the shipped `.claude/skills/sssf/templates/sssf.config.yaml` (`SHIPPED`), never a test-only one. They also reuse `PUSH`, `_real_forwarder`, `denial` and `_fake_pi`.

Add a `loop_run` fixture modelled on `forged_run`, pi only, with `params=["text", "agentsh"]`:

- Start from `_fake_pi(tmp_path, monkeypatch, _turn("stop", "hi"))` for its monkeypatches and request.
- Overwrite `PI_PATH`'s script with a Python fake pi (`#!{sys.executable}`, as `stand_in` in `test_rules.py`). The fake pi:
  1. prints each line of a JSON-lines file given by an env var, flushing after each;
  2. starts a child `sleep 60` and writes its pid to `tmp_path / "child.pid"`;
  3. sleeps `2` seconds, writes `tmp_path / "marker"`, then sleeps 60.
- In `agentsh` mode, set `cwd` to `machine.root` and `local_guard` to `machine.guard`, as `forged_run` does. In `text` mode, keep `LocalGuard(mode="text")`.
- Wrap `agentsh_guard.close` with `monkeypatch.setattr` in a counter that calls the real one. Collect `on_exit` pids in a list.
- Return a `run(lines, on_event=None)` callable that writes the lines and calls `agent_pi.run(req, on_event=..., on_exit=exits.append)`. It should expose `run.closes`, `run.exits` and `run.mode`.

Helpers:

- `PI_PUSH = {"type": "message_end", "message": {"role": "assistant", "stopReason": "toolUse", "content": [{"type": "toolCall", "id": "c1", "name": "bash", "arguments": {"command": "git push"}}], "usage": {"input": 100, "output": 20, "cost": {"total": 0.004}}}}`. Reuse it if an equal constant already exists.
- `gone(path_or_pid)`: polls up to 5 s for a pid to be dead, as `alive` does in `test_rules.py`. Copy that helper; don't import it.

Tests, each parametrized over both modes through `loop_run`:

1. **`test_a_line_that_is_not_an_object_leaves_pi_watched`**, parametrized over these lines:
   - `null`
   - `[1]`
   - `5`
   - `"x"`
   - `{"type":"message_end","message":5}`
   - `{"type":"message_end","message":{"role":"assistant","usage":5}}`

   Write the line raw, not through `json.dumps` of a Python value, so that `"x"` is the JSON string. Then send `PI_PUSH` with the real forwarder `_real_forwarder(agent_pi)`. Expect:
   - `pytest.raises(rules.RuleBreach, match="may have run")`;
   - `stop.value.partial.cost == pytest.approx(0.004)`;
   - `loop_run.closes == 1` and `len(loop_run.exits) == 1`;
   - the child pid is gone;
   - after `time.sleep(3)`, `tmp_path / "marker"` does not exist.

   Add a control case with no bad line, so the same assertions are shown to hold at the baseline's behaviour.
2. **`test_an_error_in_the_loop_stops_pi_before_run_raises`**:
   - The line is `{"type":"message_end","message":{"role":"assistant","content":5}}`, where `_text_of` raises `TypeError` in the loop body outside `on_event`. Use no forwarder.
   - Expect `TypeError`, `closes == 1`, `len(exits) == 1`, the child gone, and no marker after `time.sleep(3)`.
   - This is the reviewer's experiment: at the baseline the marker is written after `run` has raised.
3. **`test_a_keyboard_interrupt_in_the_loop_stops_the_tree`**, parametrized over where the interrupt comes from:
   - (a) `on_event` raises `KeyboardInterrupt` on its first event;
   - (b) `silence.Watchdog.touch` is monkeypatched to raise `KeyboardInterrupt` on its first call.

   Line: `{"type":"turn_start"}`. Expect `pytest.raises(KeyboardInterrupt)`, `closes == 1`, `len(exits) == 1`, the child gone, and no marker after `time.sleep(3)`.
4. **`test_close_and_on_exit_run_once_on_a_clean_end`**:
   - The fake pi takes an option, such as an env var `FAKE_PI_EXIT=1`, to exit right after printing instead of sleeping and writing the marker.
   - Lines: `_turn("stop", "hi")` wrapped as a `message_end`. Use no forwarder.
   - Expect the result text `"hi"`, `closes == 1` and `len(exits) == 1`.
5. **`test_close_and_on_exit_run_once_on_a_three_denial_stop`**, agentsh mode only (skip `text`):
   - Set `service.final_only = [denial(1), denial(2), denial(3)]`.
   - The forwarder raises `ValueError` on its first event, using the file's `raising` helper.
   - Expect a `rules.RuleBreach` with `refusals == 3`, `partial is not None` and `__cause__` the `ValueError`, plus `closes == 1` and `len(exits) == 1`.
   - Add a second case on a clean end with the same `final_only`, where `check_stopped` raises the breach. Expect `closes == 1` and `len(exits) == 1`.

Test 1's rule-stop cases cover "once on a rule stop". No test calls the real pi or the real agentsh service: the `service` fixture is the fake, and `machine`'s wrap is the fake binary.

### Files not changed

Do not edit any of these:

- `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`
- `.claude/skills/sssf/templates/adws/adw_modules/agents.py`
- `.claude/skills/sssf/templates/adws/adw_modules/rules.py`
- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
- `.claude/skills/sssf/templates/adws/adw_modules/silence.py`
- anything under `adws/` (protected)

## Docs this change makes out of date

- **`.claude/skills/sssf/references/config.md`: no change.** It promises nothing about pi's loop that this change breaks.
  - Its line "Three denials end the call as a breach even when it was leaving by another stop, such as a rate-limit stop. Below three, the other stop stands." (agentsh section) stays true, and now holds for more of pi's exits.
  - The `forbid` paragraph ("On a match, SSSF kills the agent and every process under it…") stays true.
  - This change finds no new limit to add: what it leaves open is fail-closed or shared with Claude Code (see Out of scope).
- **Comments in `agent_pi.run`:** the moved comments, as shown above. The `run` docstring doesn't change.
- **Nothing else applies.** The repo and the skill keep no CHANGELOG. README usage and help text don't mention the read loop.
- **For the documenter, not the builder:** no `app_docs/` folder exists in this repo today. If a write-up of run 955ca723 describes pi's read loop as stopping the tree only when `on_event` raises, the documenter should note the new shape. Plan and make no edit under `app_docs/`.

## Verification

1. **The suite passes.** The builder runs the skill's suite from a copy outside the repo, as earlier runs did:
   - Copy `.claude/skills/sssf/` to a temp folder.
   - From the copy, run `uv run --directory <copy> --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.

   The full suite must pass, including:
   - `tests/test_agentsh_guard.py`, with every new test in both modes. The existing `harness_run` tests (three denials, failing final read, non-breach failure becoming the breach) must pass for `pi` unchanged.
   - `tests/test_rules.py`, including `test_a_forbidden_command_kills_a_pi_agent_and_what_it_started`;
   - `tests/test_agent_pi.py`;
   - `tests/test_agent_cc.py`;
   - `tests/test_silence.py`.
2. **The new tests fail at the baseline.** Apply only the test changes to a second copy of `8ca7e6d`'s `.claude/skills/sssf/`, and run `pytest tests/test_agentsh_guard.py -k "not_an_object or error_in_the_loop or keyboard_interrupt"`:
   - the bad-line cases, the loop-error case and the interrupt cases must fail;
   - the control case must pass.

   Report the counts.
3. **The guard diff holds only `run`.** `git diff 8ca7e6d -- .claude/skills/sssf/templates/adws/adw_modules/` must touch only `agent_pi.py`, and only inside `run`, from the `with silence.watch` line to the line before `try: agentsh_guard.check_stopped`. `git diff 8ca7e6d -- .claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` must be empty.
4. **No doc diff.** `git diff 8ca7e6d -- .claude/skills/sssf/references/config.md` must be empty.
5. **No real pi or service.** `grep -n "PI_PATH\|agentsh_guard.API" .claude/skills/sssf/tests/test_agentsh_guard.py` must show the new tests setting `PI_PATH` only to a script under `tmp_path`, and `API` only through the `service` fixture.

## For the engineer

- **No protected file needs a change for the build.** The tests live in `.claude/skills/sssf/tests/`, which is not protected.
- **Update the hub after the merge.** The hub's installed copy, `adws/adw_modules/agent_pi.py`, is protected, so the builder doesn't touch it. Update the hub to the new skill commit, as `3d24e0f` did for `0c9720a`. The hub's roster runs Claude Code, so this matters for any pi agent the hub runs later.

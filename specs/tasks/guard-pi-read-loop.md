# Stop pi when its read loop fails, as Claude Code's does

Start from `main`, which has run 955ca723 merged as c7ac4e7. Its bypass review, `adws/adw_data/sessions/955ca723/context_handoff/bypass_review_1.md`, old hole 1, found this on both base and head.

## The problem

1. **A JSON line that isn't an object ends pi's read loop and leaves pi running unwatched.** In `agent_pi.run`, each stdout line goes through `json.loads`, and the loop then calls `event.get("type")` with no check that `event` is a dict. `null`, `[1]`, `5` or `"agentsh_note"` raise `AttributeError` there. The `try` around `on_event` doesn't cover that line, so the error leaves `run` without `process.stop_tree()`, `process.wait()` or `agentsh_guard.close`. The reviewer's fake pi printed such a line, slept 2 seconds and wrote a file, and the file was written after `run` had raised. No rule is checked against pi's later tool calls. A command reaches pi's stdout by writing to `/proc/<pi pid>/fd/1`. agentsh would deny that write, but pi doesn't run under agentsh yet (Next item 7), so under `local_guard: text` nothing stops it. The same goes for a dict event whose `message` or `usage` isn't a dict, and for any other error in the loop body.
2. **pi's loop has no handler for leaving mid-call.** Claude Code's loop in `agent_cc.run` wraps the whole read in `try`/`except BaseException`/`finally`: any exception stops the tree, and the `finally` waits, closes the guard, calls `on_exit` and checks for a three-denial stop. pi's loop stops the tree only when `on_event` raises. A `KeyboardInterrupt`, a `just kill`, or an error in `silence.watch` or the raw log leaves pi to run on, and in a sandbox VM nothing else reaches it.

## What should change

1. **pi's loop skips a line that isn't a JSON object**, as Claude Code's does, and reads `message` and `usage` only when they are dicts.
2. **pi's loop has Claude Code's shape**: any exception leaving the read loop stops the tree, and the wait, `agentsh_guard.close`, `on_exit` and the three-denial check run once on every way out, a stop included. The spec says how it keeps the existing order: a `RuleBreach` still carries `partial`, a three-denial stop still replaces any other error, and the checks after the loop (`check_stopped`, `SilentStop`, the usage limit, the error turn) still run on a clean end.
3. **`config.md`** drops nothing it promises and adds no limit, unless the spec finds one.

## The bar

The earlier bars stand. In every mode:

- A line on pi's stdout that isn't a JSON object, or whose `message` or `usage` isn't an object, never ends the loop, and pi is still watched after it.
- Any exception that leaves pi's read loop stops pi's process tree before `run` returns or raises.
- `agentsh_guard.close` and `on_exit` run exactly once per call.
- Every refusal and stop at the baseline stays as it was, with the same exception type and `partial`.
- Claude Code's loop doesn't change.

Any change beyond the three above is a finding.

## Testing

Unit tests with a fake pi: each of `null`, `[1]`, `5`, `"x"`, `{"type":"message_end","message":5}` and `{"type":"message_end","message":{"role":"assistant","usage":5}}`, followed by a forbidden tool call, gives the same refusal as at the baseline; a fake pi that writes a file 2 seconds after an exception in the loop never writes it; a `KeyboardInterrupt` raised in the loop stops the tree; `close` and `on_exit` are each called once on a clean end, a rule stop and a three-denial stop. The tests never call the real pi or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. This run changes only pi's loop, and the hub's roster runs Claude Code, so the run's own agents don't use the new code. The builder runs the suite from a copy outside the repo, as earlier runs did.

## Out of scope

pi's policy under agentsh. The drop line's type taken over by agent text before it, old hole 2 of the same review. Claude Code's loop. Run F's retention.

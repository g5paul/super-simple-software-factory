# On a pi stream, read only pi's event types

## Summary

`agents._event_forwarder` reads every event through `rules` helpers that
accept both harnesses' event types. So a pi stream also accepts Claude Code's
`system` `permission_denied` and `user` `tool_result`, and a Claude Code
stream accepts pi's `tool_execution_end`. An agent's command can write to its
harness's stdout (`/proc/$PPID/fd/1`). On pi, that lets it forge a Claude Code
denial or result for its own call, so a slipped command counts as refused
(guard run 6cec4b80, `adws/adw_data/sessions/6cec4b80/context_handoff/guard_report.md`).

Fix: each interface module names its harness (`HARNESS`).
`_event_forwarder` maps each harness to the event types it writes that the
rules read. An event of any other type skips the rule reads, and still goes
to the call tracker. `rules.py`'s API doesn't change.

This plan starts from guard run b5c71e3b's approved spec
(`git show 2c64dd8:specs/b5c71e3b_harness-event-types.md`), rechecked
against the code at `f594489`. It takes spec review 1
(`adws/adw_data/sessions/ea6e82a9/context_handoff/spec_review_1.md`) into
account: the event types don't overlap, so one type filter in the forwarder
replaces that spec's per-harness modes in `rules.calls` and `rules.results`.
The checks use the hub's own test runner from `adws/adw_modules/quality.py`
(`uv run --directory .claude/skills/sssf --with pytest ...`), not a bare
`uv run pytest`, which can't start here.

## Threat model

- The agent runs as the engineer's own user. Its shell commands can write any
  line to the harness's stdout through `/proc/$PPID/fd/1`, and the stream
  check reads that stdout. It can read its own call ids from
  `raw_output.jsonl`, and it can kill its harness.
- At this level the guard can't tell a line the harness wrote from one a
  command wrote. All it can do is ignore lines whose *type* the running
  harness never writes. That closes the cross-harness forgery and nothing more.
- What stays possible is forging the running harness's **own** types. On pi, a
  never-ran `tool_execution_end` (no `durationMs`, `isError: true`) with the
  refusal text for the agent's own call still counts as refused. A forged
  usage-limit error turn followed by `kill -9 $PPID` still ends a run as
  `rate_limit_stop`. The same holds for Claude Code's own types on a Claude
  Code stream. The real boundary for those is a VM (`--sandbox`) or agents
  under their own user.

## Baseline

`main` at `f594489`. Every finding is judged against it.

At baseline, `_event_forwarder.forward` in
`.claude/skills/sssf/templates/adws/adw_modules/agents.py` (line 490 on) does
all of the following for every event, whatever the harness:

1. `denied.update(rules.denied(event))`: Claude Code `system`/`permission_denied`.
2. `pi_blocked = set(rules.blocked(event))`: pi results without `durationMs`
   (only `tool_execution_end` and `message_end` toolResult, via `rules.pi_result`).
3. `never_runs`: a pi `message_end` assistant turn with `stopReason` error/aborted.
4. `rules.calls(event)`: `assistant` or `message_end` events with **either**
   `tool_use` or `toolCall` blocks, plus `tool_execution_start`.
5. `rules.results(event)`: `tool_execution_end`, `message_end` toolResult,
   and `user` `tool_result`.
6. `by_text = rules.refused(text) and (call_id in pi_blocked or not rules.pi_result(event))`:
   a `user` result counts by its text alone, on any stream.
7. `tracker.observe(event)`: already per harness. `agent_pi.ToolCallTracker`
   reads only `message_end` and `tool_execution_*`, and `agent_cc.ToolCallTracker`
   overrides `observe` to read only `assistant` and `user`. The tracker feeds
   the trace and the silence stop (`forward.tracker.running()`, agents.py:276),
   never a rule decision.

So the rule reads use exactly these event types. On pi they are
`message_end`, `tool_execution_start` and `tool_execution_end`. On Claude
Code they are `assistant`, `user` and `system`. The two sets don't overlap.

## Bar

Must:

- On a pi stream, a refuse-rule call that ran is a `RuleBreach` even when the
  agent forged a Claude Code `permission_denied` for its id, or a `user`
  `tool_result` with the refusal text, before pi's real result.
- On a Claude Code stream, a forged pi `tool_execution_end` without
  `durationMs` that carries the refusal text does not count the call as
  refused.
- Every honest stream on both harnesses gives exactly the baseline outcome:
  the same refusals counted, the same breaches, the same `rule_refused`
  events, the same three-refusal stop, the same pending-at-exit breach
  (`forward.ended`), the same error-turn skip on pi, and the same trace
  records (the tracker still sees every event).
- Every existing test in `.claude/skills/sssf/tests/` and `adws/tests/` passes
  unchanged.

Must never, against the baseline:

- Count as refused a call that the baseline counts as ran, on either harness.
- Keep an agent file, run agent code, or delete or overwrite an engineer file
  where the baseline does otherwise. This change touches none of those paths.
- Read the harness from anything in the stream.

Allowed change: when a whole event has the other harness's type (an
`assistant` `tool_use` announcement on pi, or a `tool_execution_start` or
`message_end` announcement on Claude Code), it is now ignored, even where the
baseline turned it into a breach or a pending call. Such an event could only
add a stop the agent caused itself, so ignoring it lets nothing through.
Inside an event of the harness's own type, every block is read as at
baseline.

## Design

Each interface module declares the harness it runs, using the same values as
`AgentConfig.coding_agent` (`Literal["pi", "claude_code"]`,
`.claude/skills/sssf/templates/adws/adw_modules/data_types.py:407`):

- `agent_pi.HARNESS = "pi"`
- `agent_cc.HARNESS = "claude_code"`

`agents.py` gets one constant:

```python
# The event types each harness writes that the stream check reads for rules.
# An event of another type, which only a command writing to the harness's
# stdout can add, counts for nothing.
RULE_EVENT_TYPES = {
    "pi": frozenset({"message_end", "tool_execution_start", "tool_execution_end"}),
    "claude_code": frozenset({"assistant", "user", "system"}),
}
```

`_event_forwarder` reads `harness = getattr(interface, "HARNESS", None)`.
`None` means "read every type, as at baseline". Only a test's stand-in
interface has no `HARNESS`: `test_rules.forwarder()` and the fake in
`test_execute_stops_when_the_agent_dies_with_a_refuse_call_pending`. Those
tests mix both harnesses' events on one forwarder and must pass unchanged.
Production always gets `agent_pi` or `agent_cc` from `agents._interface`
(agents.py:103), and test 6 checks that both declare a `HARNESS` matching
`coding_agent`, with an entry in `RULE_EVENT_TYPES`.

### Which reads change

| Read | pi stream | Claude Code stream |
|---|---|---|
| `rules.denied` (`system`) | **not reached**: `system` isn't a pi type | unchanged |
| `rules.blocked` (pi result types) | unchanged | **not reached**: pi types are skipped |
| `never_runs` (`message_end`) | unchanged | **not reached** |
| `rules.calls` on `assistant` | **not reached** | unchanged, both block kinds as at baseline |
| `rules.calls` on `message_end`, `tool_execution_start` | unchanged, both block kinds as at baseline | **not reached** |
| `rules.results` on `tool_execution_end`, `message_end` toolResult | unchanged | **not reached** |
| `rules.results` on `user` `tool_result` | **not reached** | unchanged |
| `by_text` expression | code unchanged. Only pi results reach it, so it reads `refused(text) and call_id in pi_blocked` | code unchanged. Only `user` results reach it, so it reads `refused(text)` |
| `tracker.observe` (call tracker) | still called for every event; already pi-only and not a rule input | still called for every event; already Claude Code-only and not a rule input |
| Other types (`agent_start`, `message_update`, `result`, ...) | skipped for rules; the rules never read them, so no change | same |

No other module reads events for rules. `rules.commands` is used only by
tests (`enforce()` in `test_rules.py`), and `agent_pi` and `agent_cc` call no
`rules` event helper.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Add a module constant next to `PI_PATH` (line 28):
  `HARNESS = "pi"   # agents._event_forwarder reads only pi's event types for rules`.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- Add `HARNESS = "claude_code"` at module level, next to `CLAUDE_PATH`
  (line 47), with the same comment worded for Claude Code. Define it in
  `agent_cc` itself. Don't import it from `agent_pi`: the module imports only
  `ToolCallTracker` from there, so keep it that way.
- Module docstring, around line 28 ("The forwarder ... counts that call as
  refused"): add "on a Claude Code stream only", or equivalent short wording.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

- Add `RULE_EVENT_TYPES` (as in Design) at module level, near
  `_event_forwarder` or with the other module constants after
  `JSON_FIX_ATTEMPTS` (line 26).
- In `_event_forwarder` (line 490), after `tracker = interface.ToolCallTracker()`:
  `harness = getattr(interface, "HARNESS", None)   # agent_pi or agent_cc; None only for a test's stand-in`.
- In `forward`, gate the rule reads on the event's type, and leave the
  tracker loop outside the gate. The cleanest way is to move the current body
  from `denied.update(...)` through the `rules.results` loop, unchanged, into
  an inner `check(event)` (with the `nonlocal refusals`):

  ```python
  def forward(event: dict) -> None:
      if harness is None or event.get("type") in RULE_EVENT_TYPES[harness]:
          check(event)
      for record in tracker.observe(event):
          ...  # unchanged
  ```

  Keep the order: the rule reads first, then the tracker, as at baseline, so a
  `RuleBreach` still raises before that event's trace record. Change nothing
  inside the moved code.
- Docstring of `_event_forwarder`: add that it reads only the event types of
  the harness its `interface` runs (`interface.HARNESS`, `RULE_EVENT_TYPES`).
  On pi, Claude Code's `permission_denied`, `user` results and `assistant`
  announcements count for nothing. On Claude Code, pi's results and
  announcements count for nothing. Every event still reaches the tracker, and
  an interface with no `HARNESS` (a test's stand-in) reads every type.
- Do not change `execute()`. It already passes `interface` from
  `_interface(agent)` (line 232).

### 4. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

- No code change. The signatures and behavior of `calls`, `results`,
  `denied`, `blocked`, `pi_result` and `refused` stay the same.
- Module docstring only, the backstop paragraph (lines 23 to 28, "The stream
  check stays as the backstop…"): add one sentence saying the check reads
  only the running harness's own event types
  (`agents.RULE_EVENT_TYPES`), so a line of the other harness's type that a
  command writes to the stream counts for nothing.

### 5. `.claude/skills/sssf/tests/test_rules.py`: new tests (append only, edit no existing test)

Reuse the existing helpers (`announce`, `finish`, `ran`, `claude_call`,
`REFUSE`, `PUSH`). Add helpers that build a forwarder on a real interface
module:

```python
def real_forwarder(interface, rule=REFUSE):
    """A forwarder on agent_pi or agent_cc itself, so it reads that harness's types."""
    events, said = [], []
    run = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append),
                          console=SimpleNamespace(advise=said.append))
    forward = agents._event_forwarder(run, SimpleNamespace(phase_id="r1_01"), "builder",
                                      interface, [rule])
    return forward, events


def refused_events(events):
    return [e for e in events if e.name == "rule_refused"]


def claude_result(call_id, text):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": text}]}}
```

The real trackers also log `tool_call` records (`e.type == "tool_call"`), so
the assertions filter on `rule_refused`.

Tests (use these exact names; the check below runs them by node id):

1. `test_on_pi_a_forged_permission_denied_does_not_refuse_a_call_that_ran`:
   an `agent_pi` forwarder. `announce("c1")`, then forward
   `{"type": "system", "subtype": "permission_denied", "tool_use_id": "c1"}`.
   Then `ran("c1", "Everything up-to-date")` raises `RuleBreach` matching
   "may have run", and no `rule_refused` event is logged.
2. `test_on_pi_a_forged_claude_result_with_the_refusal_does_not_refuse_a_call_that_ran`:
   parametrize the text over `rules.REFUSED` and `"Exit code 126\n" + rules.REFUSED`.
   An `agent_pi` forwarder. `announce("c1")`, then forward
   `claude_result("c1", text)`: no raise and no `rule_refused`. Then
   `ran("c1", "Everything up-to-date")` raises `RuleBreach`. In a second
   forwarder, the same forgery followed by `forward.ended()` raises
   `RuleBreach`, because the call is still pending.
3. `test_on_claude_code_a_forged_pi_never_ran_result_does_not_refuse`:
   an `agent_cc` forwarder. `claude_call("t1")`, then forward
   `finish("t1", rules.REFUSED)` (pi's never-ran shape: `isError: True`, no
   `durationMs`). No `rule_refused` is logged. Then
   `claude_result("t1", "Everything up-to-date")` raises `RuleBreach`.
4. `test_a_forged_announcement_of_the_other_harness_counts_for_nothing`:
   whole events of the other harness's type only, with stop-mode `PUSH`.
   - `real_forwarder(agent_pi, PUSH)`: forwarding `claude_call("t9")` doesn't
     raise, and `forward.ended()` returns `None`.
   - `real_forwarder(agent_cc, PUSH)`: the same for `announce("c9")`
     (`tool_execution_start`).
5. `test_honest_streams_read_the_same_on_the_real_interfaces`:
   - pi (`agent_pi`): `announce("c1")`, then
     `finish("c1", rules.REFUSED + ", by this repo's rule: x")` gives one
     `rule_refused` with `refusals == 1`, and at least one `tool_call` record
     is logged (the tracker still sees pi's events). `announce("c2")` then
     `ran("c2", "Everything up-to-date")` raises. In a fresh forwarder, three
     refused calls stop with `refusals == rules.MAX_REFUSALS`.
   - Claude Code (`agent_cc`): `claude_call("t1")`, a `permission_denied` for
     `"t1"`, then `claude_result("t1", "Permission denied")` is counted, and
     a `tool_call` record is logged. `claude_call("t2")` then
     `claude_result("t2", "Exit code 126\n" + rules.REFUSED)` is counted.
     `claude_call("t3")` then `claude_result("t3", "Everything up-to-date")`
     raises.
6. `test_each_interface_names_the_harness_it_runs`: `agent_pi.HARNESS == "pi"`
   and `agent_cc.HARNESS == "claude_code"`. For each value `v` of
   `typing.get_args(AgentConfig.model_fields["coding_agent"].annotation)`,
   `agents._interface(SimpleNamespace(coding_agent=v)).HARNESS == v` and
   `v in agents.RULE_EVENT_TYPES`. Also,
   `agents.RULE_EVENT_TYPES["pi"].isdisjoint(agents.RULE_EVENT_TYPES["claude_code"])`.
   Import `AgentConfig` from `adw_modules.data_types`.

A guard test that needs a config builds it from the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`, as
`test_secret_files.shipped_config` does. The tests above need no config.

### 6. `docs/open.md`: the forgery row (the `build` row dated 2026-11-07, starting "An agent's command can write to its harness's stdout")

Shrink the first cell to what remains. Keep the other cells (`build`,
`2026-11-07`, the config.md link) as they are. Replacement text for the first
cell:

> An agent's command can write to its harness's stdout (`/proc/$PPID/fd/1`), which the stream check reads. The check reads only the running harness's own event types, so the other harness's types count for nothing, but it can't tell the harness's own types from a forgery. Guard run 6cec4b80's review forged two on pi: a never-ran `tool_execution_end` for the agent's own call, with the id read from `raw_output.jsonl`, so a slipped command counts as refused; and a usage-limit error turn followed by `kill -9 $PPID`, which ends the run as `rate_limit_stop` and tells the engineer to resume. The fix: a VM, or agents under their own user

Do not touch the next row (the Claude Code prefix-refusal `watch` row).

### Docs left unchanged

- `.claude/skills/sssf/references/config.md`: checked. Line 427 states the
  general limit (a forged result can make a command that ran look refused; a
  forged usage-limit error) without naming the cross-harness case, and it's
  still true. Lines 415 and 437 describe Claude Code's `permission_denied` on
  a Claude Code agent, which is still true. No change.
- No CHANGELOG exists in the repo, and README has no usage this touches.
- `app_docs/` doesn't exist and this plan doesn't edit it. No earlier
  write-up describes the old behavior. The documenter may note that the
  forwarder now reads only its harness's event types.

## Verification

The hub's quality command (`adws/adw_modules/quality.py` `test`) already runs
the whole skill suite, and `chain_test` runs `adws/tests`. The extra check
runs the six new tests by node id, with the same runner and flags, so it
fails until they exist and pass:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_rules.py::test_on_pi_a_forged_permission_denied_does_not_refuse_a_call_that_ran tests/test_rules.py::test_on_pi_a_forged_claude_result_with_the_refusal_does_not_refuse_a_call_that_ran tests/test_rules.py::test_on_claude_code_a_forged_pi_never_ran_result_does_not_refuse tests/test_rules.py::test_a_forged_announcement_of_the_other_harness_counts_for_nothing tests/test_rules.py::test_honest_streams_read_the_same_on_the_real_interfaces tests/test_rules.py::test_each_interface_names_the_harness_it_runs
```

1. The command above exits 0. Before the build it exits 4 (node ids not
   found). It starts in the hub and needs no new dependency.
2. The full skill suite passes:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.
   No existing test is edited: the only change under
   `.claude/skills/sssf/tests/` is the appended block in `test_rules.py`.
3. `rules.py` shows only a docstring change (no signature change to `calls`
   or `results`).
4. Against the baseline, tests 1 to 3 fail on `f594489`'s `agents.py`,
   because the forged event counts the call as refused and nothing raises
   `RuleBreach`. They pass after the change. The builder may confirm this by
   temporarily reverting the `agents.py` hunk and then restoring it. Commit
   nothing.

## Out of scope

Old holes this change leaves open, to be filed as old holes:

- On pi, a forged never-ran `tool_execution_end` (or `message_end`
  toolResult) without `durationMs`, carrying the refusal text for the
  agent's own call id, still counts a slipped command as refused.
- A forged usage-limit error turn followed by `kill -9 $PPID` still ends a
  run as `rate_limit_stop`.
- On Claude Code, the agent forging Claude Code's own types
  (`permission_denied`, or a `user` `tool_result` with the refusal text) on a
  Claude Code stream. The prefix refusal read by text has its own watch row.
- A forged block of the other harness's kind inside an event of the running
  harness's own type (a `tool_use` block in a pi `message_end`, a `toolCall`
  block in a Claude Code `assistant`). It's read as at baseline, and it can
  only add a pending call or a breach.
- Anything outside the forwarder's choice of which event types to read. The
  real fix for the holes above is a VM (`--sandbox`) or a separate user.

## For the engineer

- The installed copy under `adws/adw_modules/` (protected) is updated from the
  skill by the usual SSSF update step after this merges. The builder doesn't
  touch it.

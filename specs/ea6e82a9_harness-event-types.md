# On a pi stream, read only pi's event types

## Summary

`agents._event_forwarder` reads every event through `rules` helpers that
accept both harnesses' event types. So a pi stream also accepts Claude Code's
`system` `permission_denied` and `user` `tool_result`, and a Claude Code
stream accepts pi's `tool_execution_end`. An agent's command can write to its
harness's stdout (`/proc/$PPID/fd/1`). On pi, that lets it forge a Claude Code
denial or result for its own call and make a slipped command count as refused
(guard run 6cec4b80, `adws/adw_data/sessions/6cec4b80/context_handoff/guard_report.md`).

Fix: the forwarder gets its harness from the interface module it is given
(`agent_pi` or `agent_cc`, which `agents._interface` picks from the roster
entry's `coding_agent`). For rules, it reads only that harness's event types.

This plan starts from guard run b5c71e3b's approved spec
(`git show 2c64dd8:specs/b5c71e3b_harness-event-types.md`), rechecked
against the code at `f594489`. The code it describes is unchanged: the only
commit after `f873ce1` that touches the skill is the plan-check prompt change,
and `agents.py`, `rules.py`, `agent_pi.py`, `agent_cc.py` and `test_rules.py`
read as that spec assumed. The one fix is the verification. That run's check
was `uv run pytest ...`, which can't start in the hub because pytest is not a
project dependency. This plan's checks use the hub's own test command from
`adws/adw_modules/quality.py` (`uv run --directory .claude/skills/sssf --with pytest ...`).

## Threat model

- The agent runs as the engineer's own user. Its shell commands can write any
  line to the harness's stdout through `/proc/$PPID/fd/1`, and the stream
  check reads that stdout. It can read its own call ids from
  `raw_output.jsonl`, and it can kill its harness.
- At this level the guard can't tell a line the harness wrote from one a
  command wrote. All it can do is ignore lines whose *type* the running
  harness never writes. That closes the cross-harness forgery and nothing more.
- What stays possible is forging the running harness's **own** types. On pi, a
  never-ran `tool_execution_end` (no `durationMs`, `isError: true`) carrying
  the refusal text for the agent's own call still counts as refused. A forged
  usage-limit error turn followed by `kill -9 $PPID` still ends a run as
  `rate_limit_stop`. The same goes for Claude Code's own types on a Claude Code
  stream. The real boundary for those is a VM (`--sandbox`) or agents under
  their own user.

## Baseline

`main` at `f594489`. Every finding is judged against it.

At baseline, `_event_forwarder.forward` in
`.claude/skills/sssf/templates/adws/adw_modules/agents.py` (line 490 on) does
the following for every event, whatever the harness:

1. `denied.update(rules.denied(event))`: Claude Code `system`/`permission_denied`.
2. `pi_blocked = set(rules.blocked(event))`: pi results without `durationMs`.
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
  (`forward.ended`), the same error-turn skip on pi, and the same trace records.
- Every existing test in `.claude/skills/sssf/tests/` and `adws/tests/` passes
  unchanged.

Must never, against the baseline:

- Count as refused a call that the baseline counts as ran, on either harness.
- Keep an agent file, run agent code, or delete or overwrite an engineer file
  where the baseline does otherwise. This change touches none of those paths.
- Read the harness from anything in the stream.

Allowed change: a forged cross-harness *announcement* (for example an
`assistant` `tool_use` on pi) that the baseline turned into a breach or a
pending call is now ignored. It could only add a stop the agent caused
itself, so ignoring it lets nothing through.

## Design

Each interface module declares the harness it runs, using the same values as
`AgentConfig.coding_agent` (`Literal["pi", "claude_code"]`,
`.claude/skills/sssf/templates/adws/adw_modules/data_types.py:407`):

- `agent_pi.HARNESS = "pi"`
- `agent_cc.HARNESS = "claude_code"`

`_event_forwarder` reads `harness = getattr(interface, "HARNESS", None)`.
`None` means "read both, as at baseline". Only a test's stand-in interface
has no `HARNESS`: `test_rules.forwarder()` and the fake in
`test_execute_stops_when_the_agent_dies_with_a_refuse_call_pending`. Those
tests mix both harnesses' events on one forwarder and must pass unchanged.
Production always gets `agent_pi` or `agent_cc` from `agents._interface`
(agents.py:103), and a new test checks that both declare a `HARNESS` matching
`coding_agent`.

`rules.calls` and `rules.results` take an optional `harness` (default `None`,
meaning both). So `rules.commands`, and `rules.calls(event)` or
`rules.results(event)` called without it, behave as today.

### Which reads change

| Read | pi stream | Claude Code stream |
|---|---|---|
| `rules.denied` (`system`/`permission_denied`) | **not read** | read, unchanged |
| `rules.blocked` (pi result without `durationMs`) | read, unchanged | **not read** (empty set) |
| `never_runs` (pi `message_end` assistant error/aborted) | unchanged | code unchanged. Moot, since `calls` no longer reads `message_end` on Claude Code |
| `rules.calls`: `assistant` events | **not read** | read, `tool_use` blocks only (**`toolCall` blocks no longer read**) |
| `rules.calls`: `message_end` events | read, `toolCall` blocks only (**`tool_use` blocks no longer read**) | **not read** |
| `rules.calls`: `tool_execution_start` | read, unchanged | **not read** |
| `rules.results`: `tool_execution_end`, `message_end` toolResult | read, unchanged | **not read** |
| `rules.results`: `user` `tool_result` | **not read** | read, unchanged |
| `by_text` expression | code unchanged. With filtered results it reduces to `refused(text) and call_id in pi_blocked` | code unchanged. Reduces to `refused(text)` |
| `tracker.observe` (call tracker) | unchanged (already pi-only, and not a rule input) | unchanged (already Claude Code-only, and not a rule input) |

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
  `agent_cc` itself. Do not import it from `agent_pi`: the module already
  imports only `ToolCallTracker` from there, so keep it that way.
- Module docstring, around line 28 ("The forwarder ... counts that call as
  refused"): add "on a Claude Code stream only", or equivalent short wording.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

- Add `harness: Optional[str] = None` to `calls` (line 187) and `results`
  (line 212).
  - `calls(event, harness=None)`:
    - `"claude_code"`: only `assistant` events, only `tool_use` blocks.
    - `"pi"`: only `message_end` events with `toolCall` blocks, and
      `tool_execution_start`.
    - `None`: exactly today's behavior.
    The shell-tool filter and the `(call id, command)` shape stay the same.
  - `results(event, harness=None)`:
    - `"pi"`: only `tool_execution_end` and `message_end` toolResult.
    - `"claude_code"`: only `user` `tool_result` blocks.
    - `None`: exactly today's behavior.
- `commands(event)` keeps calling `calls(event)` with no harness.
- Leave the signatures of `denied`, `blocked`, `pi_result` and `refused` as
  they are. The forwarder decides when to call them.
- Docstrings:
  - `calls` and `results` say what each harness value reads, and that `None`
    reads both.
  - `denied` says the forwarder reads it only on a Claude Code stream.
  - `blocked`: replace "Claude Code's stream has no such field, so this
    returns nothing for it and the stream check reads its results by text
    alone" with: the forwarder reads it only on a pi stream, and reads Claude
    Code's results by text alone.
- Module docstring, the backstop paragraph ("The stream check stays as the
  backstop…"): add one sentence saying the check reads only the running
  harness's own event types, so a line of the other harness's type written
  to the stream by a command counts for nothing.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/agents.py` (`_event_forwarder`, line 490)

- After `tracker = interface.ToolCallTracker()`, add:
  `harness = getattr(interface, "HARNESS", None)   # agent_pi or agent_cc; None only for a test's stand-in`.
- In `forward`:
  - `if harness != "pi": denied.update(rules.denied(event))`
  - `pi_blocked = set(rules.blocked(event)) if harness != "claude_code" else set()`
  - `rules.calls(event, harness)` and `rules.results(event, harness)`.
  - Leave `never_runs`, the `by_text` expression, the pending, refusal and
    `ended` logic, and `tracker.observe(event)` as they are.
- Docstring: add that the forwarder reads only the event types of the harness
  its `interface` runs (`interface.HARNESS`). On pi, Claude Code's
  `permission_denied`, `user` results and `assistant` announcements count for
  nothing. On Claude Code, pi's results and announcements count for nothing.
  An interface with no `HARNESS` (a test's stand-in) reads both.
- Do not change `execute()`. It already passes `interface` from
  `_interface(agent)` (line 232).

### 5. `.claude/skills/sssf/tests/test_rules.py`: new tests (append only, edit no existing test)

Reuse the existing helpers (`announce`, `finish`, `ran`, `claude_call`,
`REFUSE`, `PUSH`). Add one helper that builds a forwarder on a real interface
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
```

The real trackers also log `tool_call` records, so assertions filter on
`rule_refused`. A Claude Code result helper is useful:
`claude_result(call_id, text)` returns
`{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": call_id, "content": text}]}}`.

Tests (use these exact names; the check below runs them by node id):

1. `test_on_pi_a_forged_permission_denied_does_not_refuse_a_call_that_ran`:
   `agent_pi` forwarder. `announce("c1")`, then forward
   `{"type": "system", "subtype": "permission_denied", "tool_use_id": "c1"}`.
   Then `ran("c1", "Everything up-to-date")` raises `RuleBreach` matching
   "may have run", and no `rule_refused` event is logged.
2. `test_on_pi_a_forged_claude_result_with_the_refusal_does_not_refuse_a_call_that_ran`:
   parametrize the text over `rules.REFUSED` and `"Exit code 126\n" + rules.REFUSED`.
   `agent_pi` forwarder. `announce("c1")`, then forward
   `claude_result("c1", text)`: no raise, no `rule_refused`. Then
   `ran("c1", "Everything up-to-date")` raises `RuleBreach`. In a second
   forwarder, the same forgery followed by `forward.ended()` raises
   `RuleBreach`, because the call is still pending.
3. `test_on_claude_code_a_forged_pi_never_ran_result_does_not_refuse`:
   `agent_cc` forwarder. `claude_call("t1")`, then forward
   `finish("t1", rules.REFUSED)` (pi's never-ran shape: `isError: True`, no
   `durationMs`). No `rule_refused` is logged. Then
   `claude_result("t1", "Everything up-to-date")` raises `RuleBreach`.
4. `test_a_forged_announcement_of_the_other_harness_counts_for_nothing`: an
   `agent_pi` forwarder with stop-mode `PUSH` (`real_forwarder(agent_pi, PUSH)`).
   Forwarding `claude_call("t9")` does not raise, and `forward.ended()`
   returns `None`. Do the same on `agent_cc` with `announce("c9")`, and with a
   pi `message_end` assistant carrying a `toolCall` block for `git push`.
5. `test_honest_streams_read_the_same_on_the_real_interfaces`:
   - pi (`agent_pi`): `announce("c1")`, then
     `finish("c1", rules.REFUSED + ", by this repo's rule: x")` gives one
     `rule_refused` with `refusals == 1`. `announce("c2")` then
     `ran("c2", "Everything up-to-date")` raises. In a fresh forwarder, three
     refused calls stop with `refusals == rules.MAX_REFUSALS`.
   - Claude Code (`agent_cc`): `claude_call("t1")`, a `permission_denied` for
     `"t1"`, then `claude_result("t1", "Permission denied")` is counted.
     `claude_call("t2")` then `claude_result("t2", "Exit code 126\n" + rules.REFUSED)`
     is counted. `claude_call("t3")` then
     `claude_result("t3", "Everything up-to-date")` raises.
6. `test_each_interface_names_the_harness_it_runs`: `agent_pi.HARNESS == "pi"`
   and `agent_cc.HARNESS == "claude_code"`. For each value `v` of
   `typing.get_args(AgentConfig.model_fields["coding_agent"].annotation)`,
   `agents._interface(SimpleNamespace(coding_agent=v)).HARNESS == v`. Import
   `AgentConfig` from `adw_modules.data_types`.
7. `test_calls_and_results_read_one_harness_when_asked`: unit checks on
   `rules.calls` and `rules.results` with `harness="pi"`, `"claude_code"` and
   `None`. Include a `message_end` carrying a `tool_use` block (read only with
   `None`), an `assistant` carrying a `toolCall` block (read only with
   `None`), `tool_execution_start` (not read with `"claude_code"`), and each
   result shape (read only by its own harness and by `None`).

A guard test that needs a config builds it from the shipped
`.claude/skills/sssf/templates/sssf.config.yaml`, as
`test_secret_files.shipped_config` does. The tests above need no config.

### 6. `docs/open.md`: the forgery row (the `build` row dated 2026-11-07, starting "An agent's command can write to its harness's stdout")

Shrink the first cell to what remains. Keep the other cells (`build`,
`2026-11-07`, the config.md link) as they are. Replacement text for the
first cell:

> An agent's command can write to its harness's stdout (`/proc/$PPID/fd/1`), which the stream check reads. The check reads only the running harness's own event types, so the other harness's types count for nothing, but it can't tell the harness's own types from a forgery. Guard run 6cec4b80's review forged two on pi: a never-ran `tool_execution_end` for the agent's own call, with the id read from `raw_output.jsonl`, so a slipped command counts as refused; and a usage-limit error turn followed by `kill -9 $PPID`, which ends the run as `rate_limit_stop` and tells the engineer to resume. The fix: a VM, or agents under their own user

Do not touch the next row (the Claude Code prefix-refusal `watch` row).

### Docs left unchanged

- `.claude/skills/sssf/references/config.md`: checked. Line 427 states the
  general limit (a forged result can make a command that ran look refused; a
  forged usage-limit error) without naming the cross-harness case, and it is
  still true. Lines 415 and 437 describe Claude Code's `permission_denied` on
  a Claude Code agent, which is still true. No change.
- No CHANGELOG exists in the repo, and README has no usage this touches.
- `app_docs/` doesn't exist and is not edited by this plan. No earlier
  write-up describes the old behavior. The documenter may note that the
  forwarder now reads only its harness's event types.

## Verification

The hub's quality command (`adws/adw_modules/quality.py` `test`) already runs
the whole skill suite. `chain_test` runs `adws/tests`. The extra check runs
the seven new tests by node id with the same runner and flags, so it fails
until they exist and pass:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider tests/test_rules.py::test_on_pi_a_forged_permission_denied_does_not_refuse_a_call_that_ran tests/test_rules.py::test_on_pi_a_forged_claude_result_with_the_refusal_does_not_refuse_a_call_that_ran tests/test_rules.py::test_on_claude_code_a_forged_pi_never_ran_result_does_not_refuse tests/test_rules.py::test_a_forged_announcement_of_the_other_harness_counts_for_nothing tests/test_rules.py::test_honest_streams_read_the_same_on_the_real_interfaces tests/test_rules.py::test_each_interface_names_the_harness_it_runs tests/test_rules.py::test_calls_and_results_read_one_harness_when_asked
```

1. The command above exits 0. Before the build it exits 4 (node ids not
   found). It starts in the hub: no new dependency is needed.
2. The full skill suite passes:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.
   No existing test is edited: the only change under
   `.claude/skills/sssf/tests/` is the appended block in `test_rules.py`.
3. Against the baseline, tests 1 to 3 fail on `f594489`'s `agents.py`,
   because the forged event counts the call as refused and nothing raises
   `RuleBreach`. They pass after the change. The builder may confirm this by
   temporarily reverting the `agents.py` hunk, then restoring it. Commit
   nothing.

## Out of scope

Old holes this change leaves open, to be filed as old holes:

- On pi, a forged never-ran `tool_execution_end` (or `message_end`
  toolResult) without `durationMs`, carrying the refusal text for the
  agent's own call id, still counts a slipped command as refused.
- A forged usage-limit error turn followed by `kill -9 $PPID` still ends a
  run as `rate_limit_stop`.
- On Claude Code, the agent can still forge Claude Code's own types
  (`permission_denied`, or a `user` `tool_result` with the refusal text) on a
  Claude Code stream. The prefix refusal read by text is its own watch row.
- Anything outside the forwarder's choice of which event types to read. The
  real fix for the holes above is a VM (`--sandbox`) or a separate user.

## For the engineer

- The installed copy under `adws/adw_modules/` (protected) is updated from the
  skill by the usual SSSF update step after this merges. The builder does not
  touch it.

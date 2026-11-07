# Skip an event whose type isn't a string

## Summary

Build 2f1fa40 (guard run ea6e82a9) added an event-type filter to
`agents._event_forwarder` in
`.claude/skills/sssf/templates/adws/adw_modules/agents.py`:

```python
if harness is None or event.get("type") in RULE_EVENT_TYPES[harness]:
    check(event)
```

`RULE_EVENT_TYPES[harness]` is a `frozenset`, so membership hashes the value.
An event whose `type` is a list or dict (`{"type": ["user"]}`), which an agent's
command can write to its harness's stdout (`/proc/$PPID/fd/1`), raises
`TypeError: unhashable type` out of `forward`, and the run ends on a bare
exception. The ea6e82a9 spec promised that an event of any other type skips the
rule reads and still reaches the call tracker (guard report, broken promise 1).

The fix: a `type` that isn't a `str` is none of the harness's types. The rule
reads skip it and it goes on to the call tracker like any other skipped event.
That's a one-line change plus tests.

I checked that both real trackers (`agent_pi.ToolCallTracker.observe` and
`agent_cc.ToolCallTracker.observe`) compare `type` with `==`/`!=` only. They
return `[]` for a list, dict, number, `None` and `True` type and raise nothing,
so the tracker needs no change.

## Threat model

- The agent runs as the engineer's own user. Any command it runs can write
  arbitrary lines to its harness's stdout with `printf '<json>' > /proc/$PPID/fd/1`,
  and SSSF's stream check reads that stdout. It can also kill its own harness
  (`kill -9 $PPID`), which ends the run no matter what this guard does.
- What this guard does at its level: it reads only the running harness's own
  event types for rule checks. It can't tell a real event of the harness's own
  type from a forged one. That's an old hole (see `## Out of scope`) and this
  change leaves it as it is.
- The hole closed here is narrow. A forged event with an unhashable `type`
  crashes the forwarder with `TypeError` when the spec says it should be skipped.
  Nothing slips past a rule through it, because the crash kills the agent (and a
  pending refuse call still raises `RuleBreach` via `forward.ended`). But the run
  ends on a bare `TypeError` instead of going on, which breaks the promise the
  ea6e82a9 spec made.

## Baseline

`main` at `f594489` ("Update SSSF to skill 1a38e08"). Every finding is judged
against it. At `f594489` the forwarder has no type filter: every event goes
through `check`, and `{"type": ["user"]}` passes without raising. The filter and
its `TypeError` come from 2f1fa40 on this branch, which sits above the baseline.

## Bar

The change must:

- Treat an event whose `type` is not a `str` (list, dict, number, bool, `None`,
  missing) as not one of the harness's types, on both `pi` and `claude_code`.
  `check` doesn't run for it (none of `rules.denied`, `rules.blocked`,
  `rules.calls`, `rules.results` is called), and `forward` raises nothing for it.
- Still pass such an event to `tracker.observe`, exactly as for any other
  skipped event.
- Leave every other behaviour of the forwarder unchanged. An event whose `type`
  is a string in `RULE_EVENT_TYPES[harness]` is checked exactly as before. A
  string type outside the set is skipped exactly as before. An interface with no
  `HARNESS` (a test's stand-in) still reads every event, whatever its type.

It must never, compared with the baseline:

- keep an agent file, run agent code, or delete or overwrite an engineer file.
  The change only adds an `isinstance` test to an in-memory filter, so it does
  none of these.
- let a command that breaks a forbid or refuse rule through on an event the
  baseline would have caught. The only events whose handling changes are ones
  with a non-`str` type on a real harness. At 2f1fa40 those raised; at the
  baseline they went through `check`. None of the `rules.*` readers acts on an
  event whose `type` isn't the harness's own string type, so skipping them
  catches nothing less than the baseline did.
- change `RULE_EVENT_TYPES`, the trackers, or anything in `rules.py`.

## Design

In `forward` inside `_event_forwarder`, read the type once and test it's a
string before the membership test:

```python
def forward(event: dict) -> None:
    etype = event.get("type")
    if harness is None or (isinstance(etype, str) and etype in RULE_EVENT_TYPES[harness]):
        check(event)
    ...
```

Nothing else in the function changes.

## Steps

1. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`
   - In `_event_forwarder.forward`, replace
     `if harness is None or event.get("type") in RULE_EVENT_TYPES[harness]:`
     with the two lines in `## Design`.
   - In the `_event_forwarder` docstring's paragraph that begins
     "The rule reads cover only the event types of the harness ...", add after
     "Every event still reaches the tracker." the sentence:
     "A `type` that isn't a string is none of the harness's types."
     Leave the rest of the docstring as it is.

2. `.claude/skills/sssf/tests/test_rules.py`: add one parametrized test after
   `test_a_forged_announcement_of_the_other_harness_counts_for_nothing`. It
   reuses the existing helpers `real_forwarder`, `announce`, `claude_call` and
   `PUSH`, plus `pytest`, `SimpleNamespace`, `agents`, `agent_pi`, `agent_cc`
   and `rules`, which the file already imports:

   ```python
   @pytest.mark.parametrize("interface", [agent_pi, agent_cc], ids=["pi", "claude_code"])
   @pytest.mark.parametrize("etype", [["user"], {"type": "user"}, 5], ids=["list", "dict", "number"])
   def test_an_event_whose_type_is_not_a_string_skips_the_rule_reads(interface, etype, monkeypatch):
       event = {**claude_call("t1"), **announce("c1"), "type": etype}
       forward, _ = real_forwarder(interface, PUSH)
       forward(event)                       # the real tracker, and nothing raises
       assert forward.ended() is None
       read, seen = [], []
       monkeypatch.setattr(rules, "denied", lambda e: read.append(e) or [])
       spy = SimpleNamespace(HARNESS=interface.HARNESS,
                             ToolCallTracker=lambda: SimpleNamespace(observe=lambda e: seen.append(e) or []))
       forward, _ = real_forwarder(spy, PUSH)
       forward(event)
       assert read == [] and seen == [event]
       own = {"type": sorted(agents.RULE_EVENT_TYPES[interface.HARNESS])[0]}
       forward(own)
       assert read == [own]                 # the spy does see a read of the harness's own type
   ```

   `check` calls `rules.denied(event)` first, so an empty `read` shows the rule
   reads were skipped. The last two lines check the spy works, so the test can't
   pass for the wrong reason. Without the fix, the list and dict cases raise
   `TypeError` and the number case already passes.

   Don't edit any existing test.

3. Docs. There's no CHANGELOG in this repo or in the skill. These stay
   accurate, so don't touch them:
   - `.claude/skills/sssf/references/config.md:427` ("SSSF reads only the
     running harness's own event types, so a forgery has to use those")
   - `docs/open.md` row 28
   - The comment above `RULE_EVENT_TYPES` in `agents.py`

   No README usage or help text describes the filter. Nothing in `app_docs/`
   describes the `TypeError`, so the documenter has nothing old to flag.

## Verification

Run from the repo root:

```
env -u SSSF_REFUSE_RULES -u CLAUDE_CODE_SHELL_PREFIX -u SSSF_AGENT -u SSSF_ADW_ID uv run --no-project --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q .claude/skills/sssf/tests/test_rules.py .claude/skills/sssf/tests/test_agent_cc.py
```

- It must exit 0. I ran it on the unchanged tree: 99 passed, exit 0.
- `env -u` is there because an agent that SSSF runs inherits
  `SSSF_REFUSE_RULES` and `CLAUDE_CODE_SHELL_PREFIX`. Three existing tests read
  those variables and fail when they're set:
  - `test_rules.py::test_pi_always_loads_the_refusing_extension_and_passes_rules_only_when_set`
  - `test_agent_cc.py::test_no_refuse_rules_leaves_the_shell_alone`
  - `test_sandbox_remote.py::test_an_agent_gets_sssfs_shell_prefix_only_with_refuse_rules[e2b]`

  They fail on the unchanged tree too, so they aren't caused by this change.
  Don't touch them.
- The new test runs six cases (pi and claude_code, each with list, dict and
  number) and all six pass.
- Every existing test passes unchanged. `git diff` on
  `.claude/skills/sssf/tests/test_rules.py` shows only additions.
- `git diff .claude/skills/sssf/templates/adws/adw_modules/agents.py` shows
  only the `forward` condition and the one docstring sentence.

## Out of scope

These holes stay open after this change, so a reviewer should file each as an
old hole:

- Forging the running harness's own event types. On pi, a forged never-ran
  `tool_execution_end` for the agent's own call id counts a call that ran as
  refused. On Claude Code, a forged `system`/`permission_denied`, or a `user`
  `tool_result` with the refusal text, does the same. Also, a forged usage-limit
  error turn plus `kill -9 $PPID` ends a run as `rate_limit_stop`. These are
  covered in `config.md:427` and `docs/open.md` row 28. The fix for them is a VM
  or a separate user, not this change.
- The agent ending its own run with `kill -9 $PPID` or by writing a malformed
  line to the stream. A run that ends that way is not a bypass.
- A non-`str` `type` on a test stand-in with no `HARNESS`. That path reads every
  event as before, and only tests use it.
- Odd shapes elsewhere in an event of the harness's own type, such as a
  `message` that isn't a dict. How `check` and the `rules.*` readers handle them
  stays as it is at the baseline.
- Tests that fail when SSSF's own agent environment variables leak into them
  (the three listed under `## Verification`).
- Everything else about the forwarder and the filter.

## For the engineer

- After this merges, the usual SSSF update step refreshes the installed copy
  under `adws/adw_modules/agents.py` (protected) from the skill. The builder
  doesn't touch it.
- Three skill tests fail when run inside an SSSF agent session, because they
  inherit `SSSF_REFUSE_RULES` / `CLAUDE_CODE_SHELL_PREFIX` (listed under
  `## Verification`). Hardening them with `monkeypatch.delenv` is a separate
  task.

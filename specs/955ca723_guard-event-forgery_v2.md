# Keep an agent from forging agentsh's notes, and tighten the drop-line parser

adw_id `955ca723`, plan v2. Run 0463a6c3's bypass review, `adws/adw_data/sessions/0463a6c3/context_handoff/bypass_review_1.md`, found a broken promise and three holes `config.md` doesn't list. This plan fixes the broken promise and closes or documents the three holes.

**Changes since v1**, from `spec_review_1.md`:

- The threat model now limits the "can't call `on_event`" claim to the agent's output. It also says this guard can't stop an agent that attaches to the runner process, and Out of scope lists that as an old hole.
- The `config.md` sentence now says the line is dropped before SSSF's checks and console see it, and that the raw log still keeps it.
- Out of scope cites `config.md` line 449.
- Test 2's agentsh-mode asserts no longer assume the guard gives exactly one note.

## Summary

There are three small changes to the guard code in `.claude/skills/sssf/templates/adws/adw_modules/`, plus docs and tests:

1. **The read loops drop forged events.** Both harness read loops drop any stream event whose `type` starts with `agentsh_` before calling `on_event`. A new predicate decides this: `agentsh_guard.guard_event(event)`. The guard's own `agentsh_note` and `agentsh_denials` events don't pass through the read loops. They still reach `agents._event_forwarder` and are handled as now.
2. **`_DROP_TYPE` gets a lookbehind**, so `type` matches only as a whole key. It's in `agentsh_guard.py`.
3. **`config.md` lists the two journal limits.** In "Running local agents under agentsh", it also says the drop counts and notes are advice, and that only the guard sends `agentsh_` events.

## Baseline

The baseline is `main` at **`89bbbc6`** ("Record run 0463a6c3 and write run E's spec"). It contains run 0463a6c3, merged as `db8d6eb`. Every finding is judged against `89bbbc6`.

At the baseline:

- **`agent_cc.run` and `agent_pi.run`** call `on_event(event)` for every JSON object line in the harness's stdout. The loops are in `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`, around lines 368–440, and `agent_pi.py`, around lines 710–760.
- **`agentsh_guard.start`** calls `on_event({"type": "agentsh_note", "note": …})` for each `check_machine` note, directly from the runner process (line ~1058).
- **`agentsh_guard.close`** calls `on_event({"type": "agentsh_denials", …})` the same way (line ~1183).
- **`agents._event_forwarder.forward`** (`agents.py` ~581) acts on these two types whatever sent them:
  - For any `agentsh_note`, it prints `agentsh: <note>` through `run.console.advise`.
  - For any `agentsh_denials`, it traces a `log` / `agentsh_denials` event.

  In both cases it then returns before the rule check and the tracker.
- **`_DROP_TYPE`** is `re.compile(r'"?(?:event_)?type"?\s*[=:]\s*"?([^"\s,}]+)')` (`agentsh_guard.py` ~1111).

## Threat model

The agent runs as the engineer's own user. It can run any command its harness's tools allow. These abilities matter here:

- **It can write lines to the harness's stdout.** One way is `printf '{"type":"agentsh_note","note":"…"}\n' > /proc/$PPID/fd/1` from Claude Code's Bash tool. The runner can't tell such a line from one the harness wrote.
  - Under `local_guard: agentsh`, the policy denies writes to `/proc`, so that spelling is likely blocked there. Other spellings may not be.
  - Under `local_guard: text`, nothing blocks it.

  This guard treats every stream line as agent-controlled.
- **It can get text into the `agentsh` unit's journal.** A command's arguments, a path, or a name agentsh logs can carry text the agent chose. The journal read can't tell an agentsh drop line from one built to look like it. This change doesn't stop that. It documents it.
- **Its output reaches `on_event` only through the read loop.** The only path is the loop's `json.loads(line)` → `on_event(event)`. `on_event` is a Python callable inside the runner process. The guard's events are dicts the runner builds in `start()` and `close()` and passes straight to that callable. They are never written to the child's stdout. So once the loop filters, nothing the agent writes to its output can reach the forwarder as an `agentsh_` event.
- **It may be able to reach into the runner process itself.** The runner also runs as the engineer's user. With `kernel.yama.ptrace_scope` at 0, a same-user agent can attach to it with ptrace, or write to `/proc/<runner pid>/mem`. It could then call `on_event`, or change any state the runner holds.
  - A guard inside that process can't stop this.
  - Under `local_guard: agentsh`, the policy's `/proc` write denial and its ptrace handling may block some spellings. This plan claims nothing about that.

  This is an old hole, listed in Out of scope.

What else this guard can't stop at its level:

- an agent writing to the engineer's console or terminal by other means, such as the terminal device itself if it can reach it;
- an agent faking journal lines;
- anything the raw output log (`raw_output.jsonl`) records. The raw log still keeps every line, as at the baseline.

## Where the filter goes, and why not a separate callback

**The filter goes in both read loops, just before the `on_event` call:**

- In `agent_cc.py`, the `if on_event:` at ~line 430 becomes `if on_event and not agentsh_guard.guard_event(event):`.
- In `agent_pi.py`, the `if on_event:` at ~line 741 becomes the same.

**Why the stream can't get past it:**

- **It covers every line.** This is the only path from the child's stdout to `on_event`, and every line the loop parses passes the check before `on_event` sees it.
- **It checks the decoded dict**, the same object `forward` would read. So JSON escapes such as `"agentsh_note"` can't slip a different `type` past it, and neither can duplicate keys (`json.loads` keeps the last one).
- **Near-miss spellings were never a problem.** The forwarder compares `etype == "agentsh_note"` exactly. A case or whitespace variant, such as `"AGENTSH_note"` or `" agentsh_note"`, never reached those branches and still doesn't. A non-string `type` never matched, and still passes through as before.
- **The guard's own events are untouched.** `start()` and `close()` call `on_event` directly and never go through the loop.
- **The rest of the loop is unchanged.** That covers what runs before the `on_event` call:
  - the raw-log write;
  - `dog.touch()`;
  - the `system`/`assistant`/`result`/`rate_limit_event`/`agent_end`/`message_end` handling. None of those branches matches a type starting `agentsh_`.

**A separate callback is larger.** Splitting the guard's events onto, say, `on_guard` would change all of these:

- the signatures of `agentsh_guard.start` and `agentsh_guard.close`;
- `agent_cc.run` and `agent_pi.run`, including their four `close()` call sites;
- `agents.run_agent`'s call into `interface.run`;
- the forwarder, split in two;
- every test stand-in that passes `on_event`.

The filter is two changed lines and a five-line predicate, and the forwarder needs no change.

## Bar

These hold against `89bbbc6` in every mode (`local_guard: text` and `local_guard: agentsh`), for both harnesses (`agent_cc`, `agent_pi`).

**Must:**

1. **A forged line has no effect.** Take a line in a harness's stream whose decoded `type` is a string starting with `agentsh_`. It prints nothing on the console and adds no trace event. It doesn't reach `on_event` at all. The raw output log still records the line, as at the baseline.
2. **The guard's own events still work.**
   - Its notes still print, as `agentsh: <note>`, before the agent starts.
   - Its end-of-call `agentsh_denials` event still reaches the trace, with the same payload keys: `agent`, `denials`, `notes`, `polls`, `pages` and `slowest_poll_seconds`.
3. **Every other stream event behaves as at the baseline.** It reaches `on_event`, and through it the stream check and the tracker. That includes events whose `type` isn't a string, or is missing.
4. **The parser finds the real type.** Under `local_guard: agentsh`, `journal_drops` counts `type=ptrace_execve` in a line that also carries `subtype=ptrace_file` or `event_subtype=…` before it. Lines that matched at the baseline still parse to the same type: `type=`, `"type":`, `type:` and `event_type=`.
5. **Every refusal and stop is unchanged:**
   - rule breaches;
   - refuse-rule counting;
   - the three-denial stop;
   - `AgentshRefused`;
   - budget, rate-limit and silence stops.

**Must never**, where the baseline does otherwise:

- keep an agent file;
- run agent code;
- delete or overwrite an engineer file.

None of the three changes reads, writes or runs anything new.

**Any change to the guard beyond the three in the Summary is a finding.** So is a `config.md` line that promises more than the tests show.

## Out of scope

These holes stay open. A reviewer files them as old holes.

- **Already listed in `config.md`:**
  - A dropped `ptrace_execve` denial isn't counted toward `MAX_REFUSALS`.
  - journald's rate limit can hide drop lines, so the count is a floor.
  - Drops aren't tied to a session, so concurrent runs share them.
- **The journal window and journal content:**
  - Widening the journal window, or reading the journal later. This change only documents the window.
  - Agent-chosen text faking a drop line in the journal. This change documents it and doesn't fix it.
- **Other stream types.** Text in the stream whose `type` doesn't start with `agentsh_` is still read as at the baseline. For example, a forged `assistant` event is still read by the stream check. `config.md` line 449 already lists forged events of the harness's own types: "adds a forged event to the stream … a forgery has to use those".
- **Agent text in the raw output log.**
- **An agent that attaches to the runner process.** With ptrace at `ptrace_scope` 0, or by writing `/proc/<runner pid>/mem`, it can call `on_event` or change the runner's state directly. A guard running as the engineer's user in that same process can't stop it. A VM sandbox is the boundary for this.
- **The agentsh fork's code, and pi's policy.**
- **The hub's installed copy** under `adws/adw_modules/`, which is protected (see "For the engineer").

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

1. **Add a prefix constant and a predicate** near `start()`:

   ```python
   GUARD_EVENT_PREFIX = "agentsh_"


   def guard_event(event) -> bool:
       """True for an event whose type only this guard may send (agentsh_note, agentsh_denials, and any
       later agentsh_ type). The harness read loops drop such an event from the agent's stream before
       on_event, because a command can write lines to the harness's stdout; start() and close() call
       on_event directly. Never raises."""
       return isinstance(event, dict) and isinstance(event.get("type"), str) \
           and event["type"].startswith(GUARD_EVENT_PREFIX)
   ```

   This is part of change 1, not an extra guard change.

2. **Change `_DROP_TYPE`** to:

   ```python
   _DROP_TYPE = re.compile(r'(?<![A-Za-z0-9_])"?(?:event_)?type"?\s*[=:]\s*"?([^"\s,}]+)')
   ```

   The planner checked it against these lines:

   | Line | Result |
   |---|---|
   | `a subtype=ptrace_file type=ptrace_execve` | `ptrace_execve` |
   | `event_subtype=x type=ptrace_execve` | `ptrace_execve` |
   | `{"subtype":"x","type":"ptrace_execve"}` | `ptrace_execve` |
   | `event_type="ptrace_file"` | `ptrace_file` |
   | `id: e3 type: ptrace_file` | `ptrace_file` |
   | `x subtype=only` | no match |

   Add a one-line comment above it: the lookbehind keeps `subtype=` and `event_subtype=` from being read as the type.

3. **Add one sentence to the `journal_drops` docstring.** It says two things:
   - The window is in whole seconds and ends when the read runs, so a line journald delivers late isn't counted.
   - A line under the unit can carry agent-chosen text, so the counts are advice.

   Nothing else in `journal_drops`, `close` or `start` changes.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py`

- Around line 430, change `if on_event:` to `if on_event and not agentsh_guard.guard_event(event):`. `agentsh_guard` is already imported.
- Add a one-line comment: `# agentsh_ events come only from the guard (start/close); a stream copy is a command's forgery.`
- Leave the rest of the loop alone. The line is still written to the raw log above.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

- Around line 741, make the same change and add the same comment. `agentsh_guard` is already imported.
- The nested `agentsh_guard.close(process, on_event)` calls stay as they are.

### `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

This file gets comment changes only, no code change:

- **The `agentsh_note` comment in `forward`** becomes: "A store note from check_machine, sent by agentsh_guard.start before the agent starts; the read loops drop any agentsh_ event from the agent's stream (agentsh_guard.guard_event). Never a refusal."
- **The `agentsh_denials` comment** gets the same pointer.
- **The `RULE_EVENT_TYPES` comment** gets this addition: "…; one whose type starts `agentsh_` doesn't reach the forwarder at all."

### `.claude/skills/sssf/references/config.md`, section "Running local agents under agentsh, `local_guard`"

1. **The store-notes paragraph** begins "The run also reads `/var/lib/sssf-agentsh-prune/status.json`". Append one sentence: "Only SSSF's guard sends these notes and the end-of-call event: a line in the agent's own output whose `type` starts `agentsh_` is dropped before SSSF's checks and console see it, in every mode. The raw output log still keeps it."
2. **Add two bullets after "When the read fails"**, before the paragraph that starts `**The event store.**`:
   - `- **The journal window can miss lines.** The read covers whole seconds, from the second the wrap started to the second after the call ends, and runs once, as the call closes. A line journald delivers late, or one agentsh logs after the read, isn't counted.`
   - ``- **A drop line can be faked.** The read counts every line under the `agentsh` unit, and such a line can carry text the agent chose, such as a command's arguments. A line with "event channel full" and a `type=` value adds a count, and its type, any lowercase name of up to 64 letters, digits or underscores, is printed in the note. A faked `type=ptrace_execve` adds the dropped-exec note too.``
3. **After those two bullets**, add one plain line: "The drop counts and their notes are advice. They never refuse or stop a call."

Change no other line in `config.md`.

### `.claude/skills/sssf/tests/test_agentsh_guard.py`

Put the new tests in the "warnings, poll figures and dropped events" section.

**Fixtures and config:**

- Use the existing fixtures and helpers: `machine`, `service`, `fake`, `request`, `_fake_pi`, `_turn`, `_write_status`, `utc`, `REAL_JOURNAL_DROPS` and `_journal`.
- The config comes from the shipped `.claude/skills/sssf/templates/sssf.config.yaml`, through the existing `cfg`/`machine` fixtures. Never build a test-only one.
- The autouse `no_real_journal` fixture already keeps the real `journalctl` out, and `service` keeps the real service out.

**A forwarder helper.** Add a local helper that builds a real `agents._event_forwarder` for a real harness module, so `HARNESS` and `RULE_EVENT_TYPES` apply. Build it the way `test_rules.forwarder` does, with:

- `run = SimpleNamespace(adw_id="r1", tracer=SimpleNamespace(event=events.append), console=SimpleNamespace(advise=said.append))`;
- `interface = agent_cc` or `agent_pi`;
- `enforced = [PUSH]`, the forbid rule from `test_rules`. Import `PUSH` alongside the existing `test_rules` imports.

**A fixture, `forged_run`**, parametrized over harness (`cc`, `pi`) × mode (`text`, `agentsh`). Model it on `harness_run`.

- **The `cc` stream** is a JSONL file in `tmp_path`. It holds the lines of `fixtures/cc_turn1.jsonl`, with these two inserted after the first line:
  - `{"type":"agentsh_note","note":"FORGED"}`
  - `{"type":"agentsh_denials","denials":[{"type":"file","path":"/forged","rule":"x","count":1}],"notes":["FORGED"],"polls":9,"pages":9,"slowest_poll_seconds":9.9}`
- **The `pi` stream:** call `_fake_pi(tmp_path, monkeypatch, _turn("stop", "hi"))`, then rewrite `tmp_path / "events.jsonl"` so it holds the two forged lines followed by the original `message_end` line.
- **An optional rule event.** The fixture takes a flag that appends one more event:
  - for `cc`, an `assistant` event with a Bash `tool_use` of `git push`, as in `test_rules.test_a_forbidden_command_kills_a_claude_agent_and_what_it_started`;
  - for `pi`, `announce("c1")`.
- **Mode `text`** uses the default `LocalGuard(mode="text")` request.
- **Mode `agentsh`:**
  - Set `cwd=str(machine.root)` and `local_guard=machine.guard`, as `harness_run` does. For `cc`, patch `sandbox.current` to return `sandbox.LOCAL` if `harness_run` needs it.
  - Call `_write_status(machine, last_prune=utc(time.time() - 4 * 3600))`, so the guard has a real note to send.

**Tests:**

1. **`test_guard_event_names_only_agentsh_types`**
   - True for `{"type": "agentsh_note"}`, `{"type": "agentsh_denials"}` and `{"type": "agentsh_other"}`.
   - False for `{"type": "assistant"}`, `{"type": "message_end"}`, `{"type": 5}`, `{"type": None}`, `{}`, `{"type": "AGENTSH_note"}`, `{"type": " agentsh_note"}`, `[1]` and `"agentsh_note"`.
   - It never raises.
2. **`test_a_forged_note_and_denials_in_the_stream_give_no_advice_and_no_trace(forged_run)`**, with no rule event.
   - **Every mode:**
     - The call succeeds.
     - No entry in `said` contains `FORGED`.
     - No trace event named `agentsh_denials` has `FORGED` in its `notes` or `/forged` in its `denials`.
     - The raw output log, `request.raw_output_path`, still contains the forged lines.
   - **In `text` mode:** `said == []`, and no trace event is named `agentsh_denials`.
   - **In `agentsh` mode:**
     - Every entry in `said` starts with `agentsh: `, and at least one contains `last pruned`. `check_machine` may give other notes, so don't assert the number of entries.
     - Exactly one trace event is named `agentsh_denials`. Its payload has the keys `agent`, `denials`, `notes`, `polls`, `pages` and `slowest_poll_seconds`. Its `notes` contain `last pruned`, and `polls != 9`.
3. **`test_a_rule_event_beside_forged_lines_is_still_checked(forged_run)`**, with the rule event. The call raises `rules.RuleBreach` (use `pytest.raises`), and no entry in `said` contains `FORGED`.
4. **`test_the_guards_own_note_prints_before_the_agent_starts`**, in `agentsh` mode only, for both harnesses.
   - Record order with a `console.advise` that appends `("advise", text)`, and an `on_spawn` that appends `("spawn", pid)`.
   - Pass `on_spawn` by calling `module.run(req, on_event=forward, on_spawn=…)` directly.
   - The `agentsh: …last pruned…` advice must come before the spawn entry.
5. **`test_drop_type_reads_type_only_as_a_whole_key`**, using `REAL_JOURNAL_DROPS` with `_journal(out)`. Each line of `out` contains `event channel full`:

   | Line | Counted as |
   |---|---|
   | `… subtype=ptrace_file type=ptrace_execve` | `ptrace_execve` |
   | `… event_subtype=ptrace_file type=ptrace_execve` | `ptrace_execve` |
   | `{"msg":"event channel full","subtype":"ptrace_file","type":"ptrace_execve"}` | `ptrace_execve` |
   | `… type=ptrace_file` | `ptrace_file` |
   | `… event_type=ptrace_file` | `ptrace_file` |
   | `… subtype=ptrace_file`, with no other type key | `unknown` |

   Assert `counts == {"ptrace_execve": 3, "ptrace_file": 2, "unknown": 1}` and `problem is None`. Also assert `agentsh_guard._DROP_TYPE.search("x subtype=ptrace_file") is None`.
6. **`test_a_subtype_before_the_type_still_gives_the_execve_note(machine, service, monkeypatch)`**
   - Patch `agentsh_guard.journal_drops` with `lambda s, u, run=None: REAL_JOURNAL_DROPS(s, u, run=_journal("event channel full subtype=ptrace_file type=ptrace_execve"))`.
   - Launch and close as in `test_close_notes_at_least_the_drops_and_the_execve_drop`.
   - A note contains `ptrace_execve` and "denied exec may have gone uncounted".

Leave the existing tests unchanged. They must still pass, notably these:

- `test_journal_drops_counts_by_type`;
- `test_the_forwarder_prints_and_traces_an_agentsh_note`, which calls `forward` directly, as the guard does;
- `test_store_notes_are_announced_before_the_wrap_starts`;
- `test_the_end_of_call_event_has_poll_counts_pages_and_slowest`.

## Docs this change makes out of date

- **Covered by the steps above:**
  - `.claude/skills/sssf/references/config.md`;
  - the forwarder comments and the `RULE_EVENT_TYPES` comment in `agents.py`;
  - the `journal_drops` docstring.
- **Nothing else applies.** This repo and the skill keep no CHANGELOG. The README's usage doesn't mention these events, and no help text changes.
- **For the documenter, not the builder:** if `app_docs/` holds a write-up of run 0463a6c3, it describes the forwarder as acting on any `agentsh_note`. The documenter should note that stream copies are now dropped. Plan and make no edit under `app_docs/`.

## Verification

1. **The suite passes.** The builder runs the skill's suite from a copy outside the repo, as earlier runs did:
   - Copy `.claude/skills/sssf/` to a temp folder.
   - From the copy, run the repo's test command: `uv run --directory <copy> --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`.

   The full suite must pass, including these files:
   - `tests/test_agentsh_guard.py`, with the six new tests and all four `forged_run` parametrizations;
   - `tests/test_rules.py`;
   - `tests/test_agent_cc.py`;
   - `tests/test_agent_pi.py`.
2. **No real journal or service is used.** In the output of `grep -n "journalctl" .claude/skills/sssf/tests/test_agentsh_guard.py`, every new reference is in a fake `run` or an assert. The new tests use the `service` fixture, never the live service.
3. **The guard diff holds only the three changes.** `git diff 89bbbc6 -- .claude/skills/sssf/templates/adws/adw_modules/` must show only these:
   - In `agentsh_guard.py`: `GUARD_EVENT_PREFIX`, `guard_event`, the `_DROP_TYPE` line and its comment, and the `journal_drops` docstring sentence.
   - In `agent_cc.py` and `agent_pi.py`: the one changed `if` and its comment in each.
   - In `agents.py`: comment lines only.
4. **The `config.md` diff holds only the planned lines**: the sentence, the two bullets and the advice line above, all in the agentsh section.
5. **The hub check after the merge.** This happens in the hub session, not the builder's, once the engineer has updated the hub (see below). Run one small call under `local_guard: agentsh`, the hub's mode, and check two things:
   - Any real `agentsh:` store notes show on the console before the agent starts.
   - The trace's `agentsh_denials` event still has `polls`, `pages` and `slowest_poll_seconds`.

## For the engineer

- **No protected file needs a change for the build.**
- **Update the hub after the merge.** The hub's installed copy under `adws/adw_modules/` is protected, so the builder doesn't touch it. Update the hub to the new skill commit, as `0733cfc` did for `63962fd`, so the hub's own runs get the filter.
- **Check the real drop line's layout.** This is the open check from run 0463a6c3's spec. From the fork's source, confirm whether agentsh's real "event channel full" line carries any key ending in `type` before the event's type, such as `subtype`. The new lookbehind handles that either way. But if the real key is something other than `type` or `event_type`, the parser still counts `unknown`.

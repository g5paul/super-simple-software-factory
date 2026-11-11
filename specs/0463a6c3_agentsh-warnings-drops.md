# Show agentsh's warnings when they happen, and count the dropped events

Next item 16 in `docs/designs/agentsh-local-sandbox.md`. The task is `specs/tasks/guard-agentsh-warnings.md`.

## Summary

Under `local_guard: agentsh` only:

1. The store notes from `check_machine` are printed to the console and traced, one event per note, before the wrap starts.
2. The end-of-call `agentsh_denials` event carries `polls`, `pages` and `slowest_poll_seconds`. It adds a note when the slowest poll request took over 1 s.
3. At the end of each call SSSF reads agentsh's journal for the call's window, counts the "event channel full" lines by event type, and adds notes about them.
4. `agentsh_sssf.yaml` sets `audit.storage.channel_size: 16384` and `audit.storage.batch_size: 256`.
5. `agentsh_prune.py` exits 1 when the current attempt records an error.
6. The docs are updated to match.

None of the new notes refuses or stops a run.

## Baseline

`main` at `4eaac2a`, which holds the event-store run 149e2bda (merged as 4cf7ab3) and its rollout record (06a552a). Every finding is judged against `4eaac2a`.

## Threat model

Agents run as the engineer's own user, inside `agentsh wrap` under a default-deny policy. An agent can:

- flood agentsh with events (file opens, execs) so the store's channel fills and agentsh drops events, including `ptrace_execve` denials that SSSF would otherwise count toward the three-refusal stop. agentsh still enforces every decision. Only SSSF's count is lost.
- slow the poll by producing many events.
- not read the journal: the policy has no allow on `/var/log/journal`. It can't write the journal directly either. It can call `logger` or `systemd-cat` from inside the wrap, if the exec rules allow them. Those lines are tagged with the agent's own identifier, not the `agentsh` unit, so `journalctl -u agentsh` doesn't match them. A line injected under the agentsh unit would require the agentsh service's own stderr. The agent doesn't have that.

At this level the guard can't stop agentsh dropping an event. It can't recover a dropped denial, and it can't attribute a drop to one session, because agentsh's log line carries the event's id and type and no session id. So a concurrent run's drops in the same window are counted too. This change makes drops and slow polls visible. It adds no enforcement.

## Bar

Against the baseline, under `local_guard: agentsh`:

- Nothing that passed at the baseline refuses or stops. Every new note is a note. The journal read, the poll timing and the note emission are wrapped so that no exception escapes `start()`'s note step or `close()`.
- Every refusal and stop stays as it was, including the three-refusal stop (`breach`, `check_stopped`, `_take`'s counting) and the three-failed-poll stop (`_watch`). `_get`'s 5 s timeout, `PAGE` and `POLL_SECONDS` are unchanged.
- The journal read runs as the runner's own user (the engineer's): no `sudo`, no `runuser`. It reads only `-u agentsh` in the call's window, and is bounded by a timeout. Its notes carry event types and counts only, never an event id, path or other line content. An event type that isn't `[a-z0-9_]{1,64}` is counted as `unknown`.
- The rendered config changes only `audit.storage.channel_size` and `audit.storage.batch_size`.
- The prune exits 0 on a skip, on "not due" and on a clean run, and 1 when the current attempt records an error. Its status file's content and fields are unchanged.
- Runs under `local_guard: text` don't change. `start()` returns before any new code for text mode or a non-local tree, and `close()` still does nothing for a non-`AgentshProcess`.
- No agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. The journal read runs a root-owned system binary (`journalctl`, resolved from PATH, as `_run` already does for `systemctl` and `git`) outside the wrap, with no input from the agent.

Expected and not a finding: after the merge, the template differs from the installed `/etc/agentsh/sssf.yaml`. So every agentsh run refuses with "differs" until the engineer reinstalls and restarts. That's the pin working as designed, and the reason the reinstall comes straight after the merge.

## Out of scope

These holes stay open, so a reviewer should file them as old holes:

- **Dropped events still happen.** A writer that blocks instead of dropping, or that never drops a `ptrace_execve`, is a fork patch, and the patches are unpublished. A bigger channel only makes drops rarer.
- **A dropped `ptrace_execve` doesn't refuse or stop the run.** It is a note.
- **Drops aren't attributed to a session.** A concurrent agentsh call's drops in the same window are counted in both calls' notes.
- **The journal window can miss lines.** It is whole seconds from just before the wrap starts to `close()`, and journald can deliver a line late, so a drop logged after `close()` reads the journal goes uncounted.
- **A runner that can't read the journal**, either because the engineer isn't in `adm` or `systemd-journal`, or because the runner itself runs inside a wrap, gets only the "can't read" note.
- **No alerts outside SSSF**, such as email or desktop notifications.
- **Run and phase durations against their usual range.**
- **pi's policy.**
- **The fork's code.**
- The channel's memory figure below is an estimate. Neither the planner nor the builder can read the fork's source from inside their wraps (see "For the engineer").

## Facts this plan relies on

- The installed `/usr/bin/agentsh` has sha256 `603dbf14…`, which is `PINNED_SHA256`, build 05b3777f. Its strings contain `yaml:"channel_size"`, `yaml:"batch_size"` and `yaml:"flush_interval"`, and the log text `event channel full, dropping event`. So both keys exist in the installed build. I checked with `grep -a` on the binary on 2026-11-11.
- Per the task: `defaultChannelSize` is 4,096, batches are 64 every 50 ms, and a full channel drops the event and logs its id and type.
- Under the planner's wrap, `journalctl -u agentsh` exits **0** and prints `No journal files were opened due to insufficient permissions.` on stderr with no stdout. **An unreadable journal does not show up in the exit code.** The reader must look at stderr. Outside a wrap, the engineer's user is in `adm`, which can read the system journal.
- The journal line's exact layout (slog text `key=value`, JSON, or printf) couldn't be read from inside the wrap. The parser accepts all three (see step 3).

## Chosen values: `channel_size: 16384`, `batch_size: 256`

- Run 05f96f0f, on a 136 KB store, lost 34 events in 2 s at Claude Code's start with a 4,096-slot channel. The burst was roughly 4,096 + 2 s of draining + 34 events.
- If the writer drains at 64 per 50 ms tick, that is 1,280 events/s, so the burst was roughly 6,700 events.
- Four times the channel (16,384) and four times the batch (256 per tick, up to 5,120 events/s) absorbs that burst about 3 times over.
- It also raises the drain ceiling for the pre-rollout bursts, which peaked at 3,504 drops a minute.
- Larger batches also mean fewer SQLite commits per event.
- `flush_interval` stays at its default: the task allows only the two keys.

**Memory.** A queued event carries its id, type, timestamp, session and command ids, path or domain, policy decision and rule, and a `fields` map. A `ptrace_execve` also carries its argv. Allow about 2 KB per event on the heap, plus the channel's own slots, which Go allocates up front at `cap × element size`. That is a few hundred bytes each if the element is a struct value, or 8 bytes if it's a pointer.

So a full 16,384-slot channel holds about 32 MB of events, plus up to about 8 MB of slots. An exec with a very long argv carries more, up to the kernel's argument limit. The baseline's 4,096-slot channel holds about a quarter of that. `config.md` states the figure as an estimate.

## Files to change

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

**Constants**, next to `POLL_SECONDS`:

```python
SLOW_POLL_SECONDS = 1.0      # a poll request slower than this gets a note
JOURNAL_SECONDS = 10         # the journal read's timeout
DROP_TEXT = "event channel full"
```

**Store notes at the start.** `start(tree, harness, cmd, *, cwd, env, guard, tally=None, on_event=None)`:

- Add the `on_event: Optional[Callable[[dict], None]] = None` keyword.
- Right after `notes = check_machine(guard)`, and before any other check and before `Popen`, call `on_event({"type": "agentsh_note", "note": note})` once per note, when `on_event` is given. Wrap each call in `try/except Exception: pass`, so a failing callback never refuses.
- `process.trace_notes.extend(notes)` stays, so the end-of-call event keeps the notes as now.
- Record the call window's start: `started_at = time.time()` immediately before `subprocess.Popen`. Store it on the process as `process.started_at` (default `None` in `__init__`).

**Poll figures.** In `AgentshProcess.__init__`, add:

```python
self.started_at: Optional[float] = None
self.polls = 0
self.pages = 0
self.slowest = 0.0
self._stats = threading.Lock()
self._drops_read = False
```

- Add `_timed_get(self, query)`. It times `self._get(query)` with `time.monotonic()`, and in a `finally` it updates `self.slowest = max(self.slowest, elapsed)`, under `self._stats`. It increments `self.pages` only when `_get` returned. A failed or timed-out request counts toward `slowest`, but not toward `pages`.
- `_poll` and `_pages` call `self._timed_get(...)` instead of `self._get(...)`. `_get` itself is unchanged, so the tests that monkeypatch `_get` still work, and their fakes are timed.
- `_watch` increments `self.polls` (under `_stats`) once per `_poll()` attempt, answered or failed.
- `_end_session` increments it once for the final read, inside the `if first` path, before `_pages`.

**Drops, in `close()`.** Add a module function:

```python
def journal_drops(since: float, until: float,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[dict[str, int], Optional[str]]:
    """agentsh's "event channel full" lines between `since` and `until`, counted by event type,
    as the runner's own user. Returns (counts, problem). `problem` is set, and the counts are
    empty, when the journal can't be read. Never raises."""
```

- **Command:** `["journalctl", "-u", "agentsh", "--since", f"@{int(since)}", "--until", f"@{math.ceil(until)}", "-o", "cat", "--no-pager"]`.
  - Run it with `capture_output=True, text=True, errors="replace", timeout=JOURNAL_SECONDS`.
  - Pass `env={**os.environ, "LC_ALL": "C"}` so the messages are in English.
  - Add `import math`.
  - No sudo.
- **Problems**, as the return's second value:
  - `subprocess.TimeoutExpired` → `f"timed out after {JOURNAL_SECONDS} s"`.
  - `OSError` → `f"could not run ({type(error).__name__})"`.
  - A non-zero exit → `f"exited {returncode}"`.
  - A zero exit whose stderr contains `insufficient permissions`, `not seeing messages` or `Permission denied` (case-insensitive) → `"no permission to read the agentsh unit's lines"`.
  - Never include stderr text in the problem.
  - Any other exception → `f"failed ({type(error).__name__})"`.
- **Counting.** For each stdout line containing `DROP_TEXT`:
  - Find the type with `re.search(r'"?(?:event_)?type"?\s*[=:]\s*"?([^"\s,}]+)', line)`. That matches slog text `type=ptrace_file`, `event_type="ptrace_file"`, JSON `"type":"ptrace_file"`, and printf `type: ptrace_file`.
  - Keep the value only if it matches `[a-z0-9_]{1,64}`. Otherwise use `"unknown"`.
  - Nothing else from the line is kept.

In `close()`, after `process._end_session()` and `atexit.unregister(...)`, and only once per process (`if not process._drops_read:`, then set it), and only when `process.started_at` is set:

```python
counts, problem = journal_drops(process.started_at, time.time())
```

Then add notes to `process.trace_notes`:

- **The journal couldn't be read:** `f"agentsh's journal couldn't be read for this call ({problem}), so events agentsh dropped are not counted; check with `journalctl -u agentsh --since <start> | grep -c 'event channel full'`"`. Keep the problem text in parentheses, and make sure the backticks don't break the f-string.
- **Any drop:** `f"agentsh dropped {total} event(s) during this call, when its store's channel was full: {', '.join(f'{t} {n}' for t, n in sorted(counts.items()))}. The call's other notes and counts may be short by these. Drops are counted for the agentsh service as a whole, so a concurrent run's drops show here too"`.
- **`counts.get("ptrace_execve")`:** add a separate note, `f"{n} dropped event(s) were ptrace_execve: a denied exec may have gone uncounted toward the three-refusal stop. agentsh still enforced it"`.

**Poll note**, also in `close()`, before emitting: when `process.slowest > SLOW_POLL_SECONDS`, append `f"the slowest agentsh event poll took {process.slowest:.1f} s; a poll request gives up at {POLL_TIMEOUT} s and three failed polls in a row stop the agent. A slow poll usually means a big event store: see \"The event store\" in .claude/skills/sssf/references/config.md"`. Introduce `POLL_TIMEOUT = 5` and use it in `_get`'s `timeout=`. Same value, one name.

**Event.** `close()` now always emits the `agentsh_denials` event for an `AgentshProcess` (when `on_event` is given), since it carries the poll figures:

```python
{"type": "agentsh_denials", "denials": denials, "notes": process.trace_notes,
 "polls": process.polls, "pages": process.pages, "slowest_poll_seconds": round(process.slowest, 2)}
```

- Wrap the journal read and the notes in their own `try/except Exception`, so a failure there still emits the event.
- The whole of `close()` stays inside its existing `try/except Exception: pass`.

**Docstrings.**

- `store_notes`: "printed at the agent's start and kept in the end-of-call event".
- `check_machine`'s last line.
- `close()`: also counts agentsh's dropped events from the journal and records the poll figures.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

In `_event_forwarder`'s `forward`, before the `agentsh_denials` branch:

```python
if etype == "agentsh_note":
    # A store note from check_machine, shown before the agent starts. Never a refusal.
    run.console.advise(f"agentsh: {event.get('note', '')}")
    return
```

`console.advise` prints the note in full and writes it to the trace as its own `log` event with `level: warning`, under the current phase.

In the `agentsh_denials` branch, add `"polls"`, `"pages"` and `"slowest_poll_seconds"` from the event to the payload, with `event.get(key)`.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` and `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Pass `on_event=on_event` to `agentsh_guard.start(...)`: `agent_cc.py` line 347, `agent_pi.py` line 703. Nothing else changes.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`

Under `audit.storage`, after `sqlite_path`:

```yaml
    # agentsh drops an event when this queue is full (default 4096, batches of 64 every
    # 50 ms); a burst at an agent's start filled it on 2026-11-11. About 2 KB an event,
    # so a full queue holds about 32 MB. See config.md, "The event store".
    channel_size: 16384
    batch_size: 256
```

`render_config` round-trips through `yaml.safe_load`/`safe_dump`, so the comments don't reach the installed file. The values do, as ints. Change nothing else in the file.

### 5. `.claude/skills/sssf/scripts/agentsh_prune.py`

The exit code follows **this attempt's** error, not the status file's `error` field.

**Why not the file's field:** on a skip or a not-due return, the status file carries over the previous error (`"error": old.get("error")`). Judging by the file would exit 1 on a skip, and every 10 minutes until the next clean prune.

- Move `prune`'s body into `_prune(db, api, status, now) -> tuple[dict, bool]`. It returns `(data, errored)`, where `errored` is `True` only when one of the two `except` blocks in this call set `data["error"]`.
- `prune(...)` keeps its signature and returns `_prune(...)[0]`, so the existing tests and the status file are unchanged.
- `main()`: `_, errored = _prune(DB, API, STATUS, None)`, then `return 1 if errored else 0`. Use the same defaults `prune` has.
- A crash, meaning an uncaught exception, still exits non-zero through Python.
- Update the module docstring with the exit codes: 0 on a prune, a skip or not due; 1 when an error is recorded.

### 6. `.claude/skills/sssf/scripts/agentsh_install.py`

No code change: it already diffs the rendered config against `/etc/agentsh/sssf.yaml`, so the two new keys show in the diff. Also look at the docstring. If it describes the prune unit's exit behaviour, update it to say a recorded error now fails the unit. Otherwise leave it.

### 7. `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh"

- **Line ~487** ("The run also reads … adds a trace note"): the notes are printed to the run's console, in yellow and prefixed `agentsh:`, and traced as warnings in the phase's log before the agent starts. They are also kept in the end-of-call `agentsh_denials` event. They never refuse.
- **Line ~491** (polling and drops), add:
  - Each call's `agentsh_denials` event records `polls`, `pages` and `slowest_poll_seconds`, the slowest single poll request, failed ones included. Over 1 s adds a note naming the time and the 5 s limit, a sign the store is growing.
  - At the end of each call SSSF runs `journalctl -u agentsh` for the call's window as your user, with a 10 s timeout. It counts "event channel full" lines by event type and adds a note with the counts. A dropped `ptrace_execve` gets its own note: a denied exec may have gone uncounted toward the three-refusal stop, though agentsh enforced it.
  - Drops are counted for the whole service, so concurrent runs share them.
  - A journal your user can't read (not in `adm` or `systemd-journal`, or a runner itself inside a wrap), a timeout or a failed `journalctl` adds a note saying so. None of these refuse or stop.
- **"The event store" paragraph:** the config sets `audit.storage.channel_size: 16384` and `batch_size: 256`, four times agentsh's defaults (4,096 and 64). A full channel holds an estimated 32 MB of events. A bigger channel makes drops rarer but can't rule them out. Also: the prune service exits 1 when it records an error, so `systemctl --failed` lists `sssf-agentsh-prune.service`. A skip exits 0.

### 8. `docs/guide/troubleshooting.md`

- Replace line 60 ("`check_machine`'s warnings about the store go into the trace only…") with: the store warnings print to the console when each agent starts, and the end-of-call `agentsh_denials` event in the trace adds the slowest poll and the events agentsh dropped during the call.
- Add `systemctl --failed                                                 # a prune that recorded an error` to the check block, at lines 52–56, in the same comment column.

### 9. `docs/open.md`, line 30

Keep the row open. Append one sentence: since run 0463a6c3, each call's trace notes the drops it saw, by type, and the config's channel is 16,384. Don't change its status.

Not to touch: `app_docs/` doesn't exist in this repo, and the documenter owns it in any case. The design doc's Next item 16 is the hub's to close after the rollout.

## Tests

Add these to `.claude/skills/sssf/tests/test_agentsh_guard.py`, using its existing fixtures (`machine`, `service`, `harness`, `harness_run`, `prune_env`, `launch`, `_write_status`). The config always comes from the shipped `templates/sssf.config.yaml`.

The tests never call the real `journalctl`, never touch `/var/lib/agentsh`, and never start the real service. Add an autouse fixture, or patch `agentsh_guard.journal_drops` in `machine`, that returns `({}, None)` by default, so no existing launch test reaches the real journal. Tests of `journal_drops` itself pass a fake `run=`.

1. `test_store_notes_are_announced_before_the_wrap_starts`
   - Stale status via `_write_status`.
   - Patch `subprocess.Popen` in `agentsh_guard` to record the moment it is called, then delegate.
   - Launch with `on_event=events.append`.
   - Assert an `{"type": "agentsh_note"}` with "last pruned" was appended before Popen ran, and that the end-of-call `agentsh_denials` event still holds the note.
2. `test_a_failing_note_callback_does_not_refuse`: an `on_event` that raises still launches.
3. `test_the_forwarder_prints_and_traces_an_agentsh_note`: use `test_rules`' `forwarder` helper or a fake run with a recording console and tracer. Feed an `agentsh_note` and assert `console.advise` got `agentsh: …`, that the note produced a trace log event, and that `check` wasn't run.
4. `test_the_end_of_call_event_has_poll_counts_pages_and_slowest`
   - Launch against `service` with a few events and call `close`.
   - Assert `polls >= 1`, `pages >= 1`, `slowest_poll_seconds` is a float, and the forwarder's `agentsh_denials` payload carries all three.
5. `test_a_poll_over_one_second_adds_a_note`
   - Patch `agentsh_guard.time.monotonic` with a fake clock that a fake `_get` advances by 1.5 s, or set `process.slowest` through `_timed_get` with that fake.
   - Assert a note naming `1.5 s` and `5 s`.
   - Assert that at 0.5 s there is no note.
   - Assert that `breach`/`check_stopped` don't raise.
6. `test_journal_drops_counts_by_type`: a fake `run` returns lines in slog text, JSON and printf forms, mixed with other agentsh lines. Assert `{"ptrace_file": 2, "ptrace_execve": 1, ...}`, and that the command is exactly `journalctl -u agentsh --since @<int> --until @<int> -o cat --no-pager` with a timeout and no `sudo`.
7. `test_a_type_that_is_not_a_plain_name_counts_as_unknown`: a line with `type="/home/x/.env"` or a 200-character value counts as `unknown`. The note doesn't contain the value or the event id.
8. `test_close_notes_the_drops_and_the_execve_drop`: patch `journal_drops` to return `({"ptrace_file": 34, "ptrace_execve": 2}, None)`. Assert one note with both counts, a separate note naming the uncounted denied exec and that agentsh enforced it, and no note containing an id. Assert the run isn't stopped: `check_stopped` doesn't raise, and `failure` is empty.
9. `test_an_unreadable_journal_a_timeout_and_a_failed_journalctl_each_note_it`, parametrised:
   - a zero exit with stderr `No journal files were opened due to insufficient permissions.`;
   - `TimeoutExpired`;
   - exit 1;
   - `FileNotFoundError`.
   Each gives the "couldn't be read" note with the right reason, no stderr text, and no stop.
10. `test_the_journal_is_read_once_per_call`: `close` twice reads once.
11. `test_text_mode_reads_no_journal`: under `local_guard: text`, `start` and `close` never call `journal_drops`. Extend the existing `test_text_mode_reads_no_status_and_makes_no_note`.
12. `test_the_rendered_config_raises_the_store_buffer`: render from the shipped template. Assert `audit.storage.channel_size == 16384` and `batch_size == 256`. Also assert that the rendered config, with those two keys removed, equals the baseline template's other keys: `sqlite_path` still present, and nothing else under `audit` added.
13. Prune exit codes, using `prune_env` and the module's `_prune`/`main` with patched `DB`/`API`/`STATUS`:
    - a clean run → `main()` returns 0;
    - a skip (the fake API lists a session) → 0, and also 0 when the old status holds an `error`;
    - not due → 0;
    - a recorded error (patch `sqlite3.connect` to raise `sqlite3.OperationalError`, or make `fix_owner` raise `OSError`) → 1, and the status file has the same keys as before.

Every existing test must still pass unchanged. That includes `test_a_launch_with_a_stale_status_carries_the_note_into_the_trace`, the three-denial and three-failed-poll tests, and the prune tests.

## Verification

1. The builder runs the skill's suite from a copy outside the repo, as earlier runs did. For example, copy `.claude/skills/sssf/` to a temp folder and run its pytest suite there the way the repo's test command does. `tests/test_agentsh_guard.py`, `tests/test_rules.py`, `tests/test_agent_cc.py`, `tests/test_agent_pi.py` and `tests/test_console.py` must all pass, and so must the full suite.
2. Check with `grep -rn "journalctl" .claude/skills/sssf/tests/` that every `journalctl` reference in the tests is in a fake `run` or an assert, never a real call.
3. Confirm with `python -c` that `render_config` of the shipped template differs from the baseline's only in the two keys. Load both with `yaml.safe_load` and compare.
4. After the merge (hub session, not the builder):
   - The engineer reinstalls both repos' policies with `agentsh_install.py`. The diff must show only `channel_size`/`batch_size` in `sssf.yaml`, plus the prune script.
   - The engineer runs the printed sudo lines and restarts the service.
   - The hub runs one small call in sssf-sandbox. Check that the console shows any `agentsh:` store notes before the agent starts, that the trace's `agentsh_denials` event has `polls`, `pages` and `slowest_poll_seconds`, and that the drop count for the call, from the note or from the engineer's own `journalctl -u agentsh --since … | grep -c 'event channel full'`, is 0 or below run 05f96f0f's 34.
   - If the drop note shows only `unknown` types, the real line layout didn't match the parser. That is a finding to fix.
   - If the runner runs inside the hub's wrap, expect the "couldn't be read" note, and use the engineer's own `journalctl` instead.
5. Run `systemctl --failed` after a forced prune error to confirm the unit is listed, or have the engineer check it.

## For the engineer

- No protected file needs a change.
- Before running the reinstall, confirm from the fork's source (`internal/store/sqlite/sqlite.go`) that the channel's element type and size agree with the "about 2 KB an event, about 32 MB full" estimate. Correct `config.md` and the YAML comment if not. Neither the planner nor the builder can read `~/github/agentsh` from inside its wrap.
- Also confirm there that the "event channel full" log line names the type under a key the parser accepts (`type` or `event_type`).

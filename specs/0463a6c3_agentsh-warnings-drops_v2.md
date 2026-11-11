# Show agentsh's warnings when they happen, and count the dropped events

Next item 16 in `docs/designs/agentsh-local-sandbox.md`. The task is `specs/tasks/guard-agentsh-warnings.md`.

This is version 2. It answers `spec_review_1.md`:

- journald's rate limit is now listed as a hole, and the drop note says "at least".
- The second lock, the read-once flag, the `POLL_TIMEOUT` rename and the `docs/open.md` edit are gone.

## Summary

Under `local_guard: agentsh` only:

1. The store notes from `check_machine` are printed to the console and traced, one event per note, before the wrap starts.
2. The end-of-call `agentsh_denials` event carries `polls`, `pages` and `slowest_poll_seconds`. It adds a note when the slowest poll request took over 1 s.
3. At the end of each call, SSSF reads agentsh's journal for the call's window. It counts the "event channel full" lines by event type, and adds notes that give the counts as a floor ("at least").
4. `agentsh_sssf.yaml` sets `audit.storage.channel_size: 16384` and `audit.storage.batch_size: 256`.
5. `agentsh_prune.py` exits 1 when the current attempt records an error.
6. The docs (`config.md`, `troubleshooting.md`) match.

None of the new notes refuses or stops a run.

## Baseline

`main` at `4eaac2a`, which holds the event-store run 149e2bda (merged as 4cf7ab3) and its rollout record (06a552a). Every finding is judged against `4eaac2a`.

## Threat model

Agents run as the engineer's own user, inside `agentsh wrap` under a default-deny policy. An agent can do the following:

- **Flood agentsh with events** (file opens, execs). The store's channel fills and agentsh drops events, including `ptrace_execve` denials that SSSF would otherwise count toward the three-refusal stop. agentsh still enforces every decision. Only SSSF's count is lost.
- **Fill the journal with agentsh's own lines**, by the same flood. journald's per-service rate limit then suppresses some of them, drop lines included, so the count is a floor.
- **Slow the poll** by producing many events.
- **Put text it chose into agentsh's log lines**, through a path or command it opens or runs. agentsh may echo that text in its own log lines. Such text could imitate an "event channel full" line, or mention a type. That can only mislead a note: it adds to a count, or it is counted as `unknown`. It can't refuse, stop or unblock anything. The parser keeps only a type matching `[a-z0-9_]{1,64}`.
- **Not read the journal.** The policy has no allow on `/var/log/journal`. The agent also can't write lines tagged as the `agentsh` unit. `logger` and `systemd-cat` lines carry the agent's own identifier and unit, so `journalctl -u agentsh` doesn't match them.

At this level the guard can't stop agentsh dropping an event. It can't recover a dropped denial. It can't attribute a drop to one session, because agentsh's line carries the event's id and type and no session id. So a concurrent run's drops in the same window are counted too.

This change makes drops and slow polls visible. It adds no enforcement.

## Bar

Against the baseline, under `local_guard: agentsh`:

- **Nothing that passed at the baseline refuses or stops.** Every new note is a note. The journal read, the poll timing and the note emission are wrapped so that no exception escapes `start()`'s note step or `close()`.
- **Every refusal and stop stays as it was.** That includes the three-refusal stop (`breach`, `check_stopped`, `_take`'s counting) and the three-failed-poll stop (`_watch`). `_get`'s 5 s timeout, `PAGE` and `POLL_SECONDS` are unchanged.
- **The journal read stays narrow.**
  - It runs as the runner's own user (the engineer's): no `sudo`, no `runuser`.
  - It reads only `-u agentsh` in the call's window, and a timeout bounds it.
  - Its notes carry event types and counts only, never an event id, path or other line content.
  - An event type that doesn't match `[a-z0-9_]{1,64}` is counted as `unknown`.
- **The rendered config changes only** `audit.storage.channel_size` and `audit.storage.batch_size`.
- **The prune's exit code follows this attempt.** It exits 0 on a skip, on "not due" and on a clean run, and 1 when the current attempt records an error. The status file's content and fields are unchanged.
- **Runs under `local_guard: text` don't change.** `start()` returns before any new code for text mode or a non-local tree. `close()` still does nothing for a process that isn't an `AgentshProcess`.
- **No agent file kept, no agent code run, and no engineer file deleted or overwritten**, where the baseline does otherwise. The journal read runs a root-owned system binary (`journalctl`, resolved from PATH, as `_run` already does for `systemctl` and `git`) outside the wrap, with no input from the agent.

One effect is expected and isn't a finding. After the merge, the template differs from the installed `/etc/agentsh/sssf.yaml`, so every agentsh run refuses with "differs" until the engineer reinstalls and restarts. That is the pin working as designed, and it's why the reinstall comes straight after the merge.

## Out of scope

These holes stay open, so a reviewer should file them as old holes:

- **Dropped events still happen.** A writer that blocks instead of dropping, or that never drops a `ptrace_execve`, is a fork patch, and the patches are unpublished. A bigger channel only makes drops rarer.
- **journald's rate limit hides drop lines.** systemd 255 is installed here. Its default `RateLimitIntervalSec=30s` and `RateLimitBurst=10000` apply per service. A burst over that has its extra lines suppressed, and journald reports the suppression in its own message, not under `-u agentsh`. The 33,723 drops seen in one day are the same order of size. So the count is a floor: the note says "at least N". SSSF doesn't parse the suppression message and doesn't change journald's settings.
- **A dropped `ptrace_execve` doesn't refuse or stop the run.** It is a note.
- **Drops aren't attributed to a session.** A concurrent agentsh call's drops in the same window are counted in both calls' notes.
- **The journal window can miss lines.** It runs in whole seconds, from just before the wrap starts to `close()`. journald can deliver a line late, and a drop logged after `close()` reads the journal goes uncounted.
- **A runner that can't read the journal gets only the "can't read" note.** That happens when the engineer isn't in `adm` or `systemd-journal`, or when the runner itself runs inside a wrap.
- **Agent-chosen text in agentsh's log lines** can inflate a count (see the threat model).
- **Also out:**
  - alerts outside SSSF;
  - run and phase durations against their usual range;
  - pi's policy;
  - the fork's code.
- **The channel's memory figure is an estimate.** Neither the planner nor the reviewer could read the fork's source from inside their wraps (see "For the engineer").

## Facts this plan relies on

- **Both keys are in the installed build.** The installed `/usr/bin/agentsh` has sha256 `603dbf14…`, which is `PINNED_SHA256`, build 05b3777f. Its strings contain `yaml:"channel_size"`, `yaml:"batch_size"` and `yaml:"flush_interval"`, and the log text `event channel full, dropping event`. Checked with `grep -a` on the binary on 2026-11-11.
- **The store's defaults**, per the task: `defaultChannelSize` is 4,096, batches are 64 every 50 ms, and a full channel drops the event and logs its id and type.
- **`journalctl`'s exit code can't show an unreadable journal.** Under the planner's wrap, `journalctl -u agentsh` exits **0**, prints nothing on stdout, and prints `No journal files were opened due to insufficient permissions.` on stderr. The reader must look at stderr. Outside a wrap, the engineer's user is in `adm`, which can read the system journal.
- **The journal line's exact layout is unknown.** It could be slog text `key=value`, JSON, or printf, and it couldn't be read from inside the wrap. The parser accepts all three (see step 1).

## Chosen values: `channel_size: 16384`, `batch_size: 256`

- **What filled the channel.** Run 05f96f0f, on a 136 KB store, lost 34 events in 2 s at Claude Code's start, with a 4,096-slot channel.
- **The burst estimate.** This assumes the writer drains at its tick rate, 64 events per 50 ms or 1,280 events/s. On that assumption the burst was about 4,096 + 2,560 + 34, so about 6,700 events in 2 s. If the writer flushes as soon as a batch fills, it drains faster, and the burst was bigger by the difference.
- **Why four times.** Four times the channel (16,384) and four times the batch (256 per tick, up to 5,120 events/s at tick rate) absorb that burst about three times over. They also raise the drain ceiling for the pre-rollout bursts, which peaked at 3,504 drops a minute. Larger batches mean fewer SQLite commits per event.
- **`flush_interval` stays at its default**, because the task allows only the two keys.

**Memory, an estimate.**

- **What one event carries:** its id, type, timestamp, session and command ids, path or domain, policy decision and rule, and a `fields` map. A `ptrace_execve` also carries its argv. Allow about 2 KB per event on the heap.
- **The slots:** Go allocates a channel's slots up front, at `cap × element size`. That is a few hundred bytes each if the element is a struct value, or 8 bytes if it's a pointer.
- **A full channel:** 16,384 slots hold an estimated 32 MB of events, plus up to about 8 MB of slots. An exec with a very long argv carries more, up to the kernel's argument limit. The baseline's 4,096 slots hold about a quarter of that.
- `config.md` and the YAML comment call the figure an estimate.

## Files to change

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

**Constants**, next to `POLL_SECONDS`:

```python
SLOW_POLL_SECONDS = 1.0      # a poll request slower than this gets a note
JOURNAL_SECONDS = 10         # the journal read's timeout
DROP_TEXT = "event channel full"
```

Add `import math`.

**Store notes at the start.** Change the signature to `start(tree, harness, cmd, *, cwd, env, guard, tally=None, on_event=None)`.

- Add the keyword `on_event: Optional[Callable[[dict], None]] = None`.
- Right after `notes = check_machine(guard)`, before any other check and before `Popen`, call `on_event({"type": "agentsh_note", "note": note})` once per note, when `on_event` is given. Wrap each call in `try/except Exception: pass`, so a failing callback never refuses.
- `process.trace_notes.extend(notes)` stays, so the end-of-call event keeps the notes as now.
- Set `started_at = time.time()` immediately before `subprocess.Popen`. After the process is built, store it as `process.started_at`.

**Poll figures.** In `AgentshProcess.__init__`, add:

```python
self.started_at: Optional[float] = None
self.polls = 0
self.pages = 0
self.slowest = 0.0
```

- **`_timed_get(self, query)`** times `self._get(query)` with `time.monotonic()`. In a `finally`, under the existing `self._lock`, it sets `self.slowest = max(self.slowest, elapsed)`. It increments `self.pages` only when `_get` returned. A failed or timed-out request counts toward `slowest`, not toward `pages`.
- **The lock is safe here.** `_get` never runs while `_lock` is held: `_poll` takes the lock only after its `_get` returns, and `_end_session` calls `_pages` outside the lock. So the `finally` can take it without a deadlock.
- **Callers.** `_poll` and `_pages` call `self._timed_get(...)` instead of `self._get(...)`. `_get` itself is unchanged, so the tests that monkeypatch `_get` still work, and their fakes get timed.
- **Counting polls.** `_watch` increments `self.polls`, under `_lock`, once per `_poll()` attempt, answered or failed, before calling it. `_end_session` increments it once for the final read, inside the `if first:` lock block where it sets `_ended`.

**The journal read.** Add a module function:

```python
def journal_drops(since: float, until: float,
                  run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[dict[str, int], Optional[str]]:
    """agentsh's "event channel full" lines between `since` and `until`, counted by event type,
    as the runner's own user. Returns (counts, problem); `problem` is set, and the counts empty,
    when the journal can't be read. journald's rate limit can suppress lines, so the counts are
    a floor. Never raises."""
```

- **Command:** `["journalctl", "-u", "agentsh", "--since", f"@{int(since)}", "--until", f"@{math.ceil(until)}", "-o", "cat", "--no-pager"]`.
  - Run it with `capture_output=True, text=True, errors="replace", timeout=JOURNAL_SECONDS`.
  - Pass `env={**os.environ, "LC_ALL": "C"}`, so the messages are in English.
  - No sudo.
- **Problems**, returned as the second value:
  - `subprocess.TimeoutExpired` → `f"timed out after {JOURNAL_SECONDS} s"`.
  - `OSError` → `f"could not run ({type(error).__name__})"`.
  - A non-zero exit → `f"exited {returncode}"`.
  - A zero exit whose stderr contains `insufficient permissions`, `not seeing messages` or `Permission denied` (case-insensitive) → `"no permission to read the agentsh unit's lines"`.
  - Any other exception → `f"failed ({type(error).__name__})"`.
  - Never include stderr text in the problem.
- **Counting.** For each stdout line that contains `DROP_TEXT`:
  - Search it with `re.search(r'"?(?:event_)?type"?\s*[=:]\s*"?([^"\s,}]+)', line)`. That matches slog text `type=ptrace_file`, `event_type="ptrace_file"`, JSON `"type":"ptrace_file"`, and printf `type: ptrace_file`.
  - Keep the value only if it fully matches `[a-z0-9_]{1,64}`, else count it as `"unknown"`. Nothing else from the line is kept.

**Notes in `close()`.** `close()` runs once per process: `agent_cc.py` calls it at line 450, and in `agent_pi.py` the stop path at 751 raises before line 763. So no read-once flag is needed.

After `process._end_session()` and `atexit.unregister(...)`, and only when `process.started_at` is set, run `counts, problem = journal_drops(process.started_at, time.time())`. Then add these notes to `process.trace_notes`:

- **When the journal can't be read:** `"agentsh's journal couldn't be read for this call (" + problem + "), so events agentsh dropped are not counted; check with `journalctl -u agentsh --since <start> | grep -c 'event channel full'`"`.
- **When any drop was counted:** `f"agentsh dropped at least {total} event(s) during this call, when its store's channel was full: {', '.join(f'{t} {n}' for t, n in sorted(counts.items()))}. journald's rate limit can hide more. The trace's denials may be short by these. Drops are counted for the agentsh service as a whole, so a concurrent run's drops show here too"`.
- **When `counts.get("ptrace_execve")` is set**, a separate note: `f"at least {n} dropped event(s) were ptrace_execve: a denied exec may have gone uncounted toward the three-refusal stop. agentsh still enforced it"`.

**Poll note**, also in `close()`, before the event goes out. When `process.slowest > SLOW_POLL_SECONDS`, append `f"the slowest agentsh event poll took {process.slowest:.1f} s; a poll request gives up at 5 s and three failed polls in a row stop the agent. A slow poll usually means a big event store: see \"The event store\" in .claude/skills/sssf/references/config.md"`. `_get`'s `timeout=5` is unchanged.

**Event.** `close()` now always emits the `agentsh_denials` event for an `AgentshProcess` when `on_event` is given, because it carries the poll figures:

```python
{"type": "agentsh_denials", "denials": denials, "notes": process.trace_notes,
 "polls": process.polls, "pages": process.pages, "slowest_poll_seconds": round(process.slowest, 2)}
```

- Wrap the journal read and the new notes in their own `try/except Exception: pass`, so a failure there still emits the event.
- The whole of `close()` stays inside its existing `try/except Exception: pass`.

**Docstrings.**

- `store_notes`: say the notes are printed at the agent's start and kept in the end-of-call event.
- `check_machine`: update the last line to match.
- `close()`: say it also counts agentsh's dropped events from the journal, as a floor, and records the poll figures.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`

In `_event_forwarder`'s `forward`, before the `agentsh_denials` branch:

```python
if etype == "agentsh_note":
    # A store note from check_machine, shown before the agent starts. Never a refusal.
    run.console.advise(f"agentsh: {event.get('note', '')}")
    return
```

`console.advise` prints the note in full, then writes it to the trace as its own `log` event, with `level: warning`, under the current phase.

In the `agentsh_denials` branch, add `"polls"`, `"pages"` and `"slowest_poll_seconds"` from the event (`event.get(key)`) to the payload.

### 3. `.claude/skills/sssf/templates/adws/adw_modules/agent_cc.py` and `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Pass `on_event=on_event` to `agentsh_guard.start(...)`: at line 347 in `agent_cc.py`, and at line 703 in `agent_pi.py`. Nothing else changes.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`

Under `audit.storage`, after `sqlite_path`:

```yaml
    # agentsh drops an event when this queue is full (default 4096, batches of 64 every
    # 50 ms); a burst at an agent's start filled it on 2026-11-11. An estimated 2 KB an
    # event, so a full queue holds about 32 MB. See config.md, "The event store".
    channel_size: 16384
    batch_size: 256
```

`render_config` round-trips the file through `yaml.safe_load` and `safe_dump`. So the comments don't reach the installed file, and the values do, as ints. Change nothing else in this file.

### 5. `.claude/skills/sssf/scripts/agentsh_prune.py`

The exit code follows **this attempt's** error, not the status file's `error` field. On a skip or a not-due return, the status file carries over the previous error (`"error": old.get("error")`). Judging by the file would exit 1 on a skip, and every 10 minutes until the next clean prune.

- Move `prune`'s body into `_prune(db, api, status, now) -> tuple[dict, bool]`, which returns `(data, errored)`. `errored` is `True` only when one of the two `except` blocks set `data["error"]` in this call.
- `prune(...)` keeps its signature and returns `_prune(...)[0]`. The existing tests and the status file don't change.
- `main()` runs `_, errored = _prune(DB, API, STATUS, None)` with the same defaults `prune` has, then returns `1 if errored else 0`.
- A crash, meaning an uncaught exception, still exits non-zero through Python.
- Add the exit codes to the module docstring: 0 on a prune, a skip or not due, and 1 when an error is recorded.

### 6. `.claude/skills/sssf/scripts/agentsh_install.py`

No code change. The script already diffs the rendered config against `/etc/agentsh/sssf.yaml`, so the two new keys show in the diff. If its docstring describes the prune unit's exit behaviour, update that line. Otherwise leave the file alone.

### 7. `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh"

- **Line ~487** ("The run also reads … adds a trace note"). Say that before the agent starts, the notes are printed to the run's console, in yellow and prefixed `agentsh:`, and traced as warnings in the phase's log. They are also kept in the end-of-call `agentsh_denials` event. They never refuse.
- **Line ~491** (polling and drops). Add these points:
  - **Poll figures.** Each call's `agentsh_denials` event records `polls`, `pages` and `slowest_poll_seconds`, the slowest single poll request, failed ones included. A slowest poll over 1 s adds a note naming the time and the 5 s limit, a sign the store is growing.
  - **The journal read.** At the end of each call, SSSF runs `journalctl -u agentsh` for the call's window as your user, with a 10 s timeout. It counts "event channel full" lines by event type and adds a note giving the counts as "at least". journald's rate limit can suppress lines, so the real number can be higher. Drops are counted for the whole service, so concurrent runs share them.
  - **A dropped `ptrace_execve`** gets its own note: a denied exec may have gone uncounted toward the three-refusal stop, though agentsh enforced it.
  - **When the read fails**, a note says so. That covers a journal your user can't read (not in `adm` or `systemd-journal`, or a runner itself inside a wrap), a timeout, and a failed `journalctl`. None of these refuse or stop.
- **"The event store" paragraph.** Say the config sets `audit.storage.channel_size: 16384` and `batch_size: 256`, four times agentsh's defaults (4,096 and 64). A full channel holds an estimated 32 MB of events. A bigger channel makes drops rarer, and can't rule them out. Also say the prune service exits 1 when it records an error, so `systemctl --failed` lists `sssf-agentsh-prune.service`, and that a skip exits 0.

### 8. `docs/guide/troubleshooting.md`

- Replace line 60 ("`check_machine`'s warnings about the store go into the trace only…"). The new line says the store warnings print to the console when each agent starts. It also says the end-of-call `agentsh_denials` event in the trace adds the slowest poll and at least the events agentsh dropped during the call.
- Add `systemctl --failed                                                 # a prune that recorded an error` to the check block at lines 52–56, in the same comment column.

**Not to touch:**

- `docs/open.md`: the request doesn't list it.
- `app_docs/`: the documenter's.
- The design doc's Next item 16: the hub closes it after the rollout.

## Tests

Add these to `.claude/skills/sssf/tests/test_agentsh_guard.py`, using its existing fixtures (`machine`, `service`, `harness`, `harness_run`, `prune_env`, `launch`, `_write_status`). The config always comes from the shipped `templates/sssf.config.yaml`.

**Safety.** The tests never call the real `journalctl`, never touch `/var/lib/agentsh`, and never start the real service. Add an autouse fixture, or patch `agentsh_guard.journal_drops` in `machine`, that returns `({}, None)` by default, so no existing launch test reaches the real journal. Tests of `journal_drops` itself pass a fake `run=`.

1. **`test_store_notes_are_announced_before_the_wrap_starts`**
   - Write a stale status with `_write_status`.
   - Patch `subprocess.Popen` in `agentsh_guard` to record when it's called, then delegate.
   - Launch with `on_event=events.append`.
   - Assert that an `agentsh_note` event with "last pruned" was appended before Popen ran, and that the end-of-call `agentsh_denials` event still holds the note.
2. **`test_a_failing_note_callback_does_not_refuse`:** an `on_event` that raises still launches.
3. **`test_the_forwarder_prints_and_traces_an_agentsh_note`**
   - Use `test_rules`' `forwarder` helper, or a fake run with a recording console and tracer.
   - Feed it an `agentsh_note`.
   - Assert that `console.advise` got `agentsh: …`, that a trace log event was written for it, and that the rule check didn't run.
4. **`test_the_end_of_call_event_has_poll_counts_pages_and_slowest`**
   - Launch against `service` with a few events, then `close`.
   - Assert `polls >= 1`, `pages >= 1`, and that `slowest_poll_seconds` is a float.
   - Assert that the forwarder's `agentsh_denials` payload carries all three.
5. **`test_a_poll_over_one_second_adds_a_note`**
   - Patch `agentsh_guard.time.monotonic` with a fake clock that a fake `_get` advances by 1.5 s.
   - Assert a note naming `1.5 s` and `5 s`.
   - At 0.5 s, assert there is no note.
   - Assert that `breach` and `check_stopped` don't raise.
6. **`test_journal_drops_counts_by_type`**
   - A fake `run` returns lines in slog text, JSON and printf forms, mixed with other agentsh lines.
   - Assert the counts by type, for example `{"ptrace_file": 2, "ptrace_execve": 1}`.
   - Assert that the command is exactly `journalctl -u agentsh --since @<int> --until @<int> -o cat --no-pager`, with a timeout and no `sudo`.
7. **`test_a_type_that_is_not_a_plain_name_counts_as_unknown`:** a line with `type="/home/x/.env"`, or one with a 200-character value, counts as `unknown`. The note contains neither the value nor the event id.
8. **`test_close_notes_at_least_the_drops_and_the_execve_drop`**
   - Patch `journal_drops` to return `({"ptrace_file": 34, "ptrace_execve": 2}, None)`.
   - Assert one note that says "at least 36" and gives both counts.
   - Assert a separate note that names the uncounted denied exec and says agentsh enforced it.
   - Assert that no note contains an id.
   - Assert that `check_stopped` doesn't raise and `failure` is empty.
9. **`test_an_unreadable_journal_a_timeout_and_a_failed_journalctl_each_note_it`**, parametrised over four cases:
   - a zero exit with stderr `No journal files were opened due to insufficient permissions.`;
   - `TimeoutExpired`;
   - exit 1;
   - `FileNotFoundError`.

   Each case gives the "couldn't be read" note with the right reason, no stderr text, and no stop.
10. **`test_text_mode_reads_no_journal`:** extend `test_text_mode_reads_no_status_and_makes_no_note`. Under `local_guard: text`, `start` and `close` never call `journal_drops`.
11. **`test_the_rendered_config_raises_the_store_buffer`**
    - Render from the shipped template.
    - Assert `audit.storage.channel_size == 16384` and `batch_size == 256`.
    - With those two keys removed, assert that `audit` equals what the baseline template renders: `sqlite_path` is still there and nothing else is added.
12. **Prune exit codes**, using `prune_env` and the module's `_prune` and `main`, with `DB`, `API` and `STATUS` patched:
    - A clean run returns 0 from `main()`.
    - A skip (the fake API lists a session) returns 0, also when the old status holds an `error`.
    - Not due returns 0.
    - A recorded error returns 1, and the status file has the same keys as before. Cause it by patching `sqlite3.connect` to raise `sqlite3.OperationalError`, or by making `fix_owner` raise `OSError`.

**Existing tests.** Every existing test must still pass unchanged. That includes `test_a_launch_with_a_stale_status_carries_the_note_into_the_trace`, the three-denial and three-failed-poll tests, and the prune tests.

## Verification

1. **The suite.** The builder runs the skill's suite from a copy outside the repo, as earlier runs did: copy `.claude/skills/sssf/` to a temp folder and run its pytest suite there, the way the repo's test command does. The full suite must pass, including:
   - `tests/test_agentsh_guard.py`
   - `tests/test_rules.py`
   - `tests/test_agent_cc.py`
   - `tests/test_agent_pi.py`
   - `tests/test_console.py`
2. **No real journal.** Run `grep -rn "journalctl" .claude/skills/sssf/tests/` and check that every reference in the tests is in a fake `run` or an assert, never a real call.
3. **Only the two keys change.** Load `render_config` of the shipped template, and the baseline's, with `yaml.safe_load`, and confirm in a short `python -c` that only the two keys differ.
4. **After the merge**, in the hub session, not the builder:
   1. The engineer reinstalls both repos' policies with `agentsh_install.py`. In `sssf.yaml`, the diff must show only `channel_size` and `batch_size`. The prune script's diff shows too.
   2. The engineer runs the printed sudo lines and restarts the service.
   3. The hub runs one small call in sssf-sandbox, then checks:
      - The console shows any `agentsh:` store notes before the agent starts.
      - The trace's `agentsh_denials` event has `polls`, `pages` and `slowest_poll_seconds`.
      - The call's drop count is 0, or below run 05f96f0f's 34. Take it from the note, or from the engineer's own `journalctl -u agentsh --since … | grep -c 'event channel full'`.
   4. Read the results:
      - If the drop note shows only `unknown` types, the real line layout didn't match the parser. That is a finding.
      - If the runner itself runs inside the hub's wrap, expect the "couldn't be read" note, and use the engineer's own `journalctl`.
   5. To confirm the unit is listed by `systemctl --failed` after a recorded prune error, the engineer can force an error, for example by stopping agentsh mid-prune, or skip this check.

## For the engineer

- No protected file needs a change.
- Before the reinstall, confirm two things from the fork's source, `internal/store/sqlite/sqlite.go`. The planner and the reviewer couldn't read `~/github/agentsh` from inside their wraps.
  - The channel's element type and size fit the estimate of about 2 KB an event and about 32 MB full. If they don't, correct the figure in `config.md` and the YAML comment.
  - The "event channel full" log line names the type under a key the parser accepts, `type` or `event_type`.

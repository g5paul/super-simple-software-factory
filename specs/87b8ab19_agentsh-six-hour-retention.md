# Keep 6 hours of agentsh's events and audit log, prune on the hour, and finish pi's loop fix

## Summary

Five changes, all in the skill. Never edit the hub's own `adws/` copies, which are protected.

1. `KEEP_SECONDS` in the prune goes from 2 days to 6 hours.
2. A prune is due once the last is at least `PRUNE_EVERY - PRUNE_SLACK` old, 3,600 − 600 = 3,000 s. A check that lands a second early still prunes.
3. `agentsh_sssf.yaml` sets `audit.rotation.max_size_mb: 200` and `max_backups: 5`. A due prune also deletes rotated `audit.jsonl.<N>` files last modified more than `KEEP_SECONDS` ago, under the same session skip. The status file gains `audit_bytes`, and the guard's 10 GB note counts the store and the audit files together.
4. pi's read loop still skips a line that raises `json.JSONDecodeError`, as at the baseline. A line that raises any other `ValueError` or a `RecursionError` from `json.loads` is skipped **only when it doesn't start with `{`**. When it starts with `{`, the error propagates and pi is stopped, as at the baseline.
5. `config.md` documents:
   - the new retention and the rotation cap;
   - the two stream-check limits from run 361de0f2's bypass review;
   - that pi stops on invalid UTF-8 under `local_guard: text`;
   - that pi's loop skips a line it can't parse only when the line doesn't start with `{`.

This is the second try at this spec. Run c535b28d's plan (`adws/adw_data/sessions/c535b28d/context_handoff/plan.md`) passed spec review. Its bypass review (`.../c535b28d/context_handoff/bypass_review_1.md`) found one bypass, in change 4's unconditional skip, and no harms. This plan keeps that design and narrows change 4.

## Baseline

`main` at **`c495eb6`** ("Narrow run F's parse-error skip to lines that don't start with {"). Run 361de0f2 merged at 8f12f41, which is in that history. Every skill file this plan touches is byte-identical between `7a7cc05` (run c535b28d's base) and `c495eb6`. Every finding is judged against `c495eb6`.

## Threat model

### What the agent can do

- The agent runs as the engineer's own user. It can write anything that user can write, including its harness's stdout (`/proc/$PPID/fd/1`).
- It can fork processes. Under `local_guard: text`, with `kernel.yama.ptrace_scope` at 0, it can attach to the runner.
- The stream check can't stop an agent set on getting past it. agentsh's policy and a VM (`--sandbox`) are the boundaries.

### The model's tool arguments

The model picks its tool calls' arguments, and pi prints them. pi 1.4.0's `bashSchema` allows extra keys. So a `bash` call such as `{"command":"git push","pad":[[[…]]]}` is printed on two lines:

- a `message_end` line holding the `toolCall`;
- a `tool_execution_start` line holding the `args`.

`rules.calls` reads the command only from these two lines. A bun-compiled pi (`PI_PATH`) can print a line deeper than CPython's C JSON limit, about 10,000 levels. Python 3.11 and older stop at about 1,000 levels, so even the node pi's lines can reach that depth.

pi's events are always JSON objects, so every real event line starts with `{`. A line that starts with `{` and can't be parsed for depth or digit length must therefore fail closed. Skipping it would let a `stop` rule's call run unchecked. This was run c535b28d's bypass.

### Lines the agent writes itself

The agent can write any line to pi's stdout. A line that doesn't start with `{` and holds a huge integer or a deeply nested array ends pi's loop at the baseline. pi is stopped first, so it fails closed, but the run ends. After this change such a line is skipped, like any other line that isn't JSON.

The agent could already forge or hide lines at the baseline, so this skip gives it nothing new. The partial-line hole is one example: write `x` with no newline, and it joins the next real announcement (see [Out of scope](#out-of-scope)).

### Invalid UTF-8

Bytes that aren't UTF-8 behave differently under the two guards:

- **`local_guard: text`:** `sandbox.Local.start` opens the pipe with `text=True` and no `errors=`. The read raises `UnicodeDecodeError`, with pi stopped. That is fail-closed and unchanged; its fix is outside this run.
- **`local_guard: agentsh`:** `agentsh_guard.start` opens the pipe with `errors="replace"` (`agentsh_guard.py`, the `Popen` in `start`). The bytes become U+FFFD, and the line is parsed or skipped like any other.

### The audit folder

`/var/lib/agentsh/` belongs to the agentsh service user, and the engineer can't read it without sudo. So the agent can't plant files or links in the audit folder, and it can't read the store, the audit log or the rotated files.

The prune runs as root from `sssf-agentsh-prune.service`, so its deletion must still be narrow:

- It matches names exactly.
- It works only on regular files in that one folder, checked with `lstat`.
- It follows no link.
- It never touches the live `audit.jsonl`.

This is defence in depth against a mistake or a future change in folder ownership, not against the agent.

### Growth

The agent can make many events, and so grow the store and the audit log. The size cap limits the audit log. The store's growth during a session that never closes stays an old hole; it is denial of service only.

### What the guard can't stop at its level

This change doesn't try to stop:

- a process forked between `rules.stop_tree`'s `ps` listing and the kill;
- a partial line (no newline) that hides the next event line from the check.

Both are documented, not fixed.

## Bar

### Against `c495eb6`, the change must:

- Delete store rows older than `KEEP_SECONDS = 6 * 3600`, and keep newer ones. It still deletes only rows with `ts_unix_ns < cutoff`, in batches of `BATCH = 5000` walked by rowid.
- Treat a prune as due when `now - last_prune >= PRUNE_EVERY - PRUNE_SLACK`, with `PRUNE_SLACK = 600`.
- Delete only regular files named `audit.jsonl.<N>`, where N is ASCII digits, in the audit log's folder, last modified more than `KEEP_SECONDS` before `now`. Never the live `audit.jsonl`, a link, a directory or any other name.
- Write `audit_bytes` to the status file on every write: due, not due, skip and error. Every key the status file had at the baseline stays.
- Make the guard's 10 GB note fire on `store_bytes + audit_bytes`. A status file without `audit_bytes` reads as the store alone.
- In pi's loop:
  - keep skipping a line that raises `json.JSONDecodeError`, as at the baseline;
  - skip a line that raises another `ValueError` (the integer digit limit) or `RecursionError` only when the stripped line doesn't start with `{`;
  - let the error propagate when the line starts with `{`, so pi is stopped as at the baseline.

### It must never:

- Delete a row or an audit file, or run `ANALYZE`, while the service lists a session or doesn't answer. The audit deletion gets its own `skip_reason` check just before it runs, as `ANALYZE` does.
- Change the prune's exit codes:
  - 0 means a prune, a skip or "not due".
  - 1 means this attempt recorded an error.
  - An `OSError` from the audit step is recorded in `error`, as `fix_owner`'s is, and so exits 1.
- Skip a line that starts with `{` and raises `ValueError` (other than `JSONDecodeError`) or `RecursionError`. One example is a `message_end` or `tool_execution_start` for `bash` `git push` whose `pad` argument is nested 12,000 deep.
- Change any refusal or stop in the guard, or `check_machine`'s thresholds (`PRUNE_STALE_SECONDS = 3 * 3600`, `STORE_WARN_BYTES = 10 * 10**9`).
- Change the rendered config except by adding the two `audit.rotation` keys.
- Change Claude Code's loop (`agent_cc.py`) or `sandbox.py`.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the baseline doesn't.
- Touch `/var/lib/agentsh` or start the real service from a test.

## Sizing

- **The measured rate.** On 2026-11-11 the hub measured the store growing about 1.4 GB an hour while chains run: 135,968 bytes at 08:10 and 2,778,925,800 at 10:10.
- **The store.** It keeps 6 hours of events, plus up to an hour until the next due prune. At a sustained peak it levels off at about 8.4 to 9.8 GB.
- **The audit log's rate is estimated, not measured.** The engineer can't read `/var/lib/agentsh/`, so nobody has measured it. The estimate assumes one JSON line per event, about the size of a row, so also about 1.4 GB an hour. The hub session checks the real `audit_bytes` after rollout.
- **The cap.** `max_size_mb: 200` and `max_backups: 5` allow at most six files, the live log plus `.1` to `.5`. That is about 1.2 GB, plus one write's overshoot, well under the 10 GB note on its own.
  - At the estimated peak rate the cap holds about 50 minutes of audit events.
  - At lower rates the prune's age delete trims the log to 6 hours.
  - SSSF never reads the log, so a shorter history at peak costs nothing.
- **Together.** The store and the capped log come to about 9.6 to 11 GB at a sustained peak. **So the combined 10 GB note can fire during long busy stretches.** The note is advice only and never refuses. The yaml comment and `config.md` must say so, and must not claim the total stays under 10 GB.

## Files to change

### `.claude/skills/sssf/scripts/agentsh_prune.py`

**Module docstring.** Change the first line to "Prune agentsh's event store and its rotated audit log to the last 6 hours." Also say that the audit step skips the same way the store does.

**Constants:**

- Add `AUDIT = "/var/lib/agentsh/audit.jsonl"   # agentsh_sssf.yaml audit.output`.
- Set `KEEP_SECONDS = 6 * 3600`.
- Keep `PRUNE_EVERY = 3600`.
- Add `PRUNE_SLACK = 600   # one timer period (OnCalendar=*:0/10)`.
- Rewrite the comment on `PRUNE_EVERY` to say:
  - A prune is due once the last is at least `PRUNE_EVERY - PRUNE_SLACK` old, so a check that lands a second short of the hour still prunes.
  - Because of that age bound, prunes never come less than 50 minutes apart. They land at the 50- or 60-minute check, depending on the timer's jitter.
  - `last_prune` is stored in whole seconds, rounded down, so the bound is 3,000 s give or take under a second.

**Matching rotated names.** `import re`, and add `ROTATED = re.compile(r"[0-9]+")`. Use it with `fullmatch` on the suffix after `os.path.basename(AUDIT) + "."`. Use `[0-9]`, not `\d` or `str.isdigit`, which accept non-ASCII digits.

**New functions:**

- `rotated(audit)`:
  - Lists `os.path.dirname(audit)`.
  - Returns `(path, lstat result)` for each name `<base>.<digits>` whose `lstat` is `S_ISREG`.
  - Skips a name whose `lstat` raises `FileNotFoundError`. Any other `OSError` from listing or `lstat` propagates.
- `audit_bytes(audit)`:
  - Returns `None` when the folder can't be listed (any `OSError`).
  - Otherwise returns the sum of the `st_size` of:
    - the live `audit`, from `lstat`, counted only if it is a regular file, and 0 if it is missing;
    - every file from `rotated(audit)`.
- `prune_audit(audit, now)`:
  - For each `(path, info)` from `rotated(audit)` with `now - info.st_mtime > KEEP_SECONDS`, it calls `os.unlink(path)`, ignoring `FileNotFoundError`.
  - It returns nothing and never touches the live file.
  - It doesn't re-resolve links. `unlink` acts on the name, and `rotated` has already excluded links.

**`_prune(db, api, status, now, audit)`:**

- `data` gains `"audit_bytes": None` right after `"store_bytes"`. Every other key stays, in the same order.
- The not-due test becomes `if last is not None and now - last < PRUNE_EVERY - PRUNE_SLACK:`. That path also sets `data["audit_bytes"] = audit_bytes(audit)` before `write_status`.
- After the existing `ANALYZE` block, still inside the outer `if reason is None:` within the `try`, add:
  ```python
          if reason is None:
              reason = skip_reason(api)
              if reason is None:
                  prune_audit(audit, now)
  ```
  The audit step then runs only after the store batches finish with no session listed, and it checks the session list again just before it deletes.
- Widen `except (sqlite3.OperationalError, sqlite3.DatabaseError)` to catch `OSError` too. Format the error text the same way, and set `errored = True`.
- At the end, next to `data["store_bytes"] = store_bytes(db)`, set `data["audit_bytes"] = audit_bytes(audit)`.

**`prune` and `main`:**

- `prune(db=DB, api=API, status=STATUS, now=None, audit=AUDIT)`: add `audit` last, so existing calls are unchanged, and pass it through.
- `main()`: call `_prune(DB, API, STATUS, time.time(), AUDIT)`. Read the module globals at call time, as `DB` is read now, so tests can monkeypatch `AUDIT`.

### `.claude/skills/sssf/scripts/agentsh_install.py`

- Line ~147, the service unit's `Description=`: change it to `SSSF: prune agentsh's event store and rotated audit log to 6 hours`.
- `ReadWritePaths` already covers `/var/lib/agentsh`.
- Leave the timer unit, including its `Description=`, as it is.

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml`

Under `audit:`, after `output:`, add:

```yaml
  # agentsh rotates this log by size only: at max_size_mb it renames it to .1, shifts
  # .1 to .2 and so on, and overwrites the last of max_backups. max_age_days and
  # compress parse but do nothing in this build. At most 6 files of about 200 MB,
  # 1.2 GB; the prune deletes rotated files older than 6 hours. The store levels off
  # at about 8.4 to 9.8 GB at the measured 1.4 GB an hour, so together they can pass
  # the 10 GB note at a sustained peak; the log's own rate is an estimate. See
  # config.md, "The event store".
  rotation:
    max_size_mb: 200
    max_backups: 5
```

### `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`

Only `store_notes` (line ~503) changes.

**Which values count:**

- Read `store = data.get("store_bytes")` and `audit = data.get("audit_bytes")`.
- A value counts only if it is an `int` or `float` and not a `bool`.
- An absent or invalid `audit_bytes` counts as 0, so an old status file reads as the store alone.

**The note.** If at least one value counts, and `total = store + audit` (counted values only) is over `STORE_WARN_BYTES`, add one note:

`f"agentsh's event store and audit log are {total / 1e9:.1f} GB together, over 10 GB; with no run going, stop the service, move /var/lib/agentsh/events.db and its -wal and -shm files aside, start it, and delete the moved files once a run works (see \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md)"`

Keep the "move" and "events.db" wording. Nothing else in the function changes. Update `store_notes`'s docstring to say it counts the audit log too.

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

In `run`'s loop (line ~721), replace the single `except json.JSONDecodeError: continue` with:

```python
                        try:
                            event = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        except (ValueError, RecursionError):
                            if line.startswith("{"):
                                raise                    # pi's events are objects: one too deep or long to read stops pi
                            continue                     # an integer past Python's digit limit, or nested too deep
```

- `line` is already stripped two lines above.
- The `raise` leaves through the loop's existing `except BaseException`, which runs `process.stop_tree()`, the same path as the baseline.
- Nothing else in the loop changes. Don't touch `agent_cc.py`.

### `.claude/skills/sssf/references/config.md`

**1. The stream-check limits** (the paragraph at line ~449, "These rules stop an agent's mistakes…"). After the forged-event sentences, add two limits. They apply to every harness and matter most under `local_guard: text`:

- SSSF stops a call by killing the process tree it finds in one `ps` listing. A process the agent forks between that listing and the kill is reparented and survives the stop. In run 361de0f2's review, a forbidden command's own shell that started 10 to 20 ms after its announcement outlived the stop.
- Bytes with no newline written to the harness's stdout join its next event line. That line then fails to parse and is skipped, so a real announcement in it goes unchecked.

Then say that under `local_guard: agentsh`, agentsh's policy still denies the commands it lists. Then add these points about pi's lines:

- pi's loop skips a line that isn't a JSON object. It also skips a line it can't parse because of an integer past Python's 4,300-digit limit or nesting too deep, but only when the line doesn't start with `{`.
- pi's own events are objects, so a line that starts with `{` and is too deep or too long to read stops pi and ends the run with an error. A tool call whose arguments the model nests too deep is one such line.
- Under `local_guard: text`, a byte on pi's stdout that isn't valid UTF-8 stops pi and ends the run with an error, because SSSF reads the local pipe as text.
- Under `local_guard: agentsh`, the pipe replaces such bytes, and the line is read like any other.

**2. The "The event store." paragraph** (line ~502). Rewrite these parts and keep the rest:

- **When a prune is due.** "one is due an hour after the last" becomes: one is due once the last is at least 50 minutes old, an hour less one timer period, so a check that lands a second early still prunes, and prunes come every 50 or 60 minutes.
- **What it deletes.** "deletes events older than 2 days" becomes "older than 6 hours". Add:
  - A prune then deletes each rotated audit file, `audit.jsonl.1` and so on, last modified more than 6 hours ago.
  - It never deletes the live `audit.jsonl`, a link or any other name.
  - It skips the same way the store does.
- **The status file.** "holds the store's size in bytes" becomes: holds the store's size and the audit log's (`audit_bytes`, the live log and its rotated files together).
- **Where it levels off.** "levels off at about 2 days of events" becomes: about 6 hours of events, plus up to an hour before the next prune. At the hub's measured 1.4 GB an hour while chains run, that is about 8.4 to 9.8 GB.
- **Add the audit log.** agentsh also writes every event to `/var/lib/agentsh/audit.jsonl`, and rotates it by size only:
  - The config sets `audit.rotation.max_size_mb: 200` and `max_backups: 5`, so the log is at most six files of about 200 MB, 1.2 GB.
  - Its rate hasn't been measured. At an estimated rate like the store's, the cap holds under an hour of events; less busy hours keep 6.
  - agentsh's `max_age_days` and `compress` settings do nothing in this build.
- **Add the note.** The 10 GB note counts the store and the audit files together, so it can fire during a long busy stretch even when the prune is keeping up.

### `.claude/skills/sssf/tests/test_agentsh_guard.py`

#### Fixtures

- `prune_env` (line ~2472):
  - Create `tmp_path / "agentsh"`, and set `audit = tmp_path / "agentsh" / "audit.jsonl"`.
  - `run(**kw)` passes `audit=str(audit)` unless `kw` gives one.
  - Expose `audit` on the namespace.
  - Add a helper `touch(name, age, size=10)`. It writes a file of `size` bytes in that folder and sets its mtime to `NOW - age` with `os.utime`.
- `_main` (line ~3030): also `monkeypatch.setattr(prune_env.m, "AUDIT", str(prune_env.audit))`.
- Leave `LOOP_PI` and `loop_run` unchanged.

#### Tests to change

- **`test_the_prune_keeps_the_last_two_days_in_several_batches`:** rename it to `test_the_prune_keeps_the_last_six_hours_in_several_batches`.
  - Use `add(*([3 * DAY] * 7), 6 * 3600 + 1, 6 * 3600 - 1, 0)`.
  - Assert `ages() == [6 * 3600 - 1, 0]`, `deleted == 8`, and 3 DELETE statements.
- **`test_the_prune_runs_at_most_hourly`:** rename it to `test_a_prune_is_due_fifty_minutes_after_the_last`.
  - Keep the 1,800 s not-due case.
  - Add 2,999 s: not due, no API hit, row kept.
  - Replace 3,661 with 3,599: due, API hit, row deleted.
- **`test_the_status_file_is_public_and_holds_no_event_content`:** the key set gains `"audit_bytes"`.
- **`test_a_recorded_prune_error_exits_1_and_keeps_the_status_fields`:** assert `set(status) == keys | {"audit_bytes"}`.
- **`test_a_clean_prune_and_a_not_due_prune_exit_0`:** change the comment "an hour has not passed" to "50 minutes have not passed".
- **`test_the_rendered_config_raises_the_store_buffer`:** the last assert becomes `set(config["audit"]) == {"enabled", "output", "storage", "rotation"}`.
- **`test_prune_constants_agree_with_the_guard_and_stay_out_of_every_agent_write`:** add `assert m.AUDIT == template["audit"]["output"]`, following how that test already reads the template's paths.

#### New prune and note tests

1. **`test_the_rendered_config_caps_the_audit_log`:** `yaml.safe_load(agentsh_guard.render_config(TEMPLATE.read_text(), ["p"]))["audit"]["rotation"] == {"max_size_mb": 200, "max_backups": 5}`.
2. **`test_a_due_prune_deletes_only_old_rotated_audit_files`:**
   - Create these files:
     - the live `audit.jsonl`, age 3 × DAY
     - `audit.jsonl.1`, age 3,600
     - `audit.jsonl.2`, age 6 × 3600 + 1
     - `audit.jsonl.10`, age 3 × DAY
     - `audit.jsonl.bak`, age 3 × DAY
   - Create a directory `audit.jsonl.4` with an old mtime.
   - Create `audit.jsonl.3` as a symlink to `tmp_path / "outside"`, a file aged 3 × DAY. Set the link's own mtime old with `os.utime(..., follow_symlinks=False)`.
   - After `run()`:
     - `.2` and `.10` are gone;
     - the live log, `.1`, `.bak`, the directory, the link and its target remain;
     - `result["error"] is None`.
3. **`test_a_listed_session_keeps_every_audit_file`:**
   - Use the same old files as test 2, with `api.body = b'[{"id": "s"}]'` (match how `SessionsApi` is set elsewhere). After `run()` every file is still there, and `result["skipped"]` is set.
   - Also cover a session that appears at the audit step's own check:
     - Set up rows aged 3 × DAY, and an `on_hit` (or the fixture's equivalent) that lists a session from the 4th hit on.
     - With one batch and `last_analyze` None, the hits are: the first check, the batch check, `ANALYZE`'s check, then the audit check.
     - Expect the store rows deleted, the audit files kept, and `last_prune` None.
     - Check the fixture's starting status so the hit count is right.
4. **`test_audit_bytes_counts_the_live_log_and_rotated_files`:**
   - Create a live log of 100 bytes, a recent `.1` of 20 bytes, a `.bak` of 7 bytes, and the link `.3`.
   - Expect `result["audit_bytes"] == 120`, and the same value in the status file.
   - The not-due path writes it too: set `last_prune` 1,800 s old and run again.
   - With the audit folder missing, `audit_bytes` is None.
5. **`test_an_audit_unlink_error_is_recorded_and_exits_1`:**
   - Monkeypatch `prune_env.m.os.unlink` to raise `PermissionError` for the old rotated file.
   - Expect `_main` to return 1, `status["error"]` to start with `PermissionError`, and the status to keep its keys.
6. **`test_check_machine_notes_the_store_and_audit_log_together`:**

   | `_write_status(machine, …)` | Expected notes |
   |---|---|
   | `store_bytes=6e9, audit_bytes=5e9` | one, containing "events.db" |
   | `store_bytes=6e9, audit_bytes=3e9` | `[]` |
   | `store_bytes=None, audit_bytes=11e9` | one |
   | `store_bytes=9e9, audit_bytes=True` | `[]` |
7. **`test_check_machine_reads_an_old_status_as_the_store_alone`:** `_write_status` without `audit_bytes`. `store_bytes=11e9` gives one note, and `9e9` gives `[]`. The existing `test_check_machine_notes_a_big_store` stays as it is.

#### New pi-loop tests

These use the existing `loop_run` fixture, both the `text` and `agentsh` params.

8. **Two new `test_a_line_that_is_not_an_object_leaves_pi_watched` params:** add `"9" * 5000` with id `long-int`, and `"[" * 100000 + "]" * 100000` with id `deep-array`. Each is followed by `json.dumps(PI_PUSH)`, as the existing cases are. The existing asserts must hold: `RuleBreach` ("may have run"), cost 0.004, one close and one exit, child gone and quiet.
9. **`test_a_real_pi_line_too_deep_to_read_stops_pi`**, for the bypass found in run c535b28d. Add a helper `_pi_call_lines(command, pad)` that builds pi's two real lines for one `bash` call, as raw strings rather than `json.dumps`, which can't build the deep pad:
   - `args = '{"command":' + json.dumps(command) + ',"pad":' + pad + '}'`
   - **The `message_end` line:** `'{"type":"message_end","message":{"role":"assistant","stopReason":"toolUse","content":[{"type":"toolCall","id":"c1","name":"bash","arguments":' + args + '}],"usage":{"input":100,"output":20,"cost":{"total":0.004}}}}'`
   - **The `tool_execution_start` line:** `'{"type":"tool_execution_start","toolCallId":"c1","toolName":"bash","args":' + args + '}'`

   Parametrize the test over `which` in `["message_end", "tool_execution_start"]` and `pad` in:
   - `"[" * 12000 + "]" * 12000`, id `array-12000`;
   - `'{"a":' * 12000 + "1" + "}" * 12000`, id `object-12000`.

   For each case:
   - Feed `loop_run` the chosen line alone, then `json.dumps(PI_PUSH)`, with `on_event=forward` from `_real_forwarder(agent_pi)`.
   - Assert `pytest.raises(RecursionError)`. The line is never skipped, and the later `PI_PUSH` is never reached.
   - Assert one close and one exit, and `_child_gone_and_quiet(loop_run)`.

   12,000 levels is past CPython 3.12/3.13's C limit, about 10,000, and past 3.11's 1,000-frame limit, so the test raises on every supported Python.
10. **`test_a_long_integer_in_a_real_pi_line_stops_pi`:** `json.dumps(PI_PUSH)` with `"input": 100` replaced by a 5,000-digit integer. Do the replacement on the string, since `json.dumps` of such an int raises `ValueError` too. Expect `pytest.raises(ValueError)`, not `RuleBreach`, plus one close, one exit, and the child gone and quiet. This pins the `{` rule for the digit limit.
11. **`test_invalid_utf8_on_pis_stdout_stops_pi_under_text`.** It is text-only and not parametrized, and it uses its own fake pi:
    - Build the request with `_fake_pi(tmp_path, monkeypatch, _turn("stop", "hi"))`. Its default `local_guard` is `LocalGuard(mode="text")`.
    - Overwrite `agent_pi.PI_PATH` with a small Python script (`#!{sys.executable}`). The script:
      - opens a bytes file `tmp_path / "utf8_lines"` in `"rb"` mode, holding `b"\xff\xfe\n" + json.dumps(PI_PUSH).encode() + b"\n"`;
      - writes it to `sys.stdout.buffer` and flushes;
      - starts `sleep 60` and writes its pid to `tmp_path / "utf8_child.pid"`;
      - sleeps 2 s, writes `tmp_path / "utf8_marker"`, and sleeps 60.
    - Call `agent_pi.run(req)` inside `pytest.raises(UnicodeDecodeError)`.
    - Then check that the child pid, if its file exists, is `gone(...)`. After `time.sleep(3)`, the marker must not exist.
    - Skip with `pytest.skip` unless `locale.getpreferredencoding(False)` names UTF-8, since `text=True` decodes with the locale's encoding.
    - Add no agentsh-mode case. That pipe uses `errors="replace"`, as the threat model and `config.md` say.

Remove nothing else. All new prune tests pass `now=NOW` and tmp paths, and none touches `/var/lib/agentsh`.

## Docs this change makes out of date

- `.claude/skills/sssf/scripts/agentsh_prune.py`: the module docstring and the constant comments (above).
- `.claude/skills/sssf/scripts/agentsh_install.py`: the service `Description=` (above).
- `.claude/skills/sssf/references/config.md`: the stream-check limits and "The event store." (above).
- `store_notes`'s docstring in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`.
- There is no CHANGELOG in the repo.
- Nothing in `app_docs/` describes the prune. If the documenter finds a write-up there that says the prune keeps 2 days, or that pi's loop skips every unparseable line, it should note that the write-up is now out of date. The builder doesn't edit `app_docs/`.
- `docs/designs/agentsh-local-sandbox.md` is the hub session's log of runs; leave it to the hub session. `docs/designs/agentsh/sssf.yaml` is a proof-era snapshot; leave it.

## Verification

From the repo root:

1. Run the suite the repo's quality check runs (`adws/adw_modules/quality.py`):
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`
2. While building, run the focused files:
   `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_agentsh_guard.py tests/test_agent_pi.py -q -p no:cacheprovider`
3. Check by reading:
   - `git diff c495eb6 -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_sssf.yaml` shows only the `rotation` block and its comment.
   - `git diff c495eb6 -- .claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` shows only the `except` clauses around `json.loads`, with the `startswith("{")` check that re-raises.
   - `agent_cc.py`, `sandbox.py`, `rules.py`, `sssf_refuse.py` and `sssf_rules.ts` are unchanged.
   - In `agentsh_guard.py`, only `store_notes` changed.
   - In `config.md`, the sizing says the combined note can fire at a sustained peak and calls the audit rate an estimate. The pi-loop sentences say the skip is only for lines that don't start with `{`.
   - Nothing under `adws/`, `specs/` or any other protected path changed.

## Out of scope

These are old holes this change leaves open. A reviewer files them as old.

- **Shrinking the store file** once rows are deleted (`VACUUM`). It needs the service stopped and stays the manual step in `config.md`.
- **Pruning while a session is listed.** A chain that keeps a session open at every check prunes nothing until it pauses, and the store and the rotated audit files grow until then. The audit size cap still holds.
- **The combined note firing at a sustained peak.** It is advice. The store's size is set by `KEEP_SECONDS` and the event rate.
- **Keeping fewer allowed events through a fork patch.** `exclude_types` applies only to the OTEL and Watchtower exports.
- **Compressing rotated audit files, and making `max_age_days` work.** Both are fork patches.
- **A rotation racing the prune.** agentsh can rename a recent `.1` onto an old `.2` between the prune's `lstat` and its `unlink`, so a recent rotated file is deleted. This costs nothing: SSSF never reads the log, rotation runs only while agentsh writes events, and the prune runs only with no session listed.
- **The fork race in `rules.stop_tree`**, where a process forked between `ps` and the kill survives. Documented only; agentsh's policy covers the commands it denies.
- **The partial-line hole on the harness's stdout.** This includes a partial `[[[…` or long-integer prefix that joins the next real announcement into a line that doesn't start with `{`, which is then skipped. The agent already had this with `x` + announcement at the baseline, so it adds no capability. Documented only.
- **A real pi line that fails to parse** with `JSONDecodeError`. It is skipped, as at the baseline. pi prints its events with `JSON.stringify`, so its own lines don't raise it.
- **A node pi whose own `JSON.stringify` overflows** (depth past about 4,458). It behaves the same at base and head.
- **Invalid UTF-8 on pi's local pipe** under `local_guard: text`. It stays fail-closed, and its fix is in `sandbox.Local.start`. Decoding with `errors="replace"` there is out.
- **pi's policy**, and every other refusal and stop in the guard.

## Notes for the builder

- **Loopback HTTP may fail.** If this build runs under agentsh, loopback HTTP may be denied, and the HTTP-backed prune tests (`SessionsApi`) then fail with `URLError` on the baseline too. Run c535b28d's reviewer saw this. Report it plainly rather than changing the tests to dodge it.
- **Shared helpers.** `_fake_pi`, `_turn`, `_real_forwarder`, `gone` and `_child_gone_and_quiet` are already imported or defined in `test_agentsh_guard.py`.
- **Building the long lines.** The deep and long lines are about 24 KB to 200 KB. `LOOP_PI` reads them from a file a line at a time, which handles that size.

## For the engineer

These paths are protected, so the builder must not touch them:

1. **Update the hub's copies first.** After the merge, update the hub's own `adws/adw_modules/agentsh_guard.py`, `adws/adw_modules/agent_pi.py` and `adws/adw_modules/agentsh_sssf.yaml` to the skill's, with the usual "Update the hub to skill <sha>" step. Do this **before** the reinstall: the hub's `check_machine` renders the config from its own `adws/adw_modules/agentsh_sssf.yaml`, so a reinstalled `/etc/agentsh/sssf.yaml` with the rotation keys reads as "differs" until the hub copy has them.
2. **Reinstall.** Run `agentsh_install.py` and the sudo lines it prints. They cover the prune script, the service unit and `/etc/agentsh/sssf.yaml`. Then run `sudo systemctl daemon-reload` if it prints it, and `sudo systemctl restart agentsh`. The policies don't change.
3. **Check the first prune.** The next due prune deletes everything older than 6 hours, in batches, and the old rotated audit files. Check `/var/lib/sssf-agentsh-prune/status.json` for `deleted`, `store_bytes` and `audit_bytes`, and that a later run's poll is no slower. Record the measured audit rate, since the spec's is an estimate.
4. **Record the run** in `specs/tasks/guard-agentsh-retention.md` and the design doc, as usual.

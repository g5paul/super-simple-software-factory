# Keep 6 hours of agentsh's events and audit log, prune on the hour, and finish run 361de0f2's loop fix

Start from `main`, which has run 361de0f2 merged as 8f12f41. The hub session found problems 1 to 3 on 2026-11-11, two hours after the event-store rollout. Problems 4 and 5 come from run 361de0f2's bypass review, `adws/adw_data/sessions/361de0f2/context_handoff/bypass_review_1.md`. A first try at this spec, run c535b28d, found a bypass in the fix for problem 4, `adws/adw_data/sessions/c535b28d/context_handoff/bypass_review_1.md`; problem 4 and its change now say how to avoid it.

## The problem

1. **Two days of events is far more than anything reads, and the store grows to tens of GB.** `agentsh_prune.py` keeps `KEEP_SECONDS = 2 * 24 * 3600`. The store was 135,968 bytes at 08:10 and 2,778,925,800 bytes at 10:10, about 1.4 GB an hour while chains run. At that rate 2 days levels off between 30 and 67 GB, depending on how many hours runs go, and `check_machine`'s 10 GB note fires after about 7 hours of runs. Nothing reads an event after its call ends: the guard's poll reads only its own session, the prune skips while any session is listed, and the audit log, `/var/lib/agentsh/audit.jsonl`, keeps its own copy. The longest phase in the hub's trace is about 23 minutes. The fork has no setting that keeps fewer allowed events in SQLite; `exclude_types` applies only to the OTEL and Watchtower exports.
2. **The audit log has no time limit and nothing in SSSF sets its size.** agentsh also writes every event to `/var/lib/agentsh/audit.jsonl`. The fork's `internal/store/jsonl/jsonl.go` rotates it by size only: at `audit.rotation.max_size_mb` it renames the file to `.1`, shifts `.1` to `.2` and so on, and overwrites the last of `max_backups`. `agentsh_sssf.yaml` sets neither, so `internal/config/config.go`'s defaults apply, 500 MB and 10 backups, up to about 5.5 GB in the folder beside the store. The config parses `max_age_days` and `compress` and never uses them, so no setting removes the log by age. SSSF never reads the audit log. The engineer can't read `/var/lib/agentsh/` without sudo, so `check_machine` can't see the log's size either.
3. **A due check one second short waits another 10 minutes.** `PRUNE_EVERY = 3600`, and the timer fires every 10 minutes. The 10:10 check found the last prune 3,599 seconds old, so it was "not due", and the prune ran at the next check. Prunes come about every 70 minutes, not every 60.

4. **Two kinds of line still end pi's read loop.** Run 361de0f2 made pi's loop skip a line that isn't a JSON object, but `agent_pi.run` catches only `json.JSONDecodeError` around `json.loads`. An integer of more than 4,300 digits raises `ValueError` from Python's integer string limit, and a deeply nested array raises `RecursionError`. Either way pi is stopped first, so it fails closed, but the run ends where the spec promised it would skip the line. Skipping every such line is not safe: run c535b28d's review showed that a real pi event can be made too deep to parse by arguments the model chooses, such as `bash` called with `{"command":"git push","pad":[[[…]]]}`. pi's `bashSchema` doesn't forbid the extra key, and pi prints both the `tool_execution_start` and the `message_end` lines with the pad, so skipping them lets the call run unchecked under a `stop` rule. A bun-compiled pi can print a line over 10,000 levels deep, past CPython's JSON limit. Invalid UTF-8 on the local pipe, in text mode, raises `UnicodeDecodeError` from the read itself, also with pi stopped.
5. **Two limits of the stream check that `config.md` doesn't list.** Both apply to every harness and matter most under `local_guard: text`. A process the agent forks between `rules.stop_tree`'s `ps` listing and the kill is reparented and survives the stop; the reviewer saw the forbidden command's own shell outlive the stop when it started 10 to 20 ms after the announcement. And bytes with no newline written to the harness's stdout join its next event line, which then fails to parse and is skipped, so a real announcement goes unchecked.

## What should change

1. **`KEEP_SECONDS` is 6 hours.** The module docstring, `config.md`'s event-store paragraph ("levels off at about 2 days of events") and any other text that says 2 days say 6 hours.
2. **A prune is due when the last is at least `PRUNE_EVERY` minus one timer period's slack old**, so a check that lands a second early still prunes. The spec says the slack and why it can't make prunes come faster than every 50 minutes.
3. **The audit log keeps about 6 hours too, under a size cap.** `agentsh_sssf.yaml` sets `audit.rotation.max_size_mb` and `max_backups`, and the spec picks values that cap the log's files well under the store's 10 GB note, from the hub's measured event rate. A due prune also deletes each rotated file, `audit.jsonl.<N>`, last modified more than `KEEP_SECONDS` ago. It never touches the live `audit.jsonl`, follows no link, and deletes only names of that form in that folder. It does this under the same skip as the store: never while the service lists a session or doesn't answer. The status file adds `audit_bytes`, the live log and its rotated files together, and `check_machine`'s 10 GB note counts the store and the audit files together.
4. **pi's loop catches `ValueError` and `RecursionError` around `json.loads` and skips the line only when it doesn't start with `{`** after stripping. pi's events are always objects, so a line that starts with `{` and can't be parsed still leaves the loop with pi stopped, as at the baseline. The UTF-8 case stays fail-closed: the fix for it is in `sandbox.Local.start`, outside this run.
5. **`config.md`** lists problem 5's two limits beside the forged-event limit it already gives, says pi stops on invalid UTF-8 in text mode, and says the store and the audit log level off at about 6 hours of events, with the hub's measured rate, that the rotation settings cap the log's size, and that `max_age_days` does nothing in this build.

## The bar

The earlier bars stand:

- The prune still deletes nothing and runs no `ANALYZE` while the service lists a session or doesn't answer, so no event of a live session is ever deleted, however long the call runs.
- It still deletes only rows older than `KEEP_SECONDS`, in batches of 5,000, and its status file keeps its keys.
- Its exit codes stay as run 0463a6c3 left them.
- Every refusal and stop in the guard stays as it was. The guard's only change is the 10 GB note counting `audit_bytes`, and an old status file without it reads as the store alone.
- `check_machine`'s notes keep their thresholds: 3 hours for a stale prune, 10 GB.
- The rendered config changes only the two rotation keys.
- A line on pi's stdout holding an integer of 5,000 digits, or an array nested 100,000 deep, is skipped, and a later forbidden call gives the same `RuleBreach` as at the baseline.
- A line that starts with `{` and raises `ValueError` or `RecursionError`, such as a `tool_execution_start` whose `git push` call carries an argument nested 12,000 deep, stops pi as at the baseline. It is never skipped.
- Claude Code's loop doesn't change.

Any change beyond the five above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests: a row 6 hours and one second old is deleted and one 6 hours less one second old is kept; a check 3,599 seconds after the last prune prunes, and one 2,999 seconds after does not; a listed session still skips with nothing deleted; a rotated audit file past 6 hours is deleted and a newer one, the live log, a link named `audit.jsonl.3` and a file named `audit.jsonl.bak` are kept; `audit_bytes` in the status file; the 10 GB note on store and audit bytes together, and on an old status file; the rendered config's rotation keys; with a fake pi, the long-integer and deep-array lines followed by a forbidden call, and the real line shapes of a `bash` call to `git push` with a pad nested 12,000 deep, which must stop pi. The tests never touch `/var/lib/agentsh` or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The prune script and `sssf.yaml` change, so after the merge the engineer reinstalls with `agentsh_install.py` and the sudo lines it prints, and restarts the service; the policies don't change. The next prune after that deletes everything older than 6 hours at once, in batches, and the old rotated audit files. The hub session then checks the status file's `deleted`, `store_bytes` and `audit_bytes`, and that a later run's poll is no slower.

## Out of scope

Shrinking the file once rows are deleted (`VACUUM`), which needs the service stopped and is the manual step `config.md` already gives. A prune while a session is listed. Keeping fewer allowed events through a fork patch. Compressing rotated audit files, which this build can't do. Making `max_age_days` work, which is a fork patch. Closing the fork race in `stop_tree` or the partial-line hole, which agentsh's policy covers for the commands it denies. Decoding pi's pipe with `errors="replace"`. pi's policy.

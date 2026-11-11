# Keep 6 hours of agentsh's events and audit log, and prune on the hour

Start from `main` once run G has merged. The hub session found these on 2026-11-11, two hours after the event-store rollout.

## The problem

1. **Two days of events is far more than anything reads, and the store grows to tens of GB.** `agentsh_prune.py` keeps `KEEP_SECONDS = 2 * 24 * 3600`. The store was 135,968 bytes at 08:10 and 2,778,925,800 bytes at 10:10, about 1.4 GB an hour while chains run. At that rate 2 days levels off between 30 and 67 GB, depending on how many hours runs go, and `check_machine`'s 10 GB note fires after about 7 hours of runs. Nothing reads an event after its call ends: the guard's poll reads only its own session, the prune skips while any session is listed, and the audit log, `/var/lib/agentsh/audit.jsonl`, keeps its own copy. The longest phase in the hub's trace is about 23 minutes. The fork has no setting that keeps fewer allowed events in SQLite; `exclude_types` applies only to the OTEL and Watchtower exports.
2. **The audit log has no time limit and nothing in SSSF sets its size.** agentsh also writes every event to `/var/lib/agentsh/audit.jsonl`. The fork's `internal/store/jsonl/jsonl.go` rotates it by size only: at `audit.rotation.max_size_mb` it renames the file to `.1`, shifts `.1` to `.2` and so on, and overwrites the last of `max_backups`. `agentsh_sssf.yaml` sets neither, so `internal/config/config.go`'s defaults apply, 500 MB and 10 backups, up to about 5.5 GB in the folder beside the store. The config parses `max_age_days` and `compress` and never uses them, so no setting removes the log by age. SSSF never reads the audit log. The engineer can't read `/var/lib/agentsh/` without sudo, so `check_machine` can't see the log's size either.
3. **A due check one second short waits another 10 minutes.** `PRUNE_EVERY = 3600`, and the timer fires every 10 minutes. The 10:10 check found the last prune 3,599 seconds old, so it was "not due", and the prune ran at the next check. Prunes come about every 70 minutes, not every 60.

## What should change

1. **`KEEP_SECONDS` is 6 hours.** The module docstring, `config.md`'s event-store paragraph ("levels off at about 2 days of events") and any other text that says 2 days say 6 hours.
2. **A prune is due when the last is at least `PRUNE_EVERY` minus one timer period's slack old**, so a check that lands a second early still prunes. The spec says the slack and why it can't make prunes come faster than every 50 minutes.
3. **The audit log keeps about 6 hours too, under a size cap.** `agentsh_sssf.yaml` sets `audit.rotation.max_size_mb` and `max_backups`, and the spec picks values that cap the log's files well under the store's 10 GB note, from the hub's measured event rate. A due prune also deletes each rotated file, `audit.jsonl.<N>`, last modified more than `KEEP_SECONDS` ago. It never touches the live `audit.jsonl`, follows no link, and deletes only names of that form in that folder. It does this under the same skip as the store: never while the service lists a session or doesn't answer. The status file adds `audit_bytes`, the live log and its rotated files together, and `check_machine`'s 10 GB note counts the store and the audit files together.
4. **`config.md`** says the store and the audit log level off at about 6 hours of events, with the hub's measured rate, that the rotation settings cap the log's size, and that `max_age_days` does nothing in this build.

## The bar

The earlier bars stand:

- The prune still deletes nothing and runs no `ANALYZE` while the service lists a session or doesn't answer, so no event of a live session is ever deleted, however long the call runs.
- It still deletes only rows older than `KEEP_SECONDS`, in batches of 5,000, and its status file keeps its keys.
- Its exit codes stay as run 0463a6c3 left them.
- Every refusal and stop in the guard stays as it was. The guard's only change is the 10 GB note counting `audit_bytes`, and an old status file without it reads as the store alone.
- `check_machine`'s notes keep their thresholds: 3 hours for a stale prune, 10 GB.
- The rendered config changes only the two rotation keys.

Any change beyond the four above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests: a row 6 hours and one second old is deleted and one 6 hours less one second old is kept; a check 3,599 seconds after the last prune prunes, and one 2,999 seconds after does not; a listed session still skips with nothing deleted; a rotated audit file past 6 hours is deleted and a newer one, the live log, a link named `audit.jsonl.3` and a file named `audit.jsonl.bak` are kept; `audit_bytes` in the status file; the 10 GB note on store and audit bytes together, and on an old status file; the rendered config's rotation keys. The tests never touch `/var/lib/agentsh` or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The prune script and `sssf.yaml` change, so after the merge the engineer reinstalls with `agentsh_install.py` and the sudo lines it prints, and restarts the service; the policies don't change. The next prune after that deletes everything older than 6 hours at once, in batches, and the old rotated audit files. The hub session then checks the status file's `deleted`, `store_bytes` and `audit_bytes`, and that a later run's poll is no slower.

## Out of scope

Shrinking the file once rows are deleted (`VACUUM`), which needs the service stopped and is the manual step `config.md` already gives. A prune while a session is listed. Keeping fewer allowed events through a fork patch. Compressing rotated audit files, which this build can't do. Making `max_age_days` work, which is a fork patch. pi's policy.

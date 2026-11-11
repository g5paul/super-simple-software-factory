# Show agentsh's warnings when they happen, and stop the dropped events

Start from `main`, which has the event-store run, 149e2bda, merged as 4cf7ab3, and its rollout recorded in 06a552a. The hub session found what follows on 2026-11-11, on fork build 05b3777f, after the rollout. It is Next item 16 in `docs/designs/agentsh-local-sandbox.md`.

## The problem

1. **The store warnings reach only the trace, and late.** `check_machine` returns its notes (no prune status, a stale prune, a store over 10 GB). `start()` adds them to `process.trace_notes`, and they are written only in the `agentsh_denials` event at the end of the agent call. Nothing prints them. An engineer watching the console or the dashboard's phase list doesn't see them, and sees nothing at all until the call ends.
2. **Nothing records how long the poll takes.** On the 32.5 GB store the poll's query took 12.9 seconds cold. `_get` gives up at 5 seconds, and three failures stop the agent. Nothing showed the query slowing down before it started stopping runs. After the rollout a full page of 5,000 rows took 0.04 seconds, and one small read-only call, run 05f96f0f in sssf-sandbox, already read more than 5,000 events.
3. **agentsh drops events, and SSSF never counts them.** The fork's SQLite store queues each event on a channel of 4,096 (`defaultChannelSize` in `internal/store/sqlite/sqlite.go`) and writes them in batches of 64 every 50 ms. When the channel is full it logs "sqlite: event channel full, dropping event" with the event's id and type, and drops it. agentsh still enforces the decision, but the event never reaches the store, so SSSF's poll can't count it. On 2026-11-11 the journal logged 33,723 drops before the rollout, 410 of them `ptrace_execve`, the type the three-refusal stop counts. After the rollout, on a 136 KB store, run 05f96f0f lost 34 `ptrace_file` events in its first two seconds, while Claude Code started. So the drops come from bursts, not from the store's size.
4. **A prune that records an error still exits 0.** `agentsh_prune.py`'s `main()` returns 0 when the status file holds an `error`, so systemd marks the unit successful and `systemctl --failed` doesn't list it. The stale-prune note shows it 3 hours later.

The fork's config already has the settings for problem 3: `audit.storage.channel_size` and `audit.storage.batch_size` in `internal/config/config.go`, passed to the store in `internal/server/server.go`. `agentsh_sssf.yaml` sets neither, so the defaults apply. The planner confirms both keys are in the installed build, 05b3777f, before relying on them.

## What should change

For runs under `local_guard: agentsh` only:

1. **The store notes show when the agent starts.** Each note `check_machine` returns is printed to the run's console, and logged to the trace as its own event, before the agent starts. The end-of-call event keeps them as now.
2. **Each call records its polls.** The end-of-call `agentsh_denials` event adds the number of polls, the pages read and the slowest poll in seconds. When the slowest poll took over 1 second, the call adds a note naming the time and the 5-second limit.
3. **Each call counts agentsh's drops.** When the call ends, SSSF reads agentsh's journal for the call's window, `journalctl -u agentsh --since <start> --until <end> -o cat`, as the engineer's user, with a timeout, and counts the "event channel full" lines by event type. Any drop adds a note with the counts. A dropped `ptrace_execve` gets its own note saying a denied exec may have gone uncounted, and that agentsh still enforced it. A journal SSSF can't read, or a `journalctl` that times out or fails, adds a note saying so. None of this refuses or stops a run.
4. **The rendered config raises the store's buffer.** `agentsh_sssf.yaml` sets `audit.storage.channel_size` and `audit.storage.batch_size`. The spec picks the values from the fork's source and the bursts above, and states the memory a full channel can hold, from what one queued event carries. `agentsh_install.py`'s diff shows the change, and the engineer's reinstall applies it.
5. **The prune exits non-zero when it records an error.** A skip still exits 0. A recorded `error` exits 1, so systemd marks the unit failed. A crash exits non-zero as now.
6. **`config.md`**, in "Running local agents under agentsh", says where the notes appear, what the poll and drop notes mean, and the buffer settings. `docs/guide/troubleshooting.md` replaces its line that the store warnings are in the trace only, and adds `systemctl --failed` to its checks.

## The bar

The earlier bars stand. Under `local_guard: agentsh`:

- Nothing that passed at the baseline refuses or stops: every new note is a note.
- Every refusal and stop at the baseline stays as it was, the three-refusal stop and the three-failed-poll stop included.
- The journal read runs as the engineer's user, reads only the `agentsh` unit's lines in the call's window, and is bounded by a timeout. Its notes carry event types and counts, never an event id, path or other content.
- The rendered config changes only `audit.storage.channel_size` and `audit.storage.batch_size`.
- The prune exits 0 on a skip and 1 on a recorded error, and its status file is unchanged.
- Runs under `local_guard: text` don't change.

Any change to the guard beyond the six above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests: the notes are printed and logged before a fake agent starts; the end-of-call event has the poll count, pages and slowest poll, and a note when a fake `_get` takes over 1 second; the drop count from a fake journal reader, by type, with the `ptrace_execve` note; the notes for an unreadable journal, a timeout and a failed `journalctl`; the rendered config has the two keys with the spec's values; the prune's exit code on a skip, a recorded error and a clean run. The tests never call the real `journalctl`, touch `/var/lib/agentsh`, or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The runner uses its own checkout's module, so the new notes reach it only after the merge. The builder runs the suite from a copy outside the repo, as earlier runs did.

After the merge, the engineer reinstalls both repos' policies, which changes `sssf.yaml` and the prune script, and restarts the service. The hub session then runs one small call in sssf-sandbox and checks that the console shows the notes, that the trace has the poll figures, and that the journal's drop count for the call is 0 or lower than run 05f96f0f's 34.

## Out of scope

The fork's code: a writer that blocks instead of dropping, or that never drops a `ptrace_execve`, would be a fork patch, and the patches are unpublished. A drop of a `ptrace_execve` refusing the run. Alerts outside SSSF, such as email or desktop notifications. Run and phase durations against their usual range, which is a separate change outside the guard. pi's policy.

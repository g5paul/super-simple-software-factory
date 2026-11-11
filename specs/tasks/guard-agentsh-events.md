# Keep agentsh's event store small, and poll it by session

Start from `main`, which has the hardlink run, b684482d, merged as cc6a0d1. The hub session found the problem on 2026-11-11, on build 05b3777f, after run b684482d's planner was stopped 31 seconds in.

## The problem

1. **The poll query slows as the store grows.** `LiveEvents._poll` asks `/api/v1/events/search` for one session's events with `type=ptrace_execve`. agentsh's `events` table has an index on `(session_id, ts_unix_ns)` and one on `(type, ts_unix_ns)`, and none on both. Without statistics, SQLite took the type index, so the query read every `ptrace_execve` event the service had ever stored and kept this session's. With the store at 32.5 GB, the first query after a reboot took 12.9 seconds and a repeat 1.0 second; the same query without `type` took 0.005 seconds. `_get` gives up after 5 seconds, and three failed polls in a row stop the agent with "the agentsh service stopped answering event polls". After the engineer ran `ANALYZE` on the store, the typed query took 0.01 seconds. Nothing runs `ANALYZE` again as the store grows.
2. **The store has no retention.** `/var/lib/agentsh/events.db` reached 32.5 GB three days after the service was installed on 2026-11-08. agentsh's config has no retention setting for it and no way to skip storing allowed events; its audit options are rotation, the SQLite path, batching, a webhook, integrity, encryption and exports. The audit log rotates at 512 MB and keeps 10 files, about two hours of runs on 2026-11-10.
3. **Nobody can see the size.** `/var/lib/agentsh` is mode 0750, owned by `agentsh`, so the engineer's user and SSSF can't stat the store.
4. **The service drops events when the store can't keep up.** The journal logged 105,197 "sqlite: event channel full, dropping event" warnings between 2026-11-10 and 2026-11-11, in bursts during runs. A dropped `ptrace_execve` denial isn't counted toward `MAX_REFUSALS`. agentsh still enforces the denial; only SSSF's count misses it. agentsh opens the store with no busy timeout, so a writer holding the store's lock makes the service's inserts fail too.

5. **The hardlink run's one-time steps miss what the walk refuses.** Run b684482d's review found three broken promises, all in `config.md`'s one-time steps:
   - the `find` for unreadable folders matches only mode 000, and the walk also refuses a folder it can't list or search, such as mode 0300;
   - the length check, `awk 'length >= 4096'`, counts characters under a UTF-8 locale, while `ENAMETOOLONG` counts bytes, and the refusal message gives characters too;
   - the steps name one over-long chain in the hub, where the hub held about 90 over-long paths in seven runs' review scratch. The hub session removed them on 2026-11-11, so the inventory is out of date as well as short.

## What should change

For runs under `local_guard: agentsh` only:

1. **Poll by session only.** `_poll` asks for the session's events with no `type`, and `_take` keeps the `ptrace_execve` denials as it does now. The session index answers that query whatever the store's size. The final read already does this.
2. **A prune that runs as root.** `agentsh_install.py` renders a prune script and a systemd service and timer, and prints their `sudo install` and `sudo systemctl enable --now` lines with the others. The script is Python from the standard library, installed root-owned outside any repo. Each hour it:
   - skips the hour when the service reports an active session, so it never holds the store's lock during a run, and records why;
   - deletes events older than 2 days in batches;
   - runs `ANALYZE` once a week;
   - writes a status file anyone can read, outside `/var/lib/agentsh`: the store's size in bytes, the time of the last prune and of the last `ANALYZE`, and the rows it deleted.

   The spec says what the batch size and the lock wait are, and why. Deleting rows doesn't shrink the file; freed pages are reused, so the file stops growing at about two days of events. The one-time step below reclaims the space already used.
3. **`check_machine` warns on the store.** It reads the status file. It adds a trace note, and doesn't refuse, when the file is missing, when the last prune is more than 3 hours old, or when the store is over 10 GB. The note says what to run. A refusal would stop runs over housekeeping, which the bar doesn't ask for.
4. **The one-time steps find what the walk refuses.** The `find` matches a folder that isn't both readable and searchable, `\( ! -perm -u+r -o ! -perm -u+x \)`; the length check runs with `LC_ALL=C`; the refusal message gives the length in bytes; and the steps drop the dated inventory and say to run the two commands instead.
5. **`config.md`, in "Running local agents under agentsh"**, says what the prune does, where its status is, what the warnings mean, and that a dropped event isn't counted toward `MAX_REFUSALS` though agentsh still enforces it. `install.md` adds the timer to the install steps.

## The bar

The earlier bars stand. Under `local_guard: agentsh`:

- The poll's query names the session and no type, and a run still counts each `ptrace_execve` denial it reads, as before.
- The prune never deletes an event from the last 2 days, and never runs while the service reports an active session.
- The prune script, its unit files and its status file are written only by root, outside every path an agent may write, and the status file holds no event content.
- `check_machine` warns, not refuses, on a missing or stale status file or a large store.
- The one-time steps' two commands list every path the walk refuses for being unreadable or too long, and nothing else.
- Runs under `local_guard: text` don't change.

Any change to the guard beyond the five above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests: the poll's query has no `type`; `_take` still counts only `ptrace_execve` denials from a mixed page; the prune deletes only rows older than the cutoff, in batches, on a temp SQLite file with agentsh's schema; it skips when a fake API reports an active session; the status file has the four fields; `check_machine`'s three warnings, with a temp status file; the refusal message gives bytes for a path of non-ASCII names. The tests never touch `/var/lib/agentsh` or the real service.

After the merge, the engineer, with no run going:
1. reclaims the space: stop the service, move `events.db` and its `-wal` and `-shm` files aside, start the service, and delete the moved files once a run works;
2. reinstalls both repos' policies, which installs the timer, and restarts the service.

The hub session then checks that the timer ran, that the status file shows the size, and that a run's poll query is fast.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The runner uses its own checkout's module, so the new poll reaches it only after the merge. The builder runs the suite from a copy outside the repo, as earlier runs did.

## Out of scope

The fork's code: a composite index or a retention setting in agentsh would be a fork patch, and the patches are unpublished. The audit log's rotation. Storing fewer allowed file events. pi's policy.

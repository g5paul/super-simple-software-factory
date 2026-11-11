# Keep agentsh's event store small, and poll it by session

Request: `specs/tasks/guard-agentsh-events.md`. Five changes, for runs under `local_guard: agentsh` only: poll by session, an hourly root prune with a status file, `check_machine` warnings, corrected one-time steps, and docs.

## Baseline

`main` at `47dca0b`, which holds the hardlink run b684482d (merged as cc6a0d1). Every finding is judged against `47dca0b`.

## Threat model

Agents run as the engineer's own user inside `agentsh wrap`, under the generated default-deny policy. An agent can write its workspace, the sessions folder, the agent cache, the temp folders and its own config folder. It can't reach agentsh's HTTP API: `sssf-no-agentsh-api` denies 127.0.0.1:18080. Nothing at this level stops:

- a process outside the wrap, running as the engineer, from asking the API for sessions or events. Upstream finding #29 is still open;
- agentsh dropping an event when its store can't keep up. agentsh still enforces the denial, but SSSF's count misses it. This change makes drops rarer by keeping the store small, and doesn't close them;
- the window between the prune's session check and its next batch (one batch, tens of milliseconds), when a run that just started can lose an insert to the prune's lock;
- root. The prune runs as root, as the service's install already does.

The new files are written by `sudo install` and by a root systemd unit, at paths no policy rule lets an agent write and that the engineer's user can't write: `/usr/local/lib/sssf-agentsh/`, `/etc/systemd/system/`, `/var/lib/sssf-agentsh-prune/`. The status file holds counts and timestamps only, never event content, because anyone can read it.

## Bar

Under `local_guard: agentsh`:

- The poll's query names the session and no `type`. A run still counts each `ptrace_execve` denial it reads, exactly as `_take` does at the baseline.
- The prune never deletes an event from the last 2 days, and never deletes or runs `ANALYZE` while the service reports a session, or when it can't tell.
- The prune script, its unit files and its status file are written only by root, outside every path an agent may write. The status file holds no event content.
- `check_machine` warns, as trace notes, and never refuses, on a missing or unreadable status file, a last prune older than 3 hours, or a store over 10 GB. Every refusal it made at the baseline stays as it was.
- The one-time steps' two commands list every path the walk refuses for being unreadable or too long, and nothing else.
- As at the baseline: no agent file kept, no agent code run, no engineer file deleted or overwritten.
- Runs under `local_guard: text` don't change.

Any change to the guard beyond these is a finding, and so is a `config.md` line that promises more than the tests show.

## Out of scope

These stay open, as old holes:

- A composite index or a retention setting in agentsh. Either would be a fork patch, and the patches are unpublished.
- agentsh's lack of a busy timeout, and its dropped events ("event channel full"). A dropped `ptrace_execve` denial still isn't counted toward `MAX_REFUSALS`.
- The audit log's rotation (512 MB × 10 files).
- Storing fewer allowed file events.
- pi's policy.
- `VACUUM`. The file doesn't shrink. Freed pages are reused, so it levels off at about 2 days of events. The engineer's one-time move-aside reclaims what is already used.
- A session a signal leaves open. It is already a known limit, and it now also makes the prune skip every hour until the service restarts. The stale-prune warning says so.
- Upstream finding #29.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: the poll, the byte count, the warnings.
- `.claude/skills/sssf/scripts/agentsh_prune.py`: **new**. The prune, standard library only.
- `.claude/skills/sssf/scripts/agentsh_install.py`: render and print the prune files.
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: the fake service's poll/final split, plus the new tests.
- `.claude/skills/sssf/references/config.md`: the "Running local agents under agentsh" section.
- `docs/guide/install.md`: the "Turn on agentsh, optional" steps.
- `docs/guide/troubleshooting.md`: the event-polls paragraph at line 50.

Do not edit `adws/adw_modules/` (the hub's own copy, which is protected), `docs/open.md`, `docs/designs/agentsh-local-sandbox.md` or `app_docs/`. The hub session updates the design notes and open.md after the merge.

## Steps

### 1. Poll by session only (`agentsh_guard.py`)

- `AgentshProcess._poll`: `rows = self._pages(self._offset, {})`. Change nothing else. `_take` stays byte-for-byte the same. The poll now sees file and network denials too, and traces them in `other_denials`. `_seen` already dedups by id, so the final read doesn't add them twice, and the totals don't change.
- `self._offset` now counts every event of the session, not only `ptrace_execve` ones. That is correct for an untyped query.

### 2. Bytes in the PATH_MAX refusal (`agentsh_guard.py`)

- `_too_long(path)`: give `len(os.fsencode(path))` with the word "bytes", e.g. `({n} bytes, at or over PATH_MAX, 4,096 bytes on Linux)`. Keep `It starts: {path[:200]}` and the rest of the message as they are.

### 3. The prune script (`.claude/skills/sssf/scripts/agentsh_prune.py`, new)

It uses the standard library only and has no PEP 723 block. It runs as `/usr/bin/python3 -I -S`. Module constants:

```python
DB = "/var/lib/agentsh/events.db"            # agentsh_sssf.yaml audit.storage.sqlite_path
API = "http://127.0.0.1:18080"               # agentsh_guard.API
STATUS = "/var/lib/sssf-agentsh-prune/status.json"   # agentsh_guard.PRUNE_STATUS
KEEP_SECONDS = 2 * 24 * 3600
ANALYZE_SECONDS = 7 * 24 * 3600
BATCH = 5000
LOCK_WAIT = 5.0            # sqlite3.connect(timeout=...)
PAUSE = 0.05               # seconds between batches
```

Expose `prune(db=DB, api=API, status=STATUS, now=None) -> dict` and have `main()` call it with no arguments. Under `if __name__ == "__main__":`, return 0 even when the prune skips or records an error, so the timer doesn't flag a skipped hour as failed. A crash still exits non-zero.

What it does, in order:

1. Read the previous status, if the file holds valid JSON, to carry `last_prune` and `last_analyze` forward.
2. `sessions(api)`: GET `{api}/api/v1/sessions` through `urllib.request.build_opener(urllib.request.ProxyHandler({}))` with `timeout=5`. The body can be a JSON list, `{"sessions": [...]}`, or `null`, which means an empty list. Return the count. Raise on anything else, or on a non-200 reply. Count every listed session as active: SSSF deletes each session at the end of its call, so the list is empty between runs.
3. If the count is above 0, or the API raised, write the status with `skipped` set to `"the service reports N active session(s)"` or `"the service didn't answer its sessions list (<ErrorClass>)"`. `last_prune` and `deleted` keep their earlier values. Return.
4. Connect with `sqlite3.connect(db, timeout=LOCK_WAIT, isolation_level=None)`. Never change `journal_mode`, and never `VACUUM`. Set `cutoff = int((now - KEEP_SECONDS) * 1e9)`. Then loop:
   - re-check `sessions(api)`. If it's above 0 or raises, stop, and set `skipped` with the reason;
   - `BEGIN IMMEDIATE`, then `DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY rowid LIMIT ?) AND ts_unix_ns < ?` with `(BATCH, cutoff)`, then `COMMIT`. Add `cursor.rowcount` to `deleted`;
   - stop when a batch deleted fewer than `BATCH` rows; otherwise `time.sleep(PAUSE)`.

   Why it walks by rowid: no index covers `ts_unix_ns` alone, so `WHERE ts_unix_ns < ?` with no rowid bound scans the whole table on its last batch. That is about two days of events, tens of GB, every hour. Walking from the oldest rowid reads `BATCH` rows per batch. A row older than the cutoff that sits behind a newer row is deleted in a later hour, once the rows ahead of it age past the cutoff. The `ts_unix_ns < ?` test is what keeps the last 2 days, whatever the rowid order.
5. If `last_analyze` is missing or older than `ANALYZE_SECONDS`, and the loop didn't stop on a session: re-check `sessions(api)`, then run `PRAGMA analysis_limit=1000` and `ANALYZE`, and set `last_analyze = now`. `analysis_limit` keeps the write lock short on a large store. Older SQLite ignores the pragma.
6. When the delete loop ended without a skip, set `last_prune = now` and record that run's `deleted`.
7. `sqlite3.OperationalError` or `DatabaseError`: roll back if a transaction is open, and set `error` to `f"{type(e).__name__}: {e}"`. SQLite's messages, such as "database is locked" or "no such column: rowid", carry no row content. `last_prune` keeps its earlier value.
8. After the connection closes, `fix_owner(db)`: for `db + "-wal"` and `db + "-shm"`, if the file exists and its uid differs from `db`'s, `os.chown` it to `db`'s uid and gid. This covers the case where root's connection made the file. Without it, the service, running as `agentsh`, couldn't open it.
9. `write_status(status, data)`: make the folder if it's missing (mode 0755), write a temp file in that folder with mode 0644, and `os.replace` it over the status file. The JSON holds exactly these keys:
   - `store_bytes`: the size of `events.db` plus `events.db-wal`, or `null` if `events.db` is missing;
   - `last_prune`, `last_analyze`, `last_run`: UTC ISO 8601 strings, or `null`;
   - `deleted`: an integer, the rows deleted by the last prune that completed;
   - `skipped`: the reason string, or `null`;
   - `error`: a string, or `null`.

   It holds nothing from an event: no ids, no session ids, no paths.

Why these numbers, for the spec's record:
- `BATCH = 5000`: agentsh opens the store with no busy timeout, so every insert it tries while the prune holds the write lock fails. 5,000 rows is a few MB of pages, tens of milliseconds per transaction, so a run that starts mid-prune loses at most one batch's window. It also stays well under one second.
- `LOCK_WAIT = 5.0`: how long the prune waits for the service's own write lock. Its batched inserts hold the lock for far less. The prune gives up after 5 s and tries again next hour, rather than queue behind a busy service.

### 4. The units and the install (`agentsh_install.py`)

Add constants for the three targets: `PRUNE_SCRIPT = "/usr/local/lib/sssf-agentsh/agentsh_prune.py"`, `PRUNE_SERVICE = "/etc/systemd/system/sssf-agentsh-prune.service"` and `PRUNE_TIMER = "/etc/systemd/system/sssf-agentsh-prune.timer"`.

The service unit:

```ini
[Unit]
Description=SSSF: prune agentsh's event store to 2 days
After=agentsh.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I -S /usr/local/lib/sssf-agentsh/agentsh_prune.py
StateDirectory=sssf-agentsh-prune
StateDirectoryMode=0755
UMask=0022
Nice=10
IOSchedulingClass=idle
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/agentsh /var/lib/sssf-agentsh-prune
```

The timer unit:

```ini
[Unit]
Description=SSSF: prune agentsh's event store each hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

- Add the three items to the install script's `items` list, through the same diff-and-`sudo install` loop. The script's text is `(SKILL / "scripts/agentsh_prune.py").read_text()`, so the hub's copy is what gets installed. For `PRUNE_SCRIPT` only, the install line takes `-D` (`sudo install -D -o root -g root -m 0644 <temp> <target>`) so that `/usr/local/lib/sssf-agentsh` is created. Leave the existing lines' form as it is.
- When any of the three is in `steps`, print `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer` after the install lines. The last printed line stays `sudo systemctl restart agentsh`.
- Update the module docstring to name the prune files.

### 5. The warnings (`agentsh_guard.py`)

- Add constants: `PRUNE_STATUS = "/var/lib/sssf-agentsh-prune/status.json"`, `PRUNE_STALE_SECONDS = 3 * 3600` and `STORE_WARN_BYTES = 10 * 10**9`.
- Add `store_notes(now=None) -> list[str]`. It never raises.
  - Missing, unreadable or not a JSON object: "agentsh's event store has no prune status at {PRUNE_STATUS}, so its size is unknown; install the prune timer with `just agentsh-install` and the sudo lines it prints".
  - `last_prune` null, unparseable, or more than 3 hours old: "agentsh's event store was last pruned {when or 'never'}{; last skipped: <skipped>}{; last error: <error>}; with no run going, run `sudo systemctl start sssf-agentsh-prune.service`. If it keeps skipping on an active session, a session may have been left open: with no run going, run `sudo systemctl restart agentsh`".
  - `store_bytes` over `STORE_WARN_BYTES`: "agentsh's event store is {GB:.1f} GB, over 10 GB; with no run going, stop the service, move /var/lib/agentsh/events.db and its -wal and -shm files aside, start it, and delete the moved files once a run works (see \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md)".
  - Print `skipped` and `error` with control characters escaped, as `command_text` does.
- `check_machine(guard) -> list[str]`: every check stays as it is. After the last one passes, `return store_notes()`. Update its docstring.
- `start`: `notes = check_machine(guard)`. After `process = AgentshProcess(...)`, add `process.trace_notes.extend(notes)`. They reach the trace through `close`'s existing `agentsh_denials` event. `session.ensure` ignores the return value, so leave `session.py` unchanged.

### 6. Tests (`.claude/skills/sssf/tests/test_agentsh_guard.py`)

Tell the poll from the final read without the `type` filter:
- In the `service` fixture, monkeypatch `agentsh_guard.AgentshProcess._get` with a wrapper. When `self._done.is_set()`, the call is the final read, so it adds `"_final": "1"` to the query, then calls the original. In `do_GET`, pop `_final` from `q` before appending to `queries`. Then use `final = "_final" in raw_q` where the fake used `"type" in q`.
- Rename the flags to match: `fail_typed` becomes `fail_polls`, `fail_untyped` becomes `fail_final`, `typed_delay` becomes `poll_delay`, and `hidden_from_typed` becomes `final_only`. Keep the per-request decision, which avoids shared state between threads. Keep the existing type filter in the fake (`rows = [e for e in rows if e.get("type") == q["type"]]` when `type` is present), so a typed poll would still behave differently and fail the new assertions.
- `test_only_a_named_exec_denial_counts_across_pages`: assert `all("type" not in q for q in service.queries)` and `all(q["session_id"] == "sess1" ...)`. Keep `counted == 2`, `len(other_denials) == 61`, and the check that a repeated page counts once.

New tests. None touches `/var/lib/agentsh`, `/var/lib/sssf-agentsh-prune` or the real service.
- **The machine fixture**: monkeypatch `agentsh_guard.PRUNE_STATUS` to a temp file holding a fresh status (`last_prune` now, `store_bytes` 1000), so existing tests see no notes.
- **Poll query**: one `_poll` on `bare(service)` sends `session_id=sess1` and no `type`.
- **A mixed page**: `ptrace_execve` denials, a `ptrace_file` deny, a `ptrace_execve` allow, an unnamed exec deny and a `net_connect` deny. Only the named `ptrace_execve` denials count, and the others land in `other_denials`.
- **The prune**: load `agentsh_prune.py` with `importlib`, as the `hub` fixture loads the install script. Build a temp SQLite file with what is known of agentsh's schema: `CREATE TABLE events (id TEXT PRIMARY KEY, session_id TEXT, type TEXT, ts_unix_ns INTEGER, data TEXT)`, plus `CREATE INDEX idx_events_session_ts ON events(session_id, ts_unix_ns)` and `CREATE INDEX idx_events_type_ts ON events(type, ts_unix_ns)`, in WAL mode. Serve sessions from a small `ThreadingHTTPServer`, as `Service` does.
  - Rows from 3 days ago, 2 days and 1 second ago, 2 days less 1 second ago, and now, with `BATCH` patched to 3 and more old rows than one batch. Only the rows older than the cutoff go, and the 2-days-less-1-second row stays. Make it more than one batch: count the `DELETE` statements with `conn.set_trace_callback`, or patch `BATCH` and check `deleted`.
  - An old row placed after a recent row in rowid order isn't deleted while it sits behind the recent row, and the recent row is never deleted.
  - The fake API reports one session: no row is deleted, and `skipped` names the active session. When it fails, returns 500, or gives a non-list body: no row is deleted, and `skipped` says why. A session that appears between batches stops the loop.
  - The status file has `store_bytes`, `last_prune`, `last_analyze` and `deleted`, plus `last_run`, `skipped` and `error`, and nothing else. Its mode is 0644. It holds no id, `session_id` or `data` value from the rows.
  - `ANALYZE` runs when `last_analyze` is missing (`sqlite_stat1` exists afterwards) and not when it's 1 day old.
  - `fix_owner` calls `os.chown` (monkeypatched) only for a `-wal` or `-shm` file whose uid differs from the database's.
- **Constants agree**: `prune.API == agentsh_guard.API`, `prune.STATUS == agentsh_guard.PRUNE_STATUS`, and `prune.DB` equals `audit.storage.sqlite_path` in `agentsh_sssf.yaml`. Also check that the status, script and unit paths are outside `/var/lib/agentsh`, and that `Rules(...).first_match(path, "write")` for each of them gives a deny decision under both harnesses' shipped policies. Use `shipped_text` or the `world` fixture.
- **The install**: in `test_install_agrees_with_the_run` or a new test, monkeypatch the install module's three `PRUNE_*` targets to temp paths. The output has an install line for each, the script's line has `-D`, and the output names `daemon-reload` and `enable --now sssf-agentsh-prune.timer` before the final `sudo systemctl restart agentsh`. The installed script equals `scripts/agentsh_prune.py`, and the rendered service unit has `ExecStart=/usr/bin/python3 -I -S <PRUNE_SCRIPT>`. A second run prints `unchanged:` for all three.
- **`check_machine`'s warnings**: these use the machine fixture with a temp `PRUNE_STATUS`. A missing file gives one note naming `just agentsh-install`. A `last_prune` 4 hours old gives one note naming `sudo systemctl start sssf-agentsh-prune.service`. `store_bytes` of 11e9 gives one note naming "move" and "events.db". A fresh small status gives `[]`. In none of these does `check_machine` raise. A launch with a stale status carries the note in `process.trace_notes` and in the `agentsh_denials` event's `notes`.
- **Bytes in the refusal**: `_too_long("/" + "/".join(["é" * 100] * 30))` names `len(path.encode())` with "bytes". That count differs from `len(path)`.
- **Text mode**: the existing `test_text_mode_starts_exactly_as_the_tree_does` still passes, and with no status file a text-mode start makes no note and reads nothing.

### 7. Docs

**`.claude/skills/sssf/references/config.md`, "Running local agents under agentsh":**
- In "A run refuses to start…", add a paragraph after the list. `check_machine` also reads `/var/lib/sssf-agentsh-prune/status.json`, and adds a trace note, without refusing, when the file is missing, the last prune is more than 3 hours old, or the store is over 10 GB. Each note says what to run.
- After the paragraph on how exec denials count: SSSF polls a session's events by session id alone, and keeps the `ptrace_execve` denials. When agentsh's store can't keep up, it drops events ("sqlite: event channel full"). A dropped denial isn't counted toward `MAX_REFUSALS`, though agentsh still enforces it.
- Add a **The event store** paragraph. agentsh keeps every event in `/var/lib/agentsh/events.db`, and has no retention setting for it. The install adds a root timer, `sssf-agentsh-prune.timer`. Each hour it skips if the service reports any session, or doesn't answer. Otherwise it deletes events older than 2 days, in batches of 5,000 rows, runs `ANALYZE` once a week, and writes `/var/lib/sssf-agentsh-prune/status.json`, readable by anyone. That file holds the store's size in bytes, the times of the last prune, `ANALYZE` and run, the rows deleted, and why the last hour was skipped. It holds no event content. Deleting doesn't shrink the file: freed pages are reused, so it levels off at about 2 days of events. To reclaim space, with no run going: stop the service, move `events.db` and its `-wal` and `-shm` files aside, start the service, and delete them once a run works. A session left open keeps the prune skipping until the service restarts.
- Install step 1: the script also writes the prune script and its two units, and prints `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer`.
- The one-time steps, in the "Hard links outside the workspace" paragraph: drop the dated inventory ("the hub, as of 2026-11-11, has 8 mode-000 folders … run `06ac79f1`"). Replace the two commands with these, run from the repo root:
  - folders the walk can't list or search: ``find "$(pwd -P)" "$(git rev-parse --path-format=absolute --git-common-dir)" -type d \( ! -perm -u+r -o ! -perm -u+x \) -print 2>/dev/null | sort -u``
  - paths too long for the check: ``find "$(pwd -P)" "$(git rev-parse --path-format=absolute --git-common-dir)" -print0 2>/dev/null | tr '\n\0' '?\n' | LC_ALL=C awk 'length >= 4096 { print substr($0, 1, 200) }' | sort -u``

  Say why each part is there. The walk covers the workspace and the git common dir, and `pwd -P` gives the resolved root it passes. `tr` keeps a newline in a name from splitting a path and changes no byte count. `LC_ALL=C` counts bytes, as `ENAMETOOLONG` does. Then: "Run both, and remove what they list". Keep the `chmod -R u+rwx … && rm -rf` line, and keep the rebuild steps that follow.
- In "Limits that remain", the PATH_MAX item: "names the path's length in bytes and its first 200 characters".

**`docs/guide/install.md`, "Turn on agentsh, optional"**, step 5: the printed lines also install the prune timer, `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer`, before the restart. Add one sentence after the steps: check the timer with `systemctl list-timers sssf-agentsh-prune.timer` and the status with `cat /var/lib/sssf-agentsh-prune/status.json`.

**`docs/guide/troubleshooting.md`, line 50**: replace "Check its size with `sudo ls -la /var/lib/agentsh/`" and "A pruning timer is planned; open.md tracks it." with: read `/var/lib/sssf-agentsh-prune/status.json` for its size and last prune, and see config.md's "The event store". Keep the move-aside fix. Drop the `ANALYZE` suggestion, since the poll no longer needs it.

**For the documenter**: `app_docs/` write-ups of run b684482d (the hardlink walk) that quote the one-time steps' mode-000 `find`, the character count or the dated inventory now describe old behaviour. Note that.

## Verification

Run the suite from a copy outside the repo, as earlier runs did. This run is under agentsh, and the runner uses its own checkout's module:

```bash
D=$(mktemp -d) && cp -r .claude/skills/sssf "$D/" && cd "$D/sssf" && uv run --with pytest --with pyyaml --with pydantic --with python-dotenv --with rich pytest -q tests/test_agentsh_guard.py
```

Then run the whole skill suite the same way (`pytest -q tests`), and the repo's own quality command.

Check by hand:
- `grep -n '"type": "ptrace_execve"' .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` finds nothing. `_take` still tests `event.get("type") == "ptrace_execve"`.
- `python3 -I -S .claude/skills/sssf/scripts/agentsh_prune.py --help` isn't needed. Check instead that the script imports only the standard library: `python3 -I -S -c "import ast,sys; ..."`, or just read its imports.
- No test reads or writes `/var/lib/agentsh` or `/var/lib/sssf-agentsh-prune`: `grep -n '/var/lib' .claude/skills/sssf/tests/test_agentsh_guard.py` shows only constant comparisons.

After the merge, the engineer and the hub session do these, with no run going:
1. Reclaim the space: stop the service, move `events.db`, `-wal` and `-shm` aside, start the service, and delete the moved files once a run works.
2. Reinstall both repos' policies with `just agentsh-install`, which installs the timer, and restart the service.
3. The hub session checks that `systemctl list-timers` shows the timer ran, that the status file shows `store_bytes`, and that a run's poll query is fast. It also confirms the real schema with `sudo sqlite3 /var/lib/agentsh/events.db '.schema events'`, checking that `events` is a rowid table with `ts_unix_ns`, and that the sessions list body is one of the shapes `sessions()` accepts. If either differs, the status file's `error` or `skipped` shows it, and that is a follow-up.

## For the engineer

Nothing in `protected_files` needs a change. The hub's own `adws/adw_modules/agentsh_guard.py` gets the new code through the usual `just sssf-update` after the merge, not through this build.

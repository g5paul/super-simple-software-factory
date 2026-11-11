# Keep agentsh's event store small, and poll it by session

Request: `specs/tasks/guard-agentsh-events.md`. This plan makes five changes, for runs under `local_guard: agentsh` only: poll by session, a root prune with a status file, `check_machine` warnings, corrected one-time steps, and docs.

Revision 2 answers `spec_review_1.md`:

- The one-time `find` now tests real access with `-readable` and `-executable`, as the walk does. It was checked against the walk on a temp tree: see step 7.
- The timer now fires every 10 minutes and prunes at most once an hour, so a busy hub finds a gap.
- A busy hub that never leaves a gap is named in Out of scope.
- `fix_owner` follows no link.
- The threat model names event floods.

## Baseline

`main` at `47dca0b`, which holds the hardlink run b684482d (merged as cc6a0d1). Every finding is judged against `47dca0b`.

## Threat model

Agents run as the engineer's own user inside `agentsh wrap`, under the generated default-deny policy. An agent can write its workspace, the sessions folder, the agent cache, the temp folders and its own config folder. It can't reach agentsh's HTTP API: `sssf-no-agentsh-api` denies 127.0.0.1:18080. Nothing at this level stops:

- a process outside the wrap, running as the engineer, from asking the API for sessions or events. Upstream finding #29 is still open;
- agentsh dropping an event when its store can't keep up. agentsh still enforces the denial, but SSSF's count misses it. Keeping the store small makes drops rarer, but doesn't close them. An agent can also cause drops on purpose, by making many events quickly, such as a tight loop of file opens. That hides a denial from the count, never from agentsh;
- an agent slowing the service until three polls in a row time out. That stops the run with "the agentsh service stopped answering event polls", which fails closed, as at the baseline;
- the window between the prune's session check and its next batch (one batch, tens of milliseconds), when a run that just started can lose an insert to the prune's lock;
- root. The prune runs as root, as the service's install already does.

The new files are written by `sudo install` and by a root systemd unit, at paths no policy rule lets an agent write and that the engineer's user can't write: `/usr/local/lib/sssf-agentsh/`, `/etc/systemd/system/` and `/var/lib/sssf-agentsh-prune/`. The status file holds counts and timestamps only, never event content, because anyone can read it.

## Bar

Under `local_guard: agentsh`:

- The poll's query names the session and no `type`. A run still counts each `ptrace_execve` denial it reads, exactly as `_take` does at the baseline.
- The prune never deletes an event from the last 2 days. It never deletes or runs `ANALYZE` while the service reports a session, or when it can't tell.
- The prune script, its unit files and its status file are written only by root, outside every path an agent may write. The status file holds no event content.
- `check_machine` warns, as trace notes, and never refuses, on a missing or unreadable status file, a last prune more than 3 hours old, or a store over 10 GB. Every refusal it made at the baseline stays as it was.
- Run as the engineer from the repo root, the one-time steps' two commands list every path the walk refuses for being unreadable or too long, and nothing else.
- As at the baseline: no agent file kept, no agent code run, and no engineer file deleted or overwritten.
- Runs under `local_guard: text` don't change.

Any change to the guard beyond these is a finding, and so is a `config.md` line that promises more than the tests show.

## Out of scope

These stay open, as old holes:

- A composite index or a retention setting in agentsh. Either would be a fork patch, and the patches are unpublished.
- agentsh's missing busy timeout, and its dropped events ("event channel full"). A dropped `ptrace_execve` denial still isn't counted toward `MAX_REFUSALS`.
- The audit log's rotation (512 MB × 10 files).
- Storing fewer allowed file events.
- pi's policy.
- `VACUUM`. The file doesn't shrink. Freed pages are reused, so it levels off at about 2 days of events. The engineer's one-time move-aside reclaims what is already used.
- **A hub that never goes idle.** The prune skips whenever the service lists a session. The timer looks every 10 minutes, so a gap between agent calls at one of those times is enough. Chains that keep a session open at every check, for hours, prune nothing, and the store grows meanwhile. The stale-prune warning shows it after 3 hours, and says to run the prune with no run going.
- A session a signal leaves open. It is already a known limit, and it now also makes the prune skip until the service restarts. The stale-prune warning says so.
- Upstream finding #29.

## Files to touch

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`: the poll, the byte count, and the warnings.
- `.claude/skills/sssf/scripts/agentsh_prune.py`: **new**. The prune, standard library only.
- `.claude/skills/sssf/scripts/agentsh_install.py`: render and print the prune files.
- `.claude/skills/sssf/tests/test_agentsh_guard.py`: the fake service's poll/final split, and the new tests.
- `.claude/skills/sssf/references/config.md`: the "Running local agents under agentsh" section.
- `docs/guide/install.md`: the "Turn on agentsh, optional" steps.
- `docs/guide/troubleshooting.md`: the event-polls paragraph at line 50.

Leave alone:

- `adws/adw_modules/`, the hub's own copy, which is protected;
- `docs/open.md` and `docs/designs/agentsh-local-sandbox.md`, which the hub session updates after the merge;
- `app_docs/`.

## Steps

### 1. Poll by session only (`agentsh_guard.py`)

- `AgentshProcess._poll` becomes `rows = self._pages(self._offset, {})`, and nothing else changes. `_take` stays byte-for-byte the same.
- The poll now sees file and network denials too, and traces them in `other_denials`. `_seen` already dedups by id, so the final read doesn't add them twice, and the totals don't change.
- `self._offset` now counts every event of the session, not only `ptrace_execve` ones. That is correct for an untyped query.

### 2. Bytes in the PATH_MAX refusal (`agentsh_guard.py`)

- `_too_long(path)` gives `len(os.fsencode(path))` with the word "bytes", e.g. `({n} bytes, at or over PATH_MAX, 4,096 bytes on Linux)`. Keep `It starts: {path[:200]}` and the rest of the message as they are.

### 3. The prune script (`.claude/skills/sssf/scripts/agentsh_prune.py`, new)

It uses the standard library only and has no PEP 723 block. It runs as `/usr/bin/python3 -I -S`. Module constants:

```python
DB = "/var/lib/agentsh/events.db"            # agentsh_sssf.yaml audit.storage.sqlite_path
API = "http://127.0.0.1:18080"               # agentsh_guard.API
STATUS = "/var/lib/sssf-agentsh-prune/status.json"   # agentsh_guard.PRUNE_STATUS
KEEP_SECONDS = 2 * 24 * 3600
PRUNE_EVERY = 3600         # the timer looks every 10 minutes; a prune is due an hour after the last
ANALYZE_SECONDS = 7 * 24 * 3600
BATCH = 5000
LOCK_WAIT = 5.0            # sqlite3.connect(timeout=...)
PAUSE = 0.05               # seconds between batches
```

Expose `prune(db=DB, api=API, status=STATUS, now=None) -> dict`, and have `main()` call it with no arguments. `if __name__ == "__main__":` returns 0 when the prune skips or records an error, so the timer doesn't count a skipped hour as a failure. A crash still exits non-zero.

What it does, in order:

1. Read the previous status, if the file holds valid JSON, and carry `last_prune`, `last_analyze`, `deleted`, `skipped` and `error` forward.
2. **Not due:** if `last_prune` is less than `PRUNE_EVERY` before `now`, refresh only `store_bytes` and `last_run`, write the status and return. It asks the API nothing and opens no connection to the store.
3. `sessions(api)`: GET `{api}/api/v1/sessions` through `urllib.request.build_opener(urllib.request.ProxyHandler({}))`, with `timeout=5`. The body may be a JSON list, `{"sessions": [...]}`, or `null`, which means an empty list. Return the count. Raise on anything else, or on a non-200 reply. Every listed session counts as active: SSSF deletes its session at the end of each call, so the list is empty between agent calls.
4. If the count is above 0, or the API raised, set `skipped` to `"the service reports N active session(s)"` or `"the service didn't answer its sessions list (<ErrorClass>)"`. `last_prune` and `deleted` keep their earlier values. Write the status and return. The next 10-minute tick tries again.
5. Connect with `sqlite3.connect(db, timeout=LOCK_WAIT, isolation_level=None)`. Never change `journal_mode`, and never `VACUUM`. Set `cutoff = int((now - KEEP_SECONDS) * 1e9)`. Then loop:
   - re-check `sessions(api)`. If it's above 0, or it raises, stop and set `skipped` with the reason;
   - run `BEGIN IMMEDIATE`, then `DELETE FROM events WHERE rowid IN (SELECT rowid FROM events ORDER BY rowid LIMIT ?) AND ts_unix_ns < ?` with `(BATCH, cutoff)`, then `COMMIT`. Add `cursor.rowcount` to this run's count;
   - stop when a batch deleted fewer than `BATCH` rows. Otherwise `time.sleep(PAUSE)`.

   Why it walks by rowid: no index covers `ts_unix_ns` alone, so `WHERE ts_unix_ns < ?` with no rowid bound scans the whole table on its last batch. That is about two days of events, tens of GB, every hour. Walking from the oldest rowid reads only `BATCH` rows per batch. A row older than the cutoff that sits behind a newer row is deleted in a later hour, once the rows ahead of it age past the cutoff. The `ts_unix_ns < ?` test is what keeps the last 2 days, whatever the rowid order.
6. If `last_analyze` is missing or older than `ANALYZE_SECONDS`, and the loop didn't stop on a session: re-check `sessions(api)`, then run `PRAGMA analysis_limit=1000` and `ANALYZE`, and set `last_analyze = now`. `analysis_limit` keeps the write lock short on a large store, and older SQLite ignores it.
7. When the delete loop ends without a skip, set `last_prune = now`, set `deleted` to this run's count, and clear `skipped` and `error`.
8. On `sqlite3.OperationalError` or `DatabaseError`: roll back if a transaction is open, and set `error` to `f"{type(e).__name__}: {e}"`. SQLite's messages carry no row content: "database is locked", "no such column: rowid". `last_prune` keeps its earlier value.
9. After the connection closes, run `fix_owner(db)` for `db + "-wal"` and `db + "-shm"`. For each one, `os.lstat` it, and skip it if it's missing or not a regular file. If its uid differs from that of `os.lstat(db)`, run `os.chown(path, uid, gid, follow_symlinks=False)`. This covers a file that root's connection made, which the service, running as `agentsh`, couldn't open.
10. `write_status(status, data)`: make the folder if it's missing, at mode 0755. Write a temp file in that folder at mode 0644, and `os.replace` it over the status file. The JSON holds exactly these keys:
    - `store_bytes`: the size of `events.db` plus `events.db-wal`, or `null` when `events.db` is missing;
    - `last_prune`, `last_analyze`, `last_run`: UTC ISO 8601 strings, or `null`;
    - `deleted`: an integer, the rows the last completed prune deleted;
    - `skipped`: a reason string, or `null`;
    - `error`: a string, or `null`.

    Nothing from an event goes into it: no ids, no session ids, no paths.

Why these numbers, for the spec's record:

- `BATCH = 5000`: agentsh opens the store with no busy timeout, so every insert it tries while the prune holds the write lock fails. 5,000 rows is a few MB of pages, tens of milliseconds a transaction, so a run that starts mid-prune loses at most one batch's window.
- `LOCK_WAIT = 5.0`: how long the prune waits for the service's own write lock. Its batched inserts hold the lock for far less. The prune gives up after 5 s and tries again on a later tick, rather than queue behind a busy service.
- The 10-minute timer with a 1-hour gate: it still prunes at most once an hour, as the request asks. A skipped hour retries 10 minutes later, instead of an hour later, so a hub with short gaps between agent calls still prunes.

### 4. The units and the install (`agentsh_install.py`)

Constants: `PRUNE_SCRIPT = "/usr/local/lib/sssf-agentsh/agentsh_prune.py"`, `PRUNE_SERVICE = "/etc/systemd/system/sssf-agentsh-prune.service"` and `PRUNE_TIMER = "/etc/systemd/system/sssf-agentsh-prune.timer"`.

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
Description=SSSF: look every 10 minutes for an hourly prune of agentsh's event store

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

- Add the three files to the install's `items` list, so they go through the same diff and `sudo install` loop. The script's text is `(SKILL / "scripts/agentsh_prune.py").read_text()`, so the hub's copy is what gets installed. Only `PRUNE_SCRIPT`'s line takes `-D` (`sudo install -D -o root -g root -m 0644 <temp> <target>`), which makes `/usr/local/lib/sssf-agentsh`. The existing lines keep their form.
- When any of the three is in `steps`, print `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer` after the install lines. The last line printed stays `sudo systemctl restart agentsh`.
- Update the module docstring to name the prune files.

### 5. The warnings (`agentsh_guard.py`)

- New constants: `PRUNE_STATUS = "/var/lib/sssf-agentsh-prune/status.json"`, `PRUNE_STALE_SECONDS = 3 * 3600` and `STORE_WARN_BYTES = 10 * 10**9`.
- New `store_notes(now=None) -> list[str]`, which never raises. Its notes:
  - **Missing, unreadable, or not a JSON object:** "agentsh's event store has no prune status at {PRUNE_STATUS}, so its size is unknown; install the prune timer with `just agentsh-install` and the sudo lines it prints".
  - **`last_prune` null, unparseable, or more than 3 hours old:** "agentsh's event store was last pruned {when or 'never'}{; last skipped: <skipped>}{; last error: <error>}. The prune skips while the service lists a session; with no run going, run `sudo systemctl start sssf-agentsh-prune.service`. If it still skips on an active session, a session may have been left open: with no run going, run `sudo systemctl restart agentsh`".
  - **`store_bytes` over `STORE_WARN_BYTES`:** "agentsh's event store is {GB:.1f} GB, over 10 GB; with no run going, stop the service, move /var/lib/agentsh/events.db and its -wal and -shm files aside, start it, and delete the moved files once a run works (see \"Running local agents under agentsh\" in .claude/skills/sssf/references/config.md)".
  - `skipped` and `error` are shown with control characters escaped, as `command_text` escapes them.
- `check_machine(guard) -> list[str]`: every check stays as it is, and after the last one passes it returns `store_notes()`. Update its docstring.
- `start`: `notes = check_machine(guard)`, and after `process = AgentshProcess(...)`, `process.trace_notes.extend(notes)`. The notes reach the trace through `close`'s existing `agentsh_denials` event. `session.ensure` ignores the return value, so leave `session.py` unchanged.

### 6. Tests (`.claude/skills/sssf/tests/test_agentsh_guard.py`)

**The fake service.** It tells polls from the final read by `type`, which no longer works. Change it:

- In the `service` fixture, monkeypatch `agentsh_guard.AgentshProcess._get` with a wrapper. When `self._done.is_set()`, the call is the final read, and the wrapper adds `"_final": "1"` to the query, then calls the original.
- In `do_GET`, pop `_final` from `q` before appending to `queries`, and use `final = "_final" in raw_q` where the fake used `"type" in q`.
- Rename the flags: `fail_typed` becomes `fail_polls`, `fail_untyped` becomes `fail_final`, `typed_delay` becomes `poll_delay`, and `hidden_from_typed` becomes `final_only`. The decision stays per request, with no state shared between threads.
- Keep the fake's type filter, so a typed poll would still get fewer rows and fail the new assertions.
- `test_only_a_named_exec_denial_counts_across_pages`: assert `all("type" not in q for q in service.queries)` and that `session_id` is `sess1` in every query. Keep `counted == 2`, `len(other_denials) == 61`, and the check that a repeated page counts once.

New tests. None touches `/var/lib/agentsh`, `/var/lib/sssf-agentsh-prune` or the real service.

**Fixtures.**
- In the machine fixture, monkeypatch `agentsh_guard.PRUNE_STATUS` to a temp file holding a fresh status (`last_prune` now, `store_bytes` 1000), so existing tests see no notes.

**The poll.**
- **Query:** one `_poll` on `bare(service)` sends `session_id=sess1` and no `type`.
- **A mixed page:** `ptrace_execve` denials, a `ptrace_file` deny, a `ptrace_execve` allow, an unnamed exec deny and a `net_connect` deny. Only the named `ptrace_execve` denials count, and the others land in `other_denials`.

**The prune.** Load `agentsh_prune.py` with `importlib`, as the `hub` fixture loads the install script. Build a temp SQLite file in WAL mode with what is known of agentsh's schema:

```sql
CREATE TABLE events (id TEXT PRIMARY KEY, session_id TEXT, type TEXT, ts_unix_ns INTEGER, data TEXT);
CREATE INDEX idx_events_session_ts ON events(session_id, ts_unix_ns);
CREATE INDEX idx_events_type_ts ON events(type, ts_unix_ns);
```

Serve sessions from a small `ThreadingHTTPServer`, as `Service` does.

- **The cutoff:** rows from 3 days ago, from 2 days and 1 second ago, from 2 days less 1 second ago, and from now. Patch `BATCH` to 3, and add more old rows than one batch. Only rows older than the cutoff go, and the row from 2 days less 1 second ago stays. To show it took more than one batch, count the `DELETE` statements with `conn.set_trace_callback`, or check `deleted` against the patched `BATCH`.
- **Rowid order:** an old row that sits after a recent row in rowid order isn't deleted while it's behind that row, and the recent row is never deleted.
- **Active sessions:**
  - the fake API lists one session: no row is deleted, and `skipped` names the active session;
  - the fake API fails, answers 500, or gives a body that isn't a list: no row is deleted, and `skipped` says why;
  - a session that appears between batches stops the loop.
- **The 1-hour gate:** with `last_prune` 30 minutes old, the fake API gets no request, no row is deleted, and the status's `last_run` and `store_bytes` are refreshed. With `last_prune` 61 minutes old, the prune runs.
- **The status file:** it has `store_bytes`, `last_prune`, `last_analyze` and `deleted`, plus `last_run`, `skipped` and `error`, and nothing else. Its mode is 0644. No id, `session_id` or `data` value from the rows is in it.
- **ANALYZE:** it runs when `last_analyze` is missing, so `sqlite_stat1` exists afterwards, and doesn't run when `last_analyze` is a day old.
- **`fix_owner`:** with `os.chown` monkeypatched, it's called only for a regular `-wal` or `-shm` file whose uid differs from the database's, and always with `follow_symlinks=False`. A link at `-wal` isn't touched.

**Constants agree.**
- `prune.API == agentsh_guard.API`, `prune.STATUS == agentsh_guard.PRUNE_STATUS`, and `prune.DB` equals `audit.storage.sqlite_path` in `agentsh_sssf.yaml`.
- The status, script and unit paths are outside `/var/lib/agentsh`.
- Under both harnesses' shipped policies, `Rules(...).first_match(path, "write")` for each of those paths gives a rule whose decision is deny. Use `shipped_text` or the `world` fixture.

**The install.** In `test_install_agrees_with_the_run` or a new test, monkeypatch the install module's three `PRUNE_*` targets to temp paths.
- There is an install line for each file, and the script's line has `-D`.
- `daemon-reload` and `enable --now sssf-agentsh-prune.timer` come before the final `sudo systemctl restart agentsh`.
- The installed script equals `scripts/agentsh_prune.py`.
- The service unit has `ExecStart=/usr/bin/python3 -I -S <PRUNE_SCRIPT>`, and the timer has `OnCalendar=*:0/10`.
- A second run prints `unchanged:` for all three files.

**`check_machine`'s warnings.** Use the machine fixture with a temp `PRUNE_STATUS`.
- A missing file gives one note naming `just agentsh-install`.
- A `last_prune` 4 hours old gives one note naming `sudo systemctl start sssf-agentsh-prune.service`.
- A `store_bytes` of 11e9 gives one note naming "move" and "events.db".
- A fresh, small status gives `[]`.
- `check_machine` never raises in these cases.
- A launch with a stale status carries the note in `process.trace_notes` and in the `agentsh_denials` event's `notes`.

**Bytes in the refusal.**
- `_too_long(path)` with `path = "/" + "/".join(["é" * 100] * 30)` names `len(path.encode())` with "bytes", and that number differs from `len(path)`.

**Text mode.**
- The existing `test_text_mode_starts_exactly_as_the_tree_does` still passes.
- A text-mode start with no status file reads nothing and makes no note.

### 7. Docs

**`.claude/skills/sssf/references/config.md`, "Running local agents under agentsh":**

- **After the "A run refuses to start…" list,** add a paragraph. The run also reads `/var/lib/sssf-agentsh-prune/status.json`, and adds a trace note, without refusing, when the file is missing, the last prune is more than 3 hours old, or the store is over 10 GB. Each note says what to run.
- **After the paragraph on how exec denials count:**
  - SSSF polls a session's events by session id alone, and keeps the `ptrace_execve` denials.
  - When agentsh's store can't keep up, agentsh drops events ("sqlite: event channel full"). A dropped denial isn't counted toward `MAX_REFUSALS`, though agentsh still enforces it.
- **A new "The event store" paragraph:**
  - agentsh keeps every event in `/var/lib/agentsh/events.db`, and has no retention setting for it.
  - The install adds a root timer, `sssf-agentsh-prune.timer`. Every 10 minutes it checks whether a prune is due, and one is due an hour after the last. A due prune skips when the service lists any session or doesn't answer, and tries again 10 minutes later. Otherwise it deletes events older than 2 days, in batches of 5,000 rows, and runs `ANALYZE` once a week.
  - It writes `/var/lib/sssf-agentsh-prune/status.json`, which anyone can read. The file holds the store's size in bytes, the times of the last prune, the last `ANALYZE` and the last check, the rows deleted, and why the last attempt was skipped. It holds no event content.
  - Deleting doesn't shrink the file: freed pages are reused, so it levels off at about 2 days of events. To reclaim space, with no run going: stop the service, move `events.db` and its `-wal` and `-shm` files aside, start the service, and delete them once a run works.
  - Chains that keep a session open at every check prune nothing until they pause. So does a session left open, until the service restarts. The stale-prune warning shows either one.
- **Install step 1:** the script also writes the prune script and its two units, and prints `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer`.
- **The one-time steps, in the "Hard links outside the workspace" paragraph:**
  - Drop the dated inventory ("the hub, as of 2026-11-11, has 8 mode-000 folders … run `06ac79f1`").
  - Replace the two commands with these. Run them from the repo root as yourself, not with sudo:
    - folders the walk can't read: ``find "$(pwd -P)" "$(git rev-parse --path-format=absolute --git-common-dir)" -type d \( ! -readable -o \( ! -executable ! -empty \) \) -print 2>/dev/null | sort -u``
    - paths too long for the check: ``find "$(pwd -P)" "$(git rev-parse --path-format=absolute --git-common-dir)" -print0 2>/dev/null | tr '\n\0' '?\n' | LC_ALL=C awk 'length >= 4096 { print substr($0, 1, 200) }' | sort -u``
  - Say what each part does:
    - `-readable` and `-executable` ask the kernel, through access(2), what you may do, as the walk's `scandir` and `lstat` find out. So they catch a folder another user owns, or one an ACL closes, and not only the mode bits.
    - The walk refuses a folder it can't list, and a folder it can list but not search when it holds anything, since it has to `lstat` each entry. An empty folder it can list but not search passes, so `! -empty` leaves it out.
    - The walk covers the workspace and the git common dir, and `pwd -P` gives the resolved root that the walk passes.
    - `tr` keeps a newline in a name from splitting a path, and changes no byte count.
    - `LC_ALL=C` makes `awk` count bytes, as `ENAMETOOLONG` does.
  - Then say "Run both, and remove what they list." Keep the `chmod -R u+rwx … && rm -rf` line, and keep the rebuild steps after it.

  This departs from the request's `\( ! -perm -u+r -o ! -perm -u+x \)`. Spec review 1 found that the owner bits miss folders the walk refuses. The expression was checked on 2026-11-11 against a temp tree, with `scandir` plus `lstat` in place of the walk:

  | Folder | Walk | `find` |
  | --- | --- | --- |
  | mode 0300 | refuses | lists it |
  | mode 0400, empty | passes | doesn't list it |
  | mode 0400, holding a file | refuses | lists it |
  | mode 0700 | passes | doesn't list it |
- **In "Limits that remain", the PATH_MAX item:** it now says the refusal "names the path's length in bytes and its first 200 characters".

**`docs/guide/install.md`, "Turn on agentsh, optional":**
- Step 5: the printed lines also install the prune timer, with `sudo systemctl daemon-reload` and `sudo systemctl enable --now sssf-agentsh-prune.timer`, before the restart.
- Add one sentence after the steps: check the timer with `systemctl list-timers sssf-agentsh-prune.timer`, and the status with `cat /var/lib/sssf-agentsh-prune/status.json`.

**`docs/guide/troubleshooting.md`, line 50:**
- Replace "Check its size with `sudo ls -la /var/lib/agentsh/`" and "A pruning timer is planned; open.md tracks it." with: read `/var/lib/sssf-agentsh-prune/status.json` for the store's size and last prune, and see "The event store" in config.md.
- Keep the move-aside fix.
- Drop the `ANALYZE` suggestion, since the poll no longer needs it.

**For the documenter:** `app_docs/` write-ups of run b684482d (the hardlink walk) that quote the one-time steps now describe old behaviour: the mode-000 `find`, the character count, and the dated inventory. Note that.

## Verification

Run the suite from a copy outside the repo, as earlier runs did. This run is under agentsh, and the runner uses its own checkout's module.

```bash
D=$(mktemp -d) && cp -r .claude/skills/sssf "$D/" && cd "$D/sssf" && uv run --with pytest --with pyyaml --with pydantic --with python-dotenv --with rich pytest -q tests/test_agentsh_guard.py
```

Then run the whole skill suite the same way, with `pytest -q tests`, and the repo's own quality command.

Check by hand:

- `grep -n 'ptrace_execve' .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` shows it only in `_take`'s `event.get("type") == "ptrace_execve"` test and in comments, never in a query.
- `agentsh_prune.py` imports only standard-library modules: read its import lines.
- `grep -n '/var/lib' .claude/skills/sssf/tests/test_agentsh_guard.py` shows only comparisons with constants, and no read or write of those paths.
- The one-time `find` on a temp tree with folders at 0300, 0400 empty, 0400 holding a file, and 0700 lists only the 0300 folder and the non-empty 0400 one. Restore with `chmod -R u+rwx` before removing the tree.

After the merge, with no run going, the engineer and the hub session:

1. Reclaim the space: stop the service, move `events.db`, `-wal` and `-shm` aside, start the service, and delete the moved files once a run works.
2. Reinstall both repos' policies with `just agentsh-install`, which installs the timer, and restart the service.
3. The hub session checks that `systemctl list-timers` shows the timer ran, that the status file shows `store_bytes`, and that a run's poll query is fast. It confirms the real schema with `sudo sqlite3 /var/lib/agentsh/events.db '.schema events'`: `events` must be a rowid table with `ts_unix_ns`. It also checks that the sessions list body has one of the shapes `sessions()` accepts. If either differs, the status file's `error` or `skipped` shows it, and fixing it is a follow-up.

## For the engineer

Nothing in `protected_files` needs a change. The hub's own `adws/adw_modules/agentsh_guard.py` gets the new code through the usual `just sssf-update` after the merge, not through this build.

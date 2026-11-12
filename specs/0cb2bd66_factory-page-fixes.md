# Fix the factory page's live-VM count and agentsh store step

Two bugs in the factory page built by run e089a1ee (commit 4ec7c30). Both fixes are small and live in the dashboard's server, `.claude/skills/sssf/apps/visualizer/server/`.

## Background

### Bug 1: live-VM rows count events that are not VMs

`SssfDb.openSandboxes()` in `.claude/skills/sssf/apps/visualizer/server/db.ts` (around line 646) selects every event with `name = 'sandbox'` that has no later `sandbox_end` with the same `vm`. In a real trace, the name `sandbox` covers two kinds of event:

- The VM's start event. `adws/adw_modules/sandbox_remote.py:517` writes it as type `log`, with payload `{"provider", "vm", "session_minutes", "python"}`.
- The `sandbox` phase's own events: `phase_start`, `phase_end`, and `log` lines whose payload holds only `message` and `level`.

The phase events have no `vm`. Their `json_extract(..., '$.vm')` is NULL, and no `sandbox_end` has a NULL vm, so each one counts as a VM that never ended. That gives the "VM (unnamed)" rows. Only a start event should count. `sandbox()` in `server/runview.ts:71` already picks the VM this way: a `sandbox` event whose payload names both `provider` and `vm`.

### Bug 2: the agentsh store row's next step is wrong

`pruneRows()` in `.claude/skills/sssf/apps/visualizer/server/factory_machine.ts:137` gives the store row's next step as "ask the hub session to check why the prune isn't shrinking the store". Deleting rows never shrinks the file. `.claude/skills/sssf/references/config.md` (the "The event store" paragraph under "Running local agents under agentsh") gives the real step: "To reclaim space, with no run going: stop the service, move `events.db` and its `-wal` and `-shm` files aside, start the service, and delete them once a run works."

## Changes

### 1. `.claude/skills/sssf/apps/visualizer/server/db.ts`: `openSandboxes()`

Add these conditions to the outer `WHERE`, next to `b.name = 'sandbox'`:

```sql
WHERE b.name = 'sandbox'
  AND b.type = 'log'
  AND json_extract(b.payload_json, '$.vm') IS NOT NULL
  AND json_extract(b.payload_json, '$.provider') IS NOT NULL
  AND NOT EXISTS (...unchanged...)
```

Update the doc comment above the method so it says what counts. For example: "VMs started and not yet ended: a VM's start event (a `sandbox` log naming a provider and a vm) with no later `sandbox_end` for the same run and vm. The `sandbox` phase's own events share the name and are not VMs." Keep the `try/catch`, the return type and the `ORDER BY`.

Leave `vmRows()` in `server/factory_machine.ts` as it is. Its "(unnamed)" fallback stays for safety, but it should no longer fire on real traces.

### 2. `.claude/skills/sssf/apps/visualizer/server/factory_machine.ts`: `pruneRows()`

Replace the store row's `next` text (line 137, used when the level isn't `ok`) with the step config.md gives, naming the full path:

```
with no run going: `sudo systemctl stop agentsh`, move `/var/lib/agentsh/events.db` and its `-wal` and `-shm` files aside, `sudo systemctl start agentsh`, and delete them once a run works
```

Follow the style of the other `next` strings in this file (they use backticks around commands, for example "check `systemctl status sssf-agentsh-prune.timer`"). Keep it one string literal, and change nothing else in `pruneRows`. The service name `agentsh` matches config.md's `sudo systemctl restart agentsh`. If it reads better, pull the text into a `const` next to `enable` at the top of the function.

### 3. Tests

**`.claude/skills/sssf/apps/visualizer/server/db.test.ts`**: add a test, for example `"a VM's start counts as live, and the sandbox phase's own events don't"`. Seed a file database in `dir` the way the existing tests do:

- `CREATE TABLE events (adw_id TEXT, phase_id TEXT, type TEXT, name TEXT, payload_json TEXT, started_at TEXT)` and `CREATE TABLE sessions (adw_id TEXT, status TEXT)`. The query LEFT JOINs `sessions`, and a missing table would send it into the `catch` and return `[]`, which would let the test pass for the wrong reason. Insert a sessions row for each run.
- Run `ended` is a VM run as it really happens, in time order:
  - `phase_start`, `sandbox`, payload `{}`
  - `log`, `sandbox`, `{"message": "making the VM", "level": "info"}`
  - `log`, `sandbox`, `{"provider": "daytona", "vm": "vm-1", "session_minutes": 60, "python": "3.12"}`
  - `log`, `sandbox`, `{"message": "...", "level": "info"}` (two or three such lines)
  - `log`, `sandbox_end`, `{"provider": "daytona", "vm": "vm-1", "vm_seconds": 120}`
  - `phase_end`, `sandbox`, `{}`
  - One phase log line after the `sandbox_end` too, so a line with no later end is covered.
- Run `open` has the same phase events and a start event for `vm-2`, with no `sandbox_end`.
- Assert `new SssfDb(path).openSandboxes()` returns exactly one row: `adw_id` `open`, `vm` `vm-2`, `provider` `daytona`, with that run's `run_status`. Close the `SssfDb`.

Also check that the `ended` run alone gives `[]`. Either a second test or an assertion that no row has `adw_id` `ended` will do.

**`.claude/skills/sssf/apps/visualizer/server/factory_machine.test.ts`**: in or next to the test "the agentsh store: 10 GiB ok, 16 amber, 26 red, both null amber" (line 86), assert that an amber store row's `next` names `/var/lib/agentsh/events.db`, `-wal`, `-shm` and `systemctl stop agentsh`, does not contain "ask the hub session", and that an ok row's `next` is still `null`.

**`.claude/skills/sssf/apps/visualizer/server/factory.test.ts`**: `seedRepo` (line 38) already inserts the start event as type `log` with `provider` and `vm`, so it should pass unchanged. Run it to confirm. Do not test over a socket.

## Docs

- No README, CHANGELOG or help text names either string. `specs/tasks/factory-page-fixes.md` is the request itself, so leave it.
- The doc comment on `openSandboxes()` is updated in change 1.
- `.claude/skills/sssf/references/config.md` is out of scope and already correct.
- For the documenter: if an `app_docs/` write-up of the factory page (run e089a1ee) describes the live-VM rows or the store row's next step, it now describes the old behaviour. The VM count now includes only a VM's start event, and the store step is the move-aside step. Do not edit `app_docs/` in the build.

## Out of scope

Every other row and rule, the design's "Deferred" list, `check_projects.py`, `phase_times.py`, guard code, the agentsh policy, `quality.py` and the docs. This also covers giving the audit-log case its own step. config.md gives a different step (deleting rotated `audit.jsonl.<N>` files) when the audit log is the bigger part. The request asks only for the move-aside step, so leave that branch for a later change.

## Verification

From `.claude/skills/sssf/apps/visualizer/`:

1. `bun test server/db.test.ts server/factory_machine.test.ts server/factory.test.ts`: the new tests pass.
2. `bun test`: the whole suite passes, except `server/listen.test.ts`. In the builder's shell agentsh denies connecting to `127.0.0.1`, so that file fails with "Permission denied". The chain's test step runs it outside the sandbox. Any other failure is real.
3. `npx --no-install vue-tsc --noEmit` exits 0.
4. Run the new db test against the old query (or reason through it) to confirm it would fail: the phase events would add rows with `vm` null.

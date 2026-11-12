# Plan: record agentsh's dropped events as numbers in the end-of-call event

Piece 3 of the factory page's build order (`docs/designs/factory-page.md`, "Build order", item 3).

## Summary

`close()` in `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py` already counts the events agentsh
dropped during a call (`journal_drops()`), but only writes them into note sentences. Add one structured field,
`drops`, to the `agentsh_denials` event that `close()` sends, pass it through the forwarder in
`.claude/skills/sssf/templates/adws/adw_modules/agents.py` into the traced payload, and name it in
`.claude/skills/sssf/references/config.md`. Nothing else changes.

`drops` is:

- an object from event type to count (`{"ptrace_file": 12, "ptrace_execve": 1}`) when the journal was read and found drops;
- `{}` when the journal was read and found none;
- `null` (`None`) when the read failed (any `problem` from `journal_drops()`: no permission, timeout, non-zero exit,
  could not run, internal failure) or when the call has no start time (`process.started_at is None`).

## Baseline

`main` at `71dec0d` ("Add the brief for the drops-field guard run"). Every finding is judged against that commit.
At the baseline the template modules and the hub copies in `adws/adw_modules/` are identical for `agents.py` and
`agentsh_guard.py`.

## Threat model

- The agent runs as the engineer's own user, inside agentsh's wrap. It can write any line to the harness's
  stdout, including a JSON line of type `agentsh_denials` with a `drops` key of its choosing.
  `agentsh_guard.guard_event()` makes the harness read loops (`agent_cc.py`, `agent_pi.py`) drop every
  `agentsh_` event from the agent's stream before `on_event`, so such a line never reaches the forwarder. This
  change must keep that true; it adds no new event type and touches no read loop.
- The agent can make agentsh log lines under the `agentsh` unit carrying text it chose (a command's arguments),
  so it can fake an "event channel full ... type=ptrace_execve" line and raise a count. That is a known,
  documented limit ("A drop line can be faked" in `config.md`). `drops` inherits it: the counts are advice,
  exactly as the notes are. `_TYPE_NAME` already limits a type to `[a-z0-9_]{1,64}` or `unknown`, so no event id,
  path or other content reaches the field.
- The agent can fill agentsh's store to cause real drops, or race journald's rate limit to hide them. Counts stay a
  floor. Not this change's to fix.
- What this guard cannot stop at its level: anything the agent does as the engineer's user that agentsh's policy
  allows; drops by a concurrent run being attributed to this call (drops are counted for the whole service); a
  journal line delivered after the read.

## Bar

Under `local_guard: agentsh`, against the baseline:

1. **Must do**: the `agentsh_denials` event from `close()` carries `drops` with the three meanings in the summary,
   and the traced `agentsh_denials` payload in `agents.py` carries `drops` beside `polls`, `pages` and
   `slowest_poll_seconds`.
2. **Must never**:
   - refuse, stop, or count anything new. `drops` never feeds `tally`, `breach()`, `check_stopped()`,
     `process.failure`, or `MAX_REFUSALS`. Every refusal and stop at the baseline stays as it was.
   - change the journal read: same `journalctl` command and arguments, same user (no `sudo`), same window
     (`int(since)` to `math.ceil(until)`), same `JOURNAL_SECONDS` timeout, same parsing (`_DROP_TYPE`,
     `_TYPE_NAME`, `DROP_TEXT`). `journal_drops()` is not edited.
   - put anything in `drops` but event-type names (as `journal_drops()` returns them) and integer counts.
   - change any note's text or the conditions under which it is added.
   - rename, remove, or change the value of any field the event or traced payload carried at the baseline:
     `type`, `denials`, `notes`, `polls`, `pages`, `slowest_poll_seconds` (event), and `agent`, `denials`, `notes`,
     `polls`, `pages`, `slowest_poll_seconds` (payload).
   - let an agent set `drops`: an `agentsh_` line in the agent's own stream is still dropped before the forwarder.
   - keep an agent file, run agent code, or delete or overwrite an engineer file, where the baseline does
     otherwise.
3. Runs under `local_guard: text` don't change (`close()` returns early for a non-`AgentshProcess`; no event is sent).
4. `config.md` promises nothing the tests don't show.

## Out of scope

These holes stay open; a reviewer should file them as old holes:

- The drop counting itself and its known limits in `config.md`: journald's rate limit hides lines; drops are not
  tied to a session, so a concurrent run's drops show up in this call; a faked drop line under the `agentsh` unit
  adds a count (including a faked `ptrace_execve`); the whole-second window misses late lines.
- Counting a dropped `ptrace_execve` as a refusal.
- The factory page, which reads the field later.
- pi's policy.
- The troubleshooting guide (`docs/guide/troubleshooting.md`) and `docs/open.md` wording; see "For the engineer".

## Files to change

Only these three files, plus the test file:

- `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
- `.claude/skills/sssf/templates/adws/adw_modules/agents.py`
- `.claude/skills/sssf/references/config.md`
- `.claude/skills/sssf/tests/test_agentsh_guard.py` (new tests only; no existing test edited)

Do **not** touch `adws/adw_modules/` (the hub copies; protected), `adws/tests/`, `specs/`, `app_docs/`, or any
other doc.

## Steps

### 1. `.claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`, `close()`

Current shape (around lines 1181–1229):

```python
try:
    if process.started_at is not None:
        counts, problem = journal_drops(process.started_at, time.time())
        if problem:
            process.trace_notes.append(...couldn't be read...)
        elif counts:
            ...two notes...
    if process.slowest > SLOW_POLL_SECONDS:
        ...
except Exception:
    pass
if on_event:
    counts: dict[tuple, int] = {}       # note: reuses the name `counts`
    ...
    on_event({"type": "agentsh_denials", "denials": denials, "notes": process.trace_notes,
              "polls": process.polls, "pages": process.pages,
              "slowest_poll_seconds": round(process.slowest, 2)})
```

Change to:

- Before the inner `try:`, set `drops: Optional[dict[str, int]] = None`.
- Inside `if process.started_at is not None:`, after `journal_drops(...)` returns, keep `if problem:` exactly as is.
  In the no-problem case, set `drops = {t: n for t, n in sorted(counts.items())}` (a fresh dict, so later edits to
  `counts` can't reach the event; sorted for a stable order), then add the two existing notes under the same
  `if counts:` condition with the same text. Concretely:

  ```python
  if problem:
      process.trace_notes.append(...)          # unchanged
  else:
      drops = {t: n for t, n in sorted(counts.items())}
      if counts:
          ...                                  # the existing two notes, byte-for-byte
  ```
- Use the name `drops`, never `counts`, for the new variable: the `if on_event:` block below reuses `counts` for the
  denial tally.
- Add `"drops": drops` to the `on_event({...})` dict, after `"slowest_poll_seconds"`. Leave every other key and value
  as is.
- Update `close()`'s docstring: it traces "the denials that didn't count, with the poll figures and the drop counts
  by type (`drops`: `None` when the journal couldn't be read or the call has no start time)". One sentence; no
  other docstring changes.

Do not edit `journal_drops()`, `guard_event()`, `start()`, `breach()`, `check_stopped()`, or any constant.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/agents.py`, `forward()` in `_event_forwarder`

In the `if etype == "agentsh_denials":` branch (around line 590), add `"drops": event.get("drops")` to the traced
`payload` dict after `"slowest_poll_seconds"`. Change nothing else. An event without the key (from before the field)
traces `drops: None`, the same as an unread journal, which is what `event.get` already does for the poll figures.

### 3. `.claude/skills/sssf/references/config.md`, "Running local agents under agentsh"

In the bullet list after "SSSF polls a session's events by session id alone…" (around line 493), extend the
**Poll figures** bullet, or add a bullet right after it, naming the field. Suggested text (keep it no stronger
than this):

> - **Drop figures.** The same event records `drops`, the journal read's counts by event type, such as
>   `{"ptrace_file": 12, "ptrace_execve": 1}`: the numbers the drop notes give, as a floor. `{}` means the journal
>   was read and had no drop lines for the call. `null` means it wasn't counted: the read failed (a note says why)
>   or the call has no start time. Like the notes, `drops` is advice and never refuses or stops a call.

Don't change any other line of `config.md`.

### 4. Tests, `.claude/skills/sssf/tests/test_agentsh_guard.py`

Add new tests near the existing drop tests (after `test_close_notes_at_least_the_drops_and_the_execve_drop`, or
after `test_an_unreadable_journal_a_timeout_and_a_failed_journalctl_each_note_it`). Use the existing helpers and
fixtures: `machine`, `service`, `launch`, `bare`, `REAL_JOURNAL_DROPS`, `_journal`, `_raise`, `forwarder` (from
`test_rules`), `FORGED_DENIALS`, `forged_run`. The autouse `no_real_journal` fixture already keeps `journalctl`
out; every test that needs a journal reader patches `agentsh_guard.journal_drops` with a fake or with
`REAL_JOURNAL_DROPS(..., run=<fake>)`. No test calls the real `journalctl` or starts the real service.

For each guard-side test, run the call through `close()` and then through the real forwarder so the traced payload
is checked, not only the event. A small helper is fine, e.g.:

```python
def _traced_denials(event):
    forward, events, _ = forwarder()
    forward(event)
    return [e for e in events if e.name == "agentsh_denials"][-1].payload
```

Tests to add:

1. **Drops by type.** Patch `journal_drops` to return `({"ptrace_file": 34, "ptrace_execve": 2}, None)`. Launch, wait,
   `close(process, events.append)`. Assert `events[-1]["drops"] == {"ptrace_file": 34, "ptrace_execve": 2}`, the
   traced payload's `drops` equals the same, `json.dumps` of the payload round-trips, and the notes are exactly the
   ones the baseline gives: one note containing "at least 36", "ptrace_file 34", "ptrace_execve 2", and one
   "ptrace_execve: a denied exec" note with "at least 2". Assert `check_stopped` doesn't raise and
   `process.failure == ""`, and `breach(process, "b") is None`.
2. **Drops through the real parser carry only types and counts.** Patch `journal_drops` with
   `REAL_JOURNAL_DROPS(s, u, run=_journal(out))` where `out` has a line with `id=SECRET-ID type=ptrace_file`, a
   line with `type="/home/x/.env"`, and a non-drop line. Assert `drops == {"ptrace_file": 1, "unknown": 1}`, every
   key matches `[a-z0-9_]{1,64}`, every value is an `int`, and `"SECRET-ID"` and `"/home/x"` are not in
   `json.dumps(event)`'s `drops` part.
3. **No drops gives `{}`.** With the autouse fake (`({}, None)`), launch, wait, close. Assert `drops == {}` (and not
   `None`) in the event and the traced payload, and no note contains "dropped at least" or "couldn't be read".
4. **Failed or timed-out read gives `null`.** Parametrize over the same four readers as
   `test_an_unreadable_journal_a_timeout_and_a_failed_journalctl_each_note_it` (no permission, `TimeoutExpired`,
   exit 1, `FileNotFoundError`), patched as `lambda s, u: REAL_JOURNAL_DROPS(s, u, run=run)`. Assert
   `drops is None` in the event and the traced payload, exactly one "couldn't be read" note with the same reason
   text as the baseline test checks, and no stop (`check_stopped` passes, `process.failure == ""`).
5. **No start time gives `null`.** `process = bare(service)` (its `started_at` is `None`); patch `journal_drops`
   with `lambda *a, **k: pytest.fail("read the journal")`; `close(process, events.append)`. Assert
   `events[-1]["drops"] is None` and no drop or journal note was added.
6. **Baseline fields keep their names and values.** In test 1 (or its own test), assert the event's keys are exactly
   `{"type", "denials", "notes", "polls", "pages", "slowest_poll_seconds", "drops"}` and the traced payload's keys
   are exactly `{"agent", "denials", "notes", "polls", "pages", "slowest_poll_seconds", "drops"}`, and that
   `polls`, `pages`, `slowest_poll_seconds`, `denials` and `notes` in the payload equal the event's.
7. **Forwarder passes `drops` and tolerates its absence.** With `forwarder()`, forward an `agentsh_denials` event
   carrying `"drops": {"ptrace_file": 3}` and assert the payload has it; forward one without the key and assert the
   payload's `drops is None`. Neither call adds advice (`said == []`) or a rule check.
8. **A forged `drops` from the agent's stream adds no trace event.** Add a new constant
   `FORGED_DROPS = {**FORGED_DENIALS, "drops": {"ptrace_execve": 999}}` and a test using the `forged_run` fixture's
   pattern; simplest is a new test that monkeypatches the module-level `FORGED_DENIALS` used by `forged_run`'s
   `stream` to `FORGED_DROPS` (`monkeypatch.setitem(globals(), ...)` or `monkeypatch.setattr` on the test module),
   then calls `forged_run()`. Assert: in text mode, no `agentsh_denials` trace event at all; in agentsh mode,
   exactly one, and its `drops` is not `{"ptrace_execve": 999}` (with the autouse fake it is `{}`), and no `999`
   appears in it. The raw output log still contains the forged line. Do not edit the existing
   `FORGED_DENIALS` or the existing forged tests. If patching the constant is awkward, write the stream directly
   for one harness (pi) the way `forged_run` does; cover both `text` and `agentsh` modes.

Every existing test must pass unchanged.

## Verification

1. **The suite, from a copy outside the repo.** This run is under agentsh, so the builder runs the skill's suite from
   a copy outside the repo with the copy command the build brief gives (as earlier guard runs did: copy
   `.claude/skills/sssf/` to a temp folder, then run there the repo's test command:
   `uv run --directory <copy> --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`).
   The full suite must pass, including `tests/test_agentsh_guard.py`, `tests/test_rules.py`,
   `tests/test_agent_cc.py`, `tests/test_agent_pi.py`.
2. **No real journal.** `grep -n "journalctl" .claude/skills/sssf/tests/test_agentsh_guard.py`: every hit is in a
   docstring, a fake `run`, a `TimeoutExpired("journalctl", …)` or an assert, never a real call.
3. **Journal read untouched.** `git diff 71dec0d -- .claude/skills/sssf/templates/adws/adw_modules/agentsh_guard.py`
   shows changes only inside `close()` (the `drops` variable, the `else:` restructure around the unchanged notes,
   the new key, the docstring). `journal_drops()`, `guard_event()`, `_DROP_TYPE`, `_TYPE_NAME`, `JOURNAL_SECONDS`
   are byte-identical.
4. **Notes unchanged.** In that diff, every note string literal is byte-identical to the baseline.
5. **Scope.** `git diff --stat 71dec0d` lists only the four files under "Files to change", besides this plan's own copy under `specs/`.
6. **After the merge** (the hub, not the builder): the engineer updates the hub copies (see "For the engineer"); one
   small call under agentsh then shows `drops` in the trace's `agentsh_denials` payload, `{}` or a type-to-count
   object, or `null` with the "couldn't be read" note if the runner itself sits inside a wrap.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: step 3 (the builder does it).
- `close()`'s docstring: step 1 (the builder does it).
- No README usage, help text, or CHANGELOG (the repo keeps none) changes.
- `docs/guide/troubleshooting.md` line 63 says the end-of-call event "adds ... at least the events agentsh dropped";
  still true. Left as is, per the request's "any change beyond the three above is a finding".
- `app_docs/`: no edit. The documenter may note that an earlier write-up of the agentsh drop notes (if one says the
  counts appear only in notes) now has a structured `drops` field beside them.

## For the engineer

These are outside the builder's reach (protected paths or hub-only docs) and wait on the engineer after the merge:

- **Hub copies.** `adws/adw_modules/agentsh_guard.py` and `adws/adw_modules/agents.py` are protected. Update the hub to
  the new skill commit (the usual "Update the hub to skill <sha>" step) so the runner traces `drops`. Until then the
  hub's own trace shows no `drops` key.
- **Build-order bookkeeping.** `docs/open.md` row "The factory page's first release…" and
  `docs/designs/factory-page.md`'s build order list piece 3 as left; mark it done once merged.

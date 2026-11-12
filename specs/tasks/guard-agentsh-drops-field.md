# Record agentsh's dropped events as numbers in the end-of-call event

Start from `main`, which has the factory page's first two pieces (502e91d, 3e86590). This is piece 3 of the factory page's build order in `docs/designs/factory-page.md`.

## The problem

`close()` in `agentsh_guard.py` counts the events agentsh dropped during a call from its journal (`journal_drops()`), by event type. It writes the counts only into sentences in `trace_notes`: "agentsh dropped at least N event(s) during this call ... : ptrace_file 12, ptrace_execve 1", and a second note for a dropped `ptrace_execve`. The `agentsh_denials` event carries `denials`, `notes`, `polls`, `pages` and `slowest_poll_seconds`, and no drop figures. A reader that wants the numbers, such as the factory page's Machine row, would have to parse the note text, whose wording changed twice in the week of 2026-11-08. And a journal that couldn't be read looks the same in the event's fields as a call with no drops: only a note says which.

## What should change

1. **The `agentsh_denials` event carries `drops`.** When the journal was read, `drops` is an object from event type to count, `{}` when nothing was dropped. When it couldn't be read, or the call has no start time, `drops` is `null`. The counts are the ones `journal_drops()` returns now, the same that go into the note. `agents.py` passes the field into the traced payload beside the existing ones.
2. **The notes stay as they are.** Same text, same conditions.
3. **`config.md`**, in "Running local agents under agentsh", names the `drops` field and what `null` and `{}` mean, beside the poll figures.

## The bar

The earlier bars stand. Under `local_guard: agentsh`:

- The drop counts stay advice: nothing new refuses or stops a call, and every refusal and stop at the baseline stays as it was.
- The journal read is unchanged: same command, same user, same window, same timeout, same parsing. `drops` holds event types and counts only, never an event id, path or other content.
- Every field the event carried at the baseline keeps its name and value.
- An `agentsh_` event from the agent's own stream is still dropped before it reaches the forwarder, so an agent can't set `drops`.

Runs under `local_guard: text` don't change. Any change beyond the three above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests, with a fake journal reader as the existing drop tests use: the traced `agentsh_denials` payload has `drops` equal to the counts by type when there were drops; `{}` when the journal read found none; `null` when the read failed or timed out; and the existing notes are unchanged in each case. A stream line of type `agentsh_denials` carrying `drops` still adds no trace event. The tests never call the real `journalctl` or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`. The runner uses its own checkout's module, so the field reaches the trace only after the merge. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

## Out of scope

The drop counting itself and its known limits in `config.md` (journald's rate limit, drops not tied to a session, a faked drop line). Counting drops as refusals. The factory page, which reads the field later. pi's policy.

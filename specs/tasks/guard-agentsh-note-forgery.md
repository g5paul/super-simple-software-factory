# Keep an agent from forging agentsh's notes, and tighten the drop-line parser

Start from `main`, which has run 0463a6c3 merged as db8d6eb. These are the broken promise and the three unlisted old holes from that run's bypass review, `adws/adw_data/sessions/0463a6c3/context_handoff/bypass_review_1.md`.

## The problem

1. **An agent can forge an `agentsh_note`, and before run 0463a6c3, an `agentsh_denials`.** The guard hands its own events to `on_event`, and so do both harnesses' read loops (`agent_cc.py` and `agent_pi.py`), for every JSON line in the harness's stdout. `agents.forward` acts on any event whose `type` is `agentsh_note` or `agentsh_denials`, whoever sent it. A command that writes `{"type":"agentsh_note","note":"…"}` to the harness's stdout, such as `printf … > /proc/$PPID/fd/1` from Claude Code's Bash tool, gets its text printed on the engineer's console as a yellow `agentsh:` warning. One that writes an `agentsh_denials` line adds a trace event with denials and notes of its choosing. This happens in every mode, `local_guard: text` included. Under agentsh, writes to `/proc` are denied, so that spelling is likely blocked there. The `RULE_EVENT_TYPES` comment in `agents.py` already says a command can add events to the stream.
2. **The drop parser's type key has no word boundary.** `_DROP_TYPE` in `agentsh_guard.py` matches the first key that ends in `type`, so in `… subtype=ptrace_file type=ptrace_execve` it counts `ptrace_file`, which would hide the dropped-exec note.
3. **Two limits `config.md` doesn't list.** The journal window is in whole seconds and ends when the call closes, so a line journald delivers late, or one logged after the read, isn't counted. And a line logged under the `agentsh` unit can carry agent-chosen text: one with "event channel full" and a `type=` value adds a count, and its type, any `[a-z0-9_]{1,64}`, is printed in the note.

## What should change

1. **The guard's events reach the forwarder only from the guard.** An event from a harness's stream whose `type` starts with `agentsh_` is dropped before it reaches `on_event`, in both read loops, in every mode. The guard's own `agentsh_note` and `agentsh_denials` events are handled as now. The spec says where the filter goes and why it can't be reached from the stream, and whether a separate callback for the guard's events is smaller.
2. **`_DROP_TYPE` matches `type` only as a whole key**, with a boundary before it, so `subtype=` and `event_subtype=` aren't read as the type.
3. **`config.md`, in "Running local agents under agentsh"**, lists the two limits in problem 3, with what still applies: the drop counts and their notes are advice, and never refuse or stop.

## The bar

The earlier bars stand. In every mode:

- A line in a harness's stream whose `type` starts with `agentsh_` prints nothing on the console and adds no trace event.
- The guard's own notes still print before the agent starts, and its end-of-call event still reaches the trace with the same fields.
- Every other stream event reaches the stream check as at the baseline.
- Every refusal and stop at the baseline stays as it was.

Under `local_guard: agentsh`, the drop parser counts `type=ptrace_execve` in a line that also carries `subtype=ptrace_file`.

Any change to the guard beyond the three above is a finding, and so is a `config.md` line that promises more than the tests show.

## Testing

Unit tests: for each harness, a fake stream holding an `agentsh_note` line and an `agentsh_denials` line gives no console advice and no trace event, and a rule event beside them is still checked; the guard's own note and end-of-call event still reach the console and the trace; `_DROP_TYPE` on lines with `subtype=`, `event_subtype=`, `type=` and `event_type=`. The tests never call the real `journalctl` or start the real service.

## This run is under agentsh

The hub stays on `local_guard: agentsh`, now with run 0463a6c3's notes, so this run is also their first real use. The builder runs the suite from a copy outside the repo, as earlier runs did.

## Out of scope

The three old holes `config.md` already lists: dropped execs uncounted, journald's rate limit, drops not tied to a session. Widening the journal window. Text in the stream of any type not starting with `agentsh_`. The fork's code. pi's policy.

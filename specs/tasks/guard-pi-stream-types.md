# On a pi stream, read only pi's event types

`agents._event_forwarder`'s `forward` reads every event through helpers
that understand both harnesses: `rules.calls`, `rules.results`,
`rules.denied` and the `pi_result` test around line 539. It never asks which
harness wrote the stream. So a pi stream also accepts Claude Code's event
types: a `system` `permission_denied` event, and a `user` event's
`tool_result` block, whose text passes the refusal test because it isn't a
pi result.

An agent's command can write to its harness's stdout through
`/proc/$PPID/fd/1`, and the stream check reads that stdout. Guard run
6cec4b80's review (`adws/adw_data/sessions/6cec4b80/context_handoff/guard_report.md`)
forged a Claude Code `permission_denied`, or a `user` `tool_result` with the
refusal text, on a pi stream after a slipped command, and the call counted
as refused. `docs/open.md` tracks it with the rest of the forgeries.

## What should change

- The forwarder knows which harness it reads, from what the caller already
  has (the agent's interface or roster entry), not from the events.
- On a pi stream, Claude Code's event types count for nothing: no
  `permission_denied`, no `user` `tool_result`, no `assistant` `tool_use`
  announcement. On a Claude Code stream, pi's types count for nothing the
  same way. Find every place the forwarder or what it calls reads an event
  of the other harness's type, including the call tracker if it matters for
  rules, and say in the spec which ones change.
- Keep every honest stream's behavior the same on both harnesses.
- `docs/open.md`: shrink the forgery row to what remains, the forged pi
  `tool_execution_end` and the forged usage-limit turn, and their real fix,
  a VM or a separate user. `references/config.md` changes only if it names
  the closed case.

## Tests

- On a pi stream: a refuse-rule call that ran, then a forged
  `permission_denied` for its id, raises `RuleBreach`; the same with a forged
  `user` `tool_result` carrying the refusal text.
- On a Claude Code stream: a forged pi `tool_execution_end` without
  `durationMs` for a call doesn't count as refused.
- Every existing rules, refusal and stream-check test passes unchanged.

## Out of scope

The forged pi `tool_execution_end` and the forged usage-limit stop: both
need a boundary the agent can't write past. Everything outside the
forwarder's choice of which event types to read.

## Earlier attempt

Guard run b5c71e3b planned this, its spec review approved the second
version (`git show 2c64dd8:specs/b5c71e3b_harness-event-types.md`), and its build passed the full skill suite
and the chain tests. It failed only because its plan check,
`uv run pytest ...`, can't start in the hub. Start from that spec, recheck
it against the code, and write checks that run here, the way the hub's
quality commands run tests.

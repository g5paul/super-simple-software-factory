# Don't crash on Claude Code's permission_denied event

The secret-file guard (merge 9bef4a4) passes Claude Code agents one
`--disallowedTools "Read(//<abs>)"` per protected file. Two live runs in
sssf-sandbox on skill a3c2536 (34fb3c8c and ee920adc) showed the deny works:
Read, Grep and even Bash `cat .env` were refused, and the file never reached
the agent. But each run then failed in its agent phase with
`'str' object has no attribute 'get'`, at `agent_cc.py:170`, in
`ToolCallTracker.observe`.

Each denial makes Claude Code 2.1.319 emit a `system` event whose `message` is
a string, not a dict. Recorded from run 34fb3c8c, with the path generalised:

    {"type": "system", "subtype": "permission_denied", "tool_name": "Grep", "tool_use_id": "toolu_01BdBmiNqfMBM4JPshZVxodW", "decision_reason_type": "rule", "message": "Permission to read /repo/.env has been denied.", "uuid": "bacf0074-14fc-4c40-9871-972f71ae02f7", "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"}

and from ee920adc, for Bash:

    {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_011E3m58bgpqe7LGELGbwmZ2", "decision_reason_type": "rule", "message": "Permission to use Bash with command cat .env has been denied.", "session_id": "2b34dc8b-a340-4c0c-82bb-046a7632da0a"}

`observe` does `(event.get("message", {}) or {}).get("content", [])`, which
raises on a string. So an agent that reaches for `.env` by accident, the case
the guard exists for, kills its run instead of reading a refusal and going
on. It fails closed; nothing leaks.

## What should change

- Every place in agent_cc that reads an event's `message` treats one that is
  not a dict as having no content, so the call goes on. Check the stream loop
  in `run` too, not only `observe`: the `assistant` branches read `message`.
- A denied call is still recorded as a failed tool call in the trace, from
  its `tool_result` with `is_error: true`, as today.
- Nothing else changes: no new handling of `permission_denied` events beyond
  not crashing on them.

## Tests

- Replay a stream through agent_cc's parsing that holds a `tool_use` for
  Read of `.env`, the `tool_result` with `is_error: true` and the
  `<tool_use_error>File is in a directory that is denied by your permission
  settings.</tool_use_error>` content, then each `permission_denied` event
  above, then a normal result. The call ends normally and the denied call is
  a failed tool call.
- An `assistant` event whose `message` is a string doesn't raise.

## Out of scope

Everything the secret-file guard's own task files list. The engineer reruns
both live proofs in sssf-sandbox after the merge.

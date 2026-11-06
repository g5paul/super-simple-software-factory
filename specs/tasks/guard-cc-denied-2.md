# Count Claude Code's own denial of a call as a refusal

Run 05259b95 on this branch (6ef1833) stopped agent_cc crashing on Claude
Code's `permission_denied` event. Its bypass review found one broken promise,
and this task closes it. Everything else that run built stays.

## The broken promise

The secret-file guard passes Claude Code agents `--disallowedTools
"Read(//<abs>)"` per protected file. Claude Code applies those deny rules to
Bash file reads too: in live run ee920adc (sssf-sandbox, claude 2.1.319) the
agent's Bash `cat .env` was denied before SSSF's shell prefix
(`sssf_refuse.py`) ran, and Claude Code emitted:

    {"type": "system", "subtype": "permission_denied", "tool_name": "Bash", "tool_use_id": "toolu_011E3m58bgpqe7LGELGbwmZ2", "decision_reason_type": "rule", "message": "Permission to use Bash with command cat .env has been denied."}

SSSF's built-in secret refuse rule also matches `cat .env`, so
`agents._event_forwarder` holds the call as pending and waits for SSSF's
refusal text (`rules.REFUSED`) in its result. The result is Claude Code's
denial instead, so the forwarder decides the command ran and raises
`RuleBreach`. Nothing ran and nothing leaked, but the run stops, which breaks
config.md's promise that the agent reads the refusal and goes on.

## What should change

- When Claude Code emits `permission_denied` for a call, by its
  `tool_use_id`, that call counts as refused, exactly as if SSSF's refusal
  were its result: it counts toward `MAX_REFUSALS` like any refusal, and it
  never counts as a command that ran. Match by `tool_use_id`, never by the
  message text.
- A call with no `permission_denied` event is judged as today. A refuse-rule
  match whose result is neither SSSF's refusal nor a Claude Code denial still
  raises `RuleBreach`.
- A `forbid` rule without `on_match: refuse` still stops the run on a match,
  denied by Claude Code or not, as today.
- config.md says Claude Code may deny a protected-file read itself, in any
  tool, and that SSSF counts that as a refusal.

## Tests

- Replay through agent_cc and the forwarder: `tool_use` Bash `cat .env`,
  the `permission_denied` event above with the matching id, the call's
  `tool_result` with `is_error: true`, then a normal result. The call ends
  normally, with one refusal counted and no RuleBreach.
- The same with three denied calls raises RuleBreach at MAX_REFUSALS, as
  three SSSF refusals do.
- A `permission_denied` event for another call's id doesn't excuse a matched
  call whose result is not a refusal: that still raises RuleBreach.
- A stop-mode `forbid` match still raises RuleBreach when Claude Code denies
  the call.

## Out of scope

The out-of-scope lists of the secret-file guard's task files. The engineer
reruns both live proofs in sssf-sandbox after the merge.

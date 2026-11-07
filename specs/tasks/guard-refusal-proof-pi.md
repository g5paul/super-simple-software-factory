# Count a pi refusal only when pi shows it never ran the call

This replaces guard run 3e98eebd's change (branch `guard-refusal-proof`,
build d5c3d9f, spec `specs/3e98eebd_refusal-needs-harness-proof_v2.md` on
that branch; read both with `git show`). That run passed, but on Claude Code
it dropped text-matched refusals entirely, which turns every refuse rule
into a stop on Claude rosters. The engineer chose to fix pi only.

## The hole

The stream check in `agents.py` (`forward`) accepts a refuse-rule call as
refused when `rules.refused(text)` is true. The agent controls that text.
On pi a command can slip past `sssf_rules.ts` through a Python/JavaScript
regex difference `references/config.md` lists, run, and print "SSSF refused
this command", and the run goes on.

## What should change

- **pi:** count a refuse-rule call as refused only when the text reads as a
  refusal and pi's result shows it never ran the call. 3e98eebd's spec found
  that pi 1.4.0 sets `durationMs` on every call it executes and on none it
  blocks, and its `rules.blocked()` reads that. Reuse that finding and that
  function; recheck it against pi's source on this machine. Otherwise
  `RuleBreach`.
- **Claude Code:** keep today's behavior: a result matching `_CLAUDE_REFUSED`
  or a `permission_denied` for the call counts as refused. A real prefix
  refusal can't be told from a forged one there; the prefix and the stream
  check both use Python `re` on the same command, so a forged one needs the
  prefix to miss, which needs a change in Claude Code's command wrapping.
  Say this in `references/config.md` and in `sssf_refuse.py`'s docstring,
  and add a `docs/open.md` row (kind `watch`) for it.
- **A call still pending when the agent exits.** 3e98eebd's review found
  `<slipped command>; kill -9 $PPID`: the command runs, pi dies before the
  result, and the run ends as "pi exited 137" with no `rule_breach`. When an
  agent's process ends with a refuse-rule call still pending, raise
  `RuleBreach` for it, on both harnesses, if that is a small change in the
  forwarder or the code that ends the agent. If it isn't small, leave it and
  add a `docs/open.md` row instead, and say why in the spec.
- Leave `templates/repo.yaml` unchanged: editing it flags the file for
  review in every repo.
- `docs/open.md`'s regex row: say the differences now decide only whether a
  pi run stops or the agent is refused.

## Tests

- pi: a refuse-rule call that ran (has `durationMs`) and printed the refusal
  raises `RuleBreach`; a real block under bun, driven as
  `tests/test_rules_multiline.py` does, is still counted, logged as
  `rule_refused`, and the third still stops the run.
- Claude Code: every existing refusal test passes unchanged.
- The pending-call stop, if built: a pi stream that announces a refuse-rule
  call and ends without its result raises `RuleBreach`.
- The built-in secret-file rule's refusals behave as today on both.
- Every existing rules, refusal and stream-check test passes, changed only
  where a pi test relied on a text-only refusal.

## Out of scope

Making Python and JavaScript read patterns the same. Pi's file-tool
refusals. Closing the Claude Code case.

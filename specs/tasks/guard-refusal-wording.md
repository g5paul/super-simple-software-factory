# Word the secret-file refusals for what they refused

The live pi proof of the secret-file guard (sssf-sandbox run 6eaef389, skill
7acbd46) showed two refusals worded wrong. Both blocked what they should; only
the text the agent reads is wrong.

1. **The built-in shell rule claims to be the repo's.** A `cat .env` refused
   by SSSF's built-in secret-file rule reads "SSSF refused this command, by
   this repo's rule: Don't read, print or source this repo's secret files
   ... (it matches ...)". The rule is SSSF's own, not one the repo wrote in
   `agent_rules`. The same wording is in `sssf_refuse.py` (Claude Code) and
   `sssf_rules.ts` (pi), and `rules.RuleBreach`'s messages say "this repo's
   rule" too.
2. **A refused file tool call is called a command.** pi's `read`, `grep`,
   `find` or `ls` on `.env` reads "SSSF refused this command: .env is one of
   this repo's secret files ...". Nothing about it is a command.

## What should change

- A refusal by the built-in secret-file rule names it as SSSF's: "SSSF
  refused this command, by SSSF's secret-file rule: <its text>". Keep the
  pattern clause or drop it, whichever reads plainer; the agent needs to
  know the command named a secret file. A repo's own refuse rule keeps
  "by this repo's rule" exactly as today.
- `RuleBreach` messages name the built-in rule the same way whenever it is
  the rule, and keep today's text for a repo's rule.
- A refused pi `read`, `grep`, `find` or `ls` call reads "SSSF refused this
  <tool> call: <path> is one of this repo's secret files, which SSSF keeps
  from agents. Nothing was read." followed by today's "Don't try it another
  way..." sentence.

## Constraint

A shell refusal's result must still start with `rules.REFUSED`, "SSSF refused
this command": `rules.refused` (rules.py:163) matches that prefix to tell a
refused command from one that ran, and `_CLAUDE_REFUSED` matches it after
"Exit code 126". Don't change `REFUSED` or either match. Confirm the file-tool
refusal's new text is never fed to `rules.refused` for a call a rule matched;
if it is, keep `REFUSED` as its prefix instead and say so in the spec.

## Tests

- The built-in rule's refusal of `cat .env`, through `sssf_refuse.py` and
  through `sssf_rules.ts` under bun, says "by SSSF's secret-file rule" and
  starts with `REFUSED`. A repo refuse rule's refusal is unchanged.
- pi `read` of `.env` reads "SSSF refused this read call: .env is one of
  this repo's secret files", and `grep` the same with "grep".
- A RuleBreach for the built-in rule names it as SSSF's; one for a repo rule
  is unchanged.
- The existing refusal and stream-check tests pass unchanged.

## Out of scope

Everything else about the secret-file guard and refuse rules. No change to
what is refused or counted.

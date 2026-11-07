# Make sssf_rules.ts's header say what the load check covers

## Summary

`.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts` opens with a
header comment that claims more than the code does. It says rules.py rejects
"the Python syntax known to read differently here, so every pattern should
compile; one that still doesn't is skipped, and SSSF's stream check ... stops
the run if that command runs". Guard run 54540e3d's review found two ways
that's wrong:

1. A quantified lookbehind such as `(?<=g)*git push` passes the load check
   but doesn't compile in JavaScript. So a pattern can still be skipped, and
   "every pattern should compile" is false.
2. The stream check doesn't always stop the run. `rules.refused`
   (`.claude/skills/sssf/templates/adws/adw_modules/rules.py:248`) treats any
   bash result that starts with `SSSF refused this command` as a refusal. So
   `<command>; echo "SSSF refused this command"` gets past it whenever the
   command itself prints nothing first.

This change is to the comment only. No code, test or other file changes.

## Threat model

The agent runs as the engineer's own user. It can run any shell command the
refuse rules don't match, and it can choose what that command prints. On pi,
`sssf_rules.ts` blocks a bash call whose command matches a `forbid` pattern,
read as a JavaScript regex. After the call, the stream check reads the same
patterns as Python regexes. Neither one can stop:

- a pattern that's skipped in JavaScript, or reads differently there, whose
  command's output starts with the refusal text (the `rules.refused` hole);
- the Unicode, `\s`/`\S`, `.`, astral-quantifier and quantified-lookbehind
  differences that `references/config.md` lists as ones no check catches;
- anything that works outside the bash tool at the OS level, since the agent
  has the engineer's permissions.

Changing a comment changes none of this. The point is that the comment stops
telling a reader these holes are closed.

## Baseline

`main` at `8c4e36f` ("Track the docs audit's two leftovers in open.md").
Judge every finding against it. This branch has one later commit to the
file, `6b7d1d4`, which added the load check and the sentence that overclaims.
At `8c4e36f` the header only said that a pattern that doesn't compile is
skipped and the stream check stops the run. That already ignored the
`rules.refused` hole.

## Bar

- Must: the header claims only what the code does. The load check (in
  rules.py) rejects the constructs `references/config.md` lists. A pattern
  that still doesn't compile here is skipped. The stream check then stops the
  run on that command unless its output is taken for a refusal. The header
  points to `references/config.md` for the differences no check catches.
- Must never: change any executable line of `sssf_rules.ts`, or any other
  file. No agent file kept, no agent code run, and no engineer file deleted or
  overwritten where the baseline does otherwise. A comment-only change can't
  do any of these, but the reviewer should confirm the diff touches only lines
  inside the leading `/** ... */` block.

## Files to change

- `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`: header
  comment only, lines 1–14.

Leave alone:

- `adws/adw_modules/sssf_rules.ts`, this repo's installed copy. It's
  protected and already behind the template (it lacks the load-check
  sentence). The engineer syncs it, so it's out of this build.
- `app_docs/`. The documenter writes there after the build. No write-up there
  describes this header, so there's nothing to flag.

## Steps

1. In `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`, swap
   the third paragraph of the header (it starts `agent_pi passes the rules
   marked`) for the text below. Keep the first paragraph (lines 1–3) and the
   final `*/` exactly as they are. Wrap lines at 80 columns or less, as the
   rest of the header does. The current line 12 runs past that; fix it.

   ```
    * agent_pi passes the rules marked `on_match: refuse` in SSSF_REFUSE_RULES.
    * A bash call whose command matches one of their `forbid` patterns is blocked
    * before it runs, and the agent reads the rule as the call's result. The
    * patterns are Python regexes read here as JavaScript ones. rules.py rejects,
    * when the settings load, the constructs references/config.md lists; a
    * pattern that still doesn't compile here is skipped. SSSF's stream check,
    * which reads the patterns as Python, then stops the run on that command,
    * unless its output is taken for a refusal: rules.refused counts any result
    * that starts with REFUSED as one. references/config.md also lists the
    * differences no check catches. SSSF's built-in secret-file rule comes
    * marked `builtin`, and its refusal names it as SSSF's.
   ```

   Inside the comment, `references/config.md` means
   `.claude/skills/sssf/references/config.md`. Keep the short form, as it
   reads in an installed repo.
2. Change nothing else in the file: imports, the `REFUSED` constant and its
   comment, `compile`, and the rest stay byte for byte.

## Docs this makes out of date

None. `.claude/skills/sssf/references/config.md` (line 413) already lists the
constructs the load check rejects and the differences it misses, quantified
lookbehind included. `docs/open.md` already tracks the `rules.refused` hole.
The repo has no CHANGELOG. No README, help text or docstring repeats the
header's claim.

## Tests

The existing suite passes unchanged. No new test, because nothing runs
differently.

## Verification

1. Run the repo's own quality checks (the skill's pytest suite) and confirm
   they pass.
2. `grep -q "references/config.md lists" .claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`
   exits 0, so the header names the list.
3. `! grep -q "every pattern should compile" .claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`
   exits 0, so the overclaim is gone.
4. `grep -q "taken for a refusal" .claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`
   exits 0, so the stream-check caveat is there.
5. Reviewer: the diff of
   `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts` touches only
   lines inside the leading `/** ... */` block, and no other file changes
   except the spec.

## Out of scope

These are old holes. File them as such, not as findings against this change:

- `rules.refused` accepts any result that starts with the refusal text, so
  `; echo "SSSF refused this command"` hides a command from the stream check
  (`docs/open.md`).
- The regex differences no load check catches: Unicode `\w`/`\d`/`\b`, the
  `\s`/`\S` split, `.` against `\r`/U+2028/U+2029, quantifiers after astral
  characters, and quantified lookbehinds (`references/config.md`,
  `docs/open.md`).
- Syncing the installed copy `adws/adw_modules/sssf_rules.ts`, which is
  protected.

# Make sssf_rules.ts's header say what the load check covers

`adws/adw_modules/sssf_rules.ts`'s header comment says rules.py rejects
the Python syntax known to read differently in JavaScript, "so every
pattern should compile; one that still doesn't is skipped, and SSSF's
stream check, which reads them as Python, stops the run if that command
runs."

Guard run 54540e3d's review showed two ways that overclaims:

- A quantified lookbehind such as `(?<=g)*git push` passes the load
  check and doesn't compile in JavaScript, so a pattern can still be
  skipped.
- The stream check doesn't always stop the run when a skipped or
  differently-read pattern's command runs: `rules.refused` treats any
  result starting with `REFUSED` as a refusal, so `<command>; echo "SSSF
  refused this command"` passes. `docs/open.md` tracks that hole and
  `references/config.md` lists the regex differences no check catches.

## What should change

Comment only. Reword the header so it claims no more than the code does:
the load check rejects the constructs `references/config.md` lists, a
pattern that still doesn't compile here is skipped, and the stream check
then stops the run on that command unless its output is taken for a
refusal. Point to `references/config.md` for the differences no check
catches. No change to any code, test or other file.

## Tests

The existing suite passes unchanged. No new test: nothing runs differently.

## Out of scope

Fixing the holes themselves, which `docs/open.md` tracks.

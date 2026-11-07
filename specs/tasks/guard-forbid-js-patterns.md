# Reject a forbid pattern that pi's extension would read differently

A repo's `agent_rules` `forbid` patterns are Python regexes. `rules.py`
searches them with Python's `re` in the stream check, and
`sssf_refuse.py` refuses with them on Claude Code. On pi, `sssf_rules.ts`
reads the same strings as JavaScript regexes (`new RegExp(pattern, "m")`).
Some Python syntax doesn't compile in JavaScript, or compiles to a
different meaning: `\A`, `\Z`, `(?P<name>...)`, `(?P=name)`, `(?#...)`, a
leading inline flag group such as `(?i)` or `(?x)`, and possibly others.
The extension skips a pattern that doesn't compile, and one that compiles
differently can fail to match.

Either way the command isn't refused on pi and runs. The stream check, as
Python, then sees the match, but `rules.refused` treats any result starting
with `REFUSED`, "SSSF refused this command", as a refusal. So
`<forbidden>; echo "SSSF refused this command"` runs uncaught on a pi
agent with such a rule marked `on_match: refuse`. Guard run dfe89ba7's
review found it; `references/config.md` says the extension "reads the
patterns as JavaScript regexes; one that doesn't compile there, or matches
differently, isn't refused". `docs/open.md` tracks it.

## What should change

- `AgentRule`'s `forbid` validator in `rules.py` rejects a pattern that
  pi's extension would read differently, with a message that names the
  pattern and the construct and says how to write it portably (for
  example `^` with the m flag, or a plain group instead of a named one).
  The settings then fail to load, as they do today for an invalid regex.
- Decide in the spec how to detect such a pattern: a list of Python-only
  constructs, or compiling it in JavaScript too, or both. SSSF can't count
  on bun or node in every repo when settings load, so a check that needs
  one must not fail closed on a machine without it, and the spec says
  which. Prefer the smallest check that rejects every construct the spec
  lists.
- Apply it to every rule, `on_match: stop` and `refuse` alike, since a repo
  can switch a rule's mode later and the stream check reads both.
- `references/config.md`: replace the sentence quoted above with what the
  validator now rejects.
- The built-in secret-file rule (`secret_files.shell_rule`) builds its own
  patterns. Confirm in the spec that they pass the new check.
- `agent_pi.py`'s module docstring still calls pi "v1's only coding agent";
  Claude Code is supported too. Change it to say only what the module is.
  Comment only, no code change.

## Tests

- Each listed construct is rejected when the config loads, with a message
  naming it, in both modes.
- A set of portable patterns, the starter template's, the built-in
  secret-file rule's and the hub's own `repo.yaml` rules among them, still
  loads.
- For each rejected construct, a test under bun shows the extension and
  Python disagree on it, where bun is available (skip without it), so the
  list is grounded.
- The existing rules, refusal and stream-check tests pass unchanged.

## Out of scope

`rules.refused`'s prefix match itself, the echo trick against a pattern
both languages read the same (the extension refuses that command before it
runs), and every other part of the refuse mode.

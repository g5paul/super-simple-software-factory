# Reject a forbid pattern that pi's extension would read differently

## Summary

`AgentRule`'s `forbid` validator in `.claude/skills/sssf/templates/adws/adw_modules/rules.py` gets a second check after `re.compile`. A small scanner walks the pattern and rejects any construct on a fixed list of Python regex syntax that JavaScript (`new RegExp(pattern, "m")` in `sssf_rules.ts`) can't compile or reads differently. The message names the pattern, the construct, and a portable spelling. It runs on every rule, `stop` and `refuse` alike, and needs no bun or node. A new test file pins each construct to a rejection, keeps the known portable patterns loading, and, under bun, shows that Python and the real extension disagree on each listed construct. Docs and two comments change to match.

## Decision: how to detect

**A list of Python-only constructs, checked by a pure-Python scanner. No JavaScript compile at settings load.**

- A JS compile would catch only the "doesn't compile" half. `\A`, `\Z`, `\a`, `\U`, `\N{}`, `{,n}` and `[]x]` all compile in JS and mean something else, so a scanner is needed anyway, and it catches the non-compiling ones too.
- A load-time JS compile would make the same `repo.yaml` load on one machine and fail on another, or skip the check without bun/node, which fails open. The scanner always runs, so it fails closed everywhere, with no dependency.
- The bun test grounds the list (each entry really does disagree). It is a test, not a runtime check.

Grounded on this machine (Python 3.13, bun 1.4.4, node v24.11.1). For every case below, Python's `re.search(p, c, re.M)` matched and both bun and node either threw (ERR) or returned false:

| Construct | Example pattern / command | JS |
|---|---|---|
| `\A` | `\Agit push` / `git push` | false (literal `A`) |
| `\Z` | `push\Z` / `git push` | false (literal `Z`) |
| `\z` (Python 3.14+: same as `\Z`; earlier Python rejects it as invalid) | `push\z` | false |
| `\a` (bell) | `git\apush` / `git\x07push` | false (literal `a`) |
| `\U` (8-hex escape) | `git\U00000020push` / `git push` | false |
| `\N{...}` (named char) | `git\N{SPACE}push` / `git push` | false |
| named group `(?P<name>...)` | `(?P<g>git) push` | ERR |
| named backreference `(?P=name)` | `(?P<g>git) (?P=g)` / `git git` | ERR |
| comment group `(?#...)` | `git(?#c) push` | ERR |
| inline flag group, leading or scoped: `(?i)`, `(?x)`, `(?s)`, `(?a:...)`, `(?x:...)`, `(?u:...)` | `(?i)git push` / `GIT push`; `(?a:git) push` / `git push` | ERR |
| conditional `(?(1)...|...)` | `(git)?(?(1)\spush|push)` / `git push` | ERR |
| atomic group `(?>...)` (Python 3.11+) | `(?>git) push` | ERR |
| possessive quantifier `*+` `++` `?+` `{m,n}+` (Python 3.11+) | `gi++t push` / `giit push` | ERR |
| empty-minimum quantifier `{,n}` / `{,}` | `gi{,2}t push` / `giit push` | false (literal) |
| class opening with `]`: `[]x]`, `[^]x]` | `[]x]` / `]` | false (JS `[]` is the empty class) |

Scoped `(?i:...)`, `(?-i:...)` and `(?i-s:...)` compile in bun 1.4.4 and node 24 (ES2025 modifiers), but older node rejects them, and `a`, `x`, `u` never compile. So the scanner rejects every inline flag group, scoped or not. In the bun grounding test, the scoped case uses `(?a:git) push`, which bun rejects.

## Threat model

- The agent runs as the engineer's own user. It can run any shell command its harness allows, and on pi can append `; echo "SSSF refused this command"` to a forbidden command so the stream check's `rules.refused` takes the output for a refusal.
- The agent can't change `repo.yaml` (`adws/adw_sssf_config/` is protected: the writes guard catches the edit after the fact). So the patterns come from the engineer, and this guard is about the engineer writing a rule that pi silently doesn't enforce.
- Hole closed: a rule whose pattern uses a listed construct is no longer loaded. Before, pi skipped or misread it, and the echo trick ran the command uncaught. Now the run doesn't start until the engineer rewrites the pattern portably.
- What this guard can't stop at its level: semantic differences inside constructs both languages accept (see Out of scope), obfuscated commands no regex catches (`eval "$(base64 -d …)"`, built-up strings), and anything the agent does as the engineer's user outside a shell call the rules read. The container and VMs are the boundary. Rules are an accident guard.

## Baseline

`main` at `8c4e36f` ("Track the docs audit's two leftovers in open.md"). Every finding is judged against it. At that commit, `AgentRule._compiles` only runs `re.compile`, so every listed construct loads, and pi's extension skips or misreads it.

## Bar

Must:
- Reject, at settings load (`repo_settings.load` → `AgentRule` validation → `ValidationError`), every `forbid` pattern containing a listed construct, in rules with `on_match: stop` and `on_match: refuse` alike. The message names the pattern, the construct, and how to write it portably.
- Keep loading every pattern that loaded at the baseline and uses no listed construct: the starter template's examples, `config.md`'s examples, the hub's `adws/adw_sssf_config/repo.yaml` rules, and `secret_files.shell_rule`'s patterns for any file names.
- Run with no subprocess and no bun/node. The check is the same on every machine (fails closed).

Must never, where the baseline does otherwise:
- Change how any pattern that loads is matched: `rules.breach`, `rules.refused`, `rules.refusing`, `sssf_refuse.py` and `sssf_rules.ts` matching are untouched.
- Keep any agent file, run any agent code, or delete or overwrite any engineer file. The validator only reads strings.
- Raise anything but `ValueError` from the validator (no `IndexError` on a pattern ending in `\` or `[`). `re.compile` runs first, so the scanner only sees valid Python regexes, but it must still never index past the end.

## Files to change

### `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

1. Add a module-level table and scanner after `MAX_REFUSALS`:

   ```python
   # Python regex syntax that pi's extension, which reads a pattern as
   # new RegExp(pattern, "m") (sssf_rules.ts), can't compile or reads
   # differently: (what it is, how to write it portably). Each one is shown
   # disagreeing under bun in tests/test_rules_portable.py.
   _ESCAPES = {
       "A": ("`\\A`", "use `^`; with the m flag both read it as the start of a line"),
       "Z": ("`\\Z`", "use `$`"),
       "z": ("`\\z`", "use `$`"),
       "a": ("`\\a`", "use `\\x07`"),
       "U": ("a `\\U` escape", "use `\\xhh`, `\\uhhhh` or the character itself"),
       "N": ("a `\\N{...}` escape", "use `\\xhh`, `\\uhhhh` or the character itself"),
   }
   _GROUPS = [   # what follows "(?", checked in order
       ("P<", "a named group `(?P<name>...)`", "use a plain group `(...)`"),
       ("P=", "a named backreference `(?P=name)`", "use a plain group and a numbered backreference such as `\\1`"),
       ("#", "a comment group `(?#...)`", "drop the comment"),
       ("(", "a conditional group `(?(...)...)`", "use alternation with `|`"),
       (">", "an atomic group `(?>...)`", "use `(?:...)`"),
   ]
   _FLAGS = ("an inline flag group such as `(?i)` or `(?i:...)`",
             "spell the flag out, e.g. `[Gg]it` for i, no spaces for x, `[\\s\\S]` for s's `.`")
   _POSSESSIVE = ("a possessive quantifier such as `++` or `*+`", "drop the trailing `+`")
   _EMPTY_MIN = ("a quantifier with no minimum, `{,n}`", "write `{0,n}`")
   _CLASS_BRACKET = ("a character class opening with `]`, such as `[]x]`", "escape it: `[\\]x]`")
   _BRACES = re.compile(r"\{(\d*)(,\d*)?\}")
   ```

   And `def _python_only(pattern: str) -> Optional[tuple[str, str]]:`, returning `(construct, fix)` for the first listed construct, or `None`. One left-to-right pass with an `in_class` flag:
   - `\`: if not at the end, look up the next char in `_ESCAPES` and return its entry. Otherwise skip both chars (an escaped `[`, `(`, `{`, `*` and so on is never read as syntax). The lookup runs inside a class too: Python itself rejects `\A`/`\Z` there, and accepts `\a`/`\U`/`\N` there with the same meaning as outside.
   - In a class: `]` ends it. Everything else is skipped.
   - `[`: step past an optional `^`. If the next char is `]`, return `_CLASS_BRACKET`. Otherwise enter the class just after it.
   - `(` followed by `?`: look at the text after `(?`. If it starts with `:`, `=`, `!`, `<=` or `<!`, it's portable: advance past `(?` and go on. If it matches a `_GROUPS` prefix, return that entry. Anything else (a flag letter or `-`) returns `_FLAGS`, since `re.compile` already rejected every other `(?` form.
   - `*`, `+`, `?` (outside a class, not part of `(?`): if the next char is `+`, return `_POSSESSIVE`.
   - `{`: `_BRACES.match(pattern, i)`. If it matches with an empty first group and a `,` group (`{,n}`, `{,}`), return `_EMPTY_MIN`. If it matches with digits in the first group (a real quantifier), check the char after `m.end()`: `+` returns `_POSSESSIVE`. Either way, continue from `m.end()`. With no match, `{` is a literal: advance one.
   - Anything else: advance one.

2. Extend `AgentRule._compiles` (rename it `_portable` or keep the name; the field validator is the only caller). For each pattern: `re.compile` first, keeping today's `not a valid regex` error unchanged. Then `found = _python_only(pattern)`. If found, raise:

   ```python
   ValueError(f"forbid pattern {pattern!r} uses {construct}, which pi's extension (sssf_rules.ts) "
              f"reads as a JavaScript regex and can't compile or reads differently, so pi wouldn't "
              f"refuse the command. Write it portably: {fix}.")
   ```

   It applies to every `AgentRule`, whatever its `on_match`. Since `AgentRule(...)` runs it, `secret_files.shell_rule` goes through it too.

3. Module docstring: after the `on_match: refuse` paragraph, add one sentence: "So every `forbid` pattern must read the same in both languages: the settings fail to load on Python syntax JavaScript reads differently or can't compile (`_python_only`)."

### `.claude/skills/sssf/templates/adws/adw_modules/agent_pi.py`

Docstring line 1 only: `"""Pi coding agent interface — v1's only coding agent.` becomes `"""Pi coding agent interface.`. The rest of the docstring and all code stay unchanged.

### `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

Header comment only, no code change. Replace "The patterns are Python regexes read here as JavaScript ones; one that doesn't compile is skipped, and SSSF's stream check, which reads them as Python, stops the run if that command runs." with: "The patterns are Python regexes read here as JavaScript ones. rules.py rejects, when the settings load, the Python syntax known to read differently here, so every pattern should compile; one that still doesn't is skipped, and SSSF's stream check, which reads them as Python, stops the run if that command runs."

### `.claude/skills/sssf/references/config.md`

Line 413: replace the sentence "The extension reads the patterns as JavaScript regexes; one that doesn't compile there, or matches differently, isn't refused, and the stream check, reading it as Python, stops the run if that command runs." with:

> The extension reads the patterns as JavaScript regexes, so a pattern must read the same there as in Python. The settings fail to load, whatever the rule's `on_match`, on Python syntax JavaScript can't compile or reads differently: `\A`, `\Z`, `\z`, `\a`, `\U` and `\N{...}` escapes, named groups `(?P<name>...)` and backreferences `(?P=name)`, comment groups `(?#...)`, inline flag groups such as `(?i)` or `(?i:...)`, conditional `(?(1)...)` and atomic `(?>...)` groups, possessive quantifiers such as `++`, a quantifier with no minimum such as `{,3}`, and a character class opening with `]`, such as `[]x]`. The message names the construct and a portable spelling, such as `^` for `\A` or a plain group for a named one. Two differences no check catches: `\w`, `\d` and `\b` see non-ASCII letters and digits in Python but not in JavaScript, and JavaScript's `.` doesn't match a carriage return. So keep a pattern to ASCII words, and use `[^\n]` rather than `.` where a command could hide a `\r`.

Leave line 409's "A pattern that isn't a valid regex fails the settings, and with them the run." as it is.

### `docs/open.md`

- Delete the row that begins "A `forbid` pattern that reads differently in Python and JavaScript" (line 19). Its convention: "When an item closes, delete its row".
- Add a `build` row in its place, dated 2026-11-06, linking `config.md#rules-for-every-agent-agent_rules`: "Two Python/JavaScript regex differences the load check can't reject: `\w`, `\d` and `\b` are Unicode in Python and ASCII in pi's extension, and JavaScript's `.` stops at `\r`. A command that only matches through one of them isn't refused on pi, and `; echo "SSSF refused this command"` then hides it from the stream check. Fix candidates: compile with the `u` flag plus a matching load check, or have `rules.refused` check that the extension really refused that call"

### `.claude/skills/sssf/tests/test_rules_portable.py` (new)

Follow `tests/test_rules.py`'s header and imports (`SKILL`, `sys.path.insert(0, str(SKILL / "templates" / "adws"))`, `from adw_modules import agent_pi, repo_settings, rules, secret_files`), and reuse its `settings(tmp_path, body)` shape (define it locally). These tests validate `repo.yaml`, so they need no `sssf.config.yaml`. None is built.

- `CASES`: one table shared by the rejection and grounding tests, entries `(label, pattern, command, construct_snippet, min_python)`. One entry per table row above: `\A`, `\Z`, `\z` (min_python `(3, 14)`), `\a`, `\U`, `\N{SPACE}`, `(?P<g>`, `(?P=g)`, `(?#c)`, `(?i)git push`/`GIT push`, `(?x)`, `(?s)git.push`/`git\npush`, `(?a:git) push`, conditional, atomic (3.11), `++`, `*+`, `?+`, `{1,2}+` (3.11), `{,2}`, `{,}`, `[]x]`, `[^]x]`. Use the patterns and commands from the decision table. `construct_snippet` is a substring of the construct text in the message, e.g. "`\\A`", "named group", "inline flag group", "possessive", "no minimum", "opening with `]`".
- `test_each_python_only_construct_fails_the_settings_in_both_modes`: parametrize over `CASES` × `on_match` in (`stop`, `refuse`). Write the rule with `yaml.safe_dump` (so backslashes survive) through `settings(...)`, call `repo_settings.load`, and assert `ValidationError` with the pattern and `construct_snippet` in `str(error)`. Skip a case below its `min_python`. For `\z`, which Python < 3.14 rejects as invalid, only assert the `ValidationError` there.
- `test_portable_patterns_still_load`: build `AgentRule`s, in both modes, from: the starter template's examples `\bjust\s+deploy\b` and `\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b`; `config.md`'s `\bjust\s+(fh-|fusion|plan\d)`, `(^|[;&|]\s*)pi\b`, `(^|[;&|]\s*)gh\s+`; portable spellings of the rejected ones (`^git push`, `push$`, `(git) \1`, `[Gg]it`, `gi{0,2}t`, `[\]x]`, `(?:git)`, `(?=x)`, `(?!x)`, `(?<=x)`, `(?<!x)`, `a{2}`, `a*?`, `x{}`). Also load `templates/repo.yaml` through `repo_settings.load` (copy it to `tmp_path / repo_settings.PATH`). Load the hub's own rules with `repo_settings.load(SKILL.parents[2])` when `SKILL.parents[2] / repo_settings.PATH` exists, and assert they include at least one `forbid` rule; skip that part when the file is absent (the skill copied into another repo).
- `test_the_built_in_secret_file_rule_passes`: `secret_files.shell_rule` over names re.escape has to escape: `/r/.env`, `/r/.env.local`, `/r/my file.txt`, `/r/a+b[1].txt`, `/r/x{2}#~&.cfg`, `/r/(p)|q^$.json`. Assert it returns an `AgentRule` (construction runs the validator) and `rules._python_only(p) is None` for its pattern. This confirms that `re.escape` only escapes punctuation, never a letter, and `shell_rule` uses only `(?<!`, `(?:`, `(?!` and `[\w.-]`.
- `test_bun_shows_the_extension_and_python_disagree_on_each_construct` (`skipif(not shutil.which("bun"))`): for every `CASES` entry whose pattern compiles in this Python, assert `re.search(pattern, command, re.M)` matches and `rules._python_only(pattern)` names the construct. Then write one `check.ts` in `tmp_path` that imports `agent_pi.REFUSE_EXTENSION` (as `test_rules.py` does). For each case it reads from a JSON file passed as `argv`, it sets `process.env.SSSF_REFUSE_RULES` to that case's rules alone, then calls `ext(fakePi)` to capture a fresh `tool_call` handler, then calls `handler({toolName: "bash", input: {command}})`. It prints the JSON list of results (`?? null`). Build each case's rules with `rules.refusing([rules.AgentRule.model_construct(rule="r", forbid=[pattern], on_match="refuse")])`: `model_construct` skips the new validator. Assert every result is `null`, so the extension didn't refuse a command Python matches. Add one control case, the push pattern on `git push`, and assert it is blocked, so a broken harness can't pass. Also run the secret-file rule from the previous test on `cat 'my file.txt'` and `cat a+b[1].txt`, and assert both are blocked, so the escapes it builds read the same in the extension.

### Docs checked, no change needed

- No CHANGELOG in the repo or skill. No README or SKILL.md text describes the pattern reading.
- The comments in `templates/repo.yaml` and the hub's `repo.yaml` show only portable patterns.
- `app_docs/` doesn't exist in this repo, so the documenter has no older write-up to note.

## Verification

1. From `.claude/skills/sssf/`: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_rules_portable.py -q`. It passes, and the bun test runs, not skipped, where bun is on PATH.
2. From the same directory: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_rules.py tests/test_rules_heredoc.py tests/test_rules_multiline.py tests/test_prompt_rules.py tests/test_secret_files.py -q`. Every existing rules, refusal, stream-check and secret-file test passes, none of them edited.
3. The full skill suite (the hub's quality checks run it) passes.
4. `git diff 8c4e36f -- .claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts .claude/skills/sssf/templates/adws/adw_modules/agent_pi.py` shows only comment lines changed. A reviewer reads this; it is not an automated check.

## Out of scope

Old holes this change leaves open, for a reviewer to file as old:
- `rules.refused`'s prefix match, so the echo trick against a pattern both languages read the same. The extension refuses that command before it runs.
- Semantic differences in constructs both languages accept, which no syntactic check catches without banning `\w` or `.`. `\w`, `\d`, `\b` and `\s` are Unicode in Python and ASCII-ish without JavaScript's `u` flag. JavaScript's `.` stops at `\r`, ` ` and ` `, where Python's stops only at `\n` (seen: `git.push` against `git\rpush`). Astral characters are one code point in Python and two code units in JavaScript. A command that matches only through one of these still runs uncaught on pi with the echo trick. Documented in `config.md` and tracked in `docs/open.md` (above).
- Every other part of refuse mode: the three-refusal stop, `sssf_refuse.py`, Claude Code's `permission_denied` path, and the secret-file file-tool checks.
- Older node versions that reject constructs the scanner allows. The allowed set (`(?:`, lookarounds, lookbehind, numbered backreferences, `{m,n}`, classes, escapes) is ES2018 and in every node pi supports.

## For the engineer

- The hub's installed copies `adws/adw_modules/rules.py`, `adws/adw_modules/agent_pi.py` and `adws/adw_modules/sssf_rules.ts` are protected and identical to the templates at the baseline. After this lands, bring them up to date from the skill (`/sssf update`). The hub's own `repo.yaml` rules are portable, so the hub's settings keep loading.
- Projects with their own `repo.yaml`: a rule using a listed construct stops their next run at settings load, with a message saying how to rewrite it. That rejection is intended.

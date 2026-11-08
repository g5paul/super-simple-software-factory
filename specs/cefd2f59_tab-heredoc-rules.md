# Make the agent rules see a split command in a `<<-` heredoc

The request is `specs/tasks/guard-tab-heredoc.md`. Guard run 6e1c3a88's report (`adws/adw_data/sessions/6e1c3a88/context_handoff/guard_report.md`, in the main checkout) holds the finding.

A `<<-` heredoc strips each body line's leading tabs before the inner shell reads the body. So in

```
bash <<-'EOF'
<TAB>ju\
<TAB>st test live
<TAB>EOF
```

the inner shell reads `ju\` newline `st test live` and runs `just test live`. The checkers' joined text keeps the tab, `ju<TAB>st test live`, and matches nothing.

The fix is a third search text in each checker: the command with every backslash-newline and the tabs right after it removed. A match on any of the three texts counts.

## Threat model

The agent writes any shell command it likes and runs as the engineer's own user. These checkers read the command's text. They don't parse shell, so an agent working around a rule on purpose can still get past them: `config.md`'s rule section already says a rule catches a mistake or a casual attempt. The stream check (`rules.breach`) is the backstop for both refusers, and it can only stop a command after the harness may have started it. Nothing at this level stops a command the agent assembles at run time, such as from a variable, a file it writes first, or an encoded string.

This change closes one form: a word split by a backslash-newline inside a tab-indented `<<-` body. It adds matches and removes none.

## Baseline

`main` at `9891399`. Every finding is judged against it. At that commit each checker searches the text as written and the joined text (added by 37273db). All three miss the example above.

## Bar

**Must do**

- A command split by a backslash-newline inside a `<<-` body indented with tabs is stopped or refused as the unsplit command would be, on both harnesses, through each checker: `rules.breach`, `sssf_refuse.refusal` and `sssf_rules.ts`.
- The bar of `specs/tasks/guard-backslash-newline.md` stands, with the same accepted limits.
- `just test\` newline tab `live`, outside a heredoc, still matches. That's why the joined text stays: outside a `<<-` body the shell reads that tab as a word break.
- The three checkers agree on match or no match, and name the same first rule and pattern.

**Must never do, against the baseline**

- Stop matching a command that matches today. The two existing texts stay and are searched as they are now.
- Keep an agent file, run agent code, or delete or overwrite an engineer's file where the baseline does otherwise. This change only adds matches, so a command the baseline let through can now be refused or stopped, and none the baseline refused can now run.
- Add a search that isn't linear in the command's length. The new regex must keep the lookbehind.

**Accepted over-matches**

The third text can match a command the shell wouldn't run, because the checkers can't tell a `<<-` body from the rest of a command without parsing heredocs. Removing more text is the safe direction for a rule, as the backslash-newline spec already says of the joined text. A stop rule that over-matches stops the run. Each form below was run under real bash, runs no forbidden command, and still matches. Pin each one with a row:

- `ju\` newline tab `st test live` outside a heredoc, where the shell runs `ju st test live`.
- `cat .e\` newline tab `nv` outside a heredoc, where the shell runs `cat .e nv`.
- `bash <<-EOF` (unquoted delimiter) with a single backslash, where the outer shell joins the lines before it strips the tab, so the inner shell runs `ju<TAB>st test live`.

## Out of scope

These are old holes. A reviewer files them as old holes, not as findings against this change.

- **A `^` anchor on a tab-indented line in a `<<-` body.** `(^|[;&|]\s*)pi\b` misses `<TAB>pi -p hi` at the baseline, because the tab sits between `^` and `pi`. The split form `<TAB>p\` newline `<TAB>i -p hi` runs `pi -p hi` and misses in the same way: the third text is `<TAB>pi -p hi`, which is exactly the unsplit command. So it meets the bar, "as the unsplit command would be". The third text as the request defines it removes only the tabs after a continuation, so it doesn't touch the first line's tab. The PI rule does match when that line has no leading tab, and on `true; p\` newline `<TAB>i -p hi`. Both are tested. See `## For the engineer`.
- **Spaces after a continuation in a `<<-` body.** The shell strips only tabs there, so a space stays and breaks the word. Real bash runs nothing forbidden for `ju\` newline space tab `st test live` or `ju\` newline tab space `st test live`.
- **The accepted limits of the backslash-newline request**, which also apply inside a `<<-` body: quotes inside a word, a backslash before an ordinary character inside a word, and a line the shell doesn't continue followed by a split command. `<TAB># note\` or `<TAB>x\\` before a split command in a `<<-` body runs `just test live` and matches nothing.
- **The Python and JavaScript regex differences `config.md` lists.** They apply to the third text as to the other two.
- **Everything else out of scope in `specs/tasks/guard-backslash-newline.md`:** the model-host rules, the network policy, building an image, running a sandbox.
- **Carriage returns after a backslash.** `ju\` CR LF stays a no-match, pinned today.

## Changes

Change nothing else in these modules. The two existing texts stay and are searched as they are now.

### `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

Add this after `_CONTINUATION`, and keep `_CONTINUATION` as it is:

```python
# The same and the tabs right after it. A `<<-` heredoc strips a body line's
# leading tabs before the inner shell joins the lines, so that shell reads no tab
# there. Outside a `<<-` body the shell reads that tab as a word break, so
# _CONTINUATION's text stays too. sssf_refuse.py and sssf_rules.ts use the same pattern.
_CONTINUATION_TABS = re.compile(r"(?<!\\)\\+\n\t*")
```

In `breach`, search all three texts:

```python
    texts = (command, _CONTINUATION.sub("", command), _CONTINUATION_TABS.sub("", command))
    for rule in rules:
        if isinstance(rule, AgentRule):
            for pattern in rule.forbid:
                if any(re.search(pattern, text, re.M) for text in texts):
                    return rule, pattern
    return None
```

Replace the last paragraph of the `breach` docstring, in words with no escape sequences, since this isn't a raw string:

> It searches the command as written, again with backslash-newlines removed, as a shell reads them, and again with the tabs right after them removed too, as the inner shell of a `<<-` heredoc reads them. A match in any of the three counts.

### `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

Add this after `CONTINUATION`:

```python
CONTINUATION_TABS = re.compile(r"(?<!\\)\\+\n\t*")   # rules._CONTINUATION_TABS, and the same in sssf_rules.ts
```

In `refusal`:

```python
    texts = [*commands(line), *commands(CONTINUATION.sub("", line)), *commands(CONTINUATION_TABS.sub("", line))]
```

Extend the module docstring's sentence on backslash-newlines to name the third read: "... and again with the tabs right after them removed too, for a `<<-` heredoc." **Keep this docstring free of backslashes.** It says so itself: a warning on stderr would come before the refusal and hide it.

### `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

Add this after `CONTINUATION`:

```ts
// rules._CONTINUATION_TABS in Python: the same and the tabs right after it, as the inner shell of a `<<-` heredoc reads it.
const CONTINUATION_TABS = /(?<!\\)\\+\n\t*/g;
```

In the handler:

```ts
    const joined = command.replace(CONTINUATION, "");
    const untabbed = command.replace(CONTINUATION_TABS, "");
    ...
        if (re && (re.test(command) || re.test(joined) || re.test(untabbed))) {
```

Extend the header comment's sentence the same way as in `sssf_refuse.py`.

### `.claude/skills/sssf/references/config.md`

This is the paragraph at line 429 of the rule section. Replace

> A match in either counts, and `^` and `$` match at every line in both.

with

> It reads the text a third time with the tabs right after each backslash-newline removed too, since a `<<-` heredoc strips a body line's leading tabs before the inner shell joins the lines: `bash <<-'EOF'` with `ju\` and `st test live` on tab-indented lines runs `just test live`. The second text keeps those tabs because outside a `<<-` body the shell reads one as a word break: `just test\`, a newline, a tab, then `live` runs `just test live`. A match in any of the three counts, and `^` and `$` match at every line in all three. The third text can over-match: `cat .e\`, a newline, a tab, then `nv` outside a heredoc runs `cat .e nv` and is still refused.

Add one known limit, after the "split command after a line the shell doesn't continue" limit and before "A rule catches a mistake...":

> A `^` anchor on an indented line: a `<<-` body's leading tabs stay in the search, so `(^|[;&|]\s*)pi\b` misses `pi -p hi` on a tab-indented line of a `<<-` body, split or not, and a pattern anchored with `^` has to allow for them.

### `.claude/skills/sssf/tests/test_rules_tab_heredoc.py` (new)

Leave every existing test file as it is. Follow `.claude/skills/sssf/tests/test_rules_continuation.py`, and import from it `stubs`, `needs_bun` and `claude_events`, from `test_rules` `announce`, `claude_line`, `forwarder`, `ran` and `starter_push_rule`, and from `test_rules_multiline` `_bun_handler` and `_pi_block`.

**Rules.**

- Start from the shipped starter rules in `.claude/skills/sssf/templates/repo.yaml`, never a test-only copy: `PUSH = starter_push_rule()`, plus `DEPLOY`, read from the same commented block with `on_match: refuse`.
- `SECRET = secret_files.shell_rule(["/r/.env"])`.
- `PROBE`, forbidding `SSSF-REFUSE-PROBE`.
- `LIVE` and `PI`, the patterns `test_rules_continuation.py` uses. PI is `config.md`'s example.
- No test here needs a roster config, so none builds one. If one comes to need it, build it from `.claude/skills/sssf/templates/sssf.config.yaml`.

**Stubs. This one matters for safety.** `stubs()` provides only `just` and `pi`. Add a recording `git` stub to the same directory, and define a local `run_prefix` that uses it, so that a `git push origin main` row can never reach real git if a checker regresses. `run_prefix` in `test_rules_continuation.py` calls `stubs()` itself, so don't reuse it.

**Shared table: `(rule, command, matches, what real bash does)`.** The last column is the recorded stub lines plus stdout, as a list. Every row below was run under real bash while planning, and the outcomes are as written. With `T` for a tab, `BS` for a backslash, and `open` being each of `bash <<-'EOF'`, `sh <<-'EOF'` and `cat <<-'EOF' | bash`:

| Rule | Body lines (each tab-indented unless shown), with a closing `<TAB>EOF` | Matches | Bash does |
|---|---|---|---|
| LIVE | `open`: `ju\`, `st test live` | yes | `just test live` |
| DEPLOY | `open`: `ju\`, `st deploy` | yes | `just deploy` |
| PUSH | `open`: `gi\`, `t push origin main` | yes | `git push origin main` |
| PUSH | `bash <<-'EOF'`: `git pu\`, `sh origin main` | yes | `git push origin main` |
| PROBE | `open`: `echo SSSF-REF\`, `USE-PROBE` | yes | prints `SSSF-REFUSE-PROBE` |
| SECRET | `bash <<-'EOF'`: `cat .e\`, `nv` (`.env` in the cwd holds `SECRET`) | yes | prints `SECRET` |
| PI | `bash <<-'EOF'`: `p\` with no leading tab, then `<TAB>i -p hi` | yes | `pi -p hi` |
| PI | `bash <<-'EOF'`: `true; p\`, `i -p hi` | yes | `pi -p hi` |
| LIVE | `bash <<-'EOF'`: `ju\`, `<TAB><TAB><TAB>st test live` | yes | `just test live` |
| LIVE | `bash <<-'EOF'`: `ju\`, `\`, `st test live` | yes | `just test live` |
| LIVE | `bash <<-'EOF'`: `just test \`, `live` | yes | `just test live` |
| LIVE | `bash <<-EOF`: `ju\\`, `st test live` (two backslashes) | yes | `just test live` |
| LIVE | `sh <<-EOF`: `ju\\`, `st test live` (two backslashes) | yes | `just test live` |

Rows with no heredoc, and the pinned ones:

| Rule | Command | Matches | Bash does | Why |
|---|---|---|---|---|
| LIVE | `just test\` newline `T` `live` | yes | `just test live` | the joined text stays |
| LIVE | `just test \` newline `T` `live` | yes | `just test live` | |
| LIVE | `ju\` newline `T` `st test live` | yes | nothing | accepted over-match |
| SECRET | `cat .e\` newline `T` `nv` | yes | nothing on stdout | accepted over-match |
| LIVE | `bash <<-EOF`, `T` `ju\`, `T` `st test live`, `T` `EOF` | yes | nothing | accepted over-match |
| PI | `bash <<-'EOF'`, `T` `pi -p hi`, `T` `EOF` | no | `pi -p hi` | known limit, old hole |
| PI | `bash <<-'EOF'`, `T` `p\`, `T` `i -p hi`, `T` `EOF` | no | `pi -p hi` | known limit, as the unsplit row above |
| LIVE | `bash <<-'EOF'`, `T` `# note\`, `T` `ju\`, `T` `st test live`, `T` `EOF` | no | `just test live` | accepted limit |
| LIVE | `bash <<-'EOF'`, `T` `x\\`, `T` `ju\`, `T` `st test live`, `T` `EOF` | no | `just test live` | accepted limit |
| LIVE | `bash <<-'EOF'`, `T` `ju\`, space `T` `st test live`, `T` `EOF` | no | nothing | spaces, out of scope |
| LIVE | `bash <<-'EOF'`, `T` `ju\`, `T` space `st test live`, `T` `EOF` | no | nothing | spaces, out of scope |

**Tests over the table.** Write each with the same shape as its counterpart in `test_rules_continuation.py`:

1. `rules.breach` agrees with the Matches column.
2. The prefix, through `claude_line(command)`, exits 126 with stderr starting `rules.REFUSED` on a match. On no match it does neither.
3. Pi's handler under bun (`needs_bun`), grouped by rule, agrees with the Matches column. Together with 1 and 2 this is the Python and JavaScript agreement the request asks for.
4. The table is honest: run each command under `bash -c` with the stubs, in a `tmp_path` whose `.env` holds `SECRET`, and compare the recorded lines plus stdout with the last column.

**Other tests.**

5. All three name the first rule when only the third text matches it. `RULE_A` forbids `\bjust\s+test\s+live\b`, and `RULE_B` forbids `\bst\s+test\s+live\b`, which matches as written. On the example command, `breach`, the prefix and pi all name rule A.
6. The prefix also refuses the example in lines of other shapes: the bare line, and `x; eval '...'` with the body single-quoted.
7. A split push in a `<<-` body, on both harnesses, as `test_a_split_push_is_a_counted_refusal_on_both_harnesses_and_a_breach_when_it_ran` does: pi's block and the prefix's exit 126 each count as `rule_refused`, an announced call that ran raises `RuleBreach` matching "may have run", and the pattern agrees.
8. The built-in secret rule's refusal names it, on both harnesses: pass `builtin` so that the prefix's stderr holds "secret-file rule" and pi blocks.
9. A stop rule stops the run on the example: `forwarder(rule=stop, can_refuse=False)` raises `RuleBreach`.
10. Linear time: `rules.breach([LIVE], ...)` finishes in under 5 seconds on each of `(BS + NL + T) * 100_000`, `BS * 200_000` and `BS + NL + T * 200_000`, and returns None on each.

Put a module docstring at the top saying what the file proves and how to run it from the skill root, in the same form as `test_rules_continuation.py`. Write its backslashes as `\\`.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`: covered above. It's the only doc outside the guard modules that describes the search texts.
- The comments and docstrings in `rules.py`, `sssf_refuse.py` and `sssf_rules.ts`: covered above.
- `docs/open.md` needs nothing. It has no row for this, and the limits go in `config.md`.
- There is no CHANGELOG, no `app_docs/` and no README section on the search.

## Verification

From the repo root, the check in the Report runs the new file with the rule tests it sits beside:

```
cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_rules_tab_heredoc.py tests/test_rules_continuation.py tests/test_rules_heredoc.py tests/test_rules_multiline.py tests/test_rules_portable.py tests/test_rules.py tests/test_secret_files.py -q
```

Also run the whole skill suite before handing in, with the same command and `tests` as the only path. Every existing test must pass with its file unchanged. Both need `bun` on PATH, or the pi tests skip, and a skip isn't a pass here.

Check these by hand:

- `git diff --stat` touches only the five files above.
- No test file under `.claude/skills/sssf/tests/` other than the new one changed.
- `sssf_refuse.py`'s docstring has no backslash. The prefix tests' `stderr.startswith(rules.REFUSED)` is what fails if it gains one.

## For the engineer

Neither item is a builder step.

- **The PI form.** The review listed `p\` newline `<TAB>i -p hi` among the forms that pass. With the third text as requested, that form matches when its first line has no leading tab, and a fully tab-indented body still misses, exactly as the unsplit `<TAB>pi -p hi` does at the baseline. It meets the bar and is pinned as a known limit. To close it for both forms, you could either widen the documented example in `config.md` to `(^\s*|[;&|]\s*)pi\b`, or add a text that strips a `<<-` body's first-line tabs. Either one is a separate change. I'd leave it, because it isn't a split-command hole.
- **Rollout.** `adws/adw_modules/` is the hub's own install and `docs/projects.md` records rollouts. Both are protected, and they're updated after the merge, as 5ade16d and 9891399 did for 37273db.

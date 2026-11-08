# Make the agent rules see through backslash-newline

All three `forbid` checkers also search the command with backslash-newlines removed, so a command split across lines is stopped or refused as the unsplit one is. The OpenShell image's check prints one `rg` line and still fails when `rg` is missing.

Guard code changes only under `.claude/skills/sssf/templates/adws/adw_modules/`. This repo's own copy in `adws/adw_modules/` is protected and is not part of this change.

This plan revises run 70628873's draft after its spec review: the prefix design is smaller, and the uncontinued-line limit the engineer accepted on 2026-11-08 is now in. Below, `⏎` means a real newline.

## Threat model

An agent writes any shell command it likes and runs as the engineer's own user. The shell drops a backslash followed by a newline before it reads a command, so `ju\⏎st test live` runs `just test live`. The checkers search text, so at baseline none of them sees `just`. The common case isn't an attack: an agent writes `just test \⏎live` as ordinary formatting.

The rules are text searches. They catch a mistake or a casual attempt, not an agent set on getting round them. At this level nothing stops an agent that splits a word with quotes, builds a command at runtime, or writes a script with its file tool and runs it by a name no rule mentions. `config.md` already says so, and this change doesn't alter it.

## Baseline

`main` at `ad994f6` (`ad994f6ecff0105710e2213a028038aaa6369c97`). Every finding is judged against it. `HEAD` adds only `specs/tasks/guard-backslash-newline.md` to that commit.

At that commit:

- `rules.breach`, `sssf_refuse.refusal` and pi's `tool_call` handler each search only the text they're given.
- `sssf_refuse.commands` returns only the unquoted agent command when the line has Claude Code's wrapper shape, and the whole line plus each `eval '...'` argument otherwise.

## Bar

**Must do**

- A command that runs a forbidden command through a backslash-newline, anywhere in it, is stopped or refused as the same command without it would be, on both harnesses, in every mode, through each checker that handles it today. The only exceptions are the accepted limits below.
- Every command that matches today still matches. The text as written is still searched.
- `^` and `$` keep matching line by line in both the written and the joined text.
- All three checkers report the same rule and pattern for a command, because each tries rules and patterns in order and takes the first pattern that hits either text. Never loop over texts first: a later rule's match on the written text would beat an earlier rule's match on the joined text.
- The image's check fails the build when `rg` is missing or exits non-zero, and logs one `rg` line when it works.

**Must never do**

- Run anything other than what the agent asked. The joined text is only searched. `sssf_refuse.main` still hands the original `line` to bash or the next prefix.
- Add a mechanism beyond the one removal, or parse the shell.
- Against the baseline: no agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise.

**Accepted limits**

- Quotes inside a word: `j''ust test live`.
- A backslash before an ordinary character inside a word: `j\ust test live`.
- A line the shell doesn't continue, followed by a split command (accepted by the engineer on 2026-11-08, after guard run 70628873's spec review). Joining also joins a backslash-newline the shell keeps, so the line before swallows the command's start and a `^` or `\b` anchor misses it. Examples: `x\\⏎ju\⏎st test live` and `# note\⏎ju\⏎st test live`. Closing it means searching from each line after a continuation, which is quadratic. These cases have pinned no-match tests.

## Out of scope

These are old holes. A reviewer should file them as such.

- Quotes inside a word, `j\ust`, and all other shell unquoting.
- A line the shell doesn't continue, followed by a split command, as accepted above.
- The Python and JavaScript regex differences `config.md` lists. They apply to the joined text as to the written text.
- `$'...'` and `printf` encodings, where the text holds no literal newline.
- Commands built at runtime, or read from a file an agent wrote with its file tool.
- A backslash before CRLF. Bash doesn't treat it as a continuation: it doesn't run `just` (probed).
- The model-host rules and the rest of the network policy.
- Building an image or running a sandbox. The new check line is proven by running it under `sh` with stubs, not in a real image build.

## Decisions the builder must keep

Each came from running cases under real `bash` and `bun`.

1. **Use the pattern `(?<!\\)\\+\n`, the same in all three checkers.** It removes the whole run of backslashes before a newline. A one-backslash removal leaves `ju\st test live` from `ju\\⏎st test live`, yet `bash -c "ju\\⏎st test live"` and an unquoted `bash <<EOF` heredoc holding that text both really run `just test live`. Plain `\\+\n` is quadratic on a long run of backslashes (200,000 took 12 s). With the lookbehind it took under 2 ms. One pass is enough: a removal can't form a new match.
2. **`sssf_refuse` joins the raw line, then runs `commands()` on both.** Use `texts = [*commands(line), *commands(CONTINUATION.sub("", line))]`, with no pass after unquoting.
   - Joining first is required. `shlex` drops the backslash from an unquoted `\⏎`, and `ANY_EVAL` needs `eval\s+'`, so `x; eval \⏎'pi -p hi'` loses its argument otherwise.
   - On a real wrapper this equals the stream's two texts. The wrapper's `'\''` ends in a quote, so a backslash run before a newline always comes whole from the agent's command. A 200,000-case fuzz of wrapped commands found no miss.
   - The one line this misses, `true && eval 'ju\'"⏎"'st test live' < /dev/null && pwd -P`, is quotes inside a word, and no real wrapper produces it. It is pinned below.
3. **`rg --version | head -1` must not be used as written.** Without `pipefail` a pipeline's status is `head`'s, so under `sh` a missing `rg` passes the check. Use the assignment form in step 4.

## Changes

### 1. `.claude/skills/sssf/templates/adws/adw_modules/rules.py`

Add near `REFUSED`:

```python
# A backslash-newline and any backslashes right before it. A shell drops the
# pair; the rest of the run matters when an outer level of quoting unescapes it
# first. The lookbehind keeps a long run of backslashes linear. sssf_refuse.py
# and sssf_rules.ts use the same pattern.
_CONTINUATION = re.compile(r"(?<!\\)\\+\n")
```

In `breach`:

```python
joined = _CONTINUATION.sub("", command)
for rule in rules:
    if isinstance(rule, AgentRule):
        for pattern in rule.forbid:
            if re.search(pattern, command, re.M) or re.search(pattern, joined, re.M):
                return rule, pattern
return None
```

Extend `breach`'s docstring: it searches the command as written and again with backslash-newlines removed, and a match in either counts.

### 2. `.claude/skills/sssf/templates/adws/adw_modules/sssf_refuse.py`

- Add beside `ANY_EVAL`: `CONTINUATION = re.compile(r"(?<!\\)\\+\n")   # rules._CONTINUATION, and the same in sssf_rules.ts`. The script runs by path on the standard library alone, so it keeps its own copy, as it does for `REFUSED`.
- In `refusal`, the first line becomes `texts = [*commands(line), *commands(CONTINUATION.sub("", line))]`. The loop order and `any(...)` stay.
- `commands()` and `main()` are unchanged. `main()` still runs the original `line`.
- Add a sentence to the module docstring, in words only: the search also reads the line with backslash-newlines removed. **The docstring must stay free of backslash characters** (it says why).

### 3. `.claude/skills/sssf/templates/adws/adw_modules/sssf_rules.ts`

- Add, with a one-line comment naming `rules._CONTINUATION`: `const CONTINUATION = /(?<!\\)\\+\n/g;`
- In the `bash` branch, compute `const joined = command.replace(CONTINUATION, "");` once, before the loop.
- Change the test to `if (re && (re.test(command) || re.test(joined)))`. The patterns compile with `m` and no `g`, so `test` holds no state.
- Add to the header comment that the search also reads the command with backslash-newlines removed.

### 4. `.claude/skills/sssf/templates/adws/adw_modules/sandbox_openshell.py`

In `containerfile`, change the check's second string. The result must be exactly this, because the image tag hashes it:

```python
' && v=$(rg --version) && echo "$v" | head -1 && fdfind --version && git --version' + find,
```

An assignment's status is its command substitution's, so a missing or failing `rg` stops the `&&` chain under any POSIX `sh`. A Containerfile doesn't expand `$` in a `RUN` line; the shell does.

### 5. `.claude/skills/sssf/tests/test_sandbox_openshell.py`

**The pin must move.** `test_containerfile_without_a_python_installs_none` pins `image_tag(F, "")`, a hash of the containerfile, so the requested check-line change changes it. This is the one exception the request's own change forces to "every existing test passes unchanged".

- Change `6d605fa4ff7d` to `54c9163cc34d`, computed from the exact string in step 4.
- Change its comment to `# the tag from when the image's check trimmed rg to one line`.
- Touch no other assertion.

Add `test_the_image_check_fails_when_rg_is_missing`, parametrized on rg present, missing, and exiting 1:

- Take the line starting `RUN node --version` from `osh.containerfile(F, "")` and strip `RUN `.
- Build a temp directory with stub executables for `node`, `uv`, `claude`, `pi`, `fdfind` (recording that it ran) and `git`, a symlink to the real `head`, and an `rg` stub that prints two lines, for the present and exit-1 cases.
- Run it as `[shutil.which("sh"), "-c", line]` with `PATH` set to that directory alone.
- Present: exit 0, and the output holds the stub's first line but not its second.
- Missing, and exit 1: non-zero, and `fdfind`'s stub never ran.

### 6. `.claude/skills/sssf/tests/test_rules.py`

In `test_pi_always_loads_the_refusing_extension_and_passes_rules_only_when_set`, add `monkeypatch.delenv("SSSF_REFUSE_RULES", raising=False)` as the first line. It fails today when the shell sets that variable, because its stand-in pi echoes the inherited value. Change nothing else in the test and nothing in `agent_pi`.

### 7. `.claude/skills/sssf/tests/test_rules_continuation.py` (new)

Follow `test_rules_heredoc.py` and `test_rules_multiline.py`. Reuse `forwarder`, `announce`, `ran`, `starter_push_rule` and `claude_line` from `test_rules`, and `_bun_handler` and `_pi_block` from `test_rules_multiline`. Add no test-only config: rules are `rules.AgentRule` values, the shipped starter rule, or `secret_files.shell_rule`, as in those files.

**Safety: no-match rows and the honesty test really execute.** Every subprocess that can run a row uses `cwd=tmp_path`, `PATH=<stub dir>:/usr/bin:/bin` with recording stubs for `just` and `pi` first, and no `SSSF_NEXT_SHELL_PREFIX`. A test here must never reach a real `just`.

**Rules:** `LIVE` is `\bjust\s+test\s+live\b` and `PI` is `(^|[;&|]\s*)pi\b`, both `on_match="refuse"`. `END` is `\bjust\s+test\s+live$`.

**The shared table**, as `(pattern, command, expected match, what real bash runs)`. All three checkers must agree on it. All of these results were probed.

| Match | Rule | Command | Real bash runs |
|---|---|---|---|
| yes | LIVE | `ju\⏎st test live` | `just test live` |
| yes | LIVE | `just test \⏎live` | `just test live` |
| yes | LIVE | `just \⏎test \⏎live` | `just test live` |
| yes | LIVE | `\⏎just test live` | `just test live` |
| yes | LIVE | `ju\⏎\⏎st test live` | `just test live` |
| yes | LIVE | `echo hi⏎ju\⏎st test live` | `just test live` |
| yes | LIVE | `true; \⏎ju\⏎st test live` | `just test live` |
| yes | LIVE | `a=1 \⏎ju\⏎st test live` | `just test live` |
| yes | LIVE | `echo $(ju\⏎st test live)` | `just test live` |
| yes | LIVE | `bash -c 'ju\⏎st test live'` | `just test live` |
| yes | LIVE | `bash -c 'just test \⏎live'` | `just test live` |
| yes | LIVE | `cat <<'EOF' \| bash⏎ju\⏎st test live⏎EOF` | `just test live` |
| yes | LIVE | `bash <<'EOF'⏎just test \⏎live⏎EOF` | `just test live` |
| yes | LIVE | `eval 'ju\⏎st test live'` | `just test live` |
| yes | LIVE | `bash -c "ju\\⏎st test live"` (doubled) | `just test live` |
| yes | LIVE | `bash <<EOF⏎ju\\⏎st test live⏎EOF` (doubled) | `just test live` |
| yes | LIVE | `just test live` (matched before) | `just test live` |
| yes | PI | `p\⏎i -p hi` | `pi -p hi` |
| yes | PI | `echo hi⏎p\⏎i -p hi` (`^` in the joined text) | `pi -p hi` |
| yes | PI | `true; p\⏎i -p hi` | `pi -p hi` |
| yes | PI | `echo hi \⏎pi -p hi` (`^` only in the text as written) | nothing stubbed |
| yes | END | `just test live\⏎` (`$` in the joined text) | `just test live` |
| no | LIVE | `just test\⏎live` | `just testlive` |
| no | LIVE | `just test unit` | `just test unit` |
| no | PI | `echo pi\⏎p install x` | nothing stubbed |
| no | LIVE | `ju\` CR LF `st test live` (bash doesn't join these) | nothing stubbed |
| no (limit) | LIVE | `j\ust test live` | `just test live` |
| no (limit) | LIVE | `j''ust test live` | `just test live` |
| no (limit) | LIVE | `x\\⏎ju\⏎st test live` | `just test live` |
| no (limit) | LIVE | `# note\⏎ju\⏎st test live` | `just test live` |
| no (limit) | LIVE | `# note\⏎just test \⏎live` | `just test live` |
| no (limit) | PI | `true \\⏎p\⏎i -p hi` | `pi -p hi` |
| no (limit) | PI | `# note \⏎p\⏎i -p hi` | `pi -p hi` |

Name the five uncontinued-line rows and the two word rows as known limits in a comment, so a later fix knows to flip them.

**Tests over the table:**

- `rules.breach` gives the expected result on every row.
- The Claude Code prefix, as a subprocess on `claude_line(command)`: exit 126 with stderr starting with `rules.REFUSED`, or for a no-match row, not 126 and no refusal.
- Pi's real handler under bun, via `_bun_handler`, skipped without bun as the existing ones are: `block` equals the expected result on every row, in one bun run per rule.
- **The table is honest:** each row run with real `bash -c` and the stubs records exactly the `Real bash runs` column ("nothing stubbed" means nothing recorded).

**Prefix, lines in other shapes:**

- Exit 126: `true && eval ju\⏎st test live < /dev/null && pwd -P` (LIVE), `ju\⏎st test live` (LIVE), `x; eval \⏎'pi -p hi'` (PI), `x; eval 'p\⏎i -p hi'` (PI).
- Not refused, pinned as the quotes limit: `true && eval 'ju\'"⏎"'st test live' < /dev/null && pwd -P` (LIVE).

**Agreement:**

- With rules `[A, B]`, where A forbids `\bjust\s+test\s+live\b` and B forbids `\bst\s+test\s+live\b`, the command `ju\⏎st test live` makes all three checkers name A, though only B matches the text as written. This pins rule-then-pattern order.
- The shipped `starter_push_rule()` on `git \⏎push origin main`:
  - Pi: the real block reason passes through `_pi_block` into the forwarder and counts as `rule_refused`, as `test_a_real_pi_refusal_counts_and_the_third_stops` does.
  - Claude Code: the prefix's real stderr counts as a refusal in the forwarder. Feed an `assistant` event with a `tool_use` block named `Bash`, then a `user` event with a `tool_result` block whose content is `Exit code 126\n<stderr>`.
  - The same command with a `ran(...)` result, with no refusal, raises `RuleBreach` with `.pattern` equal to the pattern the refusal names (`match="may have run"`).
- SSSF's built-in rule, `secret_files.shell_rule(["/r/.env"])`, passed as `rules.refusing([builtin], builtin)`, refuses `cat .e\⏎nv` in the prefix and in pi's handler, and `rules.breach` matches it.
- A stop-mode rule (`on_match="stop"`) on `ju\⏎st test live` raises `RuleBreach` through the forwarder.

**Cost:** `rules.breach([LIVE], "\\" * 200_000)` returns in under 5 s (quadratic takes about 12 s, linear about 2 ms).

### 8. `.claude/skills/sssf/references/config.md`, line 429

Replace the sentence beginning `The search reads the text as written, not as the shell will run it,` and ending `match neither check.` with:

> The search reads the text as written, and again with every backslash-newline removed, along with any backslashes right before it, so `ju\` then a newline then `st test live` and `just test \` then a newline then `live` both match. A match in either counts, and `^` and `$` match at every line in both. Beyond that it doesn't read the command as the shell will run it, so these known limits get past it. Quotes inside a word: `j''ust test live` and `'just' test live` run `just test live` and match no check. A backslash before an ordinary character inside a word: `j\ust test live` does the same. A split command after a line the shell doesn't continue: the removal also joins a backslash-newline the shell keeps, as at the end of a comment or after an escaped backslash, so that line swallows the command's start and a `^` or `\b` anchor misses it. `# note\`, a newline, `ju\`, a newline, then `st test live` runs `just test live` and matches no check. Closing that would mean searching from each line after a continuation, which is quadratic.

Leave the rest of the paragraph, including its last sentence about working around a rule.

### 9. `docs/open.md`

Delete only the row beginning `Backslash-newline gets past every agent rule.` (line 19).

## Docs with no change needed

- This repo has no `CHANGELOG` and no `app_docs/` folder, so the documenter has no older write-up to note.
- `README.md` doesn't describe the rule search.
- `.claude/skills/sssf/cookbooks/install.md`'s retest step stays true: it calls the same two functions and the extension still compiles with the `m` flag.

## Verification

From `.claude/skills/sssf/`, the command `adws/adw_modules/quality.py` already runs:

```
uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
```

It must pass with exactly two existing tests edited: the `delenv` line in step 6 and the image-tag pin in step 5. Run it with `SSSF_REFUSE_RULES` set in the shell, as an agent's is. `bun` must be on `PATH`, or the pi tests skip and the pi half is unproven.

At baseline in an agent's shell, the rule and OpenShell test files gave 313 passed, 2 skipped, and 1 failed: the `delenv` test.

By reading the diff:

- `git diff` touches no existing test beyond those two edits.
- `sssf_refuse.py`'s docstrings hold no backslash character.
- All three checkers hold the identical pattern `(?<!\\)\\+\n`.
- No path under `adws/adw_modules/`, `adws/tests/` or another protected path changed.
- `sssf_refuse.main` still runs the original `line`.

## For the engineer

No protected path needs a change for this work to pass. Two things follow from merging it:

- **Image rebuild:** the image tag changes, so every repo on `--sandbox openshell` must rebuild the image once after it updates. `require_sdk` stops the run with a message naming the missing tag and the setup step, so this fails closed.
- **Rollout:** `adws/adw_modules/` and the `docs/projects.md` record get this change the usual way, through `just sssf-update`.

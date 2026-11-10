# Run no refused command from the hub's tests

## Summary

Guard run 45742573 stopped in its build because the skill suite's own tests run a stub
`just test live`. The hub's agentsh rule `sssf-no-live-tests` (`commands: [just]`,
`args_patterns: ['(^|\s)test\s+live(\s|$)']` in `agentsh_guard.py`'s `COMMAND_HEAD`) refuses it
in any process, and `agentsh_guard` counts every named exec denial in the agent's tree. This
change edits only two test files. Their rows run a stub with a made-up name,
`nosuch-sssf-fixture`, in place of `just test live`, and the fixture rules forbid that command
instead. Every row keeps its shape and its expected result.

## Baseline

`main` at `21f73ea` ("Put the hub on the text guard for the test-fixture run"). Every finding is
judged against it. At `21f73ea` the three files `tests/test_rules_continuation.py`,
`tests/test_rules_tab_heredoc.py` and `tests/test_rules_heredoc.py` pass outside the wrap
(335 passed).

## Threat model

The builder runs as the engineer's own user, with the engineer's shell, toolchains and
credentials. It can run any program its guard lets it exec, write any file its user can, and
start child processes. The text guard reads only the command the agent types, so it never sees
what a test runs. agentsh sees every exec in the agent's process tree and refuses the named
commands wherever they run. It does this on purpose, so that a refused command can't escape the
count by running a few processes down. The cost is that a test fixture that execs a refused
command looks the same as an agent doing it. This change takes the refused command out of the
fixtures. It does not change the guard. Neither guard can stop an agent that writes its own
program under an unlisted name, or one that reaches the same effect without a named exec. Both
are old holes, listed under Out of scope.

## Bar

Must:

- Every test in `.claude/skills/sssf/tests/test_rules_continuation.py` and
  `.claude/skills/sssf/tests/test_rules_tab_heredoc.py` passes outside the wrap. Both files keep
  the same rows in the same order, with the same `matches` value. Each row's "what bash runs"
  value is the same command with `just` replaced by `nosuch-sssf-fixture` (or the same `[]` or
  `None`).
- Each row keeps its shape. The backslash-newline, tab, `\r` and quote splits fall at the same
  offset in the first word as before: `ju\` + NL + `st` becomes `no\` + NL + `such-sssf-fixture`,
  `j\ust` becomes `n\osuch-sssf-fixture`, and `j''ust` becomes `n''osuch-sssf-fixture`.
- Under the hub's installed policy, neither file makes an exec that a named rule refuses. No
  exec of `just` has `test live` in its args.
- Each test still checks what it checked before, against all three matchers (`rules.breach`,
  the `sssf_refuse.py` prefix, pi's `sssf_rules.ts` handler) and against real bash.

Must never, where the baseline does otherwise:

- change any file outside those two test files, including `rules.py`, `sssf_refuse.py`,
  `sssf_rules.ts`, `agentsh_guard.py`, `templates/repo.yaml`, `templates/sssf.config.yaml`, the
  hub's `adws/adw_sssf_config/repo.yaml`, or its policy;
- drop, weaken or skip a test or a row, or flip a row's expected result;
- keep an agent file, run agent code, or delete or overwrite an engineer file. This change adds
  none of these: the tests write only under pytest's `tmp_path`, as before.

## Why `nosuch-sssf-fixture`, not `sssf-fixture`

Several rows split the first word so that bash runs only its first fragment as a command. Those
are the `[]` / `None` rows: the accepted over-matches in the tab-heredoc file, the
"spaces after a continuation" rows, and the `\r` row in the continuation file. With
`sssf-fixture`, a split after two characters runs `ss`, and `ss` is a real program on this
machine (iproute2's socket tool, `/usr/bin/ss`). A test would then run a real program, and its
stdout could break `test_the_table_is_honest_about_what_bash_does`. Check the fragments of
`nosuch-sssf-fixture` before relying on them: `command -v no n such-sssf-fixture
nosuch-sssf-fixture` must find none. `such-sssf-fixture` is the second fragment, which the `\r`
row runs as its own command. If any of them is found, pick another name whose one- and
two-character prefixes and whose remainder after two characters are not commands, and use it
everywhere below.

## Changes

Only these two files change. Both are under `.claude/skills/sssf/tests/`, which is not
protected.

### `.claude/skills/sssf/tests/test_rules_continuation.py`

1. Add `FIXTURE = "nosuch-sssf-fixture"` beside `NL` and `BS`.
2. Change `stubs()` to write recording stubs for `(FIXTURE, "just", "pi")`. Keep `just`, because
   `test_rules_tab_heredoc.py` imports `stubs` and its DEPLOY rows run `just deploy`, which no
   rule of the hub's refuses. Keep the stub body as it is (`echo "<name> $*" >> log`).
3. Rules:
   - `LIVE` forbid → `r"\bnosuch-sssf-fixture\s+test\s+live\b"`
   - `END` forbid → `r"\bnosuch-sssf-fixture\s+test\s+live$"`
   - `RULE_A` forbid → `r"\bnosuch-sssf-fixture\s+test\s+live\b"`
   - `RULE_B` forbid → `r"\bsuch-sssf-fixture\s+test\s+live\b"` (the text after the split, as
     `\bst\s+test\s+live\b` was)
   - the `stop` rule in `test_a_stop_rule_stops_the_run_on_a_split_command` → the same pattern
     as `LIVE`
   - Leave `PI` alone.
4. Rename `JUST` to `FIXTURE_RUN = "nosuch-sssf-fixture test live"` and use it in every row that
   had `JUST`.
5. Rewrite each command string in `ROWS`, in `test_the_prefix_refuses_lines_of_other_shapes`,
   in `test_the_prefix_misses_quotes_inside_a_word_across_the_wrapper`, in
   `test_all_three_name_the_first_rule_when_only_a_later_one_matches_as_written` and in the
   stop-rule test. Write the literal text in the f-strings so the shape stays visible:
   - `ju{BS}{NL}st test live` → `no{BS}{NL}such-sssf-fixture test live`. Do the same wherever
     `ju{BS}` and `st` appear, including `ju{BS}{BS}{NL}st`, `ju{BS}{NL}{BS}{NL}st` and
     `ju{BS}\r{NL}st`.
   - `just test …` → `nosuch-sssf-fixture test …` (rows with `{BS}{NL}live`, `test{BS}{NL}live`,
     `test live{BS}{NL}`, and the plain row)
   - `j{BS}ust test live` → `n{BS}osuch-sssf-fixture test live`
   - `j''ust test live` → `n''osuch-sssf-fixture test live`
   - the quotes test line → `f"true && eval 'no{BS}'\"{NL}\"'such-sssf-fixture test live' < /dev/null && pwd -P"`
   - the row expecting `"just testlive"` → `"nosuch-sssf-fixture testlive"`
   - the `"just test unit"` row → command `"nosuch-sssf-fixture test unit"`, runs
     `"nosuch-sssf-fixture test unit"`, still `False`
   - `{BS}{NL}just test live`, `$(...)`, `bash -c '...'`, `eval '...'` and the heredoc wrappers
     around the word all stay as they are.
6. Keep every row's `matches` value and its "runs" value, with the stub name swapped. Keep
   `NONE` where it is. Keep the "Known limits" comment, which stays true.
7. Module docstring: give the example as `no\\<newline>such-sssf-fixture test live` running
   `nosuch-sssf-fixture test live`. Add a short paragraph: the rows run a stub named
   `nosuch-sssf-fixture` in place of the `just` live-test command, because the hub's agentsh
   policy refuses that command in every process and counts each refusal toward the stop, so a
   builder that ran these tests under agentsh would be stopped. No real program has that name,
   nor either fragment a split leaves (`no`, `such-sssf-fixture`). Do not write the old command
   as literal text (see Verification check 1). "the `just` live-test command" is fine.

### `.claude/skills/sssf/tests/test_rules_tab_heredoc.py`

1. Import `FIXTURE` from `test_rules_continuation` with the others, if you use it.
2. `LIVE` forbid → `r"\bnosuch-sssf-fixture\s+test\s+live\b"`. `RULE_A` and `RULE_B` change as in
   the continuation file. The `stop` rule in
   `test_a_stop_rule_stops_the_run_on_a_split_command_in_a_tab_heredoc` uses the `LIVE` pattern.
3. Rename `JUST` to `FIXTURE_RUN = ["nosuch-sssf-fixture test live"]`.
4. In every LIVE row and in `EXAMPLE`: `f"ju{BS}", "st test live"` →
   `f"no{BS}", "such-sssf-fixture test live"`, and `f"ju{BS}{BS}"` → `f"no{BS}{BS}"`. The
   `f"ju{BS}", BS, "st test live"` row → `f"no{BS}", BS, "such-sssf-fixture test live"`.
   `f"just test {BS}", "live"` → `f"nosuch-sssf-fixture test {BS}", "live"`. The inline rows
   (`{T}ju{BS}{NL}{T}{T}{T}st test live`, `just test{BS}{NL}{T}live`, `ju{BS}{NL}{T}st test live`,
   and the two space/tab rows) follow the same mapping, with every tab and space where it was.
   The known-limit rows with `# note{BS}` and `x{BS}{BS}` change only their `ju`/`st` lines.
5. Leave `PUSH`, `DEPLOY`, `SECRET`, `PROBE` and `PI`, their rows and `with_stubs()` alone.
   DEPLOY comes from the shipped `templates/repo.yaml` starter rule (`just deploy`), and the
   hub's policy doesn't refuse `just deploy`. The hub's measured run showed no denial from the
   `git` stub or from `pi`.
6. Module docstring: give the example as `<tab>no\\<newline><tab>such-sssf-fixture test live`,
   and add one line pointing to the continuation file's note on why the stub has that name.

### Left as they are, and why

- `.claude/skills/sssf/tests/test_rules_heredoc.py` names `just test live` 53 times. Every
  command there is one that `rules.breach`, the prefix (`test_the_claude_prefix_refuses_it_too`)
  or pi's handler refuses before it runs, so nothing execs it. The hub's measured run found no
  denial from this file.
- `.claude/skills/sssf/tests/test_rules.py`, `test_rules_portable.py`, `test_rules_multiline.py`
  and the other suite files that name `just` do so as rule text or as commands their matchers
  refuse before they run. The measured run found no denial from them.
- `.claude/skills/sssf/references/config.md` uses `just test live` as the example of a user's
  rule, which stays accurate. These tests' fixture name isn't user-facing.

## Docs this makes out of date

- The two module docstrings above. They are part of this change.
- No README, help text or reference section describes these tests' rows. The repo keeps no
  CHANGELOG.
- No write-up in `app_docs/` names these two files, so the documenter has nothing older to mark.
  Don't edit `app_docs/`.

## Verification

From the repo root:

1. Neither file names the old command as code or text, and no split `just` row is left:
   `! grep -nE -e 'just( |\\s\+)test' -e "[}\"']st test live" -e 'j\{BS\}ust' -e "j''ust" .claude/skills/sssf/tests/test_rules_continuation.py .claude/skills/sssf/tests/test_rules_tab_heredoc.py`
   This must exit 0. At `21f73ea` it exits 1.
2. The three files pass outside the wrap, the way their docstrings run them:
   `cd .claude/skills/sssf && uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_rules_continuation.py tests/test_rules_tab_heredoc.py tests/test_rules_heredoc.py -q -p no:cacheprovider`
   The pass count must be 335, the same as at `21f73ea`, with no new skips (bun installed).
3. No fragment names a real program:
   `sh -c 'for c in no n such-sssf-fixture nosuch-sssf-fixture; do if command -v "$c"; then exit 1; fi; done'`
   must exit 0 and print nothing.
4. Row parity: `git diff -U0 21f73ea -- .claude/skills/sssf/tests/test_rules_continuation.py .claude/skills/sssf/tests/test_rules_tab_heredoc.py`
   must show no change to any `True`/`False` in a row, and no added or removed row. Then
   `git diff --stat 21f73ea` must list only those two files.
5. The full skill suite passes outside the wrap, as the chain's verify runs it.
6. After the merge (the hub session, not the builder): run the skill suite under the hub's
   installed agentsh policy from a copy of `.claude`, and count the named exec denials. There
   should be none.

## Out of scope

- Counting denials by depth or by folder. An agent could then hide a refused command a few
  processes down.
- The 203 failures and 12 errors under the hub's policy that come from file reads and writes
  outside the allowed folders, or from connects to the tests' local servers. They don't count
  toward the stop, and the hub session records them in the design doc.
- The tool-config change in `specs/tasks/guard-agentsh-tool-config.md`.
- Old holes this leaves open: an agent can exec a refused tool under another name or through a
  copy, since agentsh matches names. The text guard still reads only the typed command. Neither
  guard stops an agent that runs as the engineer's user from doing what that user can do
  without a named exec.

## For the engineer

- Put the hub back on `local_guard: agentsh` in `adws/adw_sssf_config/repo.yaml` after the
  merge, then rerun `specs/tasks/guard-agentsh-tool-config.md`. This file is protected, so the
  builder doesn't touch it.
- The hub session records the post-merge denial count and the 203 file-denial failures in
  `docs/designs/agentsh-local-sandbox.md`.

# Run no refused command from the hub's tests

Start from `main`. The engineer chose this fix on 2026-11-10, after guard run 45742573 stopped in its build.

## The problem

Run 45742573, from `specs/tasks/guard-agentsh-tool-config.md`, ran under `local_guard: agentsh`. Its builder ran the skill suite inside its wrap, and SSSF stopped it for 29 refusals under the hub's rule `sssf-no-live-tests`. The builder never ran a live test. Every refusal was a stub `just test live` that a test runs from pytest's temp folder, five processes below the builder. The hub's policy refuses `just test live` wherever it runs, which is right, and `agentsh_guard` counts every named exec denial in the agent's process tree toward the three-refusal stop, which is also right: an agent mustn't escape the count by running a refused command one process further down. So under agentsh, a builder that runs these tests gets stopped.

Under the text guard this never showed up, because the text checks read only the command an agent types.

The hub session ran the whole skill suite under the hub's installed policy, from a copy of `.claude` outside the repo, as the builder did. Of 239,299 execs, 31 were refused, all `just test live` under `sssf-no-live-tests`, from these tests:

- `test_rules_continuation.py`: `test_the_prefix_reads_through_a_backslash_newline`, `test_the_table_is_honest_about_what_bash_runs` and `test_the_prefix_misses_quotes_inside_a_word_across_the_wrapper`
- `test_rules_tab_heredoc.py`: `test_the_prefix_reads_through_a_backslash_newline_in_a_tab_heredoc` and `test_the_table_is_honest_about_what_bash_does`

No test ran `gh` or a `git push`.

## What should change

The tests run a command of their own in place of `just test live`, one no repo's rule refuses: a stub with a name no real program has, such as `sssf-fixture`, put on the stub `PATH` the way `just` is now. The fixture rules (`LIVE`, `END` and any other rule these tests run a stub for) forbid that command instead, and every row keeps its shape: the backslash-newline, tab and quote splits fall at the same places in the word, and each row's expected result stays the same. The tests then prove what they proved before.

`test_rules_heredoc.py` and any other test that names `just test live` only as text, without running it, may keep it. The spec lists each one it leaves and why.

Nothing outside the tests changes. `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `agentsh_guard.py`, the hub's `repo.yaml` and its policy stay as they are.

## The bar

The earlier bars stand. Every test in the two files passes outside the wrap, with the same rows and the same expected results. Under the hub's installed policy, the skill suite makes no exec that a named rule refuses. A test that no longer checks what it checked before is a finding, and so is any change outside the tests.

## Testing

The chain's verify runs the suite outside the wrap as usual. After the merge, the hub session runs the skill suite under the hub's policy from a copy of `.claude`, as above, and counts the named exec denials: there should be none.

Under the hub's policy, 203 tests fail and 12 error. Apart from the five above, they fail on what the policy refuses besides execs: file reads and writes outside the allowed folders, and connects to the tests' own local servers. Neither counts toward the stop. They're out of scope here; the hub session records them in the design doc.

## This run is under the text guard

Under agentsh, this run's builder would run the old tests before changing them and get stopped the same way. The engineer has the hub on `local_guard: text` for this run, and back on agentsh after the merge, for the rerun of `specs/tasks/guard-agentsh-tool-config.md`.

## Out of scope

Counting denials differently, by depth or by folder: an agent could then hide a refused command a few processes down. The 203 file-denial failures. The tool-config change.

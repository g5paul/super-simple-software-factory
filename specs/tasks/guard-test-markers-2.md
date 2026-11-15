# Mark the tests that can't pass under agentsh, second try

This replaces `specs/tasks/guard-test-markers.md`. Read that brief for the problem. Run it with `--worktree` and `--base main`, on the hub's own config.

Guard run 5fa763b5 built the first brief and stopped at its build on 2026-11-15, with nothing committed but its spec, `specs/5fa763b5_agentsh-test-markers_v2.md` on branch `sssf/5fa763b5`. Read that spec. Its threat model, baseline, bar and "Must never" list carry over, except where this brief changes them.

## What changes from 5fa763b5's spec

1. **Every test that failed in all three runs gets `host_only`.** That's the 270 in `re2.sum`. 5fa763b5's spec marked a test only if its error line named the sandbox, such as `PermissionError`, `URLError` errno 13 or a hidden `.env`. That left about 101 tests unmarked whose errors were downstream, such as `assert [] == ['e1']`, `assert 1 == 0`, `IndexError` or `config validation failed`. The engineer decided on 2026-11-15 to mark those too. Every one of them passes outside agentsh on the base, where verify runs it, and failed in all three runs under agentsh. So the sandbox is the cause, even where the error line doesn't say so. Keep that decision in the spec; don't tighten it back.
   - Each mark's comment says which kind it is. Either the error names the sandbox, or it's downstream, with the first error line.
   - The four tests that passed in one rerun stay unmarked, as flaky. They're listed in 5fa763b5's notes.
   - Parametrized tests are marked per function, as 5fa763b5 did.
2. **The briefs are out of this run.** `build_brief` and `review_brief` in `adws/adw_guard_change.py`, and their test in `adws/tests/test_guard_change.py`, are protected and not in `guard_builder`'s `writes`. The engineer's session makes those edits after this run merges. Leave them out of the spec, except for a line saying so.
3. **The fast-run copy.** The build brief's `rsync` copy can't write `.env.sample` or `templates/.env.sample` under agentsh, and four test files then fail in the copy for that reason alone. After the `rsync`, copy those two files in from a `git archive` copy of the base, as 5fa763b5's builder did. The fast run is judged in that copy. The `rsync` itself is the engineer's to fix, in the build brief.

## Reuse 5fa763b5's work

Don't repeat its base run, which took 15 minutes. These files hold its results, and they're readable from the run's worktree:
- `/tmp/tmp.V1jqpspgv8/base.log`: the base run under agentsh, at c07ab63. 261 failed, 13 errors, 2722 passed.
- `/tmp/tmp.V1jqpspgv8/re2.sum`: the 270 tests that failed in all three runs, one summary line each.
- `/tmp/mark_script/mark.py` and `/tmp/mark_script/marked_functions.txt`: the script that inserted the marks, and the 108 functions it marked.
- `adws/adw_data/worktrees/5fa763b5`: the uncommitted marks, `conftest.py`, `test_markers.py` and the `docs/sssf.md` paragraph. `git -C <that worktree> diff` and its one untracked test file are a starting point. The base has moved since c07ab63, by `docs/open.md` only.

Check each reused result before relying on it. If a `/tmp` file is gone, the base run has to be done again, as 5fa763b5's spec says.

## The bar

- 5fa763b5's "Must" and "Must never" lists hold. "Every `host_only` mark is earned" now means the test failed in all three of 5fa763b5's runs and is in `re2.sum`.
- **The fast run is clean.** `pytest tests -q -m 'not pushes and not host_only'`, under agentsh on the head, in the copy step 3 describes, has no failure, no error and no refused command. A flaky test failing in it doesn't count against this if a rerun of that test alone passes, and the notes say so.
- **Verify passes the whole suite.** That shows each marked test passes outside agentsh.

## Running the tests under agentsh

agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. Until this change lands, the builder leaves `tests/test_export.py` and `tests/test_install_hook.py` out of its own runs. The chain's verify runs as code, outside agentsh, and runs everything.

The same holds for the bypass reviewer and any harness an agent writes. To give a bare repo a commit, fetch into it instead: `git -C <bare> fetch -q <work> main:main`.

## Out of scope

- `adws/adw_guard_change.py`, `adws/tests/` and anything else under `adws/`.
- Making any marked test pass under agentsh. That's open.md's row on the 270 tests.
- The agentsh policy.
- The old hole 5fa763b5's spec named, an unprotected `conftest.py` that a later build could give a skip hook.

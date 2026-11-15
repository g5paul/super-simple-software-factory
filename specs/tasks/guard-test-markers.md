# Mark the tests that can't pass under agentsh, and have guard builds skip them

This is open.md's P3 row "A builder that runs the whole skill suite under agentsh is stopped by tests that push". Run it with `--worktree` and `--base main`, on the hub's own config.

## The problem

A guard builder runs under agentsh, and some of the skill's tests can't pass there:
- `tests/test_export.py` and `tests/test_install_hook.py` run `git push` to a bare repo. agentsh's `sssf-no-push` rule refuses every push, and three refusals end the call. Run 5db82031's first build was refused 51 times and stopped with its code fine.
- Other tests need what the policy denies: `test_agentsh_guard.py`'s fake HTTP service on loopback (`URLError` errno 13), `test_agent_pi.py`'s token broker socket, and a test's own `.env` under pytest's temp folder.

Each build's builder runs the whole suite under agentsh, which takes about 20 minutes where 9 do outside it. Then it reruns failures to tell the sandbox's from its own. Run 7bad1e2b's build took 50 minutes. Its builder also missed four real failures in `test_rules.py` among the sandbox ones, and verify caught them. Every brief today has to name the files to leave out by hand.

## The changes

1. **Two pytest markers**, registered in `tests/conftest.py` under `.claude/skills/sssf/`, so `--strict-markers` would accept them:
   - `pushes`: the test runs `git push`. Mark `test_export.py` and `test_install_hook.py` whole, with `pytestmark`.
   - `host_only`: the test needs something agentsh denies, such as a loopback connection, a Unix socket outside the policy's paths, or a file the policy hides. Mark each such test singly, with a comment naming what it needs.
2. **Find the `host_only` set by running it, not by reading.** Make an `archive` copy of the base into a temp folder, then run the skill suite there under agentsh, as the builder already runs. On the base, every test passes on the host, so each one that fails here is a sandbox failure. Mark exactly those. Name each in `notes_for_next_agent`, with its error line. A test that fails here for another reason, a flaky one included, isn't marked: say so in the notes.
3. **The guard chain's build brief** (`build_brief` in `adws/adw_guard_change.py`) tells the builder to run the tests its change touches, plus `pytest tests -q -m 'not pushes and not host_only'`. A marked test the change touches is the builder's to read, not to run. Verify runs the whole suite outside agentsh, and its result is the one that counts.
4. **The review brief** (`review_brief`) says the same for any suite run the reviewer makes. It adds that a reviewer's harness gives a bare repo a commit by fetching into it, `git -C <bare> fetch -q <work> main:main`, never by pushing.
5. **Docs.** `docs/sssf.md`'s guard changes section says in two lines what the markers mean and that verify runs everything. `GLOSSARY.md` stays as it is.

## The bar

- **Verify runs every test it runs today.** The verify command is byte-identical to `main`'s, and no marker skips or deselects a test unless `-m` is passed. A test shows that the suite collects the same number of tests with and without the new `conftest.py`.
- **A marker hides nothing from verify.** No test gains `skip`, `skipif` or `xfail`, and no assertion changes. The only edits to existing tests are marker lines and their comments.
- **Every `host_only` mark is earned.** Each marked test fails under agentsh on the base, as recorded in the notes, and passes in verify. No guard test is marked unless it meets that.
- **The fast run is clean.** `pytest tests -q -m 'not pushes and not host_only'` under agentsh, on the head, has no failures and no refusals.
- **The briefs.** A test pins that `build_brief` and `review_brief` name the `-m` expression and the fetch line, and that the verify phase's command doesn't.
- **No gate loosens.** The chain's verify, its accept and commit conditions, the round limit and the mechanism stop are byte-identical to `main`'s.

## Running the tests under agentsh

Until this change lands, the builder leaves the two pushing files out of its own runs: `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py`. The step 2 run is the one exception that runs the rest of the suite whole, once, on the base. The chain's verify runs as code, outside agentsh, and runs everything.

The same holds for the bypass reviewer and any harness an agent writes. To give a bare repo a commit, fetch into it instead: `git -C <bare> fetch -q <work> main:main`.

## Out of scope

- The skill's builder prompts for project chains. Their builders run a project's own tests, which these markers don't cover. That gets its own row once the hub's version works.
- Making any marked test pass under agentsh, and any change to the agentsh policy, such as allowing loopback.
- The hub's chain tests under `adws/tests`, which take seconds.

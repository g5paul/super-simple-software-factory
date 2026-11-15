# Mark the tests that can't pass under agentsh, second try

## Summary

A guard builder runs under agentsh, where about 330 of the skill's tests can't pass. Two files run `git push`, which agentsh refuses, and 270 other tests fail there for reasons that start at a sandbox denial. This change registers two pytest markers, `pushes` and `host_only`, marks those tests, and documents the run that leaves them out: `pytest tests -q -m 'not pushes and not host_only'`. Verify passes no `-m`, so it runs every test it runs today.

This is the second try. Guard run 5fa763b5 planned and built the first, and stopped at its build. Its spec is `specs/5fa763b5_agentsh-test-markers_v2.md` on branch `sssf/5fa763b5` (`git show sssf/5fa763b5:specs/5fa763b5_agentsh-test-markers_v2.md`). That spec's threat model, bar and "Must never" list carry over, restated below. Three things change:

1. **Every test that failed in all three of 5fa763b5's runs gets `host_only`.** That is the 270 lines of `re2.sum`, 195 test functions. 5fa763b5 marked only the 108 functions whose error line named the sandbox, and its fast run then failed on the other 87 (101 cases). The engineer decided on 2026-11-15 to mark those too: each passes outside agentsh on the base and failed three times out of three under it, so the sandbox is the cause even where the error line doesn't say so. **Don't tighten this back** to errors that name the sandbox.
2. **The briefs are out of this run.** `build_brief` and `review_brief` in `adws/adw_guard_change.py`, and their test in `adws/tests/test_guard_change.py`, are the engineer's to edit after this run merges.
3. **The fast run is judged in a copy that holds the two sample env files**, which the build brief's `rsync` can't write under agentsh.

No guard module changes, so there was none to read whole. The builder edits tests, `conftest.py` and one doc.

Files the builder touches, and no others:

- `.claude/skills/sssf/tests/conftest.py`
- `.claude/skills/sssf/tests/test_markers.py` (new)
- `.claude/skills/sssf/tests/test_export.py` and `.claude/skills/sssf/tests/test_install_hook.py` (`pushes`)
- the 22 test files under `.claude/skills/sssf/tests/` that hold a `host_only` test, listed in step 3
- `docs/sssf.md`

## Threat model

The builder runs as the engineer's own user under agentsh. It can already edit `conftest.py` and every test, and `guard_builder` can edit the listed guard tests. A marker gives it one more way to act: label a test so that an agent's own run leaves it out.

- A label could be put on a test that fails for a real reason, to keep it out of the builder's sight. The marker doesn't hide it from the gate: verify runs as code, outside agentsh, with no `-m`, and commits nothing unless every test passes.
- Marking the 87 downstream functions widens that label to tests whose error doesn't name the sandbox. What stands for each mark is the record: three failures under agentsh on the base, and a pass in verify.
- A hook in `conftest.py` could skip or deselect marked tests. This change adds none. `conftest.py` is unprotected at the baseline, so a later build could add one: an old hole, under Out of scope.
- The builder's own test run was never a gate. A narrower run finds fewer mistakes before verify, and verify finds the rest.

Nothing at this level stops an agent that edits a test's body. That is the after-call check's and the reviewers' job, unchanged.

## Baseline

`main` at `e710b65`. Every finding is judged against that commit.

5fa763b5's runs were made at `c07ab63`. `git diff --stat c07ab63 e710b65` shows `docs/open.md` and `specs/tasks/guard-test-markers-2.md` only, so every test file is the same and its results hold for `e710b65`. At the baseline the suite collects 3068 tests, 41 of them in `test_export.py` and 20 in `test_install_hook.py` (from 5fa763b5's spec, not counted again here).

## Bar

Must:

- **Verify runs every test it runs today, and passes.** `adws/adw_modules/quality.py` is byte-identical to `e710b65`'s. With no `-m`, the suite collects the same node ids with `conftest.py` loaded and with `--noconftest`, and deselects none. Verify passing the whole suite shows each marked test passes outside agentsh.
- **Both markers are registered.** Collection passes under `--strict-markers`.
- **`pushes` covers `test_export.py` and `test_install_hook.py` whole**, and nothing else.
- **Every `host_only` mark is earned.** The test failed in all three of 5fa763b5's runs and is in `re2.sum`. The marked set is exactly the 195 functions `re2.sum` names, no more and no fewer.
- **Each mark's comment says which kind it is**: the error names the sandbox, or it is downstream, with the first error line.
- **The fast run is clean.** `pytest tests -q -m 'not pushes and not host_only'`, under agentsh on the head, in the copy the Verification section describes, has no failure, no error and no refused command. A flaky test failing in it doesn't count against this if a rerun of that test alone passes and the notes say so.

Must never:

- Give any test `skip`, `skipif` or `xfail`, change an assertion, or change a test's body. The only edits to existing test files are the two `pytestmark` lines, `@pytest.mark.host_only` lines and their comments. All 22 files import `pytest` already.
- Add a hook, fixture or option that skips, deselects or ignores a test. One `pytest_configure` with two `addinivalue_line` calls is the whole `conftest.py` change.
- Mark a test that isn't in `re2.sum`. The four flaky tests stay unmarked: `tests/test_kill.py::test_a_process_younger_than_its_row_is_left_alone`, `::test_it_stops_the_chain_and_its_agent_and_closes_the_run`, `::test_the_agent_is_stopped_while_the_chain_waits_on_it`, and `tests/test_worktree.py::test_an_empty_trace_db_reads_as_no_runs[False]`.
- Change any guard module, the agentsh policy, `GLOSSARY.md`, or anything under `adws/`.
- Loosen a gate. The chain's verify, its accept and commit conditions, `MAX_ROUNDS` and the mechanism stop stay byte-identical, because `adws/` is untouched.

The default bar holds as at the baseline: no agent file kept, no agent code run, and no engineer file deleted or overwritten, where the baseline does otherwise. This change touches none of that code.

## What the planner checked in 5fa763b5's results

Checked on 2026-11-15 from this run's worktree. Check again before relying on any of it: `/tmp` can be cleared.

| File | Found |
| --- | --- |
| `/tmp/tmp.V1jqpspgv8/base.log` | ends `261 failed, 2722 passed, 12 skipped, 2 warnings, 13 errors in 881.42s`; 274 `FAILED`/`ERROR` lines |
| `/tmp/tmp.V1jqpspgv8/re.log` | the 274 rerun: 261 failed, 13 errors |
| `/tmp/tmp.V1jqpspgv8/re2.log` | the 274 rerun again: 258 failed, 12 errors, 4 passed |
| `/tmp/tmp.V1jqpspgv8/re2.sum` | 270 lines, 258 `FAILED` and 12 `ERROR`, one per node id, the same ids as `re2.ids`. The four ids in `re.ids` and not here are the flaky four above |
| `/tmp/mark_script/marked_functions.txt` | 108 `MARK` lines. They are exactly the functions in `re2.sum` whose error matches `mark.py`'s `DENIAL` pattern |
| `/tmp/tmp.PX75OPs0mP` | 5fa763b5's fast-run copy. It holds its `conftest.py`, its `test_markers.py`, its 108 marks and its `docs/sssf.md` paragraph |
| `/tmp/tmp.PX75OPs0mP/fast.log` | its fast run: 205 failing ids. 101 are the downstream cases in `re2.sum`, 4 are the flaky four, and 100 are in `test_container.py`, `test_registry.py`, `test_rosters.py`, `test_trial.py` and `test_update.py`, none of them in `re2.sum`. `test_markers.py` passed |
| `adws/adw_data/worktrees/5fa763b5` | **not readable** from this run: `Permission denied`. Don't work around it. Its work is readable in `/tmp/tmp.PX75OPs0mP` instead |

Three things differ from the request's wording, and the plan follows what the files show:

- The second sample file is `.claude/skills/sssf/templates/env.sample`, with no leading dot. `rsync` writes through a temp name that starts with `.env`, which is why it can't write it.
- The 100 copy-only failures are in five files, not four.
- The flaky four failed in three of 5fa763b5's four runs (base, first rerun, fast run) and passed once. Expect them to fail in the fast run.

`re2.sum` sorts into 195 functions: 108 whose every case names the sandbox (169 cases) and 87 whose every case is downstream (101 cases). No function has cases of both kinds. Each name is defined exactly once in its file. Per file, functions marked:

| File | Functions | File | Functions |
| --- | --- | --- | --- |
| `test_adw_tickets.py` | 21 | `test_pi_token_broker.py` | 8 |
| `test_agent_cc.py` | 6 | `test_rosters.py` | 1 |
| `test_agent_pi.py` | 5 | `test_rules.py` | 3 |
| `test_agentsh_guard.py` | 53 | `test_rules_tab_heredoc.py` | 1 |
| `test_budget.py` | 22 | `test_sandbox_extensions.py` | 1 |
| `test_container.py` | 1 | `test_sandbox_remote.py` | 11 |
| `test_engineer_recipes.py` | 5 | `test_secret_files.py` | 8 |
| `test_git_dir_guard.py` | 10 | `test_session_files.py` | 2 |
| `test_host_python.py` | 1 | `test_sssf_update.py` | 6 |
| `test_obs_relay.py` | 21 | `test_trial.py` | 5 |
| `test_openshell_setup_refuses.py` | 3 | `test_update.py` | 1 |

All are under `.claude/skills/sssf/tests/`. Seven are listed guard tests in `guard_builder`'s `writes`: `test_agent_cc.py`, `test_agent_pi.py`, `test_agentsh_guard.py`, `test_git_dir_guard.py`, `test_rules.py`, `test_rules_tab_heredoc.py`, `test_secret_files.py`. Add marker lines there and nothing else.

**If `re2.sum` is gone**, the base run has to be made again as step 3 of 5fa763b5's spec says, on an archive copy of `e710b65`, with two reruns of the failures. A test is marked only if it fails in all three.

## Changes

### How the builder runs tests in this run

- Never run the suite from the worktree. Run from a copy outside the repo.
- Every run uses the hub's prefix, from `.claude/skills/sssf` in the copy: `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider`, plus the options named.
- agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. Never run `tests/test_export.py` or `tests/test_install_hook.py`. Until both `pytestmark` lines are in the copy, add `--ignore=tests/test_export.py --ignore=tests/test_install_hook.py`.
- The repo's rules search every shell command's text. Don't put the words `git` and `push` together in a shell command, a `grep` pattern included. Search with the grep tool.
- The fast run takes about 15 minutes under agentsh, and a foreground shell call ends at 10. Start it in the background with its output in a log file in the copy.
- Write any helper script with the file tool, into a temp folder outside the repo, and run it with `python3 -I`.

### 1. `.claude/skills/sssf/tests/conftest.py`: register the markers

Add one function after the `no_dotenv_keys` fixture, and change nothing else:

```python
def pytest_configure(config):
    """Two markers for tests that can't pass under agentsh. A marker only labels a test.
    Nothing here skips or deselects one, so a run with no `-m`, verify's included, runs them
    all. An agent under agentsh runs `-m 'not pushes and not host_only'`."""
    config.addinivalue_line("markers", "pushes: the test runs `git push`, which agentsh refuses")
    config.addinivalue_line("markers", "host_only: the test passes outside agentsh and fails under "
                            "it, whether its error names the sandbox or only follows from a denial")
```

### 2. `test_export.py` and `test_install_hook.py`: `pushes`

Neither has a `pytestmark` today. In each, after the module constants and before the first function, add:

```python
# Every test here pushes to a bare repo under tmp_path. agentsh refuses every push, so a run
# under agentsh leaves this file out with -m 'not pushes'. Verify runs it.
pytestmark = pytest.mark.pushes
```

In `test_export.py` that is after `import adw_export`. In `test_install_hook.py` it is after `GITLEAKS = shutil.which("gitleaks")`. The `skipif` lines in `test_install_hook.py` stay.

### 3. Mark the 195 `host_only` functions

Each mark is two lines directly above the test's `def` line, below any decorator it already has, at the `def`'s indent:

```python
# host_only, the error names the sandbox: reaches its fake service on 127.0.0.1 or a socket, and agentsh denies it.
@pytest.mark.host_only
def test_...

@pytest.mark.parametrize("name", ROWS)
# host_only, downstream: under agentsh it fails with: AssertionError: assert [] == ['e1']
@pytest.mark.host_only
def test_...
```

- A function whose error names the sandbox gets `# host_only, the error names the sandbox: <what it needs>.`, with the four reasons `mark.py` used.
- A downstream function gets `# host_only, downstream: under agentsh it fails with: <first error line>`. The line is the text after ` - ` on the function's first line in `re2.sum`, cut at 70 characters, and cut sooner at a backtick, `/home/`, `/tmp/` or `engineer `, so no home path, user name or command lands in a comment. A cut line ends in `...`.
- A parametrized test is marked once, on the function, so its passing cases leave the fast run too.
- `test_engineer_recipes.py` and `test_sssf_update.py` have a module `pytestmark` with `skipif`, and three marked functions have a `skipif` decorator. Leave all of them as they are.

The script below does this. The planner ran it on a temp copy of the baseline's tests: it exits 0, prints 195 `MARK` lines (108 `named`, 87 `downstream`, cases summing to 270), every file still compiles, and no line is removed. Write it outside the repo and run it once, on unmarked files: `python3 -I <script> /tmp/tmp.V1jqpspgv8/re2.sum .claude/skills/sssf/tests`. It writes nothing unless it finds every test. If your output differs from those counts, stop and report, don't adjust the list by hand.

```python
"""Add @pytest.mark.host_only above each test named in SUM. Usage: mark_all.py SUM TESTS_DIR"""
import collections
import re
import sys
from pathlib import Path

DENIAL = re.compile(r"PermissionError|Permission denied|URLError|PermissionBreach|AgentshRefused"
                    r"|IO error while|Errno 13")
LINE = re.compile(r"^(FAILED|ERROR) tests/(\w+\.py)::(\w+)(\[.*\])? - (.*)$")
CUT = re.compile(r"`|/home/|/tmp/|engineer ")


def why(msg):
    if "URLError" in msg:
        return "reaches its fake service on 127.0.0.1 or a socket, and agentsh denies it"
    if re.search(r"PermissionBreach|AgentshRefused", msg):
        return "its guard run reads the git directory or the agentsh service, and agentsh denies it"
    if "IO error" in msg:
        return "runs a just recipe that writes where agentsh denies"
    return "writes or links a file that agentsh denies"


def first_line(msg):
    cut = CUT.split(msg, maxsplit=1)[0][:70].rstrip()
    return cut if cut == msg else cut + "..."


summ, tests_dir = Path(sys.argv[1]), Path(sys.argv[2])
found = collections.OrderedDict()            # (file, func) -> the messages of its failing cases
for line in summ.read_text().splitlines():
    m = LINE.match(line)
    if not m:
        sys.exit(f"unreadable line: {line[:120]}")
    found.setdefault((m.group(2), m.group(3)), []).append(m.group(5))

texts, report = {}, []
for (f, func), msgs in found.items():
    named = [bool(DENIAL.search(x)) for x in msgs]
    if any(named) != all(named):
        sys.exit(f"{f}: {func} has cases of both kinds")
    kind = "named" if named[0] else "downstream"
    comment = (f"# host_only, the error names the sandbox: {why(msgs[0])}." if named[0]
               else f"# host_only, downstream: under agentsh it fails with: {first_line(msgs[0])}")
    if f not in texts:
        texts[f] = (tests_dir / f).read_text().split("\n")
        if not any(re.match(r"import pytest\b", l) for l in texts[f]):
            sys.exit(f"{f} has no `import pytest`")
    lines = texts[f]
    idx = [i for i, l in enumerate(lines) if re.match(rf"\s*(async )?def {func}\(", l)]
    if len(idx) != 1:
        sys.exit(f"{f}: {func} is defined {len(idx)} times")
    i = idx[0]
    if "host_only" in lines[i - 1]:
        sys.exit(f"{f}: {func} is already marked")
    indent = re.match(r"\s*", lines[i]).group(0)
    lines[i:i] = [indent + comment, indent + "@pytest.mark.host_only"]
    report.append(f"MARK {kind} {f} {func} {len(msgs)}")

for f, lines in texts.items():               # nothing is written unless every test was found
    (tests_dir / f).write_text("\n".join(lines))
print("\n".join(report))
```

Keep the script's output: the notes are built from it. Don't copy 5fa763b5's 108 marks from `/tmp/tmp.PX75OPs0mP`: their comments don't say which kind they are.

### 4. `.claude/skills/sssf/tests/test_markers.py` (new)

Start from 5fa763b5's file, `/tmp/tmp.PX75OPs0mP/.claude/skills/sssf/tests/test_markers.py`, which passed in its fast run. If it is gone, write it from this description.

A helper runs `[sys.executable, "-m", "pytest", "tests", "--collect-only", "-q", "-p", "no:cacheprovider", *extra]` with `cwd` at the skill root and returns the exit code, the output and the node ids, which are the stdout lines that hold `::`. Three module-scoped fixtures hold the three collections, so each runs once: plain, `--noconftest`, and the fast run, `--strict-markers -m 'not pushes and not host_only'`. Add no more.

Four tests:

1. **The conftest changes no count.** The plain and `--noconftest` runs both exit 0 and give the same ids. The plain run's output doesn't say `deselected`.
2. **Both markers are registered and in use.** The fast run exits 0 under `--strict-markers`. Among the ids in the plain run and not in the fast run, at least one is in neither pushing file, so `host_only` marks something. This replaces 5fa763b5's version of the test, whose two `in src` assertions could not fail.
3. **`pushes` covers the two files, whole.** The plain run has ids from `tests/test_export.py` and `tests/test_install_hook.py`. The fast run has none from either.
4. **The fast run adds no test.** Its ids are a subset of the plain run's.

No test here pins a count of marked tests: a test that is later fixed to pass under agentsh loses its mark, and that must not break this file. The file gets no marker itself.

### 5. `docs/sssf.md`: one paragraph under "Guard changes"

After the paragraph that starts "The hub's `protected_files` list the shipped guard code", add:

> Two pytest markers, registered in the skill's `tests/conftest.py`, name the tests that can't pass under agentsh. `pushes` marks a test that runs `git push`. `host_only` marks a test that passes outside agentsh and failed under it in every run of the base, whether its error names the sandbox or only follows from a denial. Each mark's comment says which. An agent's own run under agentsh leaves them out with `pytest tests -q -m 'not pushes and not host_only'`. Verify runs as code outside agentsh with no `-m`, so it runs every test, and its result is the one that counts.

No other doc changes in the build. The repo keeps no CHANGELOG and has no `app_docs/`. `GLOSSARY.md` stays as it is. `docs/open.md` is the engineer's at the merge.

## Verification

1. **Fail first.** With `test_markers.py` written and no mark yet, run it alone in a copy: test 3 fails, because the fast run still holds the two files' ids. With the marks in and `pytest_configure` missing, test 2 fails under `--strict-markers`. Report both.
2. **The script's counts.** 195 `MARK` lines, 108 `named`, 87 `downstream`, cases summing to 270, and the per-file counts in the table above.
3. **The diff.** `git diff e710b65 --numstat -- .claude/skills/sssf/tests` shows no removed line. In existing files every added line is a `pytestmark` line, a `@pytest.mark.host_only` line, a comment, or part of the one `pytest_configure` function. There are 195 `@pytest.mark.host_only` lines. `git status --porcelain -- adws/ GLOSSARY.md .claude/skills/sssf/templates .claude/skills/sssf/scripts` prints nothing.
4. **The fast-run copy.** From the repo root, with the build in place:
   - `D=$(mktemp -d)`, then the build brief's `rsync` line. It exits 23, naming `.env.sample` and `.claude/skills/sssf/templates/env.sample`. Confirm those are the only two names.
   - `B=$(mktemp -d); git archive e710b65 | tar -x -C "$B"`. If `tar` can't write the two files either, use `/tmp/tmp.V1jqpspgv8` as `$B`: it is c07ab63's archive copy, and the planner compared both files there with `e710b65`'s, byte for byte.
   - `cp "$B/.env.sample" "$D/.env.sample"` and `cp "$B/.claude/skills/sssf/templates/env.sample" "$D/.claude/skills/sssf/templates/env.sample"`. The repo's rules allow the name `.env.sample`.
   - Confirm both files are in `$D`. If either can't be copied, stop and report: the fast run can't be judged without them.
5. **The fast run, on the head.** From `$D/.claude/skills/sssf`, run the hub prefix with `-rfE -m 'not pushes and not host_only'`, in the background, with output to a log in `$D`. It must end with no `failed` and no `error`, and no command of yours may be refused.
   - A test that fails here is rerun alone by node id, up to three times, stopping at the first pass. If it passes, it is flaky: name it in the notes with each attempt's result, and it doesn't count against the bar. Expect the flaky four.
   - If it fails all three, don't mark it and don't change it. Report it in the notes as the bar's fast-run line not met, with its error line. The engineer decides.
   - If any of the 100 copy-only tests still fails, the sample files didn't land: fix the copy, not the tests.
6. **Counts.** In `$D`, `--collect-only -q` with no `-m` gives 3068 plus the 4 tests of `test_markers.py`. With `-m pushes` it gives 61. With `-m host_only` it gives 270 plus the passing cases of the marked parametrized functions. Report all three numbers.
7. **Verify.** The chain's verify runs the whole skill suite outside agentsh, `test_markers.py` and every marked test included, and all pass. The plan adds no check command and no dependency.

The builder's `notes_for_next_agent` must hold:

- the script's counts, and per file the functions marked, split into `named` and `downstream`;
- each parametrized function where fewer cases failed than exist, with both counts;
- the fast run's last line, each flaky test with its reruns, and that no command was refused;
- which `$B` the sample files came from;
- that `build_brief`, `review_brief` and their test wait on the engineer.

The three-rerun limit in step 5 is the planner's reading of "a rerun of that test alone passes". The request doesn't give a number.

## Out of scope

Holes and gaps this change leaves open, for a reviewer to file as old holes:

- `adws/adw_guard_change.py`, `adws/tests/` and anything else under `adws/`. The briefs enforce nothing until the engineer edits them, and they are prompt lines after that.
- Making any marked test pass under agentsh. That is `docs/open.md`'s row on the 270 tests.
- The agentsh policy.
- `tests/conftest.py` and `test_markers.py` are unprotected, as `conftest.py` is at the baseline. A later build could add a hook that skips marked tests when they run. `test_markers.py` compares collections only, so it would not see one. Review stands against it.
- An agent can mark a test it wants out of its own run. Verify still runs it, and nothing checks that a later mark is earned.
- A downstream mark rests on 5fa763b5's three runs, kept in `/tmp` and not in the repo. Once those files are gone, the mark's comment is the only record.
- A marked test gets no run under agentsh, so a builder that changes one learns of a failure only from verify. A parametrized test is marked whole, so its passing cases leave the fast run too.
- The flaky four stay in the fast run and can fail it.
- The build brief's `rsync` copy still can't write the two sample files under agentsh.
- Verify's `test` block has a 600-second limit against a suite of about 9.5 minutes. `test_markers.py` adds three collections of about a second each.
- The skill's builder prompts for project chains, and the hub's chain tests under `adws/tests`.

## For the engineer

These wait on the engineer's own session, after this run merges. The build leaves them alone.

- **The briefs.** `build_brief` and `review_brief` in `adws/adw_guard_change.py` get the `-m` expression and the fetch line, `git -C <bare> fetch -q <work> main:main`, and `adws/tests/test_guard_change.py` gets their test. 5fa763b5's spec has text to start from.
- **The `rsync` copy.** `COPY_TO_D` in `adws/adw_guard_change.py` can't write `.env.sample` or `.claude/skills/sssf/templates/env.sample` under agentsh, since its temp names start with `.env`. Until it is fixed, each builder copies the two files in by hand.
- **`docs/open.md`.** Replace the P3 row on tests that push. The row on the 270 tests says "Once 5fa763b5's `host_only` marker lands": it lands with this run, and it covers the downstream tests too.
- **`.claude/agents/bypass-reviewer.md`**, step 5, tells the subagent to run the whole suite. If it ever runs under agentsh, it needs the `-m` expression and the fetch line.
- **Protection.** Add `test_markers.py` to `protected_files` and to `guard_builder`'s `writes` if a feature build shouldn't be able to drop its checks.

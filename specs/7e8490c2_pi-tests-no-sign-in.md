# Make six pi tests pass with no sign-in

Six tests in the skill's suite reach the real `pi` on the machine and pass only when it is signed in to ChatGPT. They pass on the host and fail in an OpenShell VM, so every hub run in a VM stops at verify. This change gives each of the six a stand-in `pi` from one shared fixture. No module changes, and no file under `templates/` changes.

## Baseline

`main` at `5e8020a` ("Brief: make six pi tests pass with no sign-in"). Every finding is judged against it.

Red on the baseline, run by the planner from the repo root on 2026-11-26, in the planner's shell (under agentsh):

```
PI_PATH=/bin/false uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest -q -p no:cacheprovider "tests/test_rosters.py::test_install_stamps_a_chatgpt_roster_that_validates" "tests/test_trial.py::test_a_roster_that_is_not_signed_in_refuses_the_whole_trial" "tests/test_trial.py::test_too_few_worktree_slots_refuses_naming_the_runs_that_hold_them" "tests/test_trial.py::test_two_attempts_on_one_plan_are_warned_about" "tests/test_trial.py::test_a_vm_trial_refuses_a_pi_roster_off_chatgpt_before_anything_starts" "tests/test_rules.py::test_a_pi_agent_in_a_vm_gets_the_refuse_extension_without_refuse_rules"
```

Result: `6 failed in 1.67s`, exit status 1. `agent_pi.PI_PATH` is read from the environment at import, so `/bin/false` makes every call to a pi other than a test's own exit 1.

Feasibility, also run by the planner: the same command with `PI_PATH` pointed at a throwaway shell script (the stand-in below, in a `mktemp -d` folder, since removed) gave `6 passed in 1.40s`, exit status 0, with no test file changed. So a stand-in that answers `--list-models` and `auth print-bearer-token` is all the six need. That run was under agentsh, so all six are expected to lose `host_only`.

## What the planner found that differs from the request

The builder should follow this section where it and the request disagree; each point was read from the baseline.

1. **`trial.pi_problems` is already stood in for, in every `test_trial.py` test.** The `repo` fixture ends with `monkeypatch.setattr(trial, "pi_problems", lambda made_by: [])` (`.claude/skills/sssf/tests/test_trial.py`, line 67). So the three tests the request says rely on the real pi being signed in (`pi auth check` answering `ready`/`oauth`, and `SSSF_PI_AUTH=subscription`) never reach `pi auth check` and never read `SSSF_PI_AUTH`. Their only call to the real pi is `agents.validate` → `agent_pi._pi_catalog()` → `pi --list-models`. The plan leaves the fixture's stand-in as it is: taking it out would change what four tests run, which the bar forbids. So the stand-in `pi` has **no** `auth check` answer and the fixture sets **no** `SSSF_PI_AUTH`: nothing in the six would use either.
2. **Claude's login is already stood in for, in every test.** `tests/conftest.py`'s autouse `claude_logged_in` fixture sets `agent_cc._logged_in = True`, and `agent_cc.check_login` returns at once when it is set. No test of the six runs a real `claude auth status`. The plan adds nothing for it.
3. **One test needs a third model row.** `test_a_vm_trial_refuses_a_pi_roster_off_chatgpt_before_anything_starts` writes a `gemini` roster whose model is `google/gemini-3.6-flash` and ends with `trial.check(repo, ["main", "gemini"], None)`, which must pass `agents.validate`. So the stand-in's `--list-models` lists `google gemini-3.6-flash` beside the two `openai` rows the request names.

## The change

### 1. `.claude/skills/sssf/tests/conftest.py`: one shared fixture

The helper is shared by three files, so it lives here. Add a module constant and a fixture (not autouse) after `no_dotenv_keys`, before `pytest_configure`:

```python
# A `pi` for tests that must not depend on the machine's pi or its sign-in. It answers the two
# calls those tests reach and exits 2 on anything else, so a new call shows up as a failure.
STAND_IN_PI = """#!/bin/sh
case "$1 $2" in
  "--list-models "*)
    echo "provider  model             context  max-out  thinking  images"
    echo "openai    gpt-6.1-sol       400K     128K     yes       yes"
    echo "openai    gpt-6.1-terra     400K     128K     yes       yes"
    echo "google    gemini-3.6-flash  1M       64K      yes       yes" ;;
  "auth print-bearer-token") echo "eyJ-stand-in-not-a-real-token" ;;
  *) exit 2 ;;
esac
"""


@pytest.fixture
def stand_in_pi(tmp_path_factory, monkeypatch):
    """Point agent_pi.PI_PATH at a script that lists the ChatGPT roster's models and
    prints a made-up bearer token, so a test passes with pi signed out or missing, as in
    an OpenShell VM. The calls through agents.validate and agent_pi.vm_token still run.
    _pi_catalog caches its rows for the process, so the cache is cleared before and after."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))
    from adw_modules import agent_pi
    script = tmp_path_factory.mktemp("pi") / "pi"
    script.write_text(STAND_IN_PI)
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.delenv(agent_pi.CONTAINER_SOCKET_VAR, raising=False)
    agent_pi._pi_catalog.cache_clear()
    yield script
    agent_pi._pi_catalog.cache_clear()
```

Notes for the builder:

- The script goes in `tmp_path_factory.mktemp("pi")`, not `tmp_path`: `test_rosters.py`'s `repo` is `tmp_path` itself and `install.py` stamps into it, so a `pi` file there would sit in the installed repo.
- The token must not start with `sk-` (`vm_token` refuses one as an API key). `eyJ-…` matches the made-up token `test_trial.py`'s `container_broker` already uses. It is not a credential.
- The context column must parse with `agent_pi._count` (`400K`, `1M` do; the feasibility run used exactly these rows). The first line is a header: `_pi_catalog` skips line one.
- `SSSF_PI_TOKEN_SOCKET` is unset as `test_agent_pi.py::test_the_host_call_has_discovery_off_and_the_operators_environment` does, so `_pi_catalog` doesn't ask a container broker.
- Follow the file's existing style: the `sys.path.insert` and the import inside the fixture are how `claude_logged_in` and `no_dotenv_keys` do it.
- Add one sentence on the fixture to the module docstring at the top of `conftest.py`.

### 2. `.claude/skills/sssf/tests/test_rosters.py`

`test_install_stamps_a_chatgpt_roster_that_validates`: take `stand_in_pi` as a second argument (`def …(repo, stand_in_pi):`). Remove the `# host_only, downstream: …` comment line and the `@pytest.mark.host_only` line above it. The body and every assertion stay as they are.

### 3. `.claude/skills/sssf/tests/test_trial.py`

For each of these four, add `stand_in_pi` to the argument list and remove its `# host_only, downstream: …` comment and `@pytest.mark.host_only` line. Bodies and assertions stay as they are.

- `test_a_roster_that_is_not_signed_in_refuses_the_whole_trial(repo, monkeypatch, stand_in_pi)`
- `test_too_few_worktree_slots_refuses_naming_the_runs_that_hold_them(repo, stand_in_pi)`
- `test_two_attempts_on_one_plan_are_warned_about(repo, stand_in_pi)`
- `test_a_vm_trial_refuses_a_pi_roster_off_chatgpt_before_anything_starts(repo, no_sdk_needed, stand_in_pi)`

Leave `test_in_the_container_the_broker_answers_for_openai` and its `host_only` mark alone: it is not one of the six. Leave the `repo` fixture alone.

### 4. `.claude/skills/sssf/tests/test_rules.py` (in `protected_files`; `guard_builder`'s `writes` names it)

`test_a_pi_agent_in_a_vm_gets_the_refuse_extension_without_refuse_rules`: change the signature to take `stand_in_pi`, and remove its `# host_only, downstream: …` comment and `@pytest.mark.host_only` line. The body and the assertion `installed == [agent_pi.REFUSE_EXTENSION]` stay. `agent_pi._in_vm` → `vm_token()` → `_host_token("openai")` then runs the stand-in's `auth print-bearer-token`.

Leave the two other `host_only` marks near it (lines 546 and 762 on the baseline) alone.

### 5. The markers

All six passed under agentsh in the planner's feasibility run, so the steps above drop all six marks. The builder confirms by running the three files (see Verification). If one of the six still fails under agentsh after the change, put its `@pytest.mark.host_only` back with a comment that gives the new reason in the file's existing form (`# host_only, downstream: under agentsh it fails with: …` or `# host_only, the error names the sandbox: …`). In the build's notes, list each of the six and whether its mark was dropped or kept.

### 6. Docs: `docs/open.md`

Rewrite the P1 row that starts "No hub change can pass an SSSF run in an OpenShell VM". It stays open, stays P1, and keeps its link. It must say:

- The six tests (`test_rosters.py`'s ChatGPT roster test, four in `test_trial.py`, `test_rules.py`'s pi-in-a-VM test) no longer need a pi sign-in: since guard run 7e8490c2 (2026-11-26) each uses a stand-in `pi` from `tests/conftest.py`'s `stand_in_pi` fixture and has lost its `host_only` mark (or name any that kept it).
- Keep the record of run 7361d325 (3977 passed, 61 skipped, those 6 failed) and of the earlier fixed causes.
- What is left: the plan-check cause from run 12ec0782 (a plan's check made its scratch folder with `mktemp -d` under `/var/tmp`, and the VM's policy refuses listing `/var`), with its fix still "keep plan checks' scratch folders inside the repo's tree". Drop the sentence offering a stand-in `pi` or `host_only` as a fix, since that part is done.
- That a throwaway-VM run of the six is the engineer's proof and has not been run yet.
- Set the row's date column to 2026-11-26.

Use plain words and no em dashes, as the rest of the file does.

Docs checked and left alone: `docs/sssf.md` (it says what `host_only` means, which is unchanged), `README.md`, the skill's `references/` and `SKILL.md` (none names these tests). The repo keeps no CHANGELOG. No write-up in `app_docs/` names these tests, so the documenter has nothing older to note.

## Threat model

This change touches no guard code: only test files and one doc row. `test_rules.py` is in `protected_files` because it tests the rules guard, which is why the change goes through the guard chain.

What an agent could do with this change: nothing new. The fixture changes `agent_pi.PI_PATH` inside a pytest process, for one test at a time, through `monkeypatch`, which puts the value back. The stand-in script is written to a pytest temp folder and is run only by the test that asked for the fixture. No run of SSSF itself reads `tests/conftest.py`.

What this can't stop, at its level: an agent runs as the engineer's own user, so an agent that may write under `.claude/skills/sssf/tests/` could already weaken any test there, with or without this fixture. The chain's reviewers and the `protected_files` list are what cover that, not this change. A stand-in `pi` also means these six no longer notice if the real pi's `--list-models` table or `auth print-bearer-token` output changes shape; they never checked that on purpose, and they only passed on a signed-in machine.

## Bar

Against the baseline `5e8020a`:

- **Red on `main`.** The command under Baseline fails all six there (recorded above: 6 failed, exit 1).
- **Green after.** The same command, with `PI_PATH=/bin/false`, passes all six on the branch. A pi signed out or missing changes nothing.
- **What each test checks is unchanged.** Each of the six has the same body and the same assertions as on the baseline. The only edits to a test are its argument list and the removed mark and comment.
- **Nothing else changes.** Every existing test passes on the host. No file under `.claude/skills/sssf/templates/` changes. No module changes (`agent_pi.py`, `trial.py`, `agents.py` or any other).
- **The usual guard bar holds trivially:** no agent file kept, no agent code run, no engineer file deleted or overwritten, where the baseline does otherwise. The change adds no module-level state and no `global` to a guard module.
- **Never** read or print `~/.pi/agent/auth.json` or a token, and never run a real pi against `~/.pi/agent`.

## Out of scope

- The plan-check cause in the same `docs/open.md` row (run 12ec0782, `mktemp -d` under `/var/tmp` in the VM). It goes in a run of its own.
- The real pi run in a VM (`docs/open.md`'s row on run 3087a922).
- Any change to `agent_pi.py`, `trial.py`, `agents.py` or another module.
- `test_trial.py::test_in_the_container_the_broker_answers_for_openai` and the other `host_only` tests: they keep their marks.
- The `repo` fixture's stand-in for `trial.pi_problems`, and conftest's `claude_logged_in`: both stay as they are.
- Other tests in the suite that may reach a real pi and pass either way (a signed-out pi still answers them). Not looked for here.
- The proof in a throwaway OpenShell VM: the engineer's, after the merge.

## Verification

The builder runs under agentsh. Run only the three files, from `.claude/skills/sssf`, never the whole suite (verify runs it on the host). If collecting a file fails because agentsh denies reading the main checkout's `.env`, don't work around it (no `.env` copy, no patched loader): say so in the build's notes and leave the judgement to verify. Judge every command by its exit status.

1. **The six, with no pi.** From the repo root, the command under Baseline. It must exit 0 with `6 passed`. This is also the plan's check, which SSSF runs after every build.
2. **The three files under agentsh**, from the repo root:

   ```
   PI_PATH=/bin/false uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_rosters.py tests/test_trial.py tests/test_rules.py -q -p no:cacheprovider -m 'not pushes and not host_only'
   ```

   Then the same without `PI_PATH=/bin/false`. Both must show the six among the passed tests. A failure in a test other than the six under `PI_PATH=/bin/false` is not this change's to fix: check that it also fails on the baseline, and name it in the notes.
3. **Assertions unchanged.** `git diff main -- .claude/skills/sssf/tests/test_rosters.py .claude/skills/sssf/tests/test_trial.py .claude/skills/sssf/tests/test_rules.py` shows, for each of the six, only the removed comment, the removed mark and the changed `def` line.
4. **Nothing under `templates/`.** `git diff --stat main -- .claude/skills/sssf/templates` prints nothing.
5. **The doc row.** `docs/open.md` still has exactly one row starting "No hub change can pass an SSSF run in an OpenShell VM", and it names run 12ec0782 as what is left.

The build's notes say: which of the six lost `host_only` and which kept it, the result of each command above, and anything agentsh denied.

## For the engineer

Nothing in this plan needs a protected path outside `guard_builder`'s `writes`.

After the merge, not part of the run: run the six in a throwaway OpenShell VM, with no pi sign-in there, and see them pass. Then update the `docs/open.md` row with that run's id.

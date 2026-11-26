# Make six pi tests pass with no sign-in

Run it with `--worktree` and `--base main`, on the hub's own config. The change is to three test files under `.claude/skills/sssf/tests`: `test_rosters.py`, `test_trial.py` and `test_rules.py`. `test_rules.py` is in `protected_files`, which only `guard_builder` may write, so the change goes through the guard chain. No file under `templates/` changes.

This is the first cause in `docs/open.md`'s P1 row that starts "No hub change can pass an SSSF run in an OpenShell VM". While these six fail in a VM, every hub run in a VM stops at verify, whatever it changes.

## What broke

Run 7361d325 (2026-11-25) ran the whole skill suite in an OpenShell VM: 3977 passed, 61 skipped, 6 failed. Each of the six reaches the real `pi` on the machine and gets an answer only when pi is signed in to ChatGPT. The host is signed in and the VM isn't, so they pass on the host and fail in the VM. They failed under agentsh for the same reason, which is why they carry `host_only`. None is a bug in the code under test. The log is `adws/adw_data/sessions/7361d325/context_handoff/quality/05_test/command.log`.

1. **`test_rosters.py::test_install_stamps_a_chatgpt_roster_that_validates`.** `agents.validate` resolves each agent's model through `agent_pi._pi_catalog()`, which runs `pi --list-models`. A signed-out pi lists no `openai/` models, so validation fails with `model pattern 'openai/gpt-6.1-sol' not found in pi --list-models`.
2. **Four in `test_trial.py`:** `test_a_roster_that_is_not_signed_in_refuses_the_whole_trial`, `test_too_few_worktree_slots_refuses_naming_the_runs_that_hold_them`, `test_two_attempts_on_one_plan_are_warned_about` and `test_a_vm_trial_refuses_a_pi_roster_off_chatgpt_before_anything_starts`. `trial.check` runs `agents.validate` on the `chatgpt` roster first, which fails as in (1), so each test gets "config validation failed" in place of the error or warning it asserts. The first test already stands in for `trial.pi_problems`, but validation fails before it runs. The other three also rely on `pi_problems` finding the real pi signed in (`pi auth check --json` answering `ready` with `authType: oauth`) and `SSSF_PI_AUTH=subscription` in the environment.
3. **`test_rules.py::test_a_pi_agent_in_a_vm_gets_the_refuse_extension_without_refuse_rules`.** `agent_pi._in_vm` calls `vm_token()`, which runs `pi auth print-bearer-token --provider openai`. Signed out, that exits 1 and `vm_token` raises `SandboxFailed: pi has no ChatGPT sign-in`.

## The change

1. **A stand-in pi.** Each of the six gets a `pi` that answers what the test needs and doesn't depend on any sign-in on the machine. Other tests already do it: `test_agent_pi.py` points `agent_pi.PI_PATH` at a script in `tmp_path` and clears `agent_pi._pi_catalog`'s cache before and after (`test_the_host_call_has_discovery_off_and_the_operators_environment`). Prefer that over replacing `_pi_catalog` or `vm_token` outright, so the calls through `agents.validate`, `trial.pi_problems` and `vm_token` still run. The stand-in answers:
   - `--list-models` with the ChatGPT roster's models (`openai gpt-6.1-sol` and `openai gpt-6.1-terra`, in pi's table format).
   - `auth check --provider openai --json` with `{"status": "ready", "authType": "oauth"}`, where a test needs a signed-in pi.
   - `auth print-bearer-token` with a made-up token that doesn't start with `sk-`.

   A test that sets up a stand-in also sets `SSSF_PI_AUTH=subscription` with `monkeypatch` where it needs it, rather than reading it from the machine. Put a helper shared by more than one file in `tests/conftest.py`, or keep one per file if that reads better; the spec says which.
2. **Claude's login.** `trial.check` also calls `agent_cc.check_login()` for a Claude Code roster. It passed in the VM, which has a token, but these tests shouldn't depend on it either. Where a test needs `main` ready, stand in for `check_login` as `test_a_roster_that_is_not_signed_in_refuses_the_whole_trial` already does for the logged-out case.
3. **The markers.** Drop `host_only` and its comment from each of the six that passes under agentsh after the change. Keep it on any that still fails there, with its comment updated to say why. Say which in the build's notes. `docs/sssf.md` says what the marker means.
4. **Docs.** In `docs/open.md`'s row that starts "No hub change can pass an SSSF run in an OpenShell VM", say the six tests no longer need a sign-in, and leave the plan-check cause (run 12ec0782) as what's left. The row stays open.

## The bar

- **Red on `main`.** With `PI_PATH=/bin/false` in the environment, so any call that reaches a pi other than the test's own fails, the six fail on `main`. Record the command and its result in the spec.
- **Green after.** The same command passes all six on the branch. A pi signed out or missing changes nothing.
- **What each test checks is unchanged.** Each test asserts the same error, warning or result as on `main`. A stand-in that made a test pass by weakening its assertion fails the bar.
- **Nothing else changes.** Every existing test passes on the host, and no file under `templates/` changes.

## After the merge, the engineer's proof

Not part of the run: run the six in a throwaway OpenShell VM and see them pass, with no pi sign-in there.

## Running the tests

The planner and the builder run under agentsh. Run only the three files, from `.claude/skills/sssf`, and leave the whole suite to verify, which runs on the host. A whole-suite run under agentsh makes the after-call event read take many minutes (`docs/open.md`). If collecting a file fails because agentsh denies reading the main checkout's `.env`, don't work around it (no `.env` copy, no patched loader). Say so in the build's notes, and verify judges the tests.

Never run a real pi against `~/.pi/agent`, and never read or print `~/.pi/agent/auth.json` or a token. A check that pi is signed out uses `PI_CODING_AGENT_DIR` pointed at an empty folder in `tmp_path`, or `PI_PATH=/bin/false`.

## Out of scope

- The plan-check cause in the same row: in run 12ec0782 a plan's check made its scratch folder with `mktemp -d`, which lands under `/var/tmp` in the VM, and pytest then failed listing `/var`. That fix goes in the planner's prompt or the VM's policy, in a run of its own.
- The real pi run in a VM (`docs/open.md`'s row on it, run 3087a922).
- Any change to `agent_pi.py`, `trial.py`, `agents.py` or another module.

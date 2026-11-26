# Say where SSSF runs the plan's checks

Run it with `--worktree --sandbox openshell`, on the hub's own config. The change is to `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py` and its tests, `tests/test_builder_brief.py` and `tests/test_review_brief.py` under `.claude/skills/sssf`. None of them is guard code or in `protected_files`, so `simple-sdlc` can make it.

This run is also the engineer's proof for `docs/open.md`'s P1 row that starts "A hub run in an OpenShell VM stopped at verify". Guard runs 7e8490c2 (46bee4c) and 2ebbe80a (c41918a) fixed both causes the row names. A run in a VM that passes verify closes the row.

## What's wrong

Two prompt slots tell agents that SSSF runs the checks outside their sandbox:
- `verify_brief` (`plan_checks.py`, about line 96), the reviewer's `host_verify` slot: "SSSF ran each command below itself, outside any agent's sandbox, and each one exited 0."
- `builder_brief` (about line 153), the builder's `verify_next` slot: "After you report, SSSF runs each command below itself, outside your sandbox."

That's true when the run uses `sandbox: none`. There the agents run under agentsh on the host, and verify runs on the host without agentsh. Under a VM sandbox (`openshell`, `vercel`, `e2b`) it isn't true: `run_check` runs each check with `sandbox.current().run`, inside the same VM the builder works in. A reviewer told that the checks ran outside any agent's reach may trust a green check more than it should. `docs/open.md`'s P2 row that starts "In a VM, a builder can make a plan check pass that should fail" has the case, a `conftest.py` the builder plants in a parent of `TMPDIR`.

## The change

1. **Each slot says where the checks ran.** Under the local sandbox (`sandbox.current()` is `sandbox.LOCAL`) the wording stays as it is. Under any other sandbox, the slot says SSSF ran (or runs) each command itself, in the run's VM, the one the agents work in. Say it plainly and briefly. The spec picks how the slot learns which sandbox is in use, `sandbox.current()` at call time or a value passed in, and says why. Keep the rest of both slots' text byte for byte.
2. **Nothing else in the prompts changes.** The builder's paragraph on a test the sandbox denied stays as it is. So does every prompt file under `templates/adws/prompts/`.
3. **`docs/open.md`.** In the P2 row named above, say the wording is fixed by this run and the planted-`conftest.py` half stays open, for a guard run. Leave the P1 row alone; the engineer closes it after reading this run's result.

## The bar

- **Red on `main`.** A test in `test_builder_brief.py` and one in `test_review_brief.py` set a non-local sandbox with `sandbox.use(...)` (restoring `sandbox.LOCAL` after) and assert that the slot doesn't say "outside" and does say the checks run in the VM. On `main` both fail.
- **Green after.** Both pass on the branch.
- **The local wording is pinned.** Each slot under `sandbox.LOCAL` gives the same text as on `main`, checked by a test that would fail if a word changed.
- **Every existing test passes.** Verify runs the whole skill suite and the hub suite in the VM. That is the point of the run.

## Running the tests

The planner and the builder run in the VM. Run only the two test files from `.claude/skills/sssf` while working, and leave the whole suite to verify.

## Out of scope

- Fixing the planted `conftest.py`: running checks in a fresh VM, on the host, or with conftest loading cut off. That's guard work, for the guard chain.
- `plan_checks.py`'s other behaviour: which checks run, their timeouts, and how results are recorded.

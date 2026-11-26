# Say where SSSF runs the plan's checks

Session `83c9ca03`. Spec: `specs/83c9ca03_plan-check-wording.md`.

## What changed and why

Two prompt slots written by `plan_checks.py` told agents that SSSF runs the plan's checks outside their sandbox:

- `verify_brief`, the reviewer's `host_verify` slot: "SSSF ran each command below itself, outside any agent's sandbox, and each one exited 0."
- `builder_brief`, the builder's `verify_next` slot: "After you report, SSSF runs each command below itself, outside your sandbox."

That is true under `sandbox: none`. Under a VM sandbox (`openshell`, `vercel`, `e2b`) it is not: `_run` runs each check with `sandbox.current().run`, inside the VM the builder works in. A reviewer told the checks ran outside any agent's reach could trust a green check more than it should.

Now each slot says where the checks ran:

- **Local sandbox** (`sandbox.current() is sandbox.LOCAL`): the text is unchanged, byte for byte.
- **Any other sandbox**: the slot says SSSF runs or ran each command itself, in the run's VM, the one the agents work in.

The slot reads `sandbox.current()` at call time, the same source `_run` uses, so the sentence and the place where the check runs cannot disagree. No caller or signature changed. Anything that is not `LOCAL` gets the VM wording, so a future sandbox provider defaults to the safe claim.

## Files

- `.claude/skills/sssf/templates/adws/adw_modules/plan_checks.py`: four constants (`RAN_LOCAL`, `RAN_IN_VM`, `RUNS_LOCAL`, `RUNS_IN_VM`), a helper `_in_vm()`, and one `where` variable in each of `verify_brief` and `builder_brief`. Both docstrings are updated. Nothing else in the module changed.
- `.claude/skills/sssf/tests/test_builder_brief.py`: two tests. One pins the local `verify_next` text by SHA-256. One sets a non-local sandbox and checks that the slot says "in the run's VM, the one you work in" and that the text differs from the local text only in that phrase.
- `.claude/skills/sssf/tests/test_review_brief.py`: two tests for the reviewer slot, with the same pin-and-VM pattern. The pinned text is `LOCAL_VERIFY`.
- `docs/open.md`: the P2 row "In a VM, a builder can make a plan check pass that should fail" now says the wording half is fixed by run 83c9ca03. The planted-`conftest.py` half stays open for a guard run. The P1 row is unchanged.
- `specs/83c9ca03_plan-check-wording.md`: the design, including the choice of reading `sandbox.current()` over a new parameter.

Not changed: no prompt file under `templates/adws/prompts/` or `templates/prompt_engineering/`, and the hub's installed copy `adws/adw_modules/plan_checks.py`, which is protected.

## How to verify

Run from `.claude/skills/sssf`:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_builder_brief.py tests/test_review_brief.py -q -p no:cacheprovider
```

Expected: 50 passed on the branch. On `main`, the two VM tests fail and the two local-pin tests pass. The full skill suite, `adws/tests`, and the dashboard checks are left to verify.

## Still to do after this change

- **Hub copy.** `adws/adw_modules/plan_checks.py` still has the old wording until the hub's install is updated from this commit, as `8c7ffaa` did for `c41918a`. Until then the hub's own runs, including the run that proves the P1 row, carry the old "outside your sandbox" text while running in the VM.
- **P1 row.** `docs/open.md`'s "A hub run in an OpenShell VM stopped at verify" row is left for the engineer to close after this run passes verify in the VM.
- **P2 row.** The planted `conftest.py` remains: the checks still run in the VM the builder wrote to. That fix is guard work and is out of scope here.

## Notes

- This write-up was produced from the code in commits `4da81f1` and `23de11a` and the working tree. The envelope's diff covered only `docs/open.md` and the spec, because it excluded `.claude/skills/sssf/`. I read the skill files directly.
- I did not run the tests while documenting. The description of the test results is from the spec's planner run on `main`, not a run of this write-up.

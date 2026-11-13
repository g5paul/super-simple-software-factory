# Keep the hub's feature agents out of the guard code SSSF ships

This is open.md's P1 row "The builder may edit the guard code SSSF ships". Run it with `--worktree`, `--base main`, and `--config adws/adw_sssf_config/sssf.config.guard-p1.yaml`, a copy of the hub's config made for this run only. That copy is the hub config with one change, described under "This run's own unlock".

## The problem

In a project, the guard code is under `adws/`, a factory path no agent may write unless its own `writes` names it. The hub is different. Its product is the skill, so the hub's `builder` and `ticket_builder` name `.claude/skills/sssf/` in their `writes`, and that unlocks the whole skill. It includes the guard code SSSF ships to every project:

- the modules CLAUDE.md lists under `.claude/skills/sssf/templates/adws/adw_modules/`: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `secret_files.py`, `permissions.py`, `agent_cc.py`, `agent_pi.py`, `gates.py` and `sandbox_openshell.py`;
- the tests that pin their behaviour under `.claude/skills/sssf/tests/`.

So a feature run's builder, or its fix round, can change guard code outside the guard-change chain, and the change ships with the next update. None has yet: run 0cb2bd66's two fix rounds changed nothing there. It is a hole all the same, and the hub's CLAUDE.md rule that guard code changes only through the chain rests on the operator noticing.

Adding the paths to the hub's `protected_files` alone isn't enough. The guard-change chain uses the same `builder` and the same config (`adws/adw_guard_change.py`, `REQUIRED_AGENTS`), so it would be refused too.

## What should change

1. **Protect the shipped guard code in the hub.** The hub's `adws/adw_sssf_config/sssf.config.yaml` lists the guard modules above in `defaults.protected_files`, with the tests that pin them. Find any other shipped file that decides what an agent may do and belongs on the list, for example `agentsh_guard.py`, which renders the agentsh policy, and the prompt lines that tell an agent what it may write. Then say in the spec which you added and why. Leave out what feature work changes every day, and say so.
2. **A guard-only builder.** The hub config gains an agent, `guard_builder` or a better name, that is the hub `builder` with those paths named in its `writes`. Under `permissions._names`, an entry unlocks a protected path when it equals the entry or sits inside a protected directory. So `.claude/skills/sssf/` in the feature builder's `writes` doesn't unlock a protected file inside it, and the guard builder's entries must name each protected entry the way `_names` reads it.
3. **The chain uses it.** `adws/adw_guard_change.py` runs its build and fix phases as the guard-only builder, and `REQUIRED_AGENTS` names it. No other chain gets it.
4. **Docs.** `docs/sssf.md` ("Guard changes", and the table of hub files near its top) names the new agent and the protected paths. The P1 row in `docs/open.md` moves to fixed, and says the engineer merges it.

The fix should need no change to `permissions.py` or any other shipped module. If it does, say why in the spec. A new mechanism stops the run.

## What three earlier runs' spec reviews settled

Runs 97746fcd, 9796fecf and 35b151a5 each wrote a spec for this brief, and the guard spec reviewer blocked something different in each. Take these as decided, and spend the spec on the rest.

- **The list.** Protect the nine modules, `scripts/agentsh_install.py` (it renders the agentsh policy every project installs), the tests under `.claude/skills/sssf/tests/` that pin those files, and the fixtures those tests read. Name each fixture file a listed test reads, such as `tests/fixtures/cc_turn1.jsonl` and `tests/fixtures/cc_turn2.jsonl`, which `test_agent_cc.py` reads, and check each listed test for others. A fixture folder read only by listed tests may be one entry.
- **Left out, with these reasons, and named in Out of scope's line on weakening a listed module from an unlisted file.** `data_types.py`, `repo_settings.py` and `templates/repo.yaml` decide what an agent may do but change with feature work every week. `sandbox.py` is where an agent runs, though the after-call check runs its git through `sandbox.current().run`; say so.
- **No completeness test over a folder.** A test that every file under `tests/fixtures/`, or any folder, is on the list turns every hub feature run that adds a file there red, and that run's builder can't fix it, since `adws/tests/` is protected. A new guard module, test or fixture stays unprotected until someone adds it to the config. Only `GUARD_MODULES` in `adw_guard_change.py`, which stays the nine, is tied to the list by a test.
- **The entries appear twice**, in `defaults.protected_files` and in the guard-only builder's `writes`, because YAML can't share a list and `_names` needs exact entries. One test holds the two equal.
- **The build brief gets one fixed paragraph**, not a new function, parameter or value passed through. The builder's system prompt says never to modify a `protected_files` path, and the guard-only builder must change some, so `build_brief` says, in words like: "This run's builder is `guard_builder`. Its `writes` names every `protected_files` path under `.claude/skills/sssf/`, so those are yours to change when the spec asks. Every other protected path stays off-limits." `build_brief` serves only this chain, so the sentence always holds, and its test checks the sentence is there.
- **The reach, stated exactly.** The guard-only builder reaches what `builder` reaches after this change, plus the listed entries. That sum is what `builder` reaches on `main`, and nothing more.

## The bar

The baseline is `main`.

- On `main`, the hub's `builder`, `ticket_builder` and `documenter` can each change a listed guard file and its test, and the after-call check lets it through. After the change, each of them is refused and rolled back. The same goes for every other hub agent except the guard-only builder.
- The guard-only builder can change every listed path, and the chain's build and fix phases run as it.
- Every refusal at `main` stays a refusal.
- No engineer file is deleted or overwritten. A refused change rolls back as `_roll_back` does today, and a path that was dirty before the call stays as it was.
- No new refusal of a hub feature run's normal work: product code in the skill outside the list, tests outside the list, docs, and the planner's spec.
- No change to any ADW script other than `adws/adw_guard_change.py`.
- No new module-level mutable state.

## Testing

In `adws/tests/`, through `permissions.permitted` and `enforce` with the hub's real config file, as the chain's existing tests load it:

- each listed path, refused for `builder`, `ticket_builder` and `documenter`, and allowed for the guard-only builder. Each test fails on `main` and passes after;
- a builder edit to a skill file off the list, and to a test off the list, still allowed;
- `adw_guard_change.py`'s build and fix phases name the guard-only builder, and the chain's config check requires it.

The tests never start a real agent.

## This run's own unlock

The guard chain's `builder` can't write under `adws/`, and this change lives there. For this run only, `sssf.config.guard-p1.yaml` adds three entries to that builder's `writes`: `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_*.py` and `adws/tests/`. `adws/adw_*.py` is there because `_names` only unlocks the hub's `adws/adw_*.py` entry by that exact pattern. The builder changes `adws/adw_guard_change.py` and no other ADW script.

Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`, never under the launch checkout above it. agentsh refuses writes outside the worktree. Run 97746fcd's builder sent an `Edit` to the launch checkout's `adws/adw_guard_change.py`, got `EACCES`, took it for the unlock failing, reverted its own change and stopped. An `EACCES` on a path outside the worktree means a wrong path, not a missing unlock.

After the run, the engineer's session checks that `git diff main..HEAD --stat -- adws/` touches only `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_guard_change.py` and `adws/tests/`. It runs the `bypass-reviewer` subagent on `main..HEAD` and deletes `sssf.config.guard-p1.yaml`. The file is never committed.

## This run is exposed to the gap it fixes

The run uses `main`'s config, under which its own builder can still edit the shipped guard code. The change belongs in the hub's config, the chain script, its tests and the docs, never in `.claude/skills/sssf/templates/adws/adw_modules/`. The engineer's session checks `git diff main..HEAD --stat -- .claude/skills/sssf/templates/adws/adw_modules/ .claude/skills/sssf/tests/` and expects it empty.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

## Out of scope

- The projects' configs and the starter rosters. In a project, the guard code is already under `adws/` and protected.
- The guard-only builder's own reach. It may write all the guard code by design, and the chain's spec review and bypass review are what limit it.
- The gaps open.md already lists for the after-call check: gitignored files, `git replace`, a background write landing after the check.

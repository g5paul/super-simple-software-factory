# Keep the hub's feature agents out of the guard code SSSF ships

## Summary

The hub's `builder` and `ticket_builder` name `.claude/skills/sssf/` in `writes`, which unlocks the whole skill, the shipped guard code included. This change lists that guard code in the hub's `defaults.protected_files`, adds a `guard_builder` agent whose `writes` names each listed entry, and makes `adws/adw_guard_change.py` plan for, build and fix as `guard_builder`.

It is a config, chain-script, test and docs change. No shipped module changes. `permissions.py` needs no change: `_names` already refuses a broad entry and accepts an equal one. No new mechanism.

Files the builder touches, and no others:

- `adws/adw_sssf_config/sssf.config.yaml`
- `adws/adw_guard_change.py`
- `adws/tests/test_guard_change.py`
- `adws/tests/test_guard_paths.py` (new)
- `docs/sssf.md`
- `docs/open.md`
- `docs/designs/hub-guard-chain.md`

For this run's builder: the first four are under `protected_files`, and this run's config names `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_*.py` and `adws/tests/` in your `writes`, so they are yours to change. `adws/adw_guard_change.py` is the only ADW script you change. Nothing under `.claude/` changes, though your `writes` reaches it. Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`. An `EACCES` on a path outside the worktree means a wrong path, not a missing unlock: fix the path and go on.

This is the second version. The first is `specs/fcbb8a7f_guard-builder-paths.md`. The spec review found one gap in it: once the listed paths are protected, the planner's system prompt sends every change to them to `## For the engineer`, so a later guard run would plan nothing for `guard_builder`. This version gives `plan_brief` one fixed paragraph, with a test and a Bar line. It also gives the three remote sandbox modules a reason for being left out, and names the index-flag limit under Out of scope.

## Threat model

A hub feature agent (`builder`, `ticket_builder`, and their fix rounds) has `bash`, `edit` and `write`, and runs as the engineer's own user. To turn a suite green, or by a plan's mistake, it can edit a shipped guard module, loosen the test that pins it, or rewrite the recorded data that test checks against. The chain commits the change, it is reviewed only as feature work, and it ships to every project on the next update.

The guard here is `permissions.enforce`. It runs after each agent call, compares the tree with the snapshot taken before, rolls back what the agent may not change, and stops the run. It reads only what git shows. It can't stop an agent that hides a change from git (a gitignored file, `git replace`), one that writes after the check, or one that kills the chain first. It protects whole paths, never one line of a file. And it can't stop an agent weakening a listed module from a file off the list.

## Baseline

`main` at `16ae2a5`. Every finding is judged against that commit. There, with the hub's real config, `permissions.permitted` gives:

| Agent | listed guard file, test or fixture | skill file off the list | `docs/` | `specs/` |
|---|---|---|---|---|
| `builder`, `ticket_builder` | allowed | allowed | allowed | refused |
| `documenter` | refused | refused, except `SKILL.md`, `references/`, `cookbooks/` | allowed | refused |
| `planner` | refused | refused | refused | allowed |
| `scout` and the five reviewers | refused | refused | refused | refused |

The request says the `documenter` can change a listed guard file on `main`. It can't: `.claude/` is a fixed factory path, and no documenter entry both matches a guard file and names `.claude/`. So the documenter's tests pin a refusal that already holds. The `builder`, `ticket_builder` and `guard_builder` cases are the ones that fail on `main`.

At the baseline the chain's planner plans guard changes as builder steps, because no listed path is in `protected_files`.

## Bar

Must do:

- Every listed path is refused, and rolled back by `enforce`, for every hub agent except `guard_builder`. For `builder` and `ticket_builder` this is new against the baseline.
- `guard_builder` may change every listed path.
- `adws/adw_guard_change.py` runs every build and fix phase as `guard_builder`, and `REQUIRED_AGENTS` names it in place of `builder`. No other chain names it.
- The chain still plans a change to a listed path as a builder step, as at the baseline. `plan_brief` tells the planner so. Every other protected path stays under `## For the engineer`.

Must never do:

- Allow anything the baseline refuses. The reach, exactly: `guard_builder` reaches what `builder` reaches after this change, plus the listed entries. That sum is what `builder` reaches on `main`, and nothing more.
- Refuse a feature run's normal work: skill code and tests off the list, a new fixture file, docs, the planner's spec, the documenter's docs.
- Delete or overwrite an engineer's file. A refused change rolls back through `_roll_back`, unchanged: it undoes only what the agent introduced, and leaves a path that was changed, untracked or flagged before the call as it is.
- Change any ADW script other than `adws/adw_guard_change.py`, any shipped module, or any file under `.claude/`.
- Add module-level mutable state. The script gains no module-level name: the agent's name is a literal at each use.
- Add a mechanism. If the fix seems to need a change to `permissions.py` or another shipped module, stop and say so in `notes_for_next_agent`.

Intended, and not a false positive: a feature run that needs a listed file, such as `agent_cc.py` or `cc_turn1.jsonl`, is now refused. `CLAUDE.md` already sends that change through the guard-change chain.

## The list

31 entries, all under `.claude/skills/sssf/`.

**The nine modules `CLAUDE.md` names**, in `templates/adws/adw_modules/`: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `secret_files.py`, `permissions.py`, `agent_cc.py`, `agent_pi.py`, `gates.py`, `sandbox_openshell.py`.

**Added, three files that decide what an agent may do:**

- `templates/adws/adw_modules/agentsh_guard.py`: `policy_text` writes the agentsh policy, which decides the files, commands and hosts a local agent reaches, and the module holds the binary's pin.
- `templates/adws/adw_modules/agentsh_sssf.yaml`: the agentsh server config that `agentsh_guard.py` and the install script render.
- `scripts/agentsh_install.py`: writes the policies and server config every project installs, with the hub's code.

**The tests that pin them**, in `tests/`. A test is listed when a listed file is its subject:

- rules, `sssf_refuse.py`, `sssf_rules.ts`: `test_rules.py`, `test_rules_continuation.py`, `test_rules_heredoc.py`, `test_rules_multiline.py`, `test_rules_portable.py`, `test_rules_tab_heredoc.py`
- `test_secret_files.py`
- permissions: `test_git_dir_guard.py`, `test_factory_files.py`, `test_factory_holes.py`, `test_agent_commits.py`
- `test_agent_cc.py`, `test_agent_pi.py`
- `test_plan_gate.py` (gates)
- `test_sandbox_openshell.py`
- `test_agentsh_guard.py` (also loads `scripts/agentsh_install.py` and reads `agentsh_sssf.yaml`)

**The fixtures those tests read.** `tests/fixtures/` holds three things, and each is an entry:

- `tests/fixtures/cc_turn1.jsonl` and `tests/fixtures/cc_turn2.jsonl`, read by `test_agent_cc.py`; `test_agentsh_guard.py` reads the first.
- `tests/fixtures/agentsh-first-real-runs/`, a folder only `test_agentsh_guard.py` reads, so it is one entry.

I searched each of the 16 listed tests for `fixtures` and for file names. No listed test reads another file under `tests/`. `test_rules_portable.py`'s `cases.json` is written to `tmp_path`.

`test_budget.py`, `test_prices.py` and `test_rate_limits.py`, off the list, also read `cc_turn1.jsonl`. They stay writable. A feature that needs a different recorded stream adds a new file beside it, which is allowed, since `tests/fixtures/` itself isn't listed.

**Left out, and why:**

- `templates/adws/adw_modules/data_types.py` (28 commits in 30 days), `templates/adws/adw_modules/repo_settings.py` (13) and `templates/repo.yaml` (21). They decide what an agent may do, but feature work changes them every week, and listing them would send most feature runs through the guard chain.
- `templates/adws/adw_modules/sandbox.py` (11). It is where an agent runs. The after-call check runs its git through `sandbox.current().run`, so a change there can weaken the check.
- `templates/adws/adw_modules/sandbox_remote.py`, `sandbox_e2b.py` and `sandbox_vercel.py`. They are the remote boxes `sandbox.current()` returns under `--sandbox`, so the same `run` goes through them. They are provider plumbing that feature work changes, and the hub's own runs are local, under agentsh.
- `templates/adws/adw_modules/agents.py` (45), which calls `snapshot` and `enforce`, and `session.py` (24): where most features land.
- The prompts under `templates/prompt_engineering/`. A prompt line tells an agent what it may write and enforces nothing, and a path entry can't protect one line.
- The starter rosters `templates/sssf.config*.yaml` and `tests/test_rosters.py`: the request puts them out of scope.
- `tests/conftest.py`: shared setup that feature work adds to.
- Tests whose subject is something else, though they import a listed module: `test_adw_tickets.py`, `test_budget.py`, `test_engineer_recipes.py`, `test_host_python.py`, `test_openshell_image.py`, `test_openshell_setup_refuses.py`, `test_prices.py`, `test_protected_paths.py`, `test_rate_limits.py`, `test_sandbox.py`, `test_sandbox_remote.py`, `test_silence.py`, `test_stops.py`, `test_trial.py`.
- Three paths outside the skill that `test_agentsh_guard.py` reads when they exist and skips without: `docs/designs/agentsh/deny-policy.py`, `docs/designs/agentsh/sssf-net.yaml` and `docs/designs/agentsh/first-real-runs/`. They are design records, they don't ship, and listing them would refuse the documenter inside `docs/`. The listed fixture folder is the shipped copy of the third.

All of these are named again under Out of scope.

`GUARD_MODULES` in `adws/adw_guard_change.py` stays the nine. An existing test ties it to `CLAUDE.md`'s guard line, and the mechanism check stays as it is.

## Changes

### 1. `adws/adw_sssf_config/sssf.config.yaml`

**`defaults.protected_files`.** Append the 31 entries after `docs/adr/`, each written in full from the repo root, for example:

```yaml
    - .claude/skills/sssf/templates/adws/adw_modules/rules.py
    - .claude/skills/sssf/scripts/agentsh_install.py
    - .claude/skills/sssf/tests/test_rules.py
    - .claude/skills/sssf/tests/fixtures/cc_turn1.jsonl
    - .claude/skills/sssf/tests/fixtures/agentsh-first-real-runs/
```

The folder entry keeps its trailing `/`. No globs. The comment above them says: the guard code SSSF ships, the tests that pin it and the fixtures those tests read; only `guard_builder` names them, so only the guard-change chain changes them; a new guard module, test or fixture is unprotected until it is added here and to `guard_builder`.

**Comments.** The comment above `protected_files` and the ones on `builder` and `ticket_builder` say the skill unlock no longer reaches the listed entries: a `writes` entry unlocks a protected entry only when it equals it or sits inside a protected folder, and `.claude/skills/sssf/` does neither. `builder` and `ticket_builder` keep their `writes` as they are.

**New agent `guard_builder`**, after `guard_spec_reviewer`, under the "Hub only" comment. It is `builder` with the list added:

```yaml
  - name: guard_builder
    max_call_cost: 1.50
    color: "#22d3ee"
    purpose: Implement a guard change's spec exactly; report every changed file in the envelope.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/builder/system.md
      user: adws/adw_data/prompt_engineering/builder/user.md
    writes:
      - "**"
      - .claude/skills/sssf/
      # then the 31 entries, in the same order and spelled exactly as in protected_files
    tools:        # the same six as builder, as a block list
```

It reuses the builder's prompts, so nothing under `adws/adw_data/prompt_engineering/` changes. Its comment says only `adws/adw_guard_change.py` uses it, and that the entries appear twice because YAML can't share a list and each must equal its `protected_files` entry.

Why this works in `permissions.permitted` at the baseline: for a listed file, the fixed check passes for both builders through `.claude/skills/sssf/`, which names `.claude/`. The roster check then finds the listed entry in `guards` and wants a `writes` entry that matches the path and names that entry. `"**"` and `.claude/skills/sssf/` match but don't name it, so `builder` is refused. The equal entry in `guard_builder` does both.

### 2. `adws/adw_guard_change.py`

- `REQUIRED_AGENTS = ["planner", "guard_spec_reviewer", "guard_builder", "bypass_reviewer"]`.
- In `build_until_green`, both `owner="builder"` become `owner="guard_builder"`.
- `plan_brief` gains one fixed paragraph, under its "How to plan a guard change" heading, after the paragraph that says what to read. No new function, parameter or value passed in:

  > This chain's builder is `guard_builder`. Its `writes` names every `protected_files` path under `.claude/skills/sssf/`, so plan changes to those paths as builder steps. Every other protected path stays under `## For the engineer`.

  The planner's system prompt says a change to a `protected_files` path goes under `## For the engineer` and out of the builder's steps, tests included. After this change the listed paths are in that list, so without the paragraph a guard run would plan nothing for its builder. `plan_brief` serves only this chain, so the sentence always holds. The spec reviewer is sent the same `planned` text, so it reads the paragraph too.
- `build_brief` gains one fixed paragraph, after its first line, in the same way:

  > This run's builder is `guard_builder`. Its `writes` names every `protected_files` path under `.claude/skills/sssf/`, so those are yours to change when the spec asks. Every other protected path stays off-limits.

  The builder's system prompt says never to modify a `protected_files` path, and `guard_builder` must change some.
- The module docstring's `Phases:` lines say `guard_builder`, and one sentence says why.

Nothing else changes: no new constant, no change to `GUARD_MODULES`, `mechanisms`, `review_brief` or the gates.

### 3. `adws/tests/test_guard_change.py`

- In `chain()`, the scripted replies are keyed `"guard_builder"`, not `"builder"`. The stubbed `validate` records the names it was given, so a test can read them.
- Each `owners(run, "builder")` becomes `owners(run, "guard_builder")`, including the `not owners(run, "builder")` in `test_a_rejected_spec_is_revised_then_the_run_stops`, which otherwise passes for the wrong reason.
- New `test_the_build_and_fix_phases_run_as_the_guard_builder`: round 1 finds a bypass, round 2 is clean. `owners(run, "guard_builder") == ["build", "fix_round_1"]` and `owners(run, "builder") == []`.
- New `test_the_config_check_requires_the_guard_builder`: `gc.REQUIRED_AGENTS` holds `"guard_builder"` and not `"builder"`, the stub received exactly `gc.REQUIRED_AGENTS`, and `agents.validate` on the hub's real config with `gc.REQUIRED_AGENTS` raises nothing.
- New `test_the_plan_brief_plans_the_listed_paths_for_the_guard_builder`: the `plan_brief` paragraph above is in `gc.plan_brief("x", "main", "16ae2a5")`, word for word.
- New `test_the_build_brief_names_the_guard_builder`: the `build_brief` paragraph above is in `gc.build_brief("specs/x.md")`, word for word.

### 4. `adws/tests/test_guard_paths.py` (new)

It loads the hub's real config, `adws/adw_sssf_config/sssf.config.yaml`, with `agents.load_config`, the way `test_guard_change.py` reaches the hub's files, and imports `adw_modules` from `adws/`. The request asks for the hub's own config, since that file is what is under test. It holds its own literal `LISTED`, the 31 paths, so the tests fail on `main` and a dropped config line fails one. No test starts an agent.

Through `permissions.permitted`:

1. Each `LISTED` path exists in the hub, so a rename is caught. No check walks a folder for unlisted files.
2. Each `LISTED` entry is in `cfg.defaults.protected_files`, and each `gc.GUARD_DIR/<name>` for `gc.GUARD_MODULES` is in `LISTED`.
3. The one test that holds the two config lists equal: with `shipped` as the `protected_files` entries that start with `.claude/skills/sssf/`, `guard_builder.writes == builder.writes + shipped`. Its model, tools, prompts and `max_call_cost` equal the builder's.
4. Parametrized over `LISTED` and every agent in the config except `guard_builder`: refused. For the folder entry, use `execve-denial-event.json` inside it and a new file name inside it. Fails on `main` for `builder` and `ticket_builder`.
5. Parametrized over `LISTED`: allowed for `guard_builder`. Fails on `main`, where the agent doesn't exist.
6. Still allowed: for `builder` and `ticket_builder`, the skill's `templates/adws/adw_modules/worktree.py`, `templates/adws/adw_modules/agents.py`, `tests/test_worktree.py`, `tests/test_budget.py`, `tests/conftest.py`, a new `tests/fixtures/cc_turn3.jsonl` and `SKILL.md`, then `docs/sssf.md` and `README.md`; for `planner`, `specs/x.md`; for `documenter`, `docs/sssf.md` and `.claude/skills/sssf/references/config.md`.
7. Still refused, for `guard_builder` and for `builder`: `adws/adw_guard_change.py`, `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_modules/permissions.py`, `adws/tests/test_guard_paths.py`, `.claude/agents/bypass-reviewer.md`, `.claude/settings.json`, `CLAUDE.md`, `specs/x.md`, `justfile`, `.githooks/pre-push`.

Through `permissions.enforce`, in a temp git repo that commits one listed module, one listed test, `cc_turn1.jsonl`, one file in `agentsh-first-real-runs/` and the off-list `worktree.py`, with the real `cfg` and a snapshot taken before the edits:

8. Parametrized over `builder`, `ticket_builder` and `documenter`: edit the four listed files and add a new file in the listed folder. `enforce` raises `PermissionBreach`, the four files hold their committed content again, and the new file is gone. For the two builders, an edit to `worktree.py` in the same call stays in the tree and is named in the message.
9. `guard_builder`, same edits: `enforce` returns every changed path and raises nothing.
10. `builder` editing only `worktree.py` and an off-list test: `enforce` returns both.
11. A listed module is edited before the snapshot, then `builder` edits it again: `enforce` raises, and the file still holds the second edit. The rollback never checks out over a path that was dirty before the call.

### 5. Docs

- `docs/sssf.md`, the table under "What's here": the `adws/adw_guard_change.py` row says it builds as `guard_builder`. Add a row for `adws/adw_sssf_config/sssf.config.yaml`: the hub's roster, whose hub-only parts are the three guard-chain agents and the shipped guard paths in `protected_files`.
- `docs/sssf.md`, "Guard changes": step 3 names `guard_builder`. Add a paragraph: the hub's `protected_files` list the shipped guard code, name the twelve files, and say the tests and fixtures are listed in the config; every other agent is refused and rolled back; the chain's plan and build briefs tell its planner and builder that those paths are `guard_builder`'s to change; a new guard module, test or fixture is unprotected until the engineer adds it to both lists in the config; say in one sentence what is left out and why. In the last paragraph, "its two roster agents" becomes three, and says `guard_builder` uses the builder's prompts.
- `docs/designs/hub-guard-chain.md`: the phase lines, the builder bullet and the loop line name `guard_builder`, with one sentence on why.
- `docs/open.md`: rewrite the P1 row "The builder may edit the guard code SSSF ships" as fixed. Say what the fix is, that run `fcbb8a7f` built it on branch `sssf/fcbb8a7f`, and that the engineer merges it after the diff checks and a `bypass-reviewer` pass. Status is `Needs decision`, since it waits on the engineer. Name what the fix leaves open, from Out of scope.
- `README.md`, `docs/concepts.md` and `docs/guide/customize.md` mention the chain. Change a line only if it says which agent builds. The repo keeps no CHANGELOG.
- The alt text of `images/12_guard_chain.svg` in `docs/sssf.md` says "builder" as a role, and stays.

## Verification

Run the suite from a copy outside the repo, with the build brief's copy command. Run 35b151a5's planner found two things at the baseline under agentsh, which are not this change's to fix. Confirm each before relying on it:

- The copy's `rsync` exits 23, because agentsh refuses two `.env.sample` names. The rest of the copy is complete, so go on to the suite in `$D`.
- In the copy, `test_tidy_clears_what_the_launch_walk_refuses` and `test_the_build_brief_copy_takes_what_git_sees_and_leaves_ignored_files_out` fail at `16ae2a5`, from the sandbox. Leave both tests alone.

Then, in `$D`:

1. `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests -q -p no:cacheprovider` passes, apart from any baseline failure confirmed above.
2. With only the tests written, before the config and script edits: cases 2 to 5, 8, 9 and 11 for the builders and `guard_builder`, and the four new chain tests, fail. Cases 6, 7 and 10 pass. Report which failed first.
3. From the worktree, `git status --porcelain -- .claude/` prints nothing, and `git status --porcelain -- adws/` names only `adws/adw_sssf_config/sssf.config.yaml`, `adws/adw_guard_change.py` and files under `adws/tests/`.
4. `git diff -- adws/adw_guard_change.py` shows only `REQUIRED_AGENTS`, two `owner=` values, the `plan_brief` paragraph, the `build_brief` paragraph and docstring lines.

The chain's verify already runs `pytest adws/tests` (`chain_test` in `adws/adw_modules/quality.py`), which picks up the new file. The plan adds no check command and no dependency.

## Out of scope

Holes this change leaves open, for a reviewer to file as old holes:

- Weakening a listed module from an unlisted file. A feature builder can still edit `data_types.py`, `repo_settings.py`, `templates/repo.yaml`, `sandbox.py`, `sandbox_remote.py`, `sandbox_e2b.py`, `sandbox_vercel.py`, `agents.py` and `session.py`. `data_types.py` holds the config defaults, `agents.py` calls `snapshot` and `enforce`, and the after-call check's git runs through `sandbox.current().run`, which the four sandbox modules provide.
- Weakening a listed test from an unlisted file. `tests/conftest.py` can skip a listed test or patch what it reads. Listed tests build their config from the starter rosters and `templates/repo.yaml`, and some read `references/config.md` and the shipped `agents.py`. `test_agentsh_guard.py` skips cases when `docs/designs/agentsh/deny-policy.py`, `sssf-net.yaml` or `first-real-runs/` is missing.
- The prompts and the starter rosters stay writable by feature agents.
- A new guard module, guard test or fixture is unprotected until someone adds it to the config. Only `GUARD_MODULES`, the nine, is tied to the list by a test. No test walks a folder.
- `guard_builder` may write all the guard code by design. The chain's spec review and bypass review limit it. Any ADW script could name it as an owner; only `adws/adw_guard_change.py` does, and ADW scripts are protected.
- The two fixed paragraphs are prompt lines. They tell the chain's planner and builder what is theirs, and enforce nothing: `permitted` decides.
- The hub's stamped copy under `adws/adw_modules/` is what enforces in the hub. It is already protected and unchanged here.
- The projects' configs. In a project the guard code is under `adws/` and protected.
- The gaps `docs/open.md` lists for the after-call check: gitignored files, `git replace`, a background write landing after the check.
- A listed path that carried an index flag, skip-worktree or assume-unchanged, before the call. `.claude/skills/sssf/references/config.md` names the skip-worktree flag among the known limits, and `_roll_back` leaves a path that was flagged before the call as it is.
- The `protected_files` list in every hub agent's prompt grows by 31 lines.

## For the engineer

These are protected and outside this run's unlock, so the builder leaves them:

- `CLAUDE.md`, "Guard changes": say the hub's config now refuses every agent but `guard_builder` on the listed paths. If the guard line should also name `agentsh_guard.py`, `agentsh_sssf.yaml` and `scripts/agentsh_install.py`, change it and `GUARD_MODULES` together, since `test_the_guard_list_is_the_one_claude_md_names` ties the two.
- `images/12_guard_chain.svg` labels the build step "builder". Relabel it if you want the picture to name the agent.
- The three `docs/designs/agentsh/` paths under Out of scope can be protected later by adding each to both lists in the config.
- Reports that group by agent show guard runs under `guard_builder` from now on.
- After the run: the two diff checks from the request, the `bypass-reviewer` pass on `main..HEAD`, then delete `sssf.config.guard-p1.yaml` and the `docs/open.md` row at the merge.

# Keep agents off the factory files that judge their work

Start from `main` (f362854). The open item is "Agents can edit factory files that `protected_files` doesn't cover" in `docs/open.md`, and [ADR 0001](../../docs/adr/0001-keep-sssf-in-the-repo.md#consequences) says why it matters: SSSF stays in the repo, so the agents it runs work in the same tree as the factory that judges them.

## The problem

`permissions.permitted` decides, after every agent call, whether each changed path was the agent's to change. It grants everything under `always_writable(cfg)` first, and that is all of `data_dir` (`adws/adw_data/`), because the session runtime lives there. Only then does it look at `protected_files`. But `data_dir` also holds `prompt_engineering/`, each agent's prompts, and `harness_engineering/`, the pi extensions agents load. So every agent may edit them, the read-only ones too, and naming those folders in `protected_files` would change nothing.

Other factory files aren't in the default `protected_files` either (`ConfigDefaults.protected_files` in `data_types.py`, and the starter `sssf.config.yaml`): `.claude/skills/sssf/`, the `justfile`, `sssf.just` and `.githooks/`. Neither is the repo's `.claude/settings.json` or `.claude/settings.local.json`, whose hooks run on the host in the engineer's next Claude Code session. A roster that sets `protected_files` replaces the default, so a repo that wrote its own list keeps its gaps even after an update fixes the default.

`prompts.render` reads a prompt from disk at each call. So a builder that edits `adws/adw_data/prompt_engineering/reviewer/system.md` changes what the reviewer is told later in the same run. The ticket chain's review diff (`adw_tickets.py`) leaves out `git_helper.SSSF_PATHS`, `adws/` and `.claude/skills/sssf/`, so the reviewer doesn't see that edit either.

Confirmed on 2026-11-12 in a clone of sssf-sandbox, with no agent: with the real starter roster, `permitted` allowed the builder, the reviewer (`writes: []`) and the documenter to change `reviewer/system.md` and `harness_engineering/subagents.ts`. It let the builder and documenter change `.claude/skills/sssf/cookbooks/run_adw.md`, and the builder change `justfile` and `.githooks/pre-push`. `enforce` raised nothing after a builder edit to the reviewer prompt. The next `prompts.render` carried the edit, and the ticket review diff of the commit was empty. A builder edit to `adws/adw_modules/rules.py` was refused.

## What should change

1. **Only the runtime is always writable.** `always_writable` grants the session runtime, `<data_dir>/sessions/`, and the agents' tool caches, `<data_dir>/agent-cache/`, not all of `data_dir`. Before narrowing it, find every path under `data_dir` that a starter chain's agent writes, in every chain under `templates/adws/`. Anything an agent must write that isn't runtime is a finding to report in the spec, not a path to keep open.
2. **SSSF always protects its factory files, whatever the roster says.** A fixed list in `permissions.py` joins the roster's `protected_files` and can't be dropped from it:
   - `<data_dir>/prompt_engineering/` and `<data_dir>/harness_engineering/`;
   - `.claude/skills/sssf/`;
   - `justfile`, `sssf.just` and `.githooks/`;
   - `.claude/settings.json` and `.claude/settings.local.json`;
   - and what the code default protects today: `adws/adw_modules/`, `adws/adw_sssf_config/` and `adws/adw_*.py`.

   As now, an agent that names one of these in its own `writes`, as the same entry or a path inside a protected folder, may change it. A broad glob such as `**/*.md` still doesn't name it. `specs/` and the roster's other entries stay in `protected_files`, where a roster can change them.
3. **The ticket chain's reviewer sees SSSF-file changes the feature made.** The review diff still leaves out SSSF files from commits that aren't the feature's, such as "Update SSSF" commits between runs. But SSSF-file changes in commits carrying the feature's `SSSF-Feature` trailer are shown to the reviewer, in their own section, as for the engineer to judge. Check the other chains' review inputs too. If one leaves SSSF files out the same way, it gets the same change.
4. **Docs.** `references/config.md`, "Write permissions, `writes` and `protected_files`", names the fixed list, says a roster can't drop it but an agent's `writes` can unlock one entry, and says that a repo whose own `justfile` holds product recipes unlocks it by naming it in the builder's `writes`. The starter `sssf.config.yaml`'s comment on `protected_files` says the same in a line.

## The bar

The earlier bars stand.

- Every refusal at the baseline stays a refusal.
- Every path in the fixed list is refused, after every call, to every agent of the starter Claude and pi rosters, unless that agent names it in its `writes`. That includes the agents with `writes: []` and the documenter with `**/*.md`.
- Every agent may still write the session runtime and its tool caches, `writes: []` included. The planner still writes `specs/`, and the documenter still writes `app_docs/`, `docs/` and the project's Markdown outside the fixed list.
- A refused change is rolled back as today, and the message names it.
- A builder's edit to the project's own `justfile` is now refused. That's intended and documented, not a false positive. Any other new refusal of a starter chain's normal work is one.
- The ticket review's new section shows only the feature's own commits' SSSF-file changes, and never blocks the run by itself.
- No new module-level mutable state. The fixed list is a constant, like `SSSF_PATHS`.

Runs under `local_guard: agentsh` and in VMs check paths the same way after every call. agentsh's own policy doesn't change.

## Testing

Use unit tests against the real starter rosters, loaded the way `agents.load_config` loads them, for both Claude and pi.

- `permitted` refuses each fixed-list path for each starter agent, and allows it for an agent that names it in `writes`.
- `permitted` still allows `<data_dir>/sessions/...` and `<data_dir>/agent-cache/...` for a `writes: []` agent.
- The confirmed repro becomes a regression test. In a temp git repo with the starter roster, a builder edit to `prompt_engineering/reviewer/system.md`, checked with `enforce`, raises `PermissionBreach` and rolls the file back.
- A roster whose `protected_files` leaves out every factory path still gets the fixed list.
- The ticket review: in a temp repo, a commit with the feature's trailer that touches `adws/adw_data/prompt_engineering/` shows in the review input's new section. An "Update SSSF" commit without the trailer doesn't.

The tests never start a real agent.

## This run is exposed to the gap it fixes

The hub runs `main`'s `adws/`, which has the gap. During this run, this run's own builder could edit `adws/adw_data/prompt_engineering/bypass_reviewer/` or `guard_spec_reviewer/` and change what reviews it. The change belongs in `.claude/skills/sssf/`, never in `adws/`. After the run, the engineer's session checks `git diff <base>..HEAD -- adws/` and `git status --short adws/`, expects both empty, and runs the `bypass-reviewer` subagent on the result. The hub's own roster in `adws/adw_sssf_config/` is updated by hand after the merge, with the hub update.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

## Out of scope

- agentsh write rules for the factory paths, as a second layer.
- Installing SSSF outside the tree (ADR 0001).
- A VM agent overwriting `sssf_refuse.py` within one call (its own open item).
- Edits a protected file sees before the after-call check runs.
- The export design in `docs/designs/upstream-patches.md`.

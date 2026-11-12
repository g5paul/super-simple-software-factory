---
status: accepted
---

# Keep SSSF installed in the repo, and export work for upstream

SSSF stays installed inside each repo it works on, committed with it. A feature goes upstream through an export that leaves SSSF files behind ([upstream-patches.md](../designs/upstream-patches.md), decisions 1 and 7 to 11). We chose this over installing SSSF outside the tree because worktree runs, VM runs and pi extensions all need SSSF's files committed in the repo. Moving out of the tree would also change guard code throughout. The price is real, though: the agents SSSF runs work in the same tree as the factory that judges them, and the guards and sandboxes we've built exist to keep agents from changing what judges them.

## Considered options

- **A branch install, cleaned by hand for each PR.** Rejected as fragile: it depends on care every time, and a branch install already cost one project its `/sssf`.
- **SSSF untracked, through `.git/info/exclude`.** Rejected because a worktree is a fresh checkout of tracked files only, a VM clones the committed repo, and a pi extension must be a file committed at the run's starting commit.
- **SSSF outside the tree,** with the skill at user scope and `adws/` beside the repo or under `.git/`. Deferred, not dropped (open.md). It's the only option that takes the factory out of the agents' reach, which is its strongest argument. It would also keep every repo's history free of SSSF, and one install could serve many repos. Against it, for now:
  - every module assumes `adws/` at the repo root: the trace, the recipes, `protected_files`, `SSSF_PATHS` and the refuse hook's path, and some of that is guard code;
  - worktree and VM runs would need the factory copied in each time;
  - a repo's roster, prompts and `quality.py` would need versioning of their own.

## Consequences

**Agents can reach the factory.** With SSSF in the tree, an agent's file tools can edit SSSF files, and some of those files decide whether its work passes. Today's defences:

- `protected_files` is checked after every agent call. An edit to a protected path fails the run. The starter roster protects `adws/adw_modules/`, `adws/adw_sssf_config/`, `adws/adw_*.py`, `specs/`, `.scratch/`, `GLOSSARY.md`, `GLOSSARY-MAP.md` and `docs/adr/`.
- The check comes after the call, so an edit to a protected file is live until that call ends. In a VM that's open item "a VM agent can overwrite `sssf_refuse.py`".

The gaps, confirmed on 2026-11-12 in a clone of sssf-sandbox with no agent, under the text guard:

- **Every agent may edit the prompts and pi extensions.** `permissions.permitted` grants all of `data_dir`, `adws/adw_data/`, before it looks at `protected_files`, since that's where the session runtime lives. But `prompt_engineering/` and `harness_engineering/` live there too. So the reviewer, with `writes: []`, may edit its own `system.md`, and listing those folders in `protected_files` wouldn't help.
- **Other factory files are open to the builder.** It may edit `.claude/skills/sssf/`, the `justfile` and `.githooks/`, and the documenter's `**/*.md` reaches the skill's cookbooks.
- **The edit is live and unseen.** Prompts are read from disk at each call (`prompts.render`), so a builder that edits `reviewer/system.md` changes what the reviewer is told later in the same run. The ticket chain's review diff leaves out `adws/` and `.claude/skills/sssf/` (`adw_tickets.py`, by `SSSF_PATHS`), and in the test it came back empty. An edit to `adws/adw_modules/rules.py` was refused, as it should be.

That's an open.md bug for the guard-change chain. It's also the main reason to reopen this decision: if protecting the factory inside the tree keeps leaking, take the factory out of the tree.

The first fix, run 37d69462 on branch `guard-factory-files`, added a fixed list of factory paths that a roster can't drop and narrowed the grant to the runtime. Its reviews then found ten more ways past the after-call check, all open on `main` as well. A second run closes those. One class it can't close: the check reads git, so it can't see gitignored files. An agent can still write `.claude/settings.local.json` where it's excluded, or a `.pyc` that Python loads in place of guard code. That needs a write rule that acts as the agent writes, such as agentsh's, not a better git check. If that also leaks, it counts toward reopening this decision.

**Everything else stays.** A repo's history holds SSSF files and "Update SSSF" commits, and its tools need excludes. Those are the costs the export exists to keep away from upstream.

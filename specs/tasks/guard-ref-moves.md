# Catch an agent that moves a ref other than its own HEAD

This is open.md's P1 row "An agent can rewrite another branch without the after-call check seeing it". Run it with `--worktree` and `--base main`, on the hub's own config. Since 28507f2, `guard_builder` writes the shipped guard code and the tests that pin it, so this run needs no one-run config.

## The problem

The after-call check knows where HEAD was and nothing else about refs. `permissions.head()` records HEAD's commit and branch, `undo_commits()` resets commits on that branch, and `GIT_WATCHED` lists no refs. agentsh's `sssf-git-data` rule allows the whole git folder (`agentsh_guard.py`).

So an agent, with HEAD on its run branch, can do this, where `R` is a protected path such as `.claude/skills/sssf/templates/adws/adw_modules/rules.py`:

```
echo hijack > R && git add R && T=$(git write-tree) && \
C=$(git -c user.name=a -c user.email=a@e commit-tree $T -p main -m x) && \
git update-ref refs/heads/main $C && git reset -q && git checkout -q -- R
```

Its own branch and tree come back clean, so `undo_commits` and `enforce` allow it for every agent, `writes: []` reviewers included, and `main` now carries the edit. The `bypass-reviewer` subagent found it on run fcbb8a7f. The same route reaches tags, other runs' `sssf/*` branches and `refs/replace/`.

## The design

This brief settles the design. Spend the spec on making it exact, not on choosing another.

1. **Before each agent call**, record every ref: `git for-each-ref` in the run's own repo, so loose and packed refs both count.
2. **After the call**, once `undo_commits` has handled HEAD's own branch and the git-directory check has restored the paths it watches, find the commits that became reachable during the call: those reachable from the refs after it and from none of the refs before it, leaving out HEAD's own branch. For each one, list the paths it changes against each parent, or against nothing for a root commit.
3. **Stop the run** if any of those paths is one this agent may not write because it is protected: in `protected_files`, or one of the fixed paths `permitted` refuses every agent. The breach names each ref, its old and new commit, the commits and the paths. Nothing is moved back.
4. **Stop the run** if a ref under `refs/replace/` was created, moved or deleted.
5. **Don't stop** for a deleted ref, or a ref that moved without new commits, such as a rewind or a fast-forward to commits that already existed. `just merge` and `just discard` delete branches during other runs.

Who made the commits doesn't matter. A commit on another branch that changes only paths outside the protection is allowed, whoever made it. That is how the engineer's commits to `main`, other runs' commits and merges go through without a stop.

## Rules from run 5db82031

Run 5db82031 tried a design that judged each moved branch against the files in the checkout holding it. Its bypass review found a harm and three false positives, and its fix added a module-level name, which stopped it. So:

- **All git runs in the run's own repo.** Never use a `cwd` inside another worktree or checkout, and never read another checkout's files. Another worktree's `.git/worktrees/<id>/commondir` and `gitdir` can be redirected by an agent, and run 5db82031's check ran an agent's `uploadpack` script that way.
- **Reads can't run agent code or fetch.** Every object read passes `-c core.commitGraph=false` and `--no-replace-objects`, with no hook, filter, textconv or external diff. This machine's git is 2.43, which has no `GIT_NO_LAZY_FETCH`, so a missing object counts as a stop, never a fetch.
- **Constants go in the build.** A fix round that adds any module-level name stops the run, even a constant. Put new constants in the first build, or keep them local to a function.

## The bar

The baseline is `main`.

- The command above is stopped for every agent, `writes: []` reviewers included, and `main` keeps the hijack commit for the engineer to see, with the breach naming it.
- The same holds for a tag, another branch and an `sssf/*` branch carrying a new commit that changes a protected path. Any change under `refs/replace/` stops the run.
- No stop for: commits on the run's own branch, which `undo_commits` handles as today; the engineer's commits, merges or pulls on the launch branch that touch only unprotected paths; other runs' commits on their own branches that do the same; deleted branches; rewinds and fast-forwards with no new commits.
- No engineer commit, branch or file is deleted or overwritten, and no ref is moved.
- No git command runs in another checkout, and no read can run a hook, filter, external diff, or fetch.
- Every refusal at `main` stays a refusal. A run that switches HEAD to another branch is still refused as today.
- No change to any ADW script, and no new module-level mutable state.

## Costs the spec states

- **The engineer's own protected edits.** A commit to a protected path on any branch during an agent call stops that run: in the hub, `CLAUDE.md`, `docs/projects.md`, `adws/`, `.claude/agents/` and the shipped guard code. Merging a guard run while another run's agent is working is one such commit. Say how often that should happen and what the engineer sees.
- **Concurrent guard runs.** Another guard run's commits change guard code, so they stop a feature run whose agent is working at the time.
- **What stays open.** An agent can still land commits that change only paths outside the protection on another branch, outside review. A reviewer with `writes: []` could put product code on `main` that way. Name it as the gap this design leaves, for an open.md row.

## Testing

In the skill's `tests/`, beside `test_agent_commits.py` and `test_factory_holes.py`, through `permissions.head`, `undo_commits` and `enforce` in throwaway git repos, as those tests do:

- the command above, for a builder and for a `writes: []` reviewer: stopped, with `main`'s sha checked unchanged by the check;
- a tag, another branch and an `sssf/*` branch, each given a new commit that changes a protected path: stopped;
- a `refs/replace/` entry created: stopped;
- a commit made outside the agent call on the launch branch changing an unprotected path, standing in for the engineer, and another worktree's commit on its own branch: no stop;
- a deleted branch and a rewound branch: no stop;
- a commit on the run's own branch: undone as today;
- a second worktree whose `commondir` points at a repo with a promisor remote and an `uploadpack` script, with that worktree's branch moved: the script never runs.

Each stopping case fails on `main` and passes after. The tests never start a real agent.

## Where to edit

Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`, never under the launch checkout above it. agentsh refuses writes outside the worktree, so an `EACCES` on a path outside it means a wrong path, not a missing permission.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

agentsh refuses every `git push`, a push to a local path included, and three refusals stop the call. `tests/test_export.py` and `tests/test_install_hook.py` push to bare repos under the test's temp folder, so the builder leaves them out of its own runs: `pytest tests -q --ignore=tests/test_export.py --ignore=tests/test_install_hook.py`. Neither file touches what this change does. The chain's verify phase runs as code, outside agentsh, and runs both. Run 5db82031's first build ran them, was refused 51 times, and stopped.

## Out of scope

- The `--no-replace-objects` half of the `git replace` row for the rest of the guard's git. This change's own reads use it.
- Agent commits on other branches that change only unprotected paths, as above.
- The other after-call gaps open.md lists: gitignored files, a write landing after the check, and an agent that kills the chain before the check.
- The holes filed with this one: a package standing in for a listed module, the guard files off the protected list, and `adw_prompt.py --agent guard_builder`.
- agentsh's policy. A rule that denied ref writes would be per project, not per run, so it couldn't let a run's own branch move.

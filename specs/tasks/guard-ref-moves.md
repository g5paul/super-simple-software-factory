# Catch an agent that moves a ref other than its own HEAD

This is open.md's P1 row "An agent can rewrite another branch without the after-call check seeing it". Run it with `--worktree` and `--base main`, on the hub's own config. Since 28507f2, `guard_builder` writes the shipped guard code and the tests that pin it, so this run needs no one-run config.

## The problem

The after-call check knows where HEAD was and nothing else about refs. `permissions.head()` records HEAD's commit and branch, `undo_commits()` resets commits on that branch, and `GIT_WATCHED` lists no refs. agentsh's `sssf-git-data` rule allows the whole git folder (`agentsh_guard.py`).

So an agent, with HEAD on its run branch, can do this, where `R` is any path its `writes` don't allow, such as `.claude/skills/sssf/templates/adws/adw_modules/rules.py`:

```
echo hijack > R && git add R && T=$(git write-tree) && \
C=$(git -c user.name=a -c user.email=a@e commit-tree $T -p main -m x) && \
git update-ref refs/heads/main $C && git reset -q && git checkout -q -- R
```

Its own branch and tree come back clean, so `undo_commits` and `enforce` allow it for every agent, `writes: []` reviewers included, and `main` now carries the edit. The `bypass-reviewer` subagent found it on run fcbb8a7f, on 16ae2a5 and 588b9ec alike. Its probe is `probes/enforce_refs.py` in the scratch folder that review left; reproduce it from the command above if that folder is gone.

The same route reaches tags, `refs/replace/`, `refs/stash`, other runs' `sssf/*` branches and a deleted ref. `packed-refs` holds refs too.

## What should change

Extend the existing before-and-after snapshot to every ref, not only HEAD: record the refs before each agent call, and after it, find every ref other than HEAD's own branch that was created, moved or deleted. Read them through git, as `head()` does, so packed and loose refs both count. HEAD's own branch keeps today's handling in `undo_commits`.

The fix should change `permissions.py`, the call site in `agents.py` if it needs one, and the tests that pin them. A new mechanism stops the run; widening the snapshot SSSF already takes is not one.

## What the spec must decide

The check can't tell who moved a ref. Two kinds of move during an agent call are normal and must not be refused or undone:

- **The engineer's own commits.** The engineer commits to the launch branch, often `main`, while a `--worktree` run is going. On 2026-11-13 the hub's `main` moved four times during one guard run.
- **Other runs.** Concurrent `--worktree` runs commit to their own `sssf/<adw_id>` branches, and SSSF's code makes and removes those branches.

So the spec says, for each kind of ref, what the check does when it moves, and why:

- the launch branch and other `refs/heads/*`;
- other runs' `refs/heads/sssf/*`;
- `refs/tags/*`, `refs/replace/*`, `refs/stash`, `refs/remotes/*` and anything else under `refs/`.

Undoing a ref move can destroy the engineer's work, so the default is the one `permissions.py` already uses for a change to a shared config file outside the repo: stop the run and name the ref, its old and new commit, and leave the ref where it is for the engineer. A spec that moves a ref back says why that move can't be the engineer's.

If the spec stops a run on a move it can't tell from the engineer's, it says how often that will happen and what the engineer sees. A design that tells the agent's commit from the engineer's by what the new commits change, checked against the agent's `writes`, is one option to weigh, not a requirement.

The `git replace` row in open.md asks for `--no-replace-objects` on the guard's git and a watch on `refs/replace/` and `packed-refs`. Say whether this change closes the watch half. The `--no-replace-objects` half stays with that row unless the spec takes it on and says so.

## The bar

The baseline is `main`.

- On `main`, the command above gets past the check for every agent. After the change, each agent's run stops on it and the breach names the ref, unless the spec shows why that ref's move can't be told from normal work.
- The same holds for creating, moving or deleting a tag, a `refs/replace/` entry and a ref under `refs/heads/` other than the run's own branch.
- No new refusal or stop of normal work: commits on the run's own branch, which `undo_commits` already handles, the engineer's commits to the launch branch, and other runs' commits to their branches, unless the spec names the case and its cost.
- No engineer commit, branch or file is deleted or overwritten, and no ref is moved back that the engineer might have moved.
- Every refusal at `main` stays a refusal. A run that switches HEAD to another branch is still refused as today.
- No change to any ADW script, and no new module-level mutable state.

## Testing

In the skill's `tests/`, beside `test_agent_commits.py` and `test_factory_holes.py`, through `permissions.head`, `undo_commits` and `enforce` in throwaway git repos, as those tests do:

- the command above, refused or stopped as the spec says, with `main`'s sha before and after checked;
- a tag, a `refs/replace/` entry and another branch, each created, moved and deleted;
- a commit on the run's own branch, still undone as today;
- a commit made to the launch branch from outside the agent call, standing in for the engineer, handled as the spec says, and never moved back;
- another worktree's commit on its own `sssf/*` branch during the call, handled as the spec says.

Each new case fails on `main` and passes after. The tests never start a real agent.

## Where to edit

Edit every file at its path inside the run's worktree, `adws/adw_data/worktrees/<adw_id>/`, never under the launch checkout above it. agentsh refuses writes outside the worktree, so an `EACCES` on a path outside it means a wrong path, not a missing permission.

The hub stays on `local_guard: agentsh`. The builder runs the suite from a copy outside the repo, with the copy command the build brief gives.

## Out of scope

- The `--no-replace-objects` half of the `git replace` row, unless the spec takes it on.
- The other after-call gaps open.md lists: gitignored files, a write landing after the check, and an agent that kills the chain before the check.
- The holes filed with this one: a package standing in for a listed module, the guard files off the protected list, and `adw_prompt.py --agent guard_builder`.
- agentsh's policy. A rule that denied ref writes would be per project, not per run, so it couldn't let a run's own branch move.

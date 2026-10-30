# Design: a branch per run, with a merge back

Status: settled in a grilling session on 2026-10-30, built and proven the same day, and rolled out to both projects. The decisions below are the record, with the changes the build made noted in place. Where the text and a decision disagree, the decision wins.

SSSF's README lists what the core leaves out: "It runs on your current branch. There is no sandbox, no branch per run, no merge step..." The container option ([container-operator.md](container-operator.md)) added the sandbox. This design adds the next two: each run works on its own branch, in its own folder, and its work reaches your branch only when you merge it.

## Why

Today a chain commits to whatever branch is checked out, in the checkout you're working in. That costs two things:

- **You can't work while it runs.** The agents edit the same files you would, and the tree check before and after each agent would blame them for your edits.
- **Nothing stands between a run and your branch.** A run that passes its checks and review commits straight onto your branch. The only way back is to reset.

A branch per run fixes both. Parallel runs would need the same machinery, so this leaves room for them, but doesn't build them.

## How sandcastle does it, and what we take

[Sandcastle](https://github.com/mattpocock/sandcastle), read from the fork at `~/github/sandcastle`, runs one agent in a container on a git worktree and merges the result back.

- Worktrees live in `<repo>/.sandcastle/worktrees/<name>`, on temp branches named `sandcastle/<timestamp>-<hex>`, created from `HEAD`.
- With its default isolated strategy, it merges automatically when the agent ends, with a plain `git merge` into whatever the host has checked out. On a conflict it leaves the half-done merge in the host checkout and keeps the branch.
- It removes a clean worktree after a run, keeps a dirty one with recovery commands printed, and prunes stale worktrees at the next start.
- A fresh worktree gets no dependencies unless you list paths to copy in, or add a hook.

We don't use it as a library. Its unit of work is one agent call: worktree, agent, merge, cleanup. An SSSF run is a whole chain, with several agents sharing one tree and code steps in between: quality checks, commits, and the tree check around each agent. Wrapping each agent would mean a worktree and a merge per agent, plus Node, sandcastle's language, called from Python for every one. The branch and merge part is plain git, small enough to write in SSSF.

## Decisions

### Goal and scope

1. **Isolation and a gate, now.** A run works apart from your checkout, and its work lands only when you merge it. Parallel runs are left room for, not built.
2. **The single-task chains.** `adw_simple_sdlc`, `adw_plan_build_test`, `adw_plan_build_test_quality`, and `adw_build_test`. `adw_tickets` keeps its rule: it runs on a feature branch you make, and refuses `main`. Revisit it once this works.
3. **Opt-in per launch.** Each of those chains takes `--worktree`. The operator adds it only when the engineer asks for a separate branch. The run cookbook describes the option, but never adds it on the operator's own judgement. Without the flag, a run behaves as it does today.
4. **Anywhere, any roster.** It works on the host and in the container, with Claude Code and Pi rosters. Only the container is limited to Claude Code.

### Two layers

5. **SSSF owns branch and merge. The sandbox sits behind a seam.** Branch and merge are Python in SSSF, tied to the chain run. The sandbox is a separate, swappable layer, so the Podman container is one implementation and others can sit beside it, such as sandcastle extended with more providers. The contract, for when it's built: "run this operator session, or this command, in this folder, with these mounts, and report how it ended." The seam's code waits until a second sandbox exists. Nothing in the container changes for this design, because the worktree lives inside the repo that `run.sh` already mounts.

### Mechanics

6. **A worktree per run.** `git worktree add -b sssf/<adw_id> adws/adw_data/worktrees/<adw_id> <launch tip>`. The install adds `adws/adw_data/worktrees/` to `.gitignore`, so a live worktree doesn't make the checkout look dirty, and the setup checklist already keeps `adws/` out of linters and package builds. It must be inside the repo, or a container session can't reach it.
7. **Cut from the launch branch's tip, merged back into it.** The launch branch is whatever is checked out when the run starts.
8. **SSSF runs from the launch checkout. Work happens in the worktree.** The chain's code, roster, and prompts come from the checkout the run is launched in. Agents' working folder, quality checks, plan checks, the tree check around each agent, the change capture for the documenter, and every commit use the worktree. One copy of SSSF is in play per run, and an uncommitted roster tweak still applies.
9. **One trace, in the main checkout.** The trace db and run folders stay at the main checkout's `adws/adw_data/`, so the dashboard and the update's running-chain check see worktree runs. Today those paths are relative to the folder a process starts in, so the build must anchor them to the main checkout.
10. **The trace records where a run worked.** The run's start logs a `worktree` event with the branch, the worktree path, the launch branch, and its base commit. (Built at the start, not in the request phase: merge, discard, and resume read it before any phase exists.) The dashboard shows them: a run's card names its branch, and the run view says which branch it was cut from, at which commit, and whether uncommitted changes were left out. `just merge` and `just discard` log `worktree_merged` and `worktree_discarded`, so both views also say whether the branch was merged, discarded, or still waits.
11. **A dirty checkout doesn't block a launch.** A run without `--worktree` still refuses one where it does today. A worktree run goes ahead and prints that your uncommitted changes aren't part of the run.
12. **One worktree run per repo at a time.** A second one refuses to start while one is alive. Two would share the container's project environment, and that's untested. Parallel runs lift this when they're designed.
13. **Resume stays in the worktree.** `--adw-id <id>` on a worktree run resumes inside its worktree, on its branch. It refuses once the run has been merged or discarded.
14. **Dependencies build in place.** On the host, the first `uv run` in a worktree builds that worktree's `.venv`: slower once per run, and isolated. In the container, all of a repo's worktrees share the environment `run.sh` sets, which is fine while runs go one at a time. Nothing is copied in. Sandcastle's copy list and ready hooks are the fallback if this gets too slow.

### Merge and cleanup

15. **Nothing merges on its own.** A run ends with its branch ready. `just merge <adw_id>` merges it when the engineer says so, and only for a run whose status is success, with the launch branch checked out and no uncommitted changes. (The last check was added in the build: a merge that has to abort can't promise to put uncommitted changes back.) To take a failed run's work, merge its branch by hand with git.
16. **How it merges.** It fast-forwards when it can, and makes a merge commit when the launch branch has moved on. It never rebases, so the run's three commits (spec, code, write-up) keep their shas. (Added after rollout: a repo that keeps a linear history sets `merge: ff_only` in its committed `adws/adw_sssf_config/repo.yaml`, and then a launch branch that moved on is refused with nothing changed. `--ff-only` does the same for one merge, and `SSSF_MERGE_FF_ONLY` in `.env` overrides the file on one machine.)
17. **A conflict leaves nothing behind.** The recipe aborts the merge, so your checkout is clean, keeps the run's branch and worktree, and prints the commands to merge by hand.
18. **A clean merge cleans up.** It removes the worktree and deletes `sssf/<adw_id>`.
19. **Failed runs stay for inspection.** A run that fails keeps its worktree and branch. `just discard <adw_id>` removes them. A worktree run's start runs `git worktree prune`, which drops records of worktrees whose folders are gone, as sandcastle does.
20. **No pushes.** Nothing here pushes. Merging moves a local branch only; pushing stays the engineer's call, as it is today.

## Proof before rollout

Every case runs in sssf-sandbox before any project gets it:

1. A worktree run on the host while the sandbox's checkout has uncommitted changes, then `just merge`.
2. The same from the container.
3. A run that fails, then `just discard`.
4. A merge after the launch branch moved on, which makes a merge commit.
5. A merge with a conflict, which aborts, leaves the checkout clean, and keeps the run's branch.
6. A second worktree run while one is alive, which refuses.
7. A resume with `--adw-id`.

Then yt-summarizer and pi-agent-observability get it through a normal SSSF update.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-10-30 | hub | grilling session, this design | settled |
| 2026-10-30 | hub | `--worktree`, `just merge`, `just discard`, 3ee8bc3 | built; the install adds `adws/adw_data/worktrees/` to `.gitignore`, without which a live worktree made the checkout look dirty |
| 2026-10-30 | sssf-sandbox | case 1, run 80796ad4 on the host with an uncommitted edit | success: the edit stayed out of the run; merge refused the dirty checkout, then fast-forwarded |
| 2026-10-30 | sssf-sandbox | case 6, a second worktree run while a839d183 was alive | refused, naming a839d183 |
| 2026-10-30 | sssf-sandbox | case 4, run a839d183 after a commit on master | merge commit, no rebase |
| 2026-10-30 | sssf-sandbox | case 5, run f400a6db against a conflicting README edit | merge aborted, master clean and unchanged, branch and worktree kept. The first try, 80ba30ec, didn't conflict: the function it targeted already existed |
| 2026-10-30 | sssf-sandbox | case 3, run 2f25b95b stopped mid-plan | `fail`; merge refused it; discard removed branch and folder |
| 2026-10-30 | sssf-sandbox | case 7, run accb2a16 stopped mid-build, resumed by `--adw-id` with no flag | resumed in its worktree, merged; resuming again after the merge refused |
| 2026-10-30 | sssf-sandbox | case 2, container run 84f61760 | failed mid-verify with the error "1": the operator launched it through `\| head -40`, and Rich exits on a closed stdout. Not the worktree; the same verify passed on the host |
| 2026-10-30 | hub | the console goes quiet on a closed stdout; run_adw.md says never pipe a chain's output, f0203e8 | shipped |
| 2026-10-30 | sssf-sandbox | case 2 again, container run d8fc85aa | success: the operator chose `adw_simple_sdlc --worktree` from the request's wording, logged to a file, and merged when told. Its prompt file in the checkout blocked the first merge, so run_adw.md now says to keep prompt files out (24db188) |
| 2026-10-30 | hub | merged and pushed, 24db188 | shipped |
| 2026-10-30 | hub | `just kill`, 7a582c3 and ea718ae | shipped. The starter justfile never had it, though run_adw.md named it. Live test c716a01b hung 10s: the chain can't exit while its agent runs, so kill now stops the agents once the chain has closed the run (9d83c72f: 0.8s) |
| 2026-10-30 | yt-summarizer, pi-agent-observability | SSSF update to ea718ae | updated; quality 5/5 in both |
| 2026-10-30 | hub | one `# sssf runtime` block in `.gitignore`, 836ff09 | shipped; each update had been adding its own header |
| 2026-10-30 | hub | `--ff-only` and `SSSF_MERGE_FF_ONLY`, 4523b6c; then `repo.yaml` with `merge: ff_only`, 9191931 | shipped. pi-agent-observability only takes fast-forwards, and a rule in the untracked `.env` never reaches a fresh clone. Live run ff8d0878 refused under `ff_only` and merged with the `.env` override set to 0 |
| 2026-10-30 | yt-summarizer, pi-agent-observability | SSSF update to 9191931 | updated; pi-agent-observability keeps `merge: ff_only` in `repo.yaml` (2ad5fd6) |
| 2026-10-30 | hub | the dashboard shows a worktree run's branch, on its card and in the run view | built; checked against a copy of the sandbox trace: 4 recent runs show a branch, older plain runs show none |
| 2026-10-30 | sssf-sandbox | merge and discard logged to the trace, ee155e9; run 3a7cae7f merged, run 3dc529bf discarded | both events recorded; the dashboard shows "merged" and "discarded" on the cards, and the merge's commit in the run view |

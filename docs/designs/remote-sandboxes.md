# Design: remote sandboxes

Status: building. Settled in one grilling session on 2026-11-02. Step 1 of decision 17, the seam with only the local sandbox, is built. Where the text and a decision disagree, the decision wins.

A run with `--sandbox vercel` or `--sandbox e2b` does its work in a remote VM. The ADW stays on the host and keeps the trace, the caps, the launch lock and the dashboard. Every process the run starts in its tree, agents, checks and git, goes through one runner, which sends it to the VM. Commits come back to the run's branch on the host, and the engineer merges as usual.

[Branch per run](branch-per-run.md), decision 5, put the sandbox behind a seam and left its code until a second sandbox existed. [The container operator](container-operator.md), under "What a second sandbox means", said why: built around one sandbox, the interface would guess at what the second needs. This design has two, on the free tiers of Vercel Sandbox and E2B, and builds the seam against both.

## Why

The Podman container shares the repo folder with the host, so everything the run does lands on this machine. Two needs point past it: more parallel runs than one machine holds, and a stronger boundary than rootless Podman around the code agents write and the tests that run it. A remote VM gives both, as long as the host keeps the trace and the caps.

## What was found

From [the research](../research/remote-sandboxes.md), sandcastle's providers (`~/github/sandcastle/src/sandboxes`) and disler's agent-sandbox-skill (`~/github/agent-sandbox-skill`):

- **Sandcastle has solved moving the code.** A git bundle of the repo goes in (`src/syncIn.ts`). Committed work comes back with `format-patch` and `git am --3way`, and uncommitted work as a diff and copied files, saved before anything is applied (`src/syncOut.ts`). It has a Vercel provider, in TypeScript, and no E2B one.
- **Both have an official Python SDK** that creates a VM, runs a command with streamed output and per-command environment variables, reads and writes files, and stops it: `vercel-sandbox` 0.10.0 and `e2b` 2.62.0. Vercel streams lines; E2B streams chunks, so the runner splits them.
- **Sessions are short, but resumable.** Vercel Hobby allows 45 minutes a session and E2B Hobby an hour. Both reset the limit on a resume. Vercel keeps the files across a stop; E2B's pause keeps the files, memory and processes, and keeps a paused sandbox forever.
- **Images.** Vercel's default image is Ubuntu with git, uv, bun, Node, `claude` and `pi`. E2B's base template is Debian, and a custom template is built in Python.
- **Cost.** E2B bills per second on the vCPU and memory a sandbox holds, so runtime times size gives its cost. Vercel bills on active CPU, which only Vercel sees, and reports charges per team per day.
- **The free tiers.** Vercel Hobby: 5 active CPU hours a month, 10 sandboxes at once, blocked rather than billed past the allowance, personal and non-commercial use only. E2B Hobby: a one-time $100 credit, 20 at once, no card needed, and its page doesn't say what happens after the credit.
- **The sign-in.** `claude setup-token` makes a one-year subscription token that can only make model requests, used as `CLAUDE_CODE_OAUTH_TOKEN`. Anthropic's docs recommend it for CI. An `ANTHROPIC_API_KEY` in the same environment would win over it.
- **Where processes start.** A run starts processes in its tree from about a dozen places: the two agent runners, `quality.py`, the plan's checks, the gates, git, and the tree check around each agent.

## Decisions

### Goal and shape

1. **For parallel runs and a stronger boundary, with the host on.** The host is up for the whole run. A run that outlives this machine is not a goal.
2. **A sandbox run is a worktree run.** `--sandbox <provider>` implies `--worktree`, and counts toward `max_worktree_runs`. Its commits land on `sssf/<adw_id>`, and `just merge`, `just discard` and the dashboard's branch view work as they do today.
3. **The ADW stays on the host, and every process it starts in the tree goes through one runner.** The runner is local by default, or a VM. The trace, the caps, the launch lock and the dashboard stay on the host. The VM holds the run's tree and does all its work: agents, checks and git. This runner is the seam. It covers the host's own reads of files in the tree too, such as the gates checking that an agent's declared artifacts exist and parse.
4. **`--sandbox` sits beside `--container`.** `--container` puts the operator session in Podman; `--sandbox` sends one run's work to a VM. They combine. repo.yaml's `sandbox: vercel|e2b|null`, `null` by default, sets a repo's default, and the flag overrides it.

### The VM

5. **One VM per run, refreshed between commands.** The runner creates it after the worktree and deletes it when the run ends. Before each command, when less of the session is left than the command's own timeout, it stops and resumes the VM on Vercel, or pauses and resumes it on E2B, for a fresh session. One command must fit in one session. A command that hits the limit fails like any failed phase, and the chain's retries decide what happens next.
6. **What moves.**
   - In, at launch: a git bundle of the worktree's branch, then `worktree_setup` in the VM. `.env` and other untracked files never go in.
   - Out, after every commit the run makes: `format-patch` and `git am` onto `sssf/<adw_id>`, so the host branch stays current and keeps the commits if the VM dies.
   - Out, at the end, a stop or a failure: uncommitted work comes back as a saved patch, not applied.
   - The run's `context_handoff` folder sits at the same absolute path in the VM. It's copied in before each agent call, and what changed is copied out after.
7. **Images and SDKs.** On Vercel, the default image as it is; uv picks each repo's Python. On E2B, one SSSF template, built by a script in the skill from E2B's base, with uv, bun, `claude` and `pi`. Per-repo images wait for a repo whose `worktree_setup` is too slow. The runner uses `vercel-sandbox` and `e2b`, declared in the module that uses them.
8. **Secrets and the network.** The sandbox has no environment of its own. Each agent command gets `CLAUDE_CODE_OAUTH_TOKEN` and SSSF's own variables, such as `SSSF_ADW_ID`, and the runner makes sure `ANTHROPIC_API_KEY` is unset. Checks and git get no token. Outbound network stays open for the proof. A domain allowlist, and a token added by the provider's proxy so it never enters the VM, come after.
9. **Stopping and cleanup.** Each VM is tagged with its adw_id and project. `just kill` and a run's end delete the VM and any Vercel snapshot it left. Before each launch, the runner deletes every SSSF-tagged sandbox whose run isn't live. A process in the VM is stopped through the provider, not by a host PID, and the trace records its VM ID.

### Failures, rebase and resume

10. **No silent fallback.** If the provider won't create a VM, because the allowance or credit is used up or the token is wrong, the run stops before its first agent with the provider's message, like a failed `worktree_setup`. If the VM dies, the run stops, and its commits are already on the host branch. A run that asked for a sandbox runs in one or doesn't run.
11. **`just rebase` runs on the host.** By then the VM is gone, so the rebase's setup and checks run on the host, as today. The boundary covers the run, not the rebase, which the engineer starts by hand after reading the branch. A VM for the rebase's checks waits for a repo that needs untrusted code off the host from start to finish.
12. **A resume gets a new VM.** It's made from the host branch, runs `worktree_setup`, and carries on from the failed phase. The saved patch of uncommitted work isn't applied; the engineer chooses whether to apply it first.

### Spend, sign-in and the dashboard

13. **VM time is tracked, not capped.** The trace records each run's VM seconds and, on E2B, its estimated VM cost from runtime and size. Neither counts toward `max_run_cost` or the other caps until real numbers show whether they should.
14. **The sign-in is a `claude setup-token` token,** its own, not the container's, so either can be cancelled alone. The engineer keeps it in `~/.config/sssf-sandbox/token.env` (mode 600) as `CLAUDE_CODE_OAUTH_TOKEN`, outside every repo: the ADW loads a repo's `.env` into its own environment, which would hand the token to every host agent and check. The remote runner reads the file and passes the token per command, per decision 8. Never the live `~/.claude/.credentials.json`, and never an API key. The first proof runs the Claude Code roster only. A ChatGPT roster in the VM, which needs pi's sign-in, waits.
15. **The dashboard shows the sandbox.** A sandbox run's page shows its provider, VM ID, VM seconds and, on E2B, its estimated VM cost. The process list tags each process with its VM.

### Proof, build order and rollout

16. **Proof in sssf-sandbox, per provider:**
    1. A simple SDLC run passes with a setup marker the tests need, so it passes only if `worktree_setup` ran in the VM. `just merge` lands it.
    2. A test-only setting shortens the session to a few minutes, so a normal run refreshes at least once.
    3. A failure mid-run leaves its commits on the host branch and its uncommitted work in a saved patch.
    4. `just kill` deletes the VM, and the sweep deletes one left from a run that isn't live.
    5. Two sandbox runs at once, within `max_worktree_runs`.
    6. A check prints its environment's names, and `CLAUDE_CODE_OAUTH_TOKEN` isn't among them.

    Tickets and trials get `--sandbox` once both providers pass all six.
17. **Build order.** First the runner seam with only the local runner: every in-tree process goes through it, nothing behaves differently, every test passes, and the projects take it as an ordinary update. Then Vercel, which needs no template and blocks rather than bills past its allowance. Then E2B, once the engineer has checked what it does past the credit.
18. **Rollout, on the engineer's word.** No project repo gets `--sandbox` until both providers pass, then one repo at a time, the way `--worktree` rolled out. A repo that is work for an employer stays off Vercel's free tier, which is for personal, non-commercial use. Nothing runs unattended on E2B until its behavior past the credit is known.
19. **The engineer makes the accounts and keys.** `VERCEL_TOKEN` and `E2B_API_KEY` go in sssf-sandbox's `.env`, and the Claude token in its own file, per decision 14. All are checked by key name only. The first two are sandbox keys, not model keys. No `ANTHROPIC_API_KEY` may be set where a sandbox run starts, since it would win over the token.

## Open

- **A ChatGPT roster in the VM.** It needs pi's OAuth sign-in in the VM, and a decision on how it gets there.
- **A narrower network.** A domain allowlist, and the token added by the provider's proxy instead of passed in.
- **VM cost in the caps.** Waits for real numbers, per decision 13.
- **Rebase checks off the host.** Per decision 11.
- **Not yet known:** whether a `claude -p` call survives an E2B pause mid-request, which exception Vercel's SDK raises when a session ends mid-stream, and what E2B does past the $100 credit.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-11-02 | hub | grilling session, this design | settled |
| 2026-11-02 | hub | step 1: `adw_modules/sandbox.py`, with `Local` only. Agents, quality checks, plan checks, gates, git, the tree check and a run's worktree setup go through `sandbox.current()`; a test fails if an in-tree module starts a process around it. `tickets` reads and rewrites its own files directly, and moves to the seam when tickets get `--sandbox` | 347 tests passed |
| 2026-11-02 | sssf-sandbox | step 1 live: a Claude Code agent (cb4d4b4f), a pi agent on ChatGPT (cd741b20), the quality chain (94a64b50), and a worktree `adw_simple_sdlc` run (cafae9cb) | all success; cafae9cb passed 14 of 14 phases, with setup, two rounds of quality checks and three commits, $0.59, then discarded |

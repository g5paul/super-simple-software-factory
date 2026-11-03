# Design: remote sandboxes

Status: building. Settled in one grilling session on 2026-11-02. All three steps of decision 17 are built: the seam, then Vercel and E2B, which each passed all six proof cases. Where the text and a decision disagree, the decision wins; "As built" below records where the build differs from a decision.

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

## As built: Vercel

What step 2 settled that the decisions left open, or changed:

- **The VM mirrors the host's paths.** The clone sits at the host worktree's absolute path and the handoff folder at its host path, made with `sudo` and owned by the VM's user. Every path the run passes means the same file on both sides, so nothing translates paths.
- **Commits come back as a git bundle, not `format-patch`.** The host worktree never changes on its own, so the host branch fast-forwards to the VM's commits with the same SHAs. Decision 6 said `format-patch` and `git am`; a bundle keeps the two branches identical.
- **Agents get the token in `start`, checks get none in `run`.** Each VM command gets only `SSSF_` and `OBS_` variables from the host. The run stops before making a VM if `ANTHROPIC_API_KEY` is set.
- **An agent's pid in the trace is a negative stand-in.** A VM's pids mean nothing on the host, and `just kill` skips those rows. Stopping the chain deletes the VM, which stops the agent. Each agent runs under `setsid`, so its own tree stop is one `pkill -s` in the VM.
- **The SDK isn't in the chains' headers.** `--sandbox vercel` starts the chain again once under `uv run --with vercel-sandbox==0.10.0`, so a run without it never fetches the SDK.
- **A refresh is a stop.** The VM is persistent, and the next call resumes it with its files in a new session. No command asks for more than half a session, or a short session would stop the VM before every command.
- **A VM's snapshots go with it.** Each stop saves an automatic snapshot of about 800MB, which Vercel keeps after the VM is deleted unless asked. `destroy(delete_orphan_snapshots=True)` deletes them, at the end of a run and in the sweep.
- **Not built yet:** the dashboard's sandbox view (decision 15) and repo.yaml's `sandbox:` default (decision 4). The trace already holds what the view needs, in the `sandbox`, `sandbox_commits`, `sandbox_refresh` and `sandbox_end` events.

## As built: E2B

Step 3 kept everything above and settled this:

- **The two providers share one runner.** `sandbox_remote` holds the work: the clone, the handoff folder, commits back, the patch, refreshes and the token. A provider module makes, refreshes and deletes the VM. `sandbox_e2b` puts E2B's SDK behind the calls Vercel's SDK has, so the runner doesn't know which provider it has, and the same tests run against fakes of both.
- **The template.** `scripts/e2b_template.py` builds `sssf` from E2B's base image, which has 2 vCPUs, 512MB and Node 20. It adds Node 24, which pi needs, plus uv, bun, `claude` and `pi`, and sets 2 vCPUs and 4GB, Vercel's size. It is built once per E2B team, and again to take newer tools. Node 20's npm is removed before Node 24 is unpacked, or npm fails.
- **Commands are shell strings.** E2B runs a command line, not an argv, so the adapter quotes it. A command that needs root runs as E2B's root user, not through `sudo`. Input goes in as a file in the VM. Output arrives in chunks, which the adapter splits into lines.
- **A refresh is a pause and a resume.** Both keep the files and the processes and take well under a second. A `claude -p` call paused mid-request finished normally after the resume.
- **The VM's timeout is its session.** E2B kills a VM at its timeout, so a VM whose host died is gone within the session anyway. The sweep still deletes VMs left by dead runs.
- **VM cost is estimated.** `sandbox_end` carries the VM's vCPUs, memory and `vm_cost_usd`, from E2B's published per-second rates. The trace records it; no cap counts it, per decision 13.
- **An early exit stops the agent.** On a stop or a kill, the Claude Code runner waited for the agent to exit. On the host the kill stops the agent too, but in a VM nothing did, so the first E2B kill took 32 seconds. The runner now stops the agent's tree first. Vercel runs went through the same wait.
- **The VM uses the host's Python.** The host's uv picks it in the worktree, and every VM command gets it as `UV_PYTHON`, as major.minor. yt-summarizer's `.python-version` is untracked, so its clone in the VM had none. uv there picked the image's 3.11, where argparse prints help differently, and three help tests failed until a fix round loosened them. Both providers do this.
- **Past the credit, E2B blocks.** Its billing page says a team with no payment method has every project blocked shortly after the credit runs out, so nothing is charged. Paused sandboxes aren't billed and are kept with no limit; SSSF deletes them anyway.

## Open

- **A ChatGPT roster in the VM.** It needs pi's OAuth sign-in in the VM, and a decision on how it gets there.
- **A narrower network.** A domain allowlist, and the token added by the provider's proxy instead of passed in.
- **VM cost in the caps.** Waits for real numbers, per decision 13.
- **Rebase checks off the host.** Per decision 11.
- **Vercel's Hobby limits beyond the allowance.** Resuming is rate-limited to 40 vCPUs a minute, and snapshot storage is capped; past the cap, Vercel refuses every new VM until the month resets or snapshots are deleted. Both were hit in the proof, by a bug since fixed. How far a day of real runs is from either is not known.
- **Not yet known:** which exception Vercel's SDK raises when a session ends mid-stream.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-11-02 | hub | grilling session, this design | settled |
| 2026-11-02 | hub | step 1: `adw_modules/sandbox.py`, with `Local` only. Agents, quality checks, plan checks, gates, git, the tree check and a run's worktree setup go through `sandbox.current()`; a test fails if an in-tree module starts a process around it. `tickets` reads and rewrites its own files directly, and moves to the seam when tickets get `--sandbox` | 347 tests passed |
| 2026-11-02 | sssf-sandbox | step 1 live: a Claude Code agent (cb4d4b4f), a pi agent on ChatGPT (cd741b20), the quality chain (94a64b50), and a worktree `adw_simple_sdlc` run (cafae9cb) | all success; cafae9cb passed 14 of 14 phases, with setup, two rounds of quality checks and three commits, $0.59, then discarded |
| 2026-11-02 | hub | step 2: `adw_modules/sandbox_vercel.py` and `--sandbox vercel` on four chains, tested against a fake VM | 357 tests passed |
| 2026-11-02 | sssf-sandbox | step 2, case 1: simple SDLC run 29a10c73 in VM red-capable-sturgeon, with the setup marker the tests need; `just merge` landed it | 15 of 15 phases, 3 commits back with the same SHAs, 149 VM seconds, $0.54 |
| 2026-11-02 | sssf-sandbox | case 6: a setup command printed its environment's names and failed if the token was among them | passed; only the VM's own variables and `SSSF_` ones |
| 2026-11-02 | sssf-sandbox | cases 3 and 4: run f5d93c18 stopped by `just kill` 12 seconds into the build; its launch swept a stray VM tagged to a dead run | plan commit on the host branch, builder's 4 files saved as a patch, VM deleted, stray deleted |
| 2026-11-02 | sssf-sandbox | cases 2 and 5, first try: two runs at once with 4-minute sessions (c30b1572, f8168e97) | refreshed 48 and 41 times, one failed on Vercel's 429 for resumes; then every VM was refused for snapshot storage, 90 snapshots and 72GB. Fixed in 4ce90f3 and 6a8b676 |
| 2026-11-02 | sssf-sandbox | cases 2 and 5, after the fixes: 08474800 and f59f718b at once, 4-minute sessions | both 12 of 12 phases; 1 refresh; no VMs or snapshots left. The second merge conflicted in CHANGELOG and README, as two parallel runs can, and was discarded |
| 2026-11-02 | hub | step 3: `sandbox_remote` takes the shared work, `sandbox_e2b` and `--sandbox e2b`, and `scripts/e2b_template.py`; the sandbox tests run on fakes of both SDKs | 373 tests passed; template `sssf` built, with Node 24.11.1, uv 0.13.6, bun 1.4.4, claude 2.1.316 and pi 1.5.0 |
| 2026-11-02 | sssf-sandbox | E2B probes: a `claude -p` call with the token, and one paused mid-request for 5 seconds | $0.0014, "ok"; the paused call finished with no error |
| 2026-11-02 | sssf-sandbox | E2B case 1 and case 6: simple SDLC run a58dfe5c, setup marker and token check in `worktree_setup`; `just merge` landed it | 12 of 12 phases, 3 commits back with the same SHAs, 97.8 VM seconds, $0.0045 VM estimate, $0.40 |
| 2026-11-02 | sssf-sandbox | E2B cases 3 and 4, first try: run 8f57e634 killed during review, after sweeping a stray VM tagged deadbeef | stray deleted, plan commit on the host branch, the builder's README and CHANGELOG edits saved as a patch, VM deleted; but the chain took 32 seconds to exit, waiting for the reviewer. Fixed in 2f92827 |
| 2026-11-02 | sssf-sandbox | E2B cases 3 and 4, after the fix: run fae86b4b killed 4 seconds into the build, after sweeping a new stray | `just kill` returned in 0.8 seconds and listed the builder; plan commit on the host branch, VM deleted 0.2 seconds after the kill, stray deleted, no VMs left. The builder had written nothing, so no patch |
| 2026-11-02 | sssf-sandbox | E2B cases 2 and 5: 79b3f7e5 and bda89502 at once, 4-minute sessions | both passed, 15 of 15 and 12 of 12 phases; 79b3f7e5 refreshed once; no VMs left. The second merge conflicted in CHANGELOG and README and was discarded |
| 2026-11-02 | yt-summarizer | first project run on E2B: 7337d8da, `yts --usage` by model and effort | 15 of 15 phases, $1.46, 3 commits back, 333 VM seconds, $0.0153 VM estimate. The VM's Python 3.11 against the host's 3.13 cost a fix round; fixed by passing the host's choice as `UV_PYTHON` |

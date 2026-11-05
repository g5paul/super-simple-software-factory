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
4. **`--sandbox` sits beside `--container`.** `--container` puts the operator session in Podman; `--sandbox` sends one run's work to a VM. They combine. repo.yaml's `sandbox: none|vercel|e2b`, `none` by default, sets a repo's default, and the flag overrides it. `--sandbox none` keeps one run on the host.

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

### pi extensions in the VM

Settled in a grilling session on 2026-11-05. Until then a roster with `harness_engineering` extensions was refused under `--sandbox`, so pi-agent-observability, whose eight agents all load its obs extension, couldn't use a VM.

20. **Any extension a pi roster names.** Not a list of known ones. The obs extension is the first real case. Both providers get it.
21. **Extensions come from the VM's own clone.** An extension must be a file committed in the repo. In a VM run its path is anchored to the worktree path, where the run's branch is cloned, so the files it imports come along. Nothing new is copied in; the refuse extension is still copied as today. Copying each file and following its imports, or requiring a bundled single file, were both turned down.
22. **Checked at launch.** Before the VM opens, each `harness_engineering` path must be inside the repo and committed at the run's starting commit. One that isn't stops the run with a message naming the agent and the path. This replaces the refusal in `sandbox.check_roster`. Imports aren't followed: a missing one fails when pi loads the extension in the VM, and the run stops with pi's error.
23. **No obs events from a VM, for now.** In the VM, `OBS_DISABLE=1` is set and `OBS_AUTH_TOKEN` stays on the host. The extension loads and posts nothing. SSSF's own trace still records the run from pi's JSON stream. A relay that buffers events in the VM and replays them to the host's obs server after each call is under Open. Opening the engineer's obs server to the internet is not an option.
24. **The refuse extension loads last,** as now, so it checks the command after every other extension has had its turn. A test pins the order.
25. **A known limit.** The agent can edit a committed extension in the VM, and the edited one loads for the next agent call. A host run has the same exposure, since an extension isn't a protected file. `config.md` says so; nothing checks it.
26. **Built through the guard chain,** since `agent_pi.py` changes, on a branch from `main`.
27. **Proof in sssf-sandbox, on both providers.** (1) A committed test extension that imports a file beside it loads in the VM and leaves a marker the trace shows. (2) An uncommitted extension stops the run before a VM opens. (3) So does one outside the repo. (4) With the test extension loaded, a refuse rule still blocks a command. (5) In the VM, `OBS_DISABLE=1` is set and `OBS_AUTH_TOKEN` is absent. (6) A roster with no extensions runs as before. Then pi-agent-observability is asked to try one VM run. pi in the Podman container is the next job after this one; see `container-operator.md`.

## As built: Vercel

What step 2 settled that the decisions left open, or changed:

- **The VM mirrors the host's paths.** The clone sits at the host worktree's absolute path and the handoff folder at its host path, made with `sudo` and owned by the VM's user. Every path the run passes means the same file on both sides, so nothing translates paths.
- **Commits come back as a git bundle, not `format-patch`.** The host worktree never changes on its own, so the host branch fast-forwards to the VM's commits with the same SHAs. Decision 6 said `format-patch` and `git am`; a bundle keeps the two branches identical.
- **Agents get the token in `start`, checks get none in `run`.** Each VM command gets only `SSSF_` and `OBS_` variables from the host. The run stops before making a VM if `ANTHROPIC_API_KEY` is set.
- **An agent's pid in the trace is a negative stand-in.** A VM's pids mean nothing on the host, and `just kill` skips those rows. Stopping the chain deletes the VM, which stops the agent. Each agent runs under `setsid`, so its own tree stop is one `pkill -s` in the VM.
- **The SDK isn't in the chains' headers.** `--sandbox vercel` starts the chain again once under `uv run --with vercel-sandbox==0.10.0`, so a run without it never fetches the SDK.
- **A refresh is a stop.** The VM is persistent, and the next call resumes it with its files in a new session. No command asks for more than half a session, or a short session would stop the VM before every command.
- **A VM's snapshots go with it.** Each stop saves an automatic snapshot of about 800MB, which Vercel keeps after the VM is deleted unless asked. `destroy(delete_orphan_snapshots=True)` deletes them, at the end of a run and in the sweep.
- **The dashboard's sandbox view (decision 15)** was built after both providers; see "As built: the dashboard".

## As built: the repo default

Decision 4's `sandbox:` setting, built after both providers:

- **It picks for new runs only.** `session.choose_sandbox` reads it when a chain that takes `--sandbox` starts without the flag. A run whose adw_id is already in the trace keeps where it ran. A VM run resumes in a new VM, as decision 12 says, and a host run stays on the host, so a setting committed since can't move it into a worktree halfway through.
- **`none`, not `null`.** The setting and the flag share one word for the host, so `--sandbox none` reads like the setting it overrides.
- **The runs that take `--sandbox` read it:** the four chains, and since "As built: tickets and trials", `just tickets` and `just trial`. The single-phase chains don't, so a repo with `sandbox: e2b` still runs them on the host.

## As built: usage

- **CPU comes from the VM itself.** Neither SDK reports active CPU, so the runner reads `/proc/stat` at open, before each refresh and at close, and adds each session's busy seconds: user, nice, system, irq, softirq and steal, over all vCPUs. Time waiting on I/O is left out, as Vercel leaves it out of active CPU. A count lower than its base means the VM booted again. A failed reading makes the run's `cpu_seconds` null, never a guess.
- **Vercel's `sandbox_end` adds `memory_gb_hours`,** from the VM's size and seconds. Hobby counts memory busy or idle.
- **`just sandbox-usage` adds up traces.** An allowance belongs to a team, and each repo keeps its own trace, so it takes other repos' traces too. It counts Vercel over the last 30 days and E2B over all time, against the Hobby numbers in `sandbox_usage.py`.
- **First numbers.** A full `adw_simple_sdlc` on Vercel used 12.7 CPU seconds in 130 VM seconds, about 10% of one vCPU, because the agents spend most of a run waiting on the model. At that rate Vercel's 5 hours covers well over a thousand runs a month, and its 420 GB-hours about 2,900. A repo whose tests take longer will use more.

## As built: tickets and trials

Decision 17's last step, built after the dashboard:

- **`just tickets <folder> --sandbox vercel|e2b|none`.** The whole feature is one worktree run in one VM: the scout, every ticket's build, checks and commit, both reviews and the write-up. Each ticket's commit comes back to `sssf/<adw_id>` as it lands, so a VM that dies keeps every finished ticket on the host. The choice is made before the feature claim, because a VM run is a worktree run and builds from the feature folder as committed.
- **Done marks go through the sandbox.** `tickets.mark_done` used to write the ticket file on the host, which in a VM run would have left the mark out of the ticket's commit. The sandbox has a `write_text` now, and the mark lands in the VM's tree.
- **`just trial --sandbox vercel|e2b|none "<request>" <rosters>`.** The trial picks one place, from the flag or repo.yaml, and passes it to every attempt, so a trial never mixes VM and host attempts. Each roster is checked before anything starts. A pi roster can't run in a VM yet, so it refuses the trial and names `--sandbox none`. The panel runs on the host, from the branches the attempts' commits came back to.
- **The trial doesn't load the SDK.** Loading it restarts a process under `uv run --with`, and the flag that guards that restart would have reached every attempt and stopped each one from fetching the SDK. The trial checks only the provider name and the roster, drops the flag from the attempts' environment, and each attempt fetches the SDK itself.
- **A stop's message says where the work is.** It used to say to `git stash` on the host. In a VM run it now says the work will be saved as `sandbox-uncommitted.patch` when the VM closes.
- **Changed for repos with `sandbox:` set.** A new ticket run in such a repo now goes to the VM without the flag. Trials already did: their attempts are `adw_simple_sdlc` runs, which read repo.yaml, so this design's earlier line saying trials stay on the host was wrong.

## As built: the dashboard

Decision 15, built after the usage numbers, from events the trace already held:

- **The run page has a VM line** under the branch: the provider and VM ID, VM time, CPU seconds, Vercel's GB-hours or E2B's estimated cost, the VM's size, and the commits brought back and refreshes. It says when the VM is still up, when it wasn't deleted and why, and when uncommitted work was saved as a patch. Hovering shows the Python the VM was given.
- **A run card names its provider** before its branch, from the `worktree` event, which records it.
- **The account panel has an allowance gauge** for each provider a registered repo has used: Vercel's CPU, memory and creations over 30 days, and E2B's credit over all time, with each repo's run count. It adds up every registered repo, because an allowance belongs to the account, and it agrees with `just sandbox-usage`, which uses the same Hobby numbers. `just obs-all` shows it; `just obs` shows one repo's runs.
- **No process tags.** Decision 15 asked for each process to be tagged with its VM, but the dashboard has no process list: agents are lanes, and one run has one VM, so the run's VM line covers it.

## As built: E2B

Step 3 kept everything above and settled this:

- **The two providers share one runner.** `sandbox_remote` holds the work: the clone, the handoff folder, commits back, the patch, refreshes and the token. A provider module makes, refreshes and deletes the VM. `sandbox_e2b` puts E2B's SDK behind the calls Vercel's SDK has, so the runner doesn't know which provider it has, and the same tests run against fakes of both.
- **The template.** `scripts/e2b_template.py` builds `sssf` from E2B's base image, which has 2 vCPUs, 512MB and Node 20. It adds Node 24, which pi needs, plus uv, bun, `claude` and `pi`, and sets 2 vCPUs and 4GB, Vercel's size. It is built once per E2B team, and again to take newer tools. Node 20's npm is removed before Node 24 is unpacked, or npm fails. Node and pi are pinned in the script, pi at 1.5.0 since 2026-11-03, after the ChatGPT live check found the template on 1.5.0 and the host on 1.4.0; `claude` takes the latest at each build. Vercel's default image brings its own pi, and SSSF doesn't pin that one.
- **Commands are shell strings.** E2B runs a command line, not an argv, so the adapter quotes it. A command that needs root runs as E2B's root user, not through `sudo`. Input goes in as a file in the VM. Output arrives in chunks, which the adapter splits into lines.
- **A refresh is a pause and a resume.** Both keep the files and the processes and take well under a second. A `claude -p` call paused mid-request finished normally after the resume.
- **The VM's timeout is its session.** E2B kills a VM at its timeout, so a VM whose host died is gone within the session anyway. The sweep still deletes VMs left by dead runs.
- **VM cost is estimated.** `sandbox_end` carries the VM's vCPUs, memory and `vm_cost_usd`, from E2B's published per-second rates. The trace records it; no cap counts it, per decision 13.
- **An early exit stops the agent.** On a stop or a kill, the Claude Code runner waited for the agent to exit. On the host the kill stops the agent too, but in a VM nothing did, so the first E2B kill took 32 seconds. The runner now stops the agent's tree first. Vercel runs went through the same wait.
- **The VM uses the host's Python.** The host's uv picks it in the worktree, and every VM command gets it as `UV_PYTHON`, as major.minor. One project's `.python-version` is untracked, so its clone in the VM had none. uv there picked the image's 3.11, where argparse prints help differently, and three help tests failed until a fix round loosened them. Both providers do this.
- **Past the credit, E2B blocks.** Its billing page says a team with no payment method has every project blocked shortly after the credit runs out, so nothing is charged. Paused sandboxes aren't billed and are kept with no limit; SSSF deletes them anyway.

## As built: the tool check

E2B's template pins Node and pi, but Vercel's default image brings its own `claude` and `pi`, at versions Vercel picks and can change. So once the branch is cloned into the VM and before any agent starts, `sandbox_remote` reads the version of each tool the run needs, in one command:

- Every run: git and uv.
- A Claude Code agent: `claude`, at least 2.1.312, the oldest SSSF has run on in a VM (Vercel's image on 2026-11-03).
- A pi agent: Node, at least 22.19.0, pi's own `engines` floor, and `pi`, at least 1.4.0, which has `auth print-bearer-token`.

A tool that's missing, unreadable or below its minimum fails the `sandbox` phase with a message naming each one, and the VM is deleted. Nothing has run in it. A major version newer than SSSF has run on (`claude` 3.x, `pi` 2.x) is noted on the console and the dashboard, and the run goes on. Refusing it would stop every run the day Vercel updates its image.

The versions go into the trace as a `sandbox_tools` event and into the `sandbox` phase's log, and the dashboard's VM line shows them when you hover over it. The minimums are in `TOOLS` in `sandbox_remote.py`. Raise one when SSSF starts using a newer tool feature, and bump `tested` after a run on a new major.

## Open

- **A ChatGPT roster in the VM: built in 51d54f6 and 673a70d.** Before each pi call the host runs `pi auth print-bearer-token --provider openai --min-expiry 35m`, and the token crosses as `SSSF_PI_TOKEN`, which becomes `OPENAI_API_KEY` only in the VM, so a host API key never does. The refuse extension is copied in. Still refused in a VM: pi on a provider other than `openai/`, whose key would have to go in, and rosters with `harness_engineering` extensions until decisions 20 to 27 are built. Vercel's image installs the latest pi, unpinned; the roster ran there too, in b7487076.
- **Obs events from a VM run.** Decision 23 turns them off. A relay would take the extension's posts in the VM, buffer them, and replay them to the host's obs server after each agent call, so the obs token never enters the VM either.
- **A narrower network.** A domain allowlist, and the token added by the provider's proxy instead of passed in. Matters only for untrusted code, like rebase checks off the host.
- **VM cost in the caps: not needed.** Decision 13 waited for real numbers, and they're in: a run's VM costs about 1% of its Claude usage ($0.016 against $1.46 and $1.94 on one project), so the Claude caps already bound a run. Revisit only if a repo's VM time grows far past its agent time.
- **`--sandbox` for tickets and trials** was built after the dashboard; see "As built: tickets and trials".
- **Rebase checks off the host.** Per decision 11.
- **Vercel's Hobby limits beyond the allowance.** Resuming is rate-limited to 40 vCPUs a minute, and snapshot storage is capped; past the cap, Vercel refuses every new VM until the month resets or snapshots are deleted. Both were hit in the proof, by a bug since fixed. How far a day of real runs is from either is not known.
- **Not yet known:** which exception Vercel's SDK raises when a session ends mid-stream. The trace will show it when it happens; nothing to build until then.

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
| 2026-11-02 | an unlisted project | first project run on E2B | 15 of 15 phases, $1.46, 3 commits back, 333 VM seconds, $0.0153 VM estimate. The VM's Python 3.11 against the host's 3.13 cost a fix round; fixed by passing the host's choice as `UV_PYTHON` |
| 2026-11-02 | sssf-sandbox | repo.yaml `sandbox: e2b`, no flag: build_test run 9435413c | went to E2B VM i02iyvgotfev649b32sfx on Python 3.13, 5 of 5 phases, $0.04, 21.9 VM seconds. The chain doesn't commit, so the work came back as the patch. Discarded, and the setting put back to `none` |
| 2026-11-03 | sssf-sandbox | `cpu_seconds` on both providers: build_test b9bc57f9 on Vercel and c23224a7 on E2B, then simple_sdlc 3753ce47 on Vercel | b9bc57f9 5.0 CPU seconds in 18.4 VM seconds, 0.02 GB-hours; c23224a7 4.6 in 20.0, $0.0009; 3753ce47 12 of 12 phases, $0.45, 12.7 in 129.5, 0.144 GB-hours. All three discarded. `just sandbox-usage` over sssf-sandbox's and the project's traces added them up |
| 2026-11-03 | sssf-sandbox and every registered repo | the dashboard's sandbox view, on a test server on port 4691 | run 3753ce47's page showed its Vercel VM, 2m 10s, 12.7 s CPU, 0.144 GB-h, 3 commits back; cards named vercel and e2b; the gauge matched `just sandbox-usage`: Vercel 8 runs, 1.23 GB-h; E2B 9 runs, $0.0542, sssf-sandbox 7 and the project 2. Checked at desktop and phone width |
| 2026-11-03 | sssf-sandbox | `just tickets .scratch/line-stats/ --sandbox vercel`, a two-ticket feature, run 9c3020e6 | success, $0.46. VM yellow-typical-cephalopod-kAX6N5: 181.5s, 36.3 CPU seconds, 0.202 GB-h, deleted. Three commits came back one at a time (01, 02, the write-up), and both tickets' done marks are in their own commits |
| 2026-11-03 | sssf-sandbox | `just trial --sandbox e2b "<count_spaces>" main main`, trial b2c03567 | both attempts success and passed the panel, $1.28 in all. 7ea8ab05 in VM irrlr1vgbo4hvs4rhcspr (113.3s, 9.9 CPU s, ~$0.0052) and d8ef9f1d in iruhtgjudvgzfzuepffx1 (145.5s, 13.0 CPU s, ~$0.0067), 3 commits back each, both deleted; each attempt's trial tag says `sandbox: e2b`. The panel ran on the host. Discarded after |
| 2026-11-03 | sssf-sandbox | the chatgpt roster in E2B, plan_build_test. First try 10e3a50d failed at the planner: pi couldn't make its session folder, which stayed root's (fixed in 673a70d). Rerun 27129786 | success, $0.06: planner and builder on gpt-6.1-sol, suite passed in the VM, 1 commit back, 84.7 VM seconds, VM deleted. No token in the trace, the session files or the log. Discarded |
| 2026-11-03 | sssf-sandbox | the chatgpt roster in Vercel, plan_build_test, run b7487076 | success, $0.06: suite passed in VM scarlet-obedient-lamprey-n9uZ3F, 1 commit back, 66.3 VM seconds, VM deleted. No token in the trace, the session files or the log. Discarded |
| 2026-11-03 | sssf-sandbox | the tool check, build_test on each image: chatgpt in E2B (0d2c0e92) and Vercel (7e085166), Claude Code in Vercel (ce5226f0, then 4f17f236) | E2B: git 2.39.5, uv 0.13.6, node 24.11.1, pi 1.5.0. Vercel: git 2.53.0, uv 0.13.6, node 24.22.1, pi 1.4.0, claude 2.1.312. ce5226f0 stopped in 4 seconds on the first floor, claude 2.1.316, which no run had proved; the floor went to 2.1.312 and 4f17f236 passed. All discarded |

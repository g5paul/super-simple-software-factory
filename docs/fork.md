# What this fork adds

This fork branched from [disler/super-simple-software-factory](https://github.com/disler/super-simple-software-factory) at de31374, from 2026-08-02. Upstream's `main` hasn't moved since. Its `example` branch adds one commit, b2dcb8e from 2026-08-04, an example app that the fork doesn't take. By 2026-11-05 the fork was 401 commits ahead, across 347 files: about 54,900 lines added and 700 removed. About 13,400 of those lines are tests, now 1,064 for the skill, 35 for the hub and 70 in the dashboard.

This page is the changelog: what changed from upstream, and where each change was designed. Many changes weren't asked for as features; they fixed something a run or a review turned up. Those say what happened, in a sentence starting "Added after". The [guide](README.md) describes SSSF as it is now. To bring it up to date, read the commits since the last one listed in the log at the bottom:

```bash
git log --reverse --format='%cs %h %s' de31374..main
```

## Upstream, in one paragraph

A Claude Code skill that stamps a factory into a repo. The factory is a set of ADWs, short Python chains of agents and code checks. Python owns the order, the retries and the acceptance checks, and every event goes into a SQLite trace that a read-only visualizer shows. The agents ran on pi, paid by the token with API keys through OpenRouter, one run at a time in the repo's own checkout.

## The fork, in one paragraph

The fork runs several repos at once on two subscriptions. A run can't spend past its caps, gets its own branch, and keeps to rules the repo owns. One hub session sets SSSF up in each repo and hands the work to that repo's own Claude session.

## What changed

### Subscriptions instead of API keys

- A runner for `coding_agent: claude_code`, and a starter roster that runs every agent on Claude Code with the Claude subscription.
- A ChatGPT roster, stamped with `install.py --chatgpt`, that runs pi agents on the ChatGPT subscription's OAuth sign-in.
- A lapsed pi sign-in stops the run instead of falling back to an API key.
- A Claude agent doesn't start when `claude` has no login. Added after a container run's planner failed with a bare "Not logged in" that said nothing of the cause.

### Spend and usage limits

- **Claude's usage windows.** A run stops before a Claude agent starts when a window is past `rate_limit_stop`, or when the account has moved on to usage credits. A call already running is stopped the moment Claude reports credits in use.
- **Caps in dollars.** `max_run_cost` and `max_call_cost` in the roster cap one run and one agent call. `max_live_cost` and `max_daily_cost` in repo.yaml cap what the repo's live runs spend together and what it spends in a day. `max_daily_cost` in `~/.config/sssf/limits.yaml` caps the whole machine's day. Design: [parallel-worktree-runs.md](designs/parallel-worktree-runs.md).
- **The ChatGPT plan's limit.** That route sends no usage windows, so a pi run stops by name on the plan's 429. With `SSSF_CODEX_LIMITS=1`, the dashboard reads the plan's 5-hour and weekly windows through Codex CLI's app server. No stop reads them. Design: [pi-usage-stop.md](designs/pi-usage-stop.md). Research: [pi-usage-limits.md](research/pi-usage-limits.md).
- **Plans checked before the build.** `gates.plan_well_formed` sends a plan back to its planner when its two copies differ, it overwrites a committed spec, it has no verification heading, or it names no real file. A fix costs one turn instead of a build. The plan's own checks may not compare the tree with git, through `git diff --exit-code` or `git status --porcelain` and the like, since verify runs them before the build is committed and no fix can pass them. Added after a project's run spent two fix rounds on a check ending in `git diff --exit-code`.
- **Agents that go quiet.** An agent that writes nothing for `max_silence_minutes` in repo.yaml, 15 by default, is stopped with the run. A hang spends nothing, so the caps above never catch it. Design: [silent-agents.md](designs/silent-agents.md).
- **Private repos.** The [operator session](concepts.md#the-operator-session) asks at install whether a repo is private, and keeps a private repo on `sandbox: none`, so its runs stay off remote VMs unless the engineer asks. The pre-push hook refuses a push naming a project listed in `.git/sssf-private-names`, so a public fork can install into repos it doesn't name, private or not. This hub leaves a repo unlisted unless the engineer says otherwise: [projects.md](projects.md#unlisted-projects).
- **Pricing.** A killed Claude call is priced from the output counts it streamed. Added after two proof runs recorded their stopped calls at $0. `prices.json` holds Claude's list prices, a run and the dashboard warn about a model it doesn't list, and `scripts/check_prices.py` compares it with claude.com/pricing. Prices for models that page doesn't list, such as Sonnet 5, sit under its `elsewhere` map, which the check skips. Added after the account panel left out calls to two models `prices.json` didn't list.

### A branch per run, and runs in parallel

- `--worktree` runs a request on its own branch in its own worktree. The engineer then runs `just merge`, `just rebase` or `just discard`, and `just kill` stops a run by its adw_id. The cookbooks told operators to use `just kill` from the start, but the starter justfile had no such recipe, and the old one matched any Python process; it now stops only the run's recorded processes. Design: [branch-per-run.md](designs/branch-per-run.md).
- Worktree runs go in parallel, up to `max_worktree_runs`. Each new worktree runs repo.yaml's `worktree_setup` before its first agent. Design: [parallel-worktree-runs.md](designs/parallel-worktree-runs.md).
- `--container` runs the operator session in a Podman container. A pi roster runs there too: a broker on the host hands each pi call a one-hour ChatGPT token over a socket, so the login stays on the host, and a login or an openai key stored in the container stops the run. The key check was added after a review found an agent could plant an openai key in pi's `models.json` and bill it instead of the plan. Obs works as on the host. Design: [container-operator.md](designs/container-operator.md).

### Runs in a remote VM

- `--sandbox vercel` or `--sandbox e2b` runs a worktree run's agents, checks and git in a VM made for the run and deleted at its end, within Vercel's free Hobby tier or E2B's one-time credit. Commits come back to the host branch after each commit, and uncommitted work is saved as a patch. `just tickets` and `just trial` take it too, and repo.yaml's `sandbox:` sets a repo's default. Design: [remote-sandboxes.md](designs/remote-sandboxes.md).
- `--sandbox openshell` runs a roster's worktree run in a local OpenShell microVM: free, no API key, and a network closed except each roster's model host (`api.anthropic.com`, `api.openai.com`) and read-only package registries, plus a repo's `sandbox_allow`. `just openshell-setup` starts the gateway and builds the image. Env values go in over stdin, a sentinel tells a CLI failure from a command's exit, and denials are `sandbox_denied` events. Built; the proof run is next. Design: [openshell-sandbox.md](designs/openshell-sandbox.md).
- Claude Code agents sign in with a `claude setup-token` token. Pi agents on `openai/` models get a short-lived access token from the host's ChatGPT sign-in before each call, so the login never leaves the machine. Research: [pi-chatgpt-in-vm.md](research/pi-chatgpt-in-vm.md).
- Before any agent starts, the VM's git, uv, `claude`, Node and `pi` versions are read. A missing or too-old tool fails the run with each one named, and nothing runs in that VM.
- The dashboard shows each run's VM and what runs have used of each provider's allowance, as does `just sandbox-usage`.
- A pi roster's extensions load in the VM from its own clone; an extension must be committed in the repo. The obs extension posts to a relay on the VM's loopback, and the host forwards the events to the engineer's obs with its own token, which never enters the VM.
- Every SDK call has a deadline, and so does each agent's output stream: after 2 minutes of quiet the host asks the VM whether the agent still runs, and a dead stream fails the call as `agent_stream_lost`. A dropped connection once held a run for 15.7 minutes.

### Two new workflows

- `adw_tickets` builds a specced feature one ticket at a time, each with a fresh builder working test first, and reviews each ticket against the repo's standards and the spec. With `--worktree`, the whole ticket run gets its own branch. Design: [ticket-sdlc.md](designs/ticket-sdlc.md).
- `adw_trial` races several rosters on one request, scores each attempt, and merges the one the engineer picks. Design: [trials.md](designs/trials.md).

### Rules the repo owns, and the guards

[concepts.md](concepts.md#guards) says what guard code is for and where each piece came from. In short: projects asked for enforced rules, and the pre-push hook came with making the fork public. The rest was added after something went wrong.

- `adws/adw_sssf_config/repo.yaml`, committed in each repo, holds its merge style, worktree setup, spend caps and `agent_rules`.
- Every agent gets the rules in its prompt. A rule with a `forbid` pattern is enforced: a matching command stops the run, and with `on_match: refuse` it is refused before it runs and the run goes on, on Claude Code and pi agents alike.
- No agent may read the repo's secret files: `.env` and its kin, plus repo.yaml's `secret_files`. Claude Code gets a deny rule per file, pi's file tools refuse the path, and a built-in refuse rule refuses any command naming one. An agent gets none of `.env`'s keys beyond SSSF's own and those `agent_env` lists. See [config.md](../.claude/skills/sssf/references/config.md#secret-files--secret_files-and-agent_env).
- When Claude Code denies a call itself, before SSSF's refuse rule sees it, SSSF counts the call as refused, as if its own rule had refused it. Added after the first two runs with the secret-file guard crashed on Claude Code's denial event, and a review then found those denials weren't counted.
- No agent may change a committed spec except the planner. Added after a builder's `ruff format .` rewrote one, and the change rode into the code commit.
- An agent's own commits are undone before SSSF checks what it wrote. Added after an agent that committed hid its changes from the check, and the run failed with nothing to commit.
- The git-directory guard puts back any agent change to `.git`, its config or the global git config, and stops the run. Added after a review showed that a hook planted in `.git/hooks` runs on the chain's own commit. It took 17 review rounds to build: [the post-mortem](postmortems/git-dir-guard.md).
- How rules match has been fixed many times, each after a project session or a review found a miss: the starter push rule stopped `git stash push`, three ways of skipping heredoc bodies each let `cat <<EOF | bash` through, a pattern could read differently in pi's JavaScript, and a command's output could pass for a refusal. [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules) lists what's still open.
- Hub only: every change to guard code goes through `adw_guard_change`, which reviews it for a bypass before it merges. Added after the `cat <<EOF | bash` bypass reached projects through an update, and the git-directory guard's 17 rounds by hand. See [sssf.md](sssf.md#guard-changes).
- Every agent's environment carries `SSSF_ADW_ID`, so a repo's own scripts and hooks can tell an SSSF agent from the engineer.
- `install.py` stamps a `pre-push` hook, `.githooks/pre-push`, that scans what a push would send with gitleaks, refuses on a find, and refuses any push from an SSSF agent. Each clone turns it on with `git config core.hooksPath .githooks`. See [secret-scan.md](secret-scan.md).

### Running SSSF across several repos

- `install.py --update` and `just sssf-update` refresh a repo from the skill and keep the repo's own edits. A file both sides changed is flagged, and `--keep` or `--take` settles it. The install records the skill commit it came from.
- An onboarding cookbook sets a new repo up; each step is one that cost a fix in the first project install, and `choose_a_flow.md` says when to use one run and when to use tickets.
- `just obs-all` shows every registered repo in one dashboard. Its account panel has a card per account, Claude and pi, each with spans from 5 hours to 90 days. Each usage window shows its pace in $ per hour and in percent per hour, and a table sets the closed 7-day weeks side by side from `window_archive.jsonl`. Both cards split spend into SSSF agents and interactive sessions, pi's from the sessions pi saves. Gauges show live and daily spend against their caps, and the dashboard has views for tickets, trials, worktree branches, merges and rebases.
- `scripts/statusline.py`, set as Claude Code's statusline, records the account's 5-hour and 7-day windows after every interactive reply, so the meters and `rate_limit_stop` stay current between runs. Added after the panel's reading sat six hours old, since only agent calls recorded it.
- The hub has three read-only reports on its projects. `adws/check_projects.py` checks each repo's update against the skill, `adws/levers.py` counts runs, minutes, agents and touches per week, and `adws/features.py` prices each feature that landed. See [sssf.md](sssf.md).
- [open.md](open.md) is the hub's one list of open SSSF work.
- [projects.md](projects.md) lists the repos running SSSF, the skill commit each one is on, and their open items.

### Smaller fixes

Each of these fixed something a run turned up.

- A code change is committed under the first build's message, not the last repair's. A feature once landed as "Complete slugify test coverage per review findings".
- `adw_simple_sdlc` runs every quality check, not only the tests, since lint and typecheck never ran and CI could still fail after the commit.
- `adw_simple_sdlc` ends before review when its checks stay red. That build is never committed, so review spent its calls on code the run then threw away.
- Each review round keeps its own file. In the first real run, round 2 overwrote round 1's.
- Docs updates are part of every change. In the first real run the builder updated the docs only because it chose to.
- SSSF's own files stay out of the feature reviews and the documenter's diff. A spec reviewer called an SSSF update commit scope creep, and a write-up spent a section on SSSF internals.
- The installer tells each repo to keep SSSF's folders out of its own linters and packages, after a project's CI failed with 434 lint errors in them.
- A plan's own checks run at verify, and the planner tries each one first. A check in pi-agent-observability's run 0e822c23 never ran, and a hub run failed with a passing build because its plan's check couldn't start.
- A pi call whose last turn ended in an error fails with pi's message. It used to surface as "never produced valid JSON" after corrections sent into the same error.
- A stopped run ends with its message, not a traceback, which read as a crash.
- A run survives a closed stdout. Run 84f61760 died when its output was piped to `head`.
- `just merge` refuses while the run's worktree holds uncommitted work, which it used to delete with the worktree, and `just discard` names what it dropped.

## Log

| Date | Last commit | What |
|---|---|---|
| 2026-11-02 | 901e7f4 | First write, covering the fork from de31374 |
| 2026-11-03 | 247ebda | Remote VMs, the ChatGPT roster in them, the VM tool check, and the pre-push secret scan |
| 2026-11-03 | 448e88c | The installer stamps the pre-push hook; agents that go quiet are stopped |
| 2026-11-05 | b72d057 | pi extensions in a VM, the SDK and stream deadlines, and pi in the operator container |
| 2026-11-05 | 5184023 | The obs relay for VM runs |
| 2026-11-05 | 7decd41 | `just merge` keeps a worktree's uncommitted work |
| 2026-11-05 | 09406c8 | The user guide under `docs/guide/`, a short README, an empty trace db no longer breaks a chain, and no fix runs after the last check |
| 2026-11-06 | 33a1ed2 | Secret files and `.env` keys kept from agents, Claude Code's own denials counted as refusals, simple SDLC ending on red checks, plan checks that compare the tree with git rejected, and `docs/open.md` |
| 2026-11-06 | c2df332 | `check_projects.py`, `levers.py` and `features.py`; the account cards with spans from 5 hours to 90 days, the statusline, each window's pace, interactive pi spend, ChatGPT's windows through Codex, the weekly archive and week table, and new model prices |
| 2026-11-07 | 3250706 | Each change that fixed something says what happened; the guards' origins, the git-directory guard, rule matching and the guard chain |

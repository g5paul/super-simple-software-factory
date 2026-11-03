# What this fork adds

This fork branched from [disler/super-simple-software-factory](https://github.com/disler/super-simple-software-factory) at de31374, from 2026-08-02. Upstream has added nothing since. By 2026-11-02 the fork was 149 commits ahead, across 151 files: about 16,200 lines added and 400 removed. A fifth of that is tests, now 335 in Python and 60 in the dashboard.

The README describes SSSF as it is now. This page says what changed from upstream and where each change was designed. To bring it up to date, read the commits since the last one listed in the log at the bottom:

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
- A Claude agent doesn't start when `claude` has no login.

### Spend and usage limits

- **Claude's usage windows.** A run stops before a Claude agent starts when a window is past `rate_limit_stop`, or when the account has moved on to usage credits. A call already running is stopped the moment Claude reports credits in use.
- **Caps in dollars.** `max_run_cost` and `max_call_cost` in the roster cap one run and one agent call. `max_live_cost` and `max_daily_cost` in repo.yaml cap what the repo's live runs spend together and what it spends in a day. `max_daily_cost` in `~/.config/sssf/limits.yaml` caps the whole machine's day. Design: [parallel-worktree-runs.md](designs/parallel-worktree-runs.md).
- **The ChatGPT plan's limit.** That route sends no usage windows, so a pi run stops by name on the plan's 429. Design: [pi-usage-stop.md](designs/pi-usage-stop.md). Research: [pi-usage-limits.md](research/pi-usage-limits.md).
- **Plans checked before the build.** `gates.plan_well_formed` sends a plan back to its planner when its two copies differ, it overwrites a committed spec, it has no verification heading, or it names no real file. A fix costs one turn instead of a build.
- **Agents that go quiet.** An agent that writes nothing for `max_silence_minutes` in repo.yaml, 15 by default, is stopped with the run. A hang spends nothing, so the caps above never catch it. Design: [silent-agents.md](designs/silent-agents.md).
- **Private repos.** The installer asks whether a repo is private and keeps its runs off remote VMs unless the engineer asks. The pre-push hook refuses a push naming a project listed in `.git/sssf-private-names`, so a public fork can install into repos it doesn't name, private or not. This hub leaves a repo unlisted unless the engineer says otherwise: [projects.md](projects.md#unlisted-projects).
- **Pricing.** A killed Claude call is priced from the output counts it streamed. `prices.json` holds Claude's list prices, a run and the dashboard warn about a model it doesn't list, and `scripts/check_prices.py` compares it with claude.com/pricing.

### A branch per run, and runs in parallel

- `--worktree` runs a request on its own branch in its own worktree. The engineer then runs `just merge`, `just rebase` or `just discard`, and `just kill` stops a run by its adw_id. Design: [branch-per-run.md](designs/branch-per-run.md).
- Worktree runs go in parallel, up to `max_worktree_runs`. Each new worktree runs repo.yaml's `worktree_setup` before its first agent. Design: [parallel-worktree-runs.md](designs/parallel-worktree-runs.md).
- `--container` runs the operator session in a Podman container. Design: [container-operator.md](designs/container-operator.md).

### Runs in a remote VM

- `--sandbox vercel` or `--sandbox e2b` runs a worktree run's agents, checks and git in a VM made for the run and deleted at its end, on a free Hobby tier. Commits come back to the host branch after each commit, and uncommitted work is saved as a patch. `just tickets` and `just trial` take it too, and repo.yaml's `sandbox:` sets a repo's default. Design: [remote-sandboxes.md](designs/remote-sandboxes.md).
- Claude Code agents sign in with a `claude setup-token` token. Pi agents on `openai/` models get a short-lived access token from the host's ChatGPT sign-in before each call, so the login never leaves the machine. Research: [pi-chatgpt-in-vm.md](research/pi-chatgpt-in-vm.md).
- Before any agent starts, the VM's git, uv, `claude`, Node and `pi` versions are read. A missing or too-old tool fails the run with each one named, and nothing runs in that VM.
- The dashboard shows each run's VM and what runs have used of each provider's allowance, as does `just sandbox-usage`.

### Two new workflows

- `adw_tickets` builds a specced feature one ticket at a time, each with a fresh builder working test first, and reviews each ticket against the repo's standards and the spec. With `--worktree`, the whole ticket run gets its own branch. Design: [ticket-sdlc.md](designs/ticket-sdlc.md).
- `adw_trial` races several rosters on one request, scores each attempt, and merges the one the engineer picks. Design: [trials.md](designs/trials.md).

### Rules the repo owns

- `adws/adw_sssf_config/repo.yaml`, committed in each repo, holds its merge style, worktree setup, spend caps and `agent_rules`.
- Every agent gets the rules in its prompt. A rule with a `forbid` pattern is enforced: a matching command stops the run, and with `on_match: refuse` it is refused before it runs and the run goes on, on Claude Code and pi agents alike.
- Every agent's environment carries `SSSF_ADW_ID`, so a repo's own scripts and hooks can tell an SSSF agent from the engineer.
- `install.py` stamps a `pre-push` hook, `.githooks/pre-push`, that scans what a push would send with gitleaks, refuses on a find, and refuses any push from an SSSF agent. Each clone turns it on with `git config core.hooksPath .githooks`. See [secret-scan.md](secret-scan.md).

### Running SSSF across several repos

- `install.py --update` and `just sssf-update` refresh a repo from the skill and keep the repo's own edits. A file both sides changed is flagged, and `--keep` or `--take` settles it. The install records the skill commit it came from.
- An onboarding cookbook sets a new repo up, and `choose_a_flow.md` says when to use one run and when to use tickets.
- `just obs-all` shows every registered repo in one dashboard. Its account panel shows Claude's usage windows and spend, pi agents' spend and the ChatGPT plan's limit. Gauges show live and daily spend against their caps, and the dashboard has views for tickets, trials, worktree branches, merges and rebases.
- [projects.md](projects.md) lists the repos running SSSF, the skill commit each one is on, and their open items.

### Smaller fixes

A code change is committed under the first build's message, not the last repair's. Each review round keeps its own file. Docs updates are part of every change. SSSF's own files stay out of the feature reviews and the documenter's diff. A pi call whose last turn ended in an error fails with pi's message. A stopped run ends with its message, not a traceback.

## Log

| Date | Last commit | What |
|---|---|---|
| 2026-11-02 | 901e7f4 | First write, covering the fork from de31374 |
| 2026-11-03 | 247ebda | Remote VMs, the ChatGPT roster in them, the VM tool check, and the pre-push secret scan |
| 2026-11-03 | 448e88c | The installer stamps the pre-push hook; agents that go quiet are stopped |

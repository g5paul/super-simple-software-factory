# Forks of disler's SSSF

Surveyed 2026-11-02. Who else is building on disler/super-simple-software-factory, and does anyone overlap with this fork's work?

## Short answer

Many forks, few active, none doing the whole of this one. 272 forks, 45 with commits of their own. Nobody has remote VM sandboxes, pi on a ChatGPT subscription, refuse rules, trials or this fork's cost caps. Claude Code on a subscription is the common addition, in about a dozen forks. No fork copies this one.

## How it was measured

- `gh repo view` gave the fork count, 272. GitHub's forks page, sorted by last update, has 11 pages. Pages 1 to 6 and 11 were read. From page 3 on, every fork says "Never updated", so pages 7 to 10 were taken to say the same; the page totals add up to 272.
- Every branch of the 50 updated forks was fetched into one scratch bare repo, and commits were counted with `git rev-list` against disler's `main`. GitHub's "N commits ahead" line doesn't come through a page fetch. One count was checked against GitHub's own: tviles, 49 of its own.
- Features come from commit subjects and greps of the files, not from reading the code closely. Treat each as a lead to check.
- `gh api` wasn't used: this machine's deny rules block it (`docs/secret-scan.md`). No fork's code was run.
- Not seen: private forks, tags, and commits a fork pushed and later deleted.

## Counts

- Disler's `main` is one commit, de31374, from 2026-08-04. It hasn't moved, so no fork is behind it.
- 50 forks have pushed since they were made. Four of those have no commits of their own, so 45 do, besides this one.
- 222 forks were never updated after forking.
- This fork's public `main` was 48cc657, 150 commits ahead, when surveyed. The commits since were local then, while pushes were on hold.

## Active forks

"Own" counts commits across all branches. "Main" counts how far `main` is ahead of disler's.

| Fork | Last push | Own / main | What it adds |
|---|---|---|---|
| GratefulDave/maestro | 2026-10-23 | 363 / 334 | A rewrite. An approved plan becomes a graph of lanes. Tests are written, reviewed and sealed first, then the builder works against tests it can't change. Lane tests run on GitHub Actions machines. Budget floors, liveness tracking, restart recovery |
| schurik/software-factory-one | 2026-09-15 | 127 / 110 | Issue-driven runs with GitHub label states, PR watchers, a human approval gate, a scout before the planner, worktrees, `doctor` |
| joystick | 2026-10-11 | 120 / 120 | Mostly docs: an adoption playbook, vendoring other skills into the factory, monorepo notes |
| jflavan | 2026-09-30 | 119 / 119 | Stack profiles (.NET, Svelte) the installer detects, prompt overlays per framework, doc gates, Windows fixes |
| tviles/factory | 2026-09-22 | 49 / 49 | A Claude Code adapter: auth preflight, a watchdog for silent hangs, a stop the moment `isUsingOverage` appears, and a refusal up front when the rate-limit window can't finish the chain. Records `permission_denials` and a `cost_basis` |
| przemek64 | 2026-09-24 | 47 / 4 | An issues ledger, PR-review families, write guards, Windows support, Codex agents on gpt-5.5 |
| ryancatlin | 2026-08-06 | 41 / 41 | An app validation stage judging screenshots and diffs, a stall watchdog, a self-healing updater, failure triage. Dashboard moved to React |
| iksnae | 2026-09-10 | 37 / 37 | One shared factory checkout across projects instead of a copy in each. A Codex CLI harness, `doctor --probe`, limits on what the shell may read |
| Woodshape | 2026-08-11 | 36 / 31 | Nested pi subagent observability, a Codex roster |
| gauravdhiman | 2026-09-18 | 35 / 35 | A worker contract with Claude Code, Codex, OpenCode and other backends. A worktree and branch per run, plan gates, "sealed handoffs", a run manifest |
| pl018 | 2026-08-13 | 31 / 31 | Claude Code and Codex on subscriptions, a stall watchdog, `just kill`, a live transcript monitor, no git commit or push for the builder |
| kdridi | 2026-08-09 | 31 / 31 | Documentation runs that check every symbol |
| bossjones | 2026-10-29 | 30 / 27 | pi replaced by the GitHub Copilot SDK, packaged as a Copilot plugin |
| viktorcirbus | 2026-08-30 | 26 / 26 | A macOS operator console |
| craverath/SSSF-AI-SDCL | 2026-09-09 | 26 / 26 | Adapters for Claude Code, Codex, Kiro and Antigravity, spend per agent in each one's billing unit |
| AuRousKiNN | 2026-09-27 | 24 / 24 | Codex instead of pi, ticket decomposition, an evidence freshness gate |
| BruthaVoodoo | 2026-09-13 | 20 / 20 | A roadmap: safety hardening, a dashboard redesign. Stays on pi |
| ChenReuven | 2026-08-15 | 20 / 0 | A Claude Code agent, a start on a TypeScript port |
| MadeSimpleWorkshop | 2026-08-22 | 17 / 0 | An intake cookbook, an upgrade manifest, acceptance tests agents can't edit |
| sergei-iakovlev | 2026-08-08 | 14 / 14 | Task files with blocked-by, a worktree per task, GitLab merge requests |
| bishalu | 2026-09-07 | 12 / 0 | One engine shared across repos, a "spend guard", a "sandbox guard" (commit subjects only) |
| roanurquhart/software-factory | 2026-10-12 | 7 / 7 | A Claude roster, a Playwright UI tester |
| BiTy1024 | 2026-10-29 | 6 / 0 | Headless Claude Code beside pi, a usage view |

The other 22 have between 1 and 11 commits each: fixes, CI, small dashboard changes, a Grok agent, a 3D dashboard experiment, a model default.

## Overlap with this fork

| This fork | Elsewhere |
|---|---|
| Remote VMs on Vercel and E2B, and the Podman container | None. Every fork has "vercel" and "e2b" in text inherited from upstream; no code. GratefulDave runs tests on GitHub Actions machines; iksnae and craverath rely on `codex --sandbox read-only` |
| pi on a ChatGPT subscription | None. About eight forks reach OpenAI through the Codex CLI instead |
| `agent_rules` that refuse commands | None in this form. Narrower guards: shell read limits (iksnae), no builder push (pl018), protected factory files (TheQuietNet), write guards (przemek64, ryancatlin) |
| Cost caps per run and per day | None like these. bishalu's "spend guard" and GratefulDave's budget floors are the nearest |
| Trials racing rosters | None |
| Claude Code on a subscription | About a dozen. tviles is closest, with rate-limit stops much like `rate_limit_stop` |
| Ticket runs, a branch per run | Several, each its own design: AuRousKiNN, schurik, sergei-iakovlev, przemek64, gauravdhiman, GratefulDave |
| `install.py --update` across repos | Updaters in ryancatlin, MadeSimpleWorkshop and schurik. bishalu and iksnae share one engine instead of a copy per repo. None has a hub handing work to project sessions |

## Copies of this fork

None. No fork shares a commit with g5paul's `main` beyond disler's. No commit author is George Paul or g5paul. No fork has terms only this fork uses: `agent_rules`, `rate_limit_stop`, `max_run_cost`, `max_daily_cost`, `adw_trial`, `adw_tickets`, `SSSF_ADW_ID`, `install.py --chatgpt`. Nobody has forked g5paul's fork.

## Worth a look

Each is a lead from commit subjects and file greps. Check it against what SSSF already has before building it.

- **tviles:** refuse a chain before it starts when the rate-limit window can't finish it. Record `permission_denials` from Claude Code's result in the trace. A `cost_basis` field, so the dashboard doesn't show dollars it can't know.
- **pl018, ryancatlin:** a watchdog that stops an agent once it goes silent.
- **MadeSimpleWorkshop, GratefulDave:** acceptance tests the builder can't edit.
- **iksnae:** `doctor --probe`, which asks the provider what it serves instead of trusting the model catalog.
- **gauravdhiman:** plan gates that reject a malformed plan before the build.
- **sarjarapu:** a persistent Claude Code process, and a smaller system prompt and tool list per call.

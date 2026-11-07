# SSSF Overview

The system map the orchestrator reads on startup — what SSSF is, how a stamped repo is laid out, and which cookbook to load next.

## What SSSF is

Super Simple Software Factory builds repeatable **agents plus code** workflows. Deterministic Python (an ADW script) owns sequencing, retries, and acceptance; agents are bounded nodes inside that graph. Agent proposes, code disposes.

Your job as orchestrator: **run the system, observe the system, help the engineer interact with it.** You do not do the work an ADW exists to do.

## Layout of a stamped repo

```
adws/
├── adw_sssf_config/
│   ├── sssf.config.yaml         the agent roster: one agent, one prompt, one purpose
│   ├── repo.yaml                the repo's own rules: agent_rules, merges, cost caps, sandbox, worktree setup
│   └── stamp.json               a hash of every stamped file and the skill commit, for --update
├── adw_prompt.py                smallest ADW: one agent, one prompt, traced end-to-end
├── adw_plan.py, adw_scout.py, adw_build.py, adw_plan_build.py, adw_build_test.py, adw_plan_build_test.py
├── adw_plan_build_test_quality.py   plan, build, then quality.py checks and tests in a bounded fix loop
├── adw_quality.py               lint, typecheck and build the project, no agents
├── adw_build_review.py          build, then review: is this what was asked for? Not testing
├── adw_document.py              write up the work just done, from git diff vs main
├── adw_simple_sdlc.py           plan, build, test, review, document; commits each product
├── adw_tickets.py               build an approved feature one ticket at a time, test-first
├── adw_trial.py                 one request under several rosters, a panel judges, the best one merged
├── adw_worktree.py              land or drop a run started with --worktree
├── adw_kill.py                  stop a run by adw_id, the chain first, then its coding agents
├── adw_sandbox_usage.py         what remote VM runs have used of Vercel's and E2B's free allowances
├── adw_modules/                 ALL low-level logic; ADW scripts stay thin
│   ├── data_types.py            AgentCall, PhaseParams, Phase, Envelope + one output type per agent call
│   ├── agents.py, runner.py, session.py, prompts.py   roster, the Run object, adw_id sessions, prompt rendering
│   ├── agent_pi.py, agent_cc.py the Pi and Claude Code interfaces
│   ├── gates.py, plan_checks.py gate(envelope, run) -> GateReport; the plan's own checks
│   ├── quality.py               the repo's fixed checks: test, lint, format, typecheck, build. Yours to edit
│   ├── changes.py, git_helper.py, worktree.py   git diff into an envelope, git plumbing, worktree runs
│   ├── permissions.py, rules.py, secret_files.py, sssf_refuse.py, sssf_rules.ts   what an agent may change, run and read
│   ├── budget.py, account.py, prices.py, prices.json, silence.py, kill.py   cost caps, usage limits, quiet agents, stopping runs
│   ├── sandbox*.py, obs_relay.py, sssf_obs_relay.cjs   where a run's tree lives: local, Vercel or E2B VM
│   ├── tickets.py, trial.py, repo_settings.py   feature folders, trials, repo.yaml
│   └── tracer.py, console.py, utils.py   the trace db, the terminal narrative, small helpers
└── adw_data/
    ├── prompt_engineering/{agent}/{system.md,user.md}   tracked: edit prompts HERE, never in the skill
    │                                planner · builder · scout · reviewer · documenter · ticket_builder
    │                                standards_reviewer · spec_reviewer · referee, for trials
    ├── harness_engineering/     tracked pi extensions: subagents.ts, themeMap.ts
    ├── sessions/{adw_id}/                               gitignored runtime
    │   ├── agent_map.json       agent → coding-agent session_id + model
    │   ├── context_handoff/     the one place agents write files for the agents that follow
    │   └── {agent}/{prompts/, raw_output.jsonl, envelope.json}
    └── sssf.db                  gitignored SQLite trace db the visualizer polls
justfile                         starter recipes, or sssf.just as a module when the repo has its own
.env.sample, .githooks/pre-push  env template; the secret scan before every push
```

**Two coding agents.** `coding_agent: pi` is the default, with model `gemini-3.6-flash` and thinking `medium`. `coding_agent: claude_code` runs `anthropic/*` models through the `claude` CLI on the operator's subscription. See `references/config.md` for how the roster maps onto it.

## The phase model

Every ADW run is a sequence of **phases**, each one `with run.phase(PhaseParams(...))`. Three kinds, three swim lanes:

- **engineer** — the human lane; today the system-input phase (who asked, and for what).
- **agent** — `ph.call(AgentCall(...))`: prompt in → typed envelope out → gates verified.
- **code** — deterministic steps that stand alone (git branch, git commit, migrate). Never buried inside an agent phase.

**Success must be earned — every phase defaults to `fail`.** A clean exit flips it to success; agent phases additionally require the envelope to parse and all gates to come back green. A raise keeps it failed, records an error event, and aborts the run. `retries=N` on an agent phase buys extra gate-correction rounds through the same session before that raise happens.

## Envelopes

Agents have exactly two output channels: reference files written into `context_handoff/`, and a **final valid-JSON response** parsed against the output type the call declared. Code persists it as `envelope.json` and injects it into the next agent's `user.md` via `{{previous_envelope}}`. Bad JSON is never a restart — the harness re-prompts the *same session, context intact*, until it parses (bounded). See `references/handoff.md`.

**The output contract is a synced triad**: the type in `data_types.py` ↔ the `## Report` JSON example in the agent's `user.md` ↔ `output_type=` at the call site. Editing any one of the three means editing all three in the same change — drift between them taxes every call with correction retries.

## Running an ADW

```bash
uv run adws/adw_plan.py "add a /health endpoint"
uv run adws/adw_plan_build.py requests/health.md --adw-id a1b2c3d4
```

The prompt is inline text or a file path. `--adw-id` is optional on every ADW: given one, the run joins that session (same dirs, same `context_handoff/`, agents resume their existing context windows); omitted, a fresh id is minted and printed.

## When you have finished reading this

You are done with startup. List the ADWs (`ls adws/adw_*.py`, plus each `Phases:` docstring line) as a table, and **wait for the engineer's request.**

Do not survey anything else — not the trace db, not the config, not past runs, not the repo tree. You do not yet know what the request is, so anything you gather now is a guess about what will matter, spent from the context the real work needs. Every cookbook and reference below is lazy-loaded, one per request, and that is the whole design.

## Where to go next

Load one cookbook per request — this overview is the only one you read up front.

| Request | Cookbook |
|---|---|
| Bring SSSF into a new project, up to the first reviewed run | `onboard.md` |
| Choose one run or a ticket feature | `choose_a_flow.md` |
| Build a feature from a spec and tickets | `tickets.md` |
| Run one request under several rosters and merge the best | `trial.md` |
| Turn a request into the prompt an ADW gets | `how_to_prompt_for_the_eng.md` — **read before every launch** |
| Set the system up in a repo | `install.md` |
| Write a new ADW script | `create_adw.md` |
| Change an existing ADW chain | `update_adw.md` |
| Generate `sssf.config.yaml` | `create_config.md` |
| Add or retune an agent | `update_config.md` |
| Add low-level logic or a gate | `update_modules.md` |
| Run and monitor a workflow | `how_to_prompt_for_the_eng.md`, then `run_adw.md` |

References, loaded when you need the spec: `references/config.md` (full config schema), `references/handoff.md` (envelope + session layout), `references/observability.md` (events, db tables, polling).

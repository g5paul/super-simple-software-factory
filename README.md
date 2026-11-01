# Super Simple Software Factory

> **Repeatable agents-plus-code workflows, packaged as one skill, stamped into any repo.**
> Deterministic Python owns the graph. Coding agents are bounded nodes inside it.

📺 Full breakdown on YouTube: **[Super Simple Software Factory](https://youtu.be/haUfb1ievTE)**

<p align="center">
  <img src="images/00_swimlane_waterfall.svg" alt="A run as swim lanes: engineer, code, planner, builder, and reviewer phases laid on a time axis, each block labelled with its duration, one phase still running and the next still queued" width="850">
</p>

<p align="center">
  <img src="images/01_factory_spine.svg" alt="A run spine: engineer, agent, and code phases on a deterministic rail, every event dropping into a SQLite trace db that the UI polls" width="850">
</p>

A software factory does one thing: it gives you more leverage on your prompt. How much leverage depends entirely on what you invest in it. At the low end you chain two agents together and hope. At the high end you build a system of agents plus code that runs without you, and does the job about as well as you would.

Everyone can get an agent to write code once. Almost nobody gets the same result twice. This fixes that by moving the control plane out of the prompt and into Python. An ADW script (AI Developer Workflow) owns sequencing, retries, and acceptance. Agents work inside named phases. Typed JSON envelopes carry context across the seams. Every event streams into SQLite while it is still happening. **Agent proposes, code disposes.**

> [!NOTE]
> **This repo is the skill**, which is the thing you install, plus `docs/designs/`, the design records for what this fork added to it.

---

## Why this exists

<p align="center">
  <img src="images/02_control_plane.svg" alt="Left: one big agent owning its own loop with no phase boundary and no acceptance. Right: code owning the loop with agents as bounded, gated nodes" width="780">
</p>

Hand a capable model your whole SDLC and you get a machine with no seams. There is no phase boundary, so you cannot say which step failed. There is no acceptance criterion you can name, so "done" means "the agent stopped talking." A retry is a cold start that throws away everything the agent just learned. The only trace is a transcript you have to read like a novel. Run it twice, get two different systems.

The fix is not a better prompt. The fix is deciding, deliberately, that **code owns sequencing, retries, and acceptance, and the agent owns only the work inside one bounded phase**. Everything else falls out of that one line. Phases become the unit of the trace. Envelopes become the only way context crosses a seam. Gates become the definition of done. A correction becomes cheaper than a restart, because the session is still alive.

### Agents are great. You do not always need one.

This is the part most engineers are going to skip, and pay for later.

Code costs nothing. It runs at the speed of light. You can change it in a second. And you actually own it, which is not true of any model you are renting by the token.

So when the invocation is already known, write it down. `bun test` is not a judgement call. Neither is `ruff check`. An agent rediscovering your test runner burns a context window to learn what a subprocess already knows, and it charges you for the privilege every single run. Worse, it puts a passing test suite into a context window, which buys you nothing at all.

Agents are for the parts that need reading and deciding. Everything else is a `kind="code"` phase. When code fails, the failure comes back to the builder as an envelope, through the same door an agent's report would have used. The repair loop is identical. You just stopped paying an agent to do arithmetic.

The bill for skipping this is not only tokens. It is cost, speed, and consistency, and you pay it on run one hundred and run one thousand, not on run one.

> *Same models. Same prompts. The difference is who owns the loop.*

---

## Install

Two steps: get the skill into your repo, then stamp the factory.

### Agentic Install

Copy `.claude/skills/sssf/` into the target repo and type `/sssf install` inside Claude Code. The skill is named `sssf`, so that is the skill name followed by the `install` argument. There is no bare `/install` command. The agent reads the skill's own `cookbooks/install.md` and does the rest.

### Manual Install

**Prereqs:** [`uv`](https://docs.astral.sh/uv/), `sqlite3`, and one of three ways to run agents:

- a Claude subscription, with [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in (`claude auth status`). No API key.
- a ChatGPT subscription, with [`pi`](https://github.com/mariozechner/pi-coding-agent) signed into it (`/login` inside Pi, then pick `openai`). No API key.
- [`pi`](https://github.com/mariozechner/pi-coding-agent) and an API key for each provider your roster names. See [Which API keys you actually need](#which-api-keys-you-actually-need).

For the first two, see [Run on a subscription](#run-on-a-subscription).

[`bun`](https://bun.sh) only if you want the visualizer.

```bash
# 1. get the skill into the target repo
mkdir -p .claude/skills
cp -r /path/to/super-simple-software-factory/.claude/skills/sssf .claude/skills/

# 2. stamp the factory (run from the target repo ROOT, the cwd is where everything lands)
#    on your Claude subscription, no API key:
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
cp .env.sample .env                              # leave the API keys out
#    or on pi with your ChatGPT subscription:
#      uv run .claude/skills/sssf/scripts/install.py --chatgpt
#      cp .env.sample .env                       # then set SSSF_PI_AUTH=subscription
#      pi auth check --provider openai --json    # must say "authType": "oauth"
#    or on pi with API keys:
#      uv run .claude/skills/sssf/scripts/install.py
#      cp .env.sample .env                       # then set OPENROUTER_API_KEY
#      pi --version                              # confirm pi is on PATH, or set PI_PATH in .env
git init && git commit --allow-empty -m init     # chains that end in a commit phase need a repo

# 3. smoke test: two cheap read-only runs, end to end
just demo
just sessions              # what just happened
just obs                   # the trace UI, needs bun
just obs-all               # one dashboard for every repo SSSF is installed in

# no just? every recipe is one line. the raw form of `just demo` is:
uv run adws/adw_prompt.py "reply with a one-line summary of this repo" --agent scout
```

Re-running `install.py` is safe. It skips every file that already exists and reports what it skipped, so a second run doubles as a drift check. To bring a repo up to a newer skill, set `SSSF_SOURCE` in its `.env` and run `just sssf-update`, which refreshes the copy of the skill and runs `install.py --update`. It replaces only the files the repo never changed and lists the rest. `--force` overwrites **all** stamped files including your `sssf.config.yaml`, `quality.py`, and your prompts, so commit first. See `cookbooks/install.md`.

To run the operator session in a sandbox, add `--container` when you install. It also stamps `container/`, a Podman image and a `run.sh` that starts Claude Code inside it with only the repo mounted. It works with Claude Code rosters only. See `cookbooks/install.md`.

To bring SSSF into a new project, start a Claude session in that repo and point it at `cookbooks/onboard.md`. It checks whether SSSF fits, installs it, sets `quality.py` to the commands CI runs, writes `docs/sssf.md`, and makes a first small run.

Green on the smoke test means the whole path works: config validated, session minted, the agent ran, envelope parsed, events landed in `adws/adw_data/sssf.db`. Fix it there before composing anything larger, because every multi-agent chain rides this exact path.

### Run on a subscription

SSSF can run its agents on a Claude subscription through Claude Code, or on a ChatGPT subscription through Pi. Either way, there are no keys in `.env` and no bill per token.

**What both give you.**

- **A flat price.** The subscription covers the runs. What limits you is the plan's usage, not money.
- **No key can bill the API by accident.** `.env` is loaded into every ADW, so a key in it would reach every agent. SSSF keeps it out, so a lapsed sign-in fails the run instead of quietly falling back to the API. Claude Code agents get this by default. Pi agents get it with `SSSF_PI_AUTH=subscription` in `.env`, which keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from them.
- **Dollar figures are list price, not a charge.** Every cost SSSF shows, in the console, the trace, and the dashboard, is what the tokens would cost on the API. You aren't charged it. `max_run_cost` still matters: it limits how much of your plan's usage one run can take.
- **Spend on the dashboard.** The account panel in `just obs-all` shows what SSSF's agents spent, by project and model.

**Claude, through Claude Code.** Install with `--coding-agent claude_code` and every agent runs on the account `claude` is logged into.

- **You can see the usage windows.** The account panel shows how full the 5-hour and 7-day windows are, when each resets, and which projects and models filled them. It counts your interactive sessions too.
- **A run stops before the account runs out.** Before each agent starts, SSSF checks the windows and stops the run cleanly at `defaults.rate_limit_stop` (80% by default), with the reset time. That beats a run that dies halfway through a phase.
- **A runaway call ends itself.** Each agent's `max_call_cost` goes to `claude -p --max-budget-usd`.
- **The agents stay isolated.** Each call runs with `--safe-mode`, so an agent never loads your own `CLAUDE.md`, skills, hooks, or MCP servers. It sees its prompt and the repo.
- If Claude still reports an API key as its auth source, the run stops before the model is called. Set `SSSF_CLAUDE_AUTH=api_key` if you do want the API.

**ChatGPT, through Pi.** Sign Pi into ChatGPT once: run `pi`, then `/login`, and pick `openai`. `pi auth check --provider openai --json` should report `"authType": "oauth"`. Install with `--chatgpt`, which stamps a Pi roster with the ChatGPT models in the table below. Set `SSSF_PI_AUTH=subscription` in `.env`.

- SSSF can't see ChatGPT's usage windows. Pi's ChatGPT sign-in calls OpenAI's public API through token sharing, which sends none with its responses; only the Codex backend does. So the panel shows spend only, and `rate_limit_stop` doesn't cover a Pi run. Check your plan's limits in ChatGPT or Codex. See [`docs/research/pi-usage-limits.md`](docs/research/pi-usage-limits.md).
- There is no per-call cap on Pi. `max_run_cost`, checked before every send, and `max_live_cost` and `max_daily_cost` in `repo.yaml` are its cost limits.

| | Claude via Claude Code | ChatGPT via Pi |
|---|---|---|
| Usage windows on the dashboard | yes | no |
| Run stops before the plan runs out | yes, `rate_limit_stop` | no |
| Per-call cost cap | yes, `max_call_cost` | no |
| Run cost cap | yes, `max_run_cost` | yes, `max_run_cost` |
| Cap on what live runs spend together | yes, `max_live_cost` | yes, `max_live_cost` |
| Cap on what the repo spends in 24 hours | yes, `max_daily_cost` | yes, `max_daily_cost` |
| Spend on the dashboard | yes | yes |
| Key guard | on by default | `SSSF_PI_AUTH=subscription` |

**Models as of 2026-10-29.** The stamped `sssf.config.yaml` is the source of truth, and new models will make this table stale. The Claude column is the roster that `install.py --coding-agent claude_code` stamps, used for every Claude run so far. The ChatGPT column is the roster that `install.py --chatgpt` stamps. gpt-6.1-sol ran every agent in one simple-sdlc run. For the scout and documenter, gpt-6.1-terra and gpt-6-luna were then tried against sol on one small feature. All three were correct. Terra cost about a quarter of sol and kept nearly all the detail. Luna cost about a twentieth but left detail out.

| Agent | Claude via Claude Code | ChatGPT via Pi (tested) | Thinking |
|---|---|---|---|
| planner | `anthropic/claude-opus-5-5` | `openai/gpt-6.1-sol` | high |
| builder, ticket_builder | `anthropic/claude-sonnet-5-5` | `openai/gpt-6.1-sol` | medium |
| reviewer, standards_reviewer, spec_reviewer | `anthropic/claude-opus-5-5` | `openai/gpt-6.1-sol` | high |
| scout | `anthropic/claude-haiku-5-5` | `openai/gpt-6.1-terra` | medium |
| documenter | `anthropic/claude-haiku-5-5` | `openai/gpt-6.1-terra` | medium |

The Claude roster gives the reviewers a different model from the builder, so they don't share its blind spots. In the ChatGPT column the reviewers share the builder's model, so they can't have that.

**The trade-offs.**

- SSSF runs draw on the same plan as your own use. A long unattended run can leave less for you, and a busy day at the keyboard can stop a Claude run. Nothing warns or stops a ChatGPT run before the plan runs out.
- Each subscription covers its own provider's models. A roster that mixes in other providers needs Pi and API keys for them.
- Claude's interactive sessions don't report the windows, so the reading SSSF checks is as fresh as its last agent call. `/usage` in any Claude Code session gives the current figure.

See `references/config.md` for the details.

### Which API keys you actually need

This section is for Pi agents on API keys. On a Claude or ChatGPT subscription, those agents need no key.

That depends on your roster, not on this repo. Every `model:` in `sssf.config.yaml` is written `provider/model-id`, and the provider half decides the key. Which key pi reads for a given provider comes from `~/.pi/agent/models.json`.

The starter roster deliberately mixes providers to show the point, so out of the box it wants three:

| Model in the starter roster | Provider | Key |
|---|---|---|
| `google/gemini-3.6-flash` (default, builder, scout) | served via openrouter | `OPENROUTER_API_KEY` |
| `fireworks/accounts/fireworks/models/kimi-k3` (planner) | fireworks | `FIREWORKS_API_KEY` |
| `openai/gpt-5.6-terra`, `openai/gpt-5.6-luna` (reviewer, documenter) | openai | `OPENAI_API_KEY` |

**Want one key instead of three?** Delete the per-agent `model:` lines and let every agent inherit `defaults.model`. The whole roster then runs on one provider. Cheapest way to get a first green run.

One sharp edge worth knowing: `agents.validate()` checks that a model is *written* as `provider/id`, not that the provider is reachable or that its key is set. A missing key does not fail at startup. It fails when that agent runs, partway into a chain.


---

## Three principles

Everything here is built to be **observable**, **customizable**, and **reusable**. Those are not adjectives, they are the reason the parts are shaped the way they are.

**Observable.** If you cannot measure your agents, you cannot improve them. Every event goes into SQLite as it happens, so you can watch a run mid-flight, not read about it afterwards.

**Customizable.** One YAML file sets the core four for every agent: context, model, prompt, tools. Different models at different price and speed points, in the same run. It is not about which model is best anymore, it is about which model is right for that one phase.

**Reusable.** The whole thing is a skill you stamp into any repo, then bend to fit. The tests it ships are not your tests. The prompts it ships are starters. It is designed to be edited.

There are three actors here, and the design keeps them separate on purpose: **the engineer**, **the code**, and **the agents**. The trick is not running more agents. The trick is using all three at the right moment.

---

## The skill is the product

<p align="center">
  <img src="images/03_skill_stamp.svg" alt="The sssf skill directory on the left stamping config, adws, and prompt_engineering into three different target repos" width="780">
</p>

Everything lives in `.claude/skills/sssf/`. `SKILL.md` carries the hard rules and routes each request to one of twelve cookbooks. `references/` holds the deep specs, `scripts/` holds the generators, `templates/` holds exactly what gets stamped.

| What lands in your repo | Where it comes from | Tracked |
|---|---|---|
| `adws/adw_sssf_config/sssf.config.yaml` | `templates/sssf.config.yaml` | yes, it is your agent roster |
| `adws/adw_*.py` | `templates/adws/` | yes, thirteen starter workflows, plus `adw_kill` and `adw_worktree` |
| `adws/adw_modules/` | `templates/adws/adw_modules/` | yes, all low-level logic |
| `adws/adw_data/prompt_engineering/` | `templates/prompt_engineering/` | yes, **your prompts live here** |
| `adws/adw_data/harness_engineering/` | `templates/harness_engineering/` | yes, pi extensions |
| `.env.sample` | `templates/env.sample` | yes |
| `justfile` | `templates/justfile` | yes, starter recipes to run and watch |
| `adws/adw_sssf_config/repo.yaml` | `templates/repo.yaml` | yes, the repo's own SSSF rules, such as `merge: ff_only` |
| `adws/adw_sssf_config/stamp.json` | written by `install.py` | yes, the hash of every stamped file, so `--update` can tell your edits from old copies |
| `container/` | `templates/container/`, only with `--container` | yes, the operator's Podman sandbox |
| `adws/adw_data/sessions/`, `sssf.db`, `worktrees/` | created at runtime | no, gitignored |

The prompts are yours the moment they land. Edit them in `adws/adw_data/prompt_engineering/{agent}/`, never back inside the skill.

There is no DSL here. No framework to learn. It is Python, YAML, agents, and a skill, which is exactly what these models are already trained on. Staying in distribution is a feature.

---

## The agent roster

`adws/adw_sssf_config/sssf.config.yaml` answers one question per entry: who is this agent. One agent, one prompt, one purpose.

```yaml
defaults:
  coding_agent: pi                 # pi | claude_code
  model: google/gemini-3.6-flash   # provider/model-id, a bare id can match several providers
  thinking: medium                 # off | minimal | low | medium | high | xhigh | max
  protected_files:                 # no agent may edit the machinery that grades it
    - adws/adw_modules/
    - adws/adw_sssf_config/
    - adws/adw_*.py
  data_dir: adws/adw_data

agents:
  - name: planner
    model: fireworks/accounts/fireworks/models/kimi-k3
    thinking: high                 # per-agent overrides win over defaults
    color: "#a78bfa"               # this agent's lane swatch in the trace
    purpose: Turn a request into a plan the builder can implement without asking questions.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/planner/system.md
      user: adws/adw_data/prompt_engineering/planner/user.md
    harness_engineering:
      - adws/adw_data/harness_engineering/subagents.ts   # this agent can spawn subagents
    writes:                        # the plan is all it may leave in the repo
      - specs/
```

Five starter agents ship in the box: `planner`, `builder`, `scout` (read-only recon), `reviewer`, and `documenter`. There is no tester, because running a suite is a known command and therefore code.

Every agent gets its own model, thinking level, prompts, tools, and harness. That is the core four, and it is the whole surface you tune. Give the planner a frontier model and the builder a cheap fast one. Give the scout subagents. Give the reviewer no ability to write code at all.

**`tools` is a capability list. `writes` is the boundary.** They are not the same thing, and the difference matters: `bash` runs anything, including `git checkout`, and `write` reaches any path. So "this agent changes nothing" is enforced in code, after every call, by comparing the repo before and after. Unauthorized changes are rolled back and the phase fails. A read-only agent is read-only with respect to your repo, never unable to write its own report.

Config defines who an agent **is**. The ADW call site defines how it is **used**. That split is what lets one agent serve many different calls. **ADW scripts never name a model, they name an agent.**

---

## Phases: three lanes, one primitive

<p align="center">
  <img src="images/04_phase_lanes.svg" alt="Swim lanes for engineer, git, planner, builder, and reviewer with phase blocks placed on a time axis and one dashed queued block" width="780">
</p>

Every run is a sequence of phases, and every phase is the same context manager no matter who owns it.

```python
REQUIRED_AGENTS = ["planner", "builder", "reviewer"]   # names, never models

cfg = agents.load_config(config)
agents.validate(cfg, REQUIRED_AGENTS)   # a missing agent fails before anything spawns
run = session.ensure(cfg, adw_id)       # pin-or-create the session

with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                           description="Turn the request into an implementable plan")) as ph:
    plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                             gates=[gates.artifacts_exist, gates.files_non_empty]))

with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                           description="Commit the working tree")) as ph:
    message = build.commit_message or f"sssf({run.adw_id}): {build.summary}"
    ph.log(sha=git_helper.commit_all(message), message=message)

return run.finish(accepted=review.approved, reason="the reviewer never approved")
```

Three kinds, three swim lanes. **engineer** is the human lane. **agent** is `ph.call(...)`: prompt in, typed envelope out, gates verified. **code** is a deterministic step that stands on its own, like a commit or a migration, and it is never buried inside an agent phase, so the trace shows exactly when code ran and when an agent was working.

That commit phase is the whole pattern in miniature. The builder proposes the message as a field on its envelope. Code decides whether to use it, falls back when it is empty, and performs the write. The agent never runs `git commit` itself.

**Success must be earned.** Every phase defaults to `fail`. A clean exit flips it, and an agent phase also needs its envelope to parse and every gate to come back green. `run.finish(accepted=...)` adds the second question, because phases passing is not the same as the run being acceptable: a test phase that ran a red suite did its job perfectly. One call settles the exit code, the session status, and the banner together, so they cannot disagree.

---

## Envelopes and gates

<p align="center">
  <img src="images/05_envelope_gates.svg" alt="An agent's final JSON parsed against its output type, checked by gates, with violations looping back into the same session as a correction" width="780">
</p>

An agent has exactly two output channels: reference files written into `context_handoff/`, and a final valid-JSON response parsed against the output type the call declared. Code persists that response as `envelope.json`, records it, and injects it into the next agent's prompt. Context transfers in code, not in conversation.

```python
class EnvelopeBase(BaseModel):
    status: Literal["success", "fail"]
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    notes_for_next_agent: str = ""

class BuildOutput(EnvelopeBase):
    changed_files: list[str] = Field(default_factory=list)
    commit_message: str = ""        # consumed by the git commit phase
```

Determinism is wired into every step. Agents must return a specific structure, every time. If it does not parse, they get asked again until it does.

Gates verify claims, never predictions. Nobody knows which files an agent will touch before it finishes, so gates run **after** the fact against the envelope's own declarations: `artifacts_exist`, `files_non_empty`, `json_parses`, `diff_matches_claims`, `tests_pass(...)`. A gate is a callable with the signature `gate(envelope, run) -> GateReport`, one `check(item, ok, note)` per thing it examined, so a green gate tells you *what* it verified.

When JSON does not parse or a gate returns violations, **nothing restarts**. The harness re-prompts the same session with a correction naming exactly what was wrong, and the context window stays intact. Pi treats `--session-id` as create-or-continue, so running an agent and continuing it are the same call. A cold restart throws away everything the agent learned. A correction costs one message.

The output contract lives in three places and they are one thing: the type in `data_types.py`, the JSON example in that agent's `user.md` `## Report` section, and `output_type=` at the call site. **Change one, change all three in the same edit.**

---

## The trace

<p align="center">
  <img src="images/06_trace_path.svg" alt="Running agents to tracer.py to a WAL SQLite db with seven tables, read by a cursor poll query, with no websocket and no ingest endpoint" width="780">
</p>

One data path, no exceptions: **agents write to SQLite, readers poll SQLite.** `agent_pi.py` tails the coding agent's JSONL stdout line by line and the tracer inserts each event while the agent is still working, so tool calls are visible mid-run instead of batched at the end.

Ten event types land across seven tables: `sessions`, `phases`, `events`, `envelopes`, `gate_results`, `agent_sessions`, and `processes` (adw_id to pid, so a stuck run can be found and stopped). Every event logs against both its `adw_id` and its `phase_id`, and `parent_id` nests spans, so an agent phase expands into its own tool calls.

Pi announces a tool call across three raw events, so the interface folds them into exactly **one** `tool_call` row per real call. Each row is named the way you would read it aloud (`bash: ls -la src`) and carries `{tool, tool_call_id, args, result_snippet, ok, duration_ms, agent}`.

```sql
select * from events where adw_id = ? and rowid > ? order by rowid limit 500;
```

That one cursor query is the entire transport. Live view and full history are the same query at different cadence, which is why there is no ingest endpoint, no WebSocket, no backfill, and no separate replay path. Every connection opens WAL, so reads never block the running writers.

Files stay the raw record (`raw_output.jsonl`, `envelope.json`, `agent_map.json`). The db is the queryable mirror. Losing it loses nothing you cannot rebuild.

The skill ships a read-only UI for this db at `.claude/skills/sssf/apps/visualizer/`: Vue and Vite served by Bun, with sessions, a trace waterfall, and per-phase tool-call detail. A `--worktree` run also shows its branch and whether it was merged or discarded.

`just obs` builds the UI and serves it, with the API, from one process. It takes the first free port from 4600 up and prints the address, so several repos can each run their own at once. Set `SSSF_OBS_PORT` in `.env` to pin a port. The raw form:

```bash
cd .claude/skills/sssf/apps/visualizer && bun install && bunx vite build
SSSF_DB=/abs/path/to/your-repo/adws/adw_data/sssf.db bun run server/index.ts
```

It resolves its target through `--db`, then `SSSF_DB`, then `<cwd>/adws/adw_data/sssf.db`, so one instance can point at any stamped repo. Pass the db explicitly, because the server runs from the app dir.

`just obs-all` serves every repo SSSF is installed in, from one process. `install.py` and `install.py --update` record each repo in `~/.config/sssf/projects.json` (or `$SSSF_REGISTRY`). The page opens on a list of projects, with running ones first, and each project opens its own sessions. The server reads the registry again on every listing, so a newly installed repo appears without a restart. To drop a repo, delete its entry from the file. The raw form is `bun run server/index.ts --all`.

Above the projects, a panel shows the Claude account they all share. It has two parts, and a third when Pi agents have run:

- **The usage limits.** These are how full the 5-hour and 7-day windows are, and when each resets. Claude Code reports them on every SSSF agent call, and SSSF keeps the newest reading in `rate_limits.json` beside the registry. Interactive sessions don't report them, so the panel says how old the reading is. `/usage` in any Claude Code session gives the current figure.
- **Where the spend went.** This is the spend in the current 5-hour window, by kind (SSSF agents or interactive sessions), by project, and by model. The server reads it from Claude Code's transcripts in `~/.claude/projects` and estimates the cost from token counts at list price. The subscription pays that cost, but it shows what fills the windows.
- **Pi agents.** When Pi agents have run in the last 7 days, the panel adds their spend over the last 5 hours and 7 days, by project and model. It reads each registered repo's trace database and uses the list-price cost Pi reports. A ChatGPT sign-in pays that cost. Pi's providers report no usage limits to SSSF, so the panel shows no windows for them and `rate_limit_stop` doesn't cover them. Check a ChatGPT plan's limits in ChatGPT or Codex.

Before each Claude Code agent starts, SSSF checks the same reading. If either window is at or over `defaults.rate_limit_stop` (default 0.8), the run stops, with the time the window resets. That beats a run that dies halfway through a phase. See `references/config.md`.

Two cost caps stop a run that's spending too much, which matters most when nobody is watching it. `defaults.max_run_cost` (default $5) is checked before every send. Each agent's `max_call_cost` goes to `claude -p --max-budget-usd`, so a runaway call ends itself. The starter roster sets each one at about twice the most that agent has cost so far. Two more caps are the repo's own, in `adws/adw_sssf_config/repo.yaml`, and both are off unless set. `max_live_cost` caps what every live run spends together. `max_daily_cost` caps what the repo's runs spent in the last 24 hours, rolling, finished ones included, and its stop says when enough spend ages out. The dashboard shows the last 24 hours against it on each repo's card and atop its runs list, amber from 80% and red at the cap. Both are checked before every send and at launch, so a run stops, or a new one doesn't start, while the repo is at either. No cap retries, and none discards work: if the tree holds uncommitted changes, the stop message prints how to look at them, keep them, or drop them.

A repo on another machine is not in this registry. Run `just obs` there, and reach it through an SSH tunnel: `ssh -L 4600:localhost:4600 host`.

---

## What is in this repo

```
super-simple-software-factory/
├── docs/designs/                       # design records for what this fork added
└── .claude/skills/sssf/
    ├── SKILL.md                        # hard rules + request routing table
    ├── cookbooks/                      # 12 orchestrator playbooks, loaded lazily
    ├── references/                     # config / handoff / observability specs
    ├── scripts/                        # install.py, make_config.py, make_adw.py
    ├── apps/visualizer/                # the read-only trace UI (Vue + Vite on Bun)
    └── templates/                      # EXACTLY what install.py stamps
        ├── sssf.config*.yaml           # the starter rosters: pi, Claude Code, ChatGPT
        ├── repo.yaml                   # the repo's own SSSF rules
        ├── container/                  # the Podman sandbox, with --container
        ├── prompt_engineering/{agent}/ # system.md + user.md per agent
        ├── harness_engineering/        # pi extensions
        └── adws/
            ├── adw_*.py                # thirteen starter workflows, plus kill and worktree
            └── adw_modules/            # ALL low-level logic, ADW scripts stay thin
```

The skill is also what an agent reads to *operate* the factory. `SKILL.md` is the central idea, and the cookbooks are lazily loaded recipes it pulls in one at a time: set up the factory, create an ADW, modify a chain, add an agent, run and monitor. If you can teach an agent to do something, teach it, then go build the thing it cannot.

---

## The thirteen starter workflows

Every ADW takes the same shape:

```bash
uv run adws/adw_*.py "<prompt or path/to/prompt.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4] [--worktree]
```

`--worktree` works on the four single-task chains, `adw_build_test`, `adw_plan_build_test`, `adw_plan_build_test_quality`, and `adw_simple_sdlc`, and on `adw_tickets`, which builds the whole feature on one branch. See [A branch per run](#a-branch-per-run).

| ADW | Chain | Reach for it when |
|---|---|---|
| `adw_prompt` | engineer to \<agent\> | one agent, one prompt, `--agent NAME` picks who |
| `adw_scout` | engineer to scout | read-only recon, nothing changes |
| `adw_plan` | engineer to planner | you want the spec before any code |
| `adw_build` | engineer to builder | the plan already exists |
| `adw_quality` | engineer to code(quality) | lint, format, typecheck, build, no agents at all |
| `adw_plan_build` | planner, builder, git(commit) | small, well-understood work |
| `adw_build_test` | builder, code(test), bounded fix loop | there is a suite to satisfy |
| `adw_build_review` | builder, reviewer, bounded revise loop | "is this what was asked for" matters more than "does it run" |
| `adw_plan_build_test` | plan, build, code(test), git(commit) | the standard chain |
| `adw_plan_build_test_quality` | same, plus lint/format/typecheck/build gates | the repo has quality commands worth enforcing |
| `adw_document` | code(git diff), documenter | write up what just shipped |
| `adw_simple_sdlc` | plan, build, verify (tests plus every quality check), review, document | the work is real and its shape is not obvious |
| `adw_tickets` | scout, then per ticket: build test-first, verify, commit; standards and spec reviews, one fix, document | the feature has a spec and tickets from Matt Pocock's skills |

`adw_simple_sdlc` lands three commits from three authors. The plan, the code, and the write-up each get their own, and each message is the words of the agent that produced it.

`adw_tickets` is the second half of [Matt Pocock's engineering skills](https://github.com/mattpocock/skills). The engineer grills, specs, and tickets a feature interactively, which leaves `.scratch/<feature>/spec.md` and one file per ticket. `just tickets .scratch/<feature>/` then builds each ticket in a fresh session, test-first at the seams the spec names, and commits each one once its checks pass. A rerun resumes at the first unfinished ticket. See `cookbooks/tickets.md`.

Which one to use depends on the work. A bug fix, a rename, or a flag that follows an existing pattern is one `adw_simple_sdlc` run. A new user-facing mode or file format, a change worth an ADR, or work that splits into slices you can check one at a time is a ticket feature, and costs your time up front for the grilling, the spec, and the tickets. [`cookbooks/choose_a_flow.md`](.claude/skills/sssf/cookbooks/choose_a_flow.md) has the two questions that decide it, the signals, and what each cost on a real repo.

`--adw-id` is optional everywhere. Omit it and a fresh id is minted and printed. Supply it and the run joins that session: same dirs, same `context_handoff/`, and each agent **resumes its existing context window** through `agent_map.json` instead of starting cold. That is how you chain workflows.

```bash
uv run adws/adw_plan.py "add a /health endpoint"              # prints adw_id a1b2c3d4
uv run adws/adw_build_test.py "implement the plan" --adw-id a1b2c3d4
```

### A branch per run

By default a chain works and commits on the branch you have checked out. With `--worktree`, the run gets its own branch, `sssf/<adw_id>`, cut from your branch's tip, in its own folder under `adws/adw_data/worktrees/`. You can keep working while it runs. Your uncommitted changes are not part of the run, and nothing reaches your branch until you merge it.

```bash
uv run adws/adw_simple_sdlc.py "add a /health endpoint" --worktree
just merge <adw_id>      # a successful run only: fast-forward, or a merge commit if your branch moved on
just discard <adw_id>    # drop the run's branch and folder, whatever state it is in
just rebase <adw_id>     # ff_only repos: onto your branch's tip, then recheck
```

`just merge` refuses unless the run succeeded, its launch branch is checked out, and the checkout is clean. It never rebases. A conflict aborts the merge, leaves your checkout as it was, keeps the branch, and prints the commands to merge by hand. A repo that keeps a linear history sets `merge: ff_only` in `adws/adw_sssf_config/repo.yaml`, and then a merge that can't fast-forward is refused with nothing changed. `just rebase <adw_id>` then moves the run's branch onto yours and reruns the quality checks in its folder; a conflict or a failed check puts the branch back. `--adw-id` resumes a worktree run in its own folder. None of these recipes pushes.

Two worktree runs can go at once, each its own launch with its own adw_id, merged on its own. `max_worktree_runs` in `repo.yaml` sets how many, 2 by default; set it to 1 if your quality checks bind a fixed port or share a test database. Each run's `max_run_cost` holds for that run alone, so two runs can spend up to twice it, unless `max_live_cost` in `repo.yaml` caps what live runs spend together. A lock keeps two launches from both passing the cap, and two merges from meeting in your checkout. In the container, each run gets its own project environment. A run's folder is a fresh checkout without your untracked dependencies, so `worktree_setup` in `repo.yaml` lists the commands that install them, such as `bun install --frozen-lockfile`; they run as the run's first phase, before any agent. Design: [`docs/designs/parallel-worktree-runs.md`](docs/designs/parallel-worktree-runs.md).

Watch a run with the trace db directly:

```bash
sqlite3 adws/adw_data/sssf.db "select adw_id, status, substr(request,1,60), total_tokens from sessions order by started_at desc limit 10;"
sqlite3 adws/adw_data/sssf.db "select seq, name, kind, owner, status from phases where adw_id='a1b2c3d4' order by seq;"
sqlite3 adws/adw_data/sssf.db "select kind, name, pid, command from processes where adw_id='a1b2c3d4' and ended_at is null;"
```

Reads never block a running workflow, the db is WAL. `install.py` stamps a `justfile` wrapping all of the above, so in a fresh repo these are `just sessions`, `just phases <adw_id>`, `just tail <adw_id>`, and `just procs <adw_id>`. `just kill <adw_id>` stops a run, and `just rosters` lists every roster and the model each agent runs.

---

## Where it can still fail

Honest edges, because knowing them is cheaper than discovering them.

| Failure | What actually happens | What to do |
|---|---|---|
| The test phase reports green on a fresh install | `quality.py` ships placeholder commands that exit 0. Three ADWs run them as their test phase | Wire your real commands into `quality.py` before trusting `adw_build_test`, `adw_plan_build_test`, or `adw_simple_sdlc`. This is the first thing to customize |
| CI fails on files you never wrote | A repo-wide `ruff check .`, eslint, or tsc also reads the ~200 SSSF files in `adws/` and `.claude/`. An sdist build can ship them too. The chain passes because `quality.py` names your paths | Exclude `adws/` and `.claude/` from those tools and from the package. Exclude `specs/` and `app_docs/` from formatters and linters too: ruff 0.16 formats Python blocks inside Markdown, so a spec's examples can fail `ruff format --check .`. The install cookbook's post-install checklist lists the setting for each tool |
| A bare model pattern | The same model sits under several providers, so `gemini-3.6-flash` matches three catalog entries and `agents.validate()` refuses to spawn | Always write `provider/model-id` |
| `just` is not installed | The stamped `justfile` is a convenience wrapper, nothing depends on it | Every recipe is a one-line `uv run` or `sqlite3` command. Open the justfile and run the line yourself |
| A coding agent hangs silently | No events, no tokens, an empty `raw_output.jsonl`. The trace goes quiet rather than red | `just procs <adw_id>` shows what is alive. `just kill <adw_id>` stops the chain first, so it can't retry with a new agent, then its agents. It checks each pid still runs what the trace recorded. A killed run finalizes its own trace to `fail` |
| The synced triad drifts | Type, `## Report` example, and `output_type=` disagree, so every call burns correction rounds | Grep the type name and fix all three in one edit |
| Gates pass, output is bad | Gates check what a predicate can check, not plan quality or code taste | Run the `reviewer`, or read it yourself |
| An agent edits something it should not | Detected and rolled back after the call, and the phase fails | Expected. Widen that agent's `writes` if the change was legitimate |
| Commit phase has nothing to commit | `commit_all` raises if the cwd is not a git repo or nothing changed | `git init` with one commit first. A no-op build fails the phase rather than committing nothing |
| `install.py --force` | Overwrites **all** stamped files, config and prompts included | Use `--update` to refresh a repo. Commit before you force |
| A worktree run's checks fail on missing packages | Its folder is a fresh checkout, without your untracked `node_modules` or other installed dependencies | List the install commands under `worktree_setup` in `repo.yaml`. They run before the first agent |
| Two runs spend more than the cap | `defaults.max_run_cost` in `sssf.config.yaml` holds per run, so two at once can spend up to twice it | Set `max_live_cost` in `repo.yaml`, which every live run shares. Each can still overshoot it by one call |
| The second of two runs won't merge | In an `ff_only` repo, it can't fast-forward once the first has landed | `just rebase <adw_id>` on your word, then `just merge <adw_id>` |

A run works on your current branch unless you ask for one of its own. For real work, use `--worktree` and merge with `just merge`, and consider `--container` at install for a sandbox around the operator.

### Still open

Known gaps in this fork, none of which blocks a run. Each design doc in [`docs/designs/`](docs/designs/) keeps its own list.

- `max_daily_cost` holds one repo; nothing caps what every repo on the machine spends together.
- The dashboard has no gauge of `max_live_cost`, only the run's caps and its stop.
- A pi run that hits the ChatGPT plan's limit fails on a 429 like any error; nothing names it a usage stop. The route reports no windows to stop before it.
- One request tried several ways, with the best merged, is left room for but not built.
- A seam for a second sandbox beside the Podman container waits for one to exist.
- Every worktree run runs all of `worktree_setup`, even when it touches one package.

**Is this overkill for a one-off feature?** Yes. Prompt an agent and move on. This earns its keep when the same workflow runs a hundred times, when validation is the only thing standing between you and a bad merge, and when you need the thousandth run to look like the first.

---

## Built to be Observed, Customized, and Reused

This is a starting point, not a product. Nothing here is meant to survive contact with your codebase unchanged.

The tests it ships are not your tests. The prompts it ships describe a demo app, not your domain. The roster names the models that were good the week it was written. All of that is supposed to be replaced, and the whole thing is shaped so that replacing it is a small edit in an obvious file instead of a rewrite. That is what those three properties are for. **Observable** so you can see which part is actually costing you. **Customizable** so the fix is one file. **Reusable** so you do it once and stamp it everywhere.

Where to start, roughly in the order that pays off fastest:

| Change | File | Why |
|---|---|---|
| Your real commands | `adws/adw_modules/quality.py` | The shipped blocks are placeholders that exit 0. Until you wire this, your test phase is theater |
| Your prompts | `adws/adw_data/prompt_engineering/{agent}/` | Where your standards live: what a good plan looks like, what a review has to catch |
| Your roster | `adws/adw_sssf_config/sssf.config.yaml` | Models, thinking levels, tools, and what each agent is allowed to write |
| Your chains | `adws/adw_*.py` | Copy the closest workflow and edit the phase list. They are 40 to 300 lines on purpose |
| Your definition of done | `adws/adw_modules/gates.py` | A gate is one function. Whatever "done" means where you work, write it here |
| Your agent capabilities | `adws/adw_data/harness_engineering/` | Pi extensions, a different set per agent if that is what the job needs |

And what it deliberately does not do. A branch per run, the merge step, and the sandbox are opt-in: `--worktree`, `just merge`, and `--container`. There is no cloud and no human-in-the-loop approval phase. Those are the obvious next things to build. They are left out so the core stays small enough to read in one sitting, which is the only reason you would trust it enough to change it.

So take it. Fork it, strip the parts you do not need, rename the agents, throw out half the workflows, and roll what is left into the factory your product actually needs. The specific chains in here matter far less than the shape: code owns the loop, agents own the phases, and every run leaves a trace you can go read.

---

## License

MIT, see [`LICENSE`](LICENSE).

---

## Master Agentic Coding

<p align="center">
  <img src="images/08_rise_with_the_ceiling.svg" alt="Vibe coding sits inside a narrow band with a short arrow of headroom above it, agentic engineering rises far above that band with a tall one" width="850">
</p>

Vibe coding is not knowing how your system works, and not looking. Agentic engineering is knowing how your system works so well that you do not have to look.

Master agentic coding by gaining a deeper understanding of the foundational units of the software factory.

Learn tactical agentic coding patterns with [Tactical Agentic Coding](https://agenticengineer.com/tactical-agentic-coding?y=sssf).

Follow the [IndyDevDan YouTube channel](https://www.youtube.com/@indydevdan) to improve your agentic coding advantage.

---

Stay Focused and Keep Building

- IndyDevDan

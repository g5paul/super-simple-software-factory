# Watch a run

Every run writes its events to `adws/adw_data/sssf.db` as they happen. You can read them from the terminal, from a dashboard, or with plain SQL. Reading never slows or blocks a run.

## From the terminal

```console
$ just sessions                 # the last 10 runs
d36de4d2|success|Add textkit.snake_case(text), which lowercases the|168721|0.3687

$ just phases d36de4d2          # each phase: seq, name, kind, owner, status, attempt
1|request|engineer|George Paul|success|0
2|plan|agent|planner|success|0
3|build|agent|builder|success|0
4|test_1|code|quality|success|0
5|commit|code|git|success|0

$ just tail d36de4d2            # the newest 25 events
12042|phase_end|commit|2026-11-06T03:30:21.021+00:00
12038|phase_start|commit|2026-11-06T03:30:21.005+00:00
12032|tool_call|quality:plan_check_1|2026-11-06T03:30:20.975+00:00
```

| Recipe | Shows |
|---|---|
| `just sessions` | the last 10 runs: id, status, request, tokens, cost |
| `just phases <adw_id>` | where a run stands |
| `just tail <adw_id>` | its newest events |
| `just procs <adw_id>` | what it has alive, with pids |
| `just rosters` | every roster, and the model each agent runs |
| `just trial-show <trial>` | a trial's scorecard |
| `just sandbox-usage` | what VM runs used of each provider's free allowance |

A repo whose own justfile loads SSSF with `mod sssf 'sssf.just'` runs these as `just sssf sessions` and so on.

## The dashboard

```bash
just obs        # this repo's runs
just obs-all    # every repo SSSF is installed in, from one process
```

Each builds the dashboard and prints its address. It needs bun. `just obs` takes the first free port from 4600, so several repos can run their own at once; `SSSF_OBS_PORT` in `.env` pins one.

<p align="center">
  <img src="../../images/00_swimlane_waterfall.svg" alt="A run as swim lanes: engineer, code, planner, builder, and reviewer phases laid on a time axis, each block labelled with its duration, one phase still running and the next still queued" width="850">
</p>

A run opens as swim lanes, one per owner, on a time axis. Click a phase to see its tool calls, gates and report. The dashboard also shows:

- a worktree run's branch, and whether it was merged or discarded;
- a ticket run's tickets, each opening its phases, with spend against the run cap;
- a page per trial, and a leaderboard per roster on the project page;
- each VM run's VM, and what runs have used of each provider's allowance;
- spend for the last 24 hours against `max_daily_cost`, and live spend against `max_live_cost`.

`just obs-all` reads `~/.config/sssf/projects.json`, where `install.py` records each repo. It opens on a list of projects, running ones first. It shows the same from any repo, since the registry is machine-wide, but each repo builds the dashboard from its own copy of the skill: run it from the repo with the newest one, such as the repo you update the others from. To drop a repo, delete its entry from the file. For a repo on another machine, run `just obs` there and reach it through a tunnel: `ssh -L 4600:localhost:4600 host`.

### The account panel

Above the projects, `just obs-all` shows the accounts every repo shares:

- **Claude's usage windows:** how full the 5-hour and 7-day windows are, and when each resets. Once a window is 10% used, its meter also says what 100% is worth at list price: the window's spend up to the reading, over the reading's utilization. Below 10%, the reading's rounding swamps the figure. Anthropic doesn't publish the limits in dollars, so treat it as an estimate. Claude Code reports them on every SSSF agent call. Your interactive sessions report them too once you set SSSF's statusline, below. Without it, the panel says how old the reading is, in amber once it's over an hour; `/usage` in any Claude Code session gives the current figure. Each meter also shows the recent pace twice, in $ per hour and in percent per hour, over the last hour for the 5-hour window and the last 24 hours for the 7-day one. It also shows what 1% cost over that span. The limit is in percent, so that's the rate that counts, and a jump in $ per 1% means the plan isn't weighing your use at list price. The percent rate comes from the readings SSSF keeps over time, in `rate_limits_history.jsonl` beside `rate_limits.json`. It gets a line when a window moves, and one an hour while nothing does. Readings are whole percents, so a span that moved under 2 points shows the rate since the window started instead, which is also what shows until the history covers the span. Below that, the meter says where the window ends up at that rate: how full it gets by its reset, or when it fills.
- **Where Claude spend went**: SSSF agents against interactive sessions, by project and by model, at list price. A picker sets the span, from 5 hours to 90 days; the browser remembers it. Each table shows its top seven rows, folds the rest into one, and ends with a total that matches the span's. For a span of a day or more, the card also lists the three busiest 5-hour stretches, which is what a plan with a 5-hour limit would have seen. Claude Code deletes transcripts after `cleanupPeriodDays`, 30 by default, so a span longer than that needs the setting raised; the card says when the data starts inside the span.
- **pi spend**: SSSF's pi agents against your own pi sessions, by project and model, with its own span picker. The traces keep every SSSF pi call, so any span is complete for them. Your own sessions come from the files pi saves in `~/.pi/agent/sessions`, or `$PI_CODING_AGENT_DIR/sessions`; SSSF's agents keep theirs inside each run, so nothing is counted twice. When a run hits the ChatGPT plan's limit, the panel shows it for 5 hours. A span with no pi calls is one line.
- **The ChatGPT plan's windows**, when you opt in, below: the 5-hour and weekly meters, with the same 100% figure and pace as Claude's. Their readings are kept in `codex_limits_history.jsonl`, only while the dashboard runs. Without the opt-in there are none, since pi's ChatGPT sign-in gets no usage windows back.
- **Sandboxes**, once a repo has run in a VM: one line per provider, its bar the allowance nearest its limit, and the rest on hover. The bar always measures the allowance's own window, 30 days for Vercel and all time for E2B's credit. The card's picker sets the runs line under it.
- **The machine's cap**, when `~/.config/sssf/limits.yaml` sets one.

#### ChatGPT's windows through Codex CLI

pi can't read the ChatGPT plan's windows, but Codex CLI can. Its `/status` asks Codex's app server for `account/rateLimits/read`. Set `SSSF_CODEX_LIMITS=1` in the hub's `.env`, and `just obs-all` makes the same request every 5 minutes while it runs. It starts `codex -s read-only -a never app-server` and sends `initialize`, `account/read` and `account/rateLimits/read`, nothing else. Codex holds the sign-in, and SSSF never reads the token. It needs Codex CLI installed and signed in to the ChatGPT account pi uses.

pi's ChatGPT use draws on the same windows. A test run on 2026-11-07 started the 5-hour window and moved it from 0% to 1%, with nothing else using ChatGPT.

Two caveats:

- OpenAI doesn't document the method, so a Codex release could change it. A failed read keeps the last good meters and says why under them.
- The windows count Codex's own use too, and the 100% figure counts only pi's `openai/` calls, so using Codex directly makes the figure read low.

#### A fresh reading from your own sessions

Claude Code hands a statusline command the account's 5-hour and 7-day windows after every reply. SSSF's `scripts/statusline.py` records them where agents' readings go, so the meters and `rate_limit_stop` stay current while you work interactively. It prints a short line, such as `Opus · 7d 3% · $0.42 session`, and runs locally with no tokens spent. Point it at the newest skill in `~/.claude/settings.json`:

```json
"statusLine": {"type": "command", "command": "python3 <hub>/.claude/skills/sssf/scripts/statusline.py"}
```

A project's `.claude/settings.json` that sets its own `statusLine` replaces this one there. The windows arrive only for a Pro, Max or Ultra login, and only after a session's first reply.

## With SQL

The recipes are one-line `sqlite3` commands, so you can write your own:

```bash
# where a run stands
sqlite3 adws/adw_data/sssf.db "select seq, name, kind, owner, status, attempt from phases where adw_id='d36de4d2' order by seq;"

# why a phase failed
sqlite3 adws/adw_data/sssf.db "select attempt, gate, passed, checks_json from gate_results where adw_id='d36de4d2';"

# a live tail: poll this, raising the rowid each time
sqlite3 adws/adw_data/sssf.db "select rowid, type, name from events where adw_id='d36de4d2' and rowid > 0 order by rowid limit 50;"

# what's still running
sqlite3 adws/adw_data/sssf.db "select kind, name, pid, command from processes where adw_id='d36de4d2' and ended_at is null;"
```

[concepts.md](../concepts.md#the-trace) lists the tables.

## The files

Each run keeps its raw record in `adws/adw_data/sessions/<adw_id>/`:

| File | Holds |
|---|---|
| `<agent>/raw_output.jsonl` | the agent's whole output stream |
| `<agent>/envelope.json` | its parsed report |
| `<agent>/prompts/` | the prompts it was sent |
| `events.jsonl` | every event, as the tracer wrote it |
| `context_handoff/` | what agents wrote for the next one: the plan, scout notes, reviews, quality logs |
| `agent_map.json` | which agent session each name maps to, so `--adw-id` can continue them |
| `sandbox-uncommitted.patch` | a VM run's uncommitted work, when it stopped early |

The database mirrors these files. Losing it loses nothing you can't rebuild.

## pi's obs extension

If your pi agents load an obs extension that posts to your own obs server, as [pi-agent-observability](../projects.md) does, their events reach that server from the host, from the container, and from a VM. In a VM, the extension posts to a relay on the VM's loopback, and the host forwards each event to your server with its own token, which never enters the VM. That server is yours and separate from `just obs`.

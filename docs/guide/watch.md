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

- **Claude's usage windows:** how full the 5-hour and 7-day windows are, and when each resets. Claude Code reports them on every SSSF agent call. Your interactive sessions don't, so the panel says how old the reading is; `/usage` in any Claude Code session gives the current figure.
- **Where Claude spend went** in the current 5-hour window: SSSF agents against interactive sessions, by project and by model, at list price.
- **pi agents' spend** over the last 5 hours and 7 days, by project and model. ChatGPT sends no usage windows, so there are none to show. When a run hits the ChatGPT plan's limit, the panel shows it for 5 hours.
- **The machine's cap**, when `~/.config/sssf/limits.yaml` sets one.

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

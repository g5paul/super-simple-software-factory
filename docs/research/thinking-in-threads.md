# Thread-based engineering and this fork

Written 2026-11-06, against skill 33a1ed2 and hub `main` 63c1e6e. It compares disler's article [Thread-Based Engineering with Claude Code](https://agenticengineer.com/thinking-in-threads) with upstream SSSF and with this fork. The page shows no publish date. Reread it when the fork changes in a way that moves a row below, and add a dated section saying what moved rather than editing the old one.

## Short answer

The article describes SSSF's shape. Its B-thread is "deterministic code orchestrating non-deterministic intelligence", and that is an ADW. The two part ways at the end. The article ends at the Z-thread, "no review node". This fork has spent most of its work making a long, unwatched thread safe, and it keeps the engineer at the merge on purpose.

## The article in brief

- **Thread:** "a unit of engineering work over time, driven by you and your agent. You show up at two nodes: the beginning (prompt or plan) and the end (review or validate)."
- **Base thread:** prompt, tool calls, review. "Tool calls roughly equal impact."
- **P-thread:** several threads at once, in a terminal you steer or in cloud sessions that run alone.
- **C-thread:** work split into phases with checks between them, for context limits and for high stakes.
- **F-thread:** parallel agents whose results are compared, merged or picked from, isolated in sandboxes as "deferred trust".
- **B-thread:** agents that start or manage other agents. "The simplest B-thread is a sub-agent."
- **L-thread:** one prompt that runs for hours. "Top agentic engineers kick off a prompt and go to bed."
- **Z-thread:** "Zero-touch execution. No review node." The article says this is not vibe coding: "It isn't that we don't look at the code. It's that we know we don't have to."
- **Four levers:** width, time, depth and attention, meaning more threads, longer threads, thicker threads and fewer checkpoints. Attention is "about earning trust". The measure it proposes is tool calls per unit of your attention, tracked weekly. It gives no method and no data.

## Upstream and this fork

Upstream is disler's single commit de31374: 12 ADWs and 20 modules. This fork was 456 commits ahead on this date, with about 28,400 more lines in the skill and 20 more modules.

| Thread | Upstream SSSF | This fork |
|---|---|---|
| Base | `adw_prompt` | Same |
| C | Phases, envelopes and gates. The checks between phases are code, not a person | More code checks: plan checks, spec review rounds, fix loops that end before review when the checks stay red, and the guard chain's bypass review. The engineer still starts each run and merges it |
| B | The ADW chains: plan, scout, build, review, document | The same, plus a level above: the hub session runs each project's session over SendMessage, and those sessions run ADWs |
| P | One run per repo at a time | Worktree runs, VM sandboxes on Vercel and E2B, and several repos updating at once |
| F | None | `adw_trial` with scorecards, and VMs as deferred trust. A judge that picks the winner alone waits in `open.md` until scorecards show its picks match the engineer's |
| L | Nothing for long runs | The silence watchdog, budgets, usage and rate-limit stops, kill, and pricing of a killed call from its raw log |
| Z | Not a goal | Turned down for now: guard code, refuse rules, secret files, merges on the engineer's word, and pushes only the engineer runs |

## Where they agree

- **Trust comes from evidence.** The article says so and stops there. Here it is the working rule: a project session's report is checked against its trace before anything acts on it. On this date that caught a stray file a test wrote into the skill, and an error in the hub's own pricing check.
- **Long and zero-touch threads multiply small holes.** A project's run spent two fix rounds and $1.55 on a plan check that could never pass, because it compared the tree with git before the build was committed. Unwatched overnight, that is a wasted night. The `cat <<EOF | bash` bypass in CLAUDE.md is the same lesson with higher stakes. Most of the fork's commits close holes like these, which the article's fewer checkpoints quietly depends on.

## Where the fork is weakest by the article's measure

Attention. On this date the agents' work was quick, and the loop around it was slow: three merges, seven pushes typed by the engineer, and four project reports to check by hand for each skill change. Some of that is chosen; the push deny rules stay until the engineer lifts them. Much of the checking is mechanical, though, such as whether a repo's stamp shows the new skill, its quality run passed and a removed file is gone. Two items in `open.md` come from this: a script that checks project reports, and a weekly query of the four levers.

## What the fork can measure that the article can't

Each repo's `sssf.db` holds most of what the levers need: concurrent runs for width, tool calls and minutes per run for time, agents per run for depth, and engineer phases and merges per run for attention. The article says to track the levers weekly and gives no way to; here a query can.

## What would move a row

- **F:** the trial judge picking winners alone.
- **Z:** a class of change the engineer lets merge without reading it, with the trace as the reason.
- **Attention:** project reports checked by code instead of by the hub session, and the lever numbers trending over weeks.

## 2026-11-06: the first lever numbers

`adws/levers.py` now prints the levers from the traces. The first run, with `--listed` so private repos are left out, covers four repos:

| Week | Runs | At once | Agent runs | Minutes per run | Calls per run | Agents | Touches | Calls per touch |
|---|---|---|---|---|---|---|---|---|
| 2026-W44 | 151 | 2 | 87 | 0.6 | 12.0 | 2 | 96 | 10.8 |
| 2026-W45 | 282 | 5 | 128 | 1.1 | 22.2 | 1 | 135 | 21.1 |

Read them with care. Most agent runs here are the test bed's short proof runs, which pull the medians down, and this week's hub runs are guard-chain runs at about 68 tool calls each. A touch counts a session's launch the same as the engineer's, so calls per touch overstates how little attention the work took. The per-repo tables, `--by-repo`, are the better baseline to compare against.

## 2026-11-06: what the levers miss

The per-repo tables, with each repo's spend and failed runs from the same traces, for the listed repos. Two unlisted projects are left out. Spend and failures group runs by SQLite's `%W` week, which can put a run near a week's edge in the next or previous week from the levers' ISO week.

| Repo | Week | Runs | Agent runs | Min/run | Calls/run | Agents | Touches | Spend | Failed |
|---|---|---|---|---|---|---|---|---|---|
| pi-agent-observability | W44 | 53 | 10 | 7.1 | 36.0 | 3 | 10 | $2.75 | 2 |
| | W45 | 48 | 2 | 5.7 | 10.5 | 2.5 | 2 | $0.08 | 0 |
| hub | W45 | 64 | 31 | 19.4 | 67.9 | 4 | 35 | $116.00 | 5 |
| sssf-sandbox | W44 | 93 | 75 | 0.6 | 8.9 | 2 | 84 | $12.41 | 16 |
| | W45 | 123 | 95 | 0.6 | 7.6 | 1 | 97 | $11.60 | 32 |
| fusion-harness | W44 | 5 | 2 | 0.2 | 5.0 | 1 | 2 | $0.01 | 2 |
| | W45 | 47 | 0 | 0 | 0 | 0 | 1 | $0.00 | 0 |

The hub's $116 is its 31 guard-chain runs that week.

### Why cost isn't one of the article's levers

The levers are things to push up: more threads, longer, thicker, fewer checkpoints. Cost rises with every one of them, so it's what the levers cost rather than a lever. The article also takes plentiful compute as given. This fork can't: its dollars are list-price estimates on a subscription, the real limit is the usage window, and budgets, `max_run_cost` and the usage and rate-limit stops exist for it. So cost belongs in the report as a ratio, cost per thing that landed. The hub's $116 bought five merged guard changes, about $23 each, which is the number to weigh when deciding how many guard runs to make.

### What else is missing

- **Whether the work was any good.** "Tool calls roughly equal impact" is the article's weakest claim. A project's run made plenty of tool calls and landed nothing, on a plan check that couldn't pass. No lever counts accepted runs, commits that landed, fix rounds or review findings; sssf-sandbox failing 32 of 123 runs looks like any other week.
- **Test noise in the totals.** The test bed's short proof runs are most of the agent runs and pull the median to 0.6 minutes, and quality-only checks inflate runs: fusion-harness had 47 in W45 with no agent in any.
- **The long end of time.** A median of 1 to 6 minutes says nothing about L-threads. The longest run, and how many ran past an hour, would show whether threads grow.
- **Depth above the run.** Agents per run is the B-thread inside one ADW. The deeper one here, the hub session running every project's session, is in no trace.
- **The engineer's attention.** Touches count launches. The engineer's messages, approvals and pushes are in no trace. Claude Code's transcripts could estimate them, at the price of a second data source and its own privacy questions.

### What to add first

Accepted agent runs, meaning runs whose status is `success`, which `Run.finish` sets only when every phase passed and the ADW's acceptance test held; cost per accepted run; the longest run in minutes; and `--real`, leaving out quality-only runs and the hub's test beds. Fix rounds and review findings need more parsing and wait.

# Agents that go quiet

Status: built on 2026-11-03, proved in sssf-sandbox.

## The gap

SSSF had no time limit on an agent call. The checks time out and the VM sessions are capped, but a Claude Code or pi agent that hung waited forever. A hang spends nothing, so the cost caps never saw it, and the run held its worktree slot until someone looked at the dashboard. The fork survey found pl018 and ryancatlin with watchdogs for the same reason ([forks-survey.md](../research/forks-survey.md)).

## As built

- **The timer.** `silence.watch` runs a thread beside each runner's read loop, in `agent_cc.py` and `agent_pi.py`. Every line the agent writes resets it. After `max_silence_minutes` with nothing, it kills the agent's whole process tree, on the host or in the VM, which ends the read loop. The runner raises `SilentStop` with what the call had spent. `agents.py` counts that spend, logs an `agent_silent` event, and the run ends with a plain message. The dashboard shows "an agent went quiet for max_silence_minutes".
- **The setting.** `max_silence_minutes` in `repo.yaml`, 15 by default, `null` for off.
- **Why 15.** Claude Code sends a heartbeat about every 30 seconds while a tool runs, and its Bash calls end within 10 minutes on their own, so a live Claude Code agent is never quiet that long. pi streams only what a command prints and sets no time limit, so a quiet pi command longer than 15 minutes is stopped. A repo whose agents run those raises the limit.
- **After the result.** An agent can stay alive after it has answered. Once the final result arrives (Claude Code's `result`, pi's `agent_end`), what's left gets 30 seconds of quiet, then the watchdog kills it, the call keeps its result, and the trace logs `leftovers_stopped`.
- **Claude Code's background tasks.** With a background command running, a Claude Code result isn't final. The CLI waits for the command, then starts a new turn and sends another result. Checked by hand on claude 2.1.317: a `sleep 20` started with `run_in_background`, a reply of "started", then 20 seconds later a second init and a second result. So the runner counts background tasks from `background_tasks_changed`. A result with any still running keeps the full limit, a new turn reopens the call, and a stop after a waiting result keeps the latest result.

## Proof

Each ran scout with "Run exactly this one shell command with your bash tool and wait for it to finish, then say done: sleep 240" and `max_silence_minutes: 1`, set for the run and put back after.

| Run | Roster | What happened |
|---|---|---|
| 29a5ce6e | Claude Code | With the first watchdog: no stop. Claude Code refused a standalone `sleep 240`, ran it in the background, and waited on it with heartbeats every 30 seconds. The run took 265 seconds and passed. This is how the heartbeats were found |
| 95412cbf | Claude Code | The first watchdog stopped it at 74 seconds, after scout had already answered: the CLI was waiting on the background sleep. It threw away a good result. Fixed in f5e8d83 |
| 7d8c3893 | Claude Code | After f5e8d83: the grace cut the wait 30 seconds after the first result, but that result wasn't final. Led to the hand check above and 448e88c |
| 6d34ca01 | ChatGPT (pi) | Stopped at 73 seconds with `SilentStop`, its `sleep` killed with it. The pi case the watchdog is for |
| 642f4c92 | Claude Code | After 448e88c: scout answered with the sleep in the background, the full minute passed, the watchdog stopped the sleep and kept the result, and the JSON retry reported the sleep as stopped. Correct |

No `sleep 240` was left running after any of them.

## Added after

- **The stop names what was running.** The event forwarder's tool tracker holds every call that has started and not returned. On a silent stop, `agents.py` asks it for the latest, and the message ends "It was waiting on `bash: npm run dev`, started ...". The `agent_silent` event carries it as `running` and `running_since`, and the dashboard's stop shows the message.
- **Leftovers on the dashboard.** The run summary has a line for each `leftovers_stopped` event, naming the agent and linking to its phase.

# Design: a pi run at the ChatGPT plan's limit

Status: built. Settled in one grilling session on 2026-11-01. Where the text and a decision disagree, the decision wins.

A pi agent signed in with ChatGPT runs on the plan's usage. When the plan runs out, OpenAI refuses the call. Before this design, the run failed with pi's error text, like any other error, and the dashboard said "a phase failed". Now the run stops by name, the same way a Claude Code run stops at its usage limit, and the dashboard says the ChatGPT plan is out.

[Parallel worktree runs](parallel-worktree-runs.md) kept this on its open list as "A pi run at the ChatGPT plan's limit".

## What was found

- **Nothing warns before the limit.** Pi's `openai` sign-in uses OpenAI's token-sharing route, which sends no usage windows and has no usage endpoint. A probe in sssf-sandbox checked real responses: no `x-codex-*` window headers, and no rate-limit stream events ([pi-usage-limits.md](../research/pi-usage-limits.md)). Claude Code reports its windows on every send, which is what lets `rate_limit_stop` stop a Claude run before the limit. A pi run can only learn at the limit.
- **The limit is one error code.** OpenAI documents `subscription_sharing_usage_limit_exceeded`, HTTP 429. Its docs say not to infer a reset time from it, because an app-specific limit can apply too.
- **Pi never retries it.** The code is on pi's no-retry list (`pi-ai/dist/utils/retry.js:21`), checked before its retry list, so the call ends at once with `stopReason: "error"`. Pi writes the message as `OpenAI API error (429): …`, or as `subscription_sharing_usage_limit_exceeded: …` when it arrives mid-stream, and adds a link to `https://chatgpt.com/settings/usage` either way.
- **Its 503 is different.** `subscription_sharing_usage_unavailable` means OpenAI couldn't check usage. Pi retries it 3 times, after 2, 4 and 8 seconds.
- **Pi's provider retry is off by default.** `retry.provider.maxRetries` is 0. Set higher, it retries every 429 by status code, this one included. Pi's own docs say to leave it at 0.
- **SSSF already fails the call.** Since 2026-11-01, `agent_pi.run` raises when a call's last turn ends in an error, with pi's message. The run failed correctly, but under no name.

## Decisions

1. **Build it now,** against the code in OpenAI's docs and pi's source, without waiting for a real 429. The first real one gets a log row.
2. **React to the 429 only.** Nothing can be read before the limit on this route. A rolling dollar cap per 5-hour window was considered as a stand-in and dropped: nobody publishes how dollars map to plan usage, it can't see the engineer's own ChatGPT or Codex use of the same plan, and `max_daily_cost` and the machine cap already stop runaway spend.
3. **Detect it by the code in pi's error.** When a call's last turn is an error whose message contains `subscription_sharing_usage_limit_exceeded`, `agent_pi.run` raises `UsageLimitStop` instead of the generic error. `agents.execute` records a `rate_limit_stop` event with `provider: "openai"`, the event name Claude's usage stop uses, so the trace and the dashboard read both the same way. The run summary labels it "the ChatGPT plan hit its usage limit".
4. **Nothing is remembered between runs.** Claude's refusal is kept machine-wide until its reset time. This error has no reset time, so SSSF would have to guess how long to block, and would block calls that would have worked. Each later pi call asks OpenAI, and a refused call costs nothing.
5. **The run stops, and SSSF doesn't retry.** The run ends `fail`. The message names the limit, says OpenAI gives no reset time, links the usage page, and says to resume with `--adw-id <id>` once the plan has usage again. A worktree run keeps its branch, as any failed run does. In a trial, the attempt fails its run gate.
6. **The account panel shows the latest hit** in the last 5 hours, the plan's shortest window, with its time, run, and project, read from every registered repo's trace. No new file. It says the plan *was* out of usage, since it may have usage again by now. An archived run is left out. Amended the same day: the first version showed a hit for 7 days, so a proof run looked like a current limit while the plan had 98% of its 5-hour window left.
7. **Proof without a real 429:** unit tests on both message forms, and one sandbox run with a stand-in `pi` on `PI_PATH` that prints the error turn. That proves the trace event, the run's stop reason, and the dashboard, at no cost.
8. **Docs:** the README, `references/config.md`, and `run_adw.md` say a pi run stops by name at the ChatGPT limit and can't stop before it. The item leaves the README's open list and parallel-worktree-runs.md's.
9. **The 503 stays an ordinary error.** Pi already retries it. If it still fails, the run fails with pi's message. A named stop would claim a limit OpenAI said it couldn't check.
10. **No check of pi's provider-retry setting.** `references/config.md` says to leave `retry.provider.maxRetries` at 0. A check would guard against an edit nobody has made.
11. **Only this code is matched.** Pi's legacy `openai-codex` provider reports `usage_limit_reached`, and pi can sign in to a Claude plan. No SSSF roster uses either, and matching their errors means guessing at text. Another provider gets its own stop when a roster first uses it.

## Open

- Whether a real 429 reads the way pi's source says. The first one gets a log row.

## Log

| Date | Where | What | Result |
|---|---|---|---|
| 2026-11-01 | hub | grilling session: decisions 1 to 11 | settled |
| 2026-11-01 | sssf-sandbox | run 7ec288be: `adw_scout` on the `chatgpt` roster, with a stand-in `pi` on `PI_PATH` that prints pi's 429 error turn | stopped with `UsageLimitStop`, but with a Python traceback: the new stop wasn't among `session.STOPS`. It now subclasses `account.RateLimitStop` |
| 2026-11-01 | sssf-sandbox | run d0c57371, the same again | `fail`, $0, 0 tokens, no traceback; the message names the limit, links the usage page, and gives `--adw-id d0c57371`. The trace has `rate_limit_stop` with `provider: openai`. On a dashboard served from a temporary registry: the run reads "Stopped: the ChatGPT plan hit its usage limit", and the account panel shows the hit with its time, run, and project |
| 2026-11-01 | the engineer's `obs-all` | the panel said the plan "hit its usage limit" for proof run d0c57371, while ChatGPT showed 98% of the 5-hour window and 86% of the week left | a stand-in's event looks real, and a 7-day line outlives the window. The line now covers 5 hours, says "was out of usage", and skips archived runs; the two proof runs are archived |

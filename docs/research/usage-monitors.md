# Usage monitors compared with SSSF's dashboard

Researched 2026-11-06. Four public tools watch Claude Code and Codex usage. What do they show that SSSF's account panel doesn't, and which of it is worth adding?

The engineer asked with one decision in mind: whether the Claude Max plan, which has a 5-hour limit, could replace Ultra, which shows none. A second goal came up afterwards: to use ChatGPT more, with visibility close to what the panel gives for Claude.

Each tool's facts come from its README and docs, read on 2026-11-06. None of the code was run.

## Short answer

SSSF's spend accounting is already ahead of all four tools. It splits SSSF agents from interactive sessions, totals cost per run, phase, feature and project, and lists the busiest 5-hour stretches. The gap is the **limit readings**. Claude's reading comes only from SSSF agent calls, so it goes stale while the engineer works interactively, and pi's ChatGPT plan has no reading at all. Two of the tools show cleaner ways to fill both gaps.

## The four tools

### CodexBar

[steipete/CodexBar](https://github.com/steipete/CodexBar) is a macOS menu bar app, with a Qt app and a CLI for Linux. It's written in Swift, MIT licensed, and has 23k stars. It shows real plan-limit percentages and reset times for over 100 providers.

- **Claude** ([docs/claude.md](https://github.com/steipete/CodexBar/blob/main/docs/claude.md)). It calls `GET https://api.anthropic.com/api/oauth/usage` with the OAuth token from `~/.claude/.credentials.json` or the macOS Keychain, and the `anthropic-beta: oauth-2025-04-20` header. The response has `five_hour`, `seven_day`, `seven_day_sonnet`, `seven_day_opus` and `extra_usage`. The token needs the `user:profile` scope. The doc says a `claude setup-token` token can't call it. Its fallbacks are claude.ai session cookies and running `claude` in a pseudo-terminal to parse `/usage`.
- **Codex and ChatGPT** ([docs/codex.md](https://github.com/steipete/CodexBar/blob/main/docs/codex.md)). It calls `GET https://chatgpt.com/backend-api/wham/usage` with the token in `~/.codex/auth.json`, or runs `codex -s read-only -a never app-server` and asks it over JSON-RPC for `account/rateLimits/read`. Either way it gets the 5-hour and weekly windows with reset times, plus a credits snapshot.
- **Cost.** It estimates cost from local logs: Claude's transcripts, and Codex and pi session files. It de-duplicates Claude calls by `message.id` plus `requestId`. The docs call the figures "API-equivalent estimates, not account billing."
- **Extras.** An adaptive refresh, two to thirty minutes. Warnings before a weekly reset. A burndown chart of the quota from recorded readings.
- **Against it.** It reads login tokens, Keychain items and browser cookies, and calls endpoints neither Anthropic nor OpenAI documents. It has no run or project view.

### Claude-Code-Usage-Monitor

[Maciek-roboblog/Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) is a Python terminal app, MIT licensed, with 8.8k stars.

- **Limits.** With `--statusline`, it "captures Claude Code's official `rate_limits`", and that reading wins over its own estimate when fresh. Otherwise it guesses from fixed plan sizes (`--plan pro|max5|max20|team|custom`) or, for `custom`, the 90th percentile of the last 8 days. Its docs disagree on the Pro figure: 19,000 tokens in one table, 44,000 elsewhere.
- **Views.** `--view realtime|daily|monthly|session|entries|sessions|burn-rate`. The burn rate uses the last hour of use and predicts when the session's tokens run out.
- **Extras.** `--once` exit codes for scripts: `10` near the limit, `11` limit hit. An opt-in `--warehouse` keeps history past Claude Code's 30-day transcript cleanup.
- **Against it.** It covers Claude only, and only the session window. Its plan sizes are guesses with no stated source. There's no project or agent breakdown.

### ccusage

[ccusage/ccusage](https://github.com/ccusage/ccusage) is a TypeScript CLI, MIT licensed, with 19k stars.

- **Sources.** Local logs from about 18 agents, Claude Code, Codex, pi-agent, Gemini CLI and OpenCode among them ([data sources](https://ccusage.com/guide/)). It supports a source only when its files hold token counts.
- **Reports.** `daily`, `weekly`, `monthly`, `session`, and `blocks` for 5-hour windows, with `--breakdown` per model, `--instances` per project and `--json`. An active block shows a burn rate and a projected total ([blocks](https://ccusage.com/guide/blocks-reports)).
- **Prices.** From the LiteLLM and models.dev tables, updated automatically, with each event priced at its date's rates ([cost modes](https://ccusage.com/guide/cost-modes)).
- **Statusline.** A beta `ccusage statusline` shows session, day and block cost, burn rate and context use.
- **Against it.** It knows no plan limits. `--token-limit` is a number the engineer supplies. Its live monitor was removed in v18. It has no run or SSSF view.

### sniffly

[chiphuyen/sniffly](https://github.com/chiphuyen/sniffly) is a Python web dashboard, MIT licensed, with 1.3k stars. It reads `~/.claude/projects/`.

- **Views.** Usage patterns, an "error breakdown" of "where Claude Code makes mistakes", and a walk through past prompts. The docs name no error categories.
- **Against it.** No cost, token or limit data. Its share button can publish to a public gallery, and the docs don't say what a share uploads.

## How they compare with SSSF

| | SSSF | CodexBar | Usage Monitor | ccusage | sniffly |
|---|---|---|---|---|---|
| Claude limit reading | from SSSF agent calls only | live, from the OAuth endpoint | from the statusline | none | none |
| ChatGPT limit reading | none | live, from `wham/usage` or `codex app-server` | none | none | none |
| Cost by project and model | yes, over 5 hours to 90 days | totals only | per model | yes | none |
| SSSF agents against interactive use | yes | no | no | no | no |
| Cost per run, phase and feature | yes | no | no | no | no |
| Busiest 5-hour stretches | yes | no | no | blocks list | no |
| Burn rate and projection | no | burndown | yes | yes | no |
| Quality signals | traces only | no | no | no | error breakdown |
| Reads credentials or cookies | no | yes | only with `--api` | no | no |

SSSF prices from `prices.json`, checked against Anthropic's page by `check_prices.py`, rather than pulling a third-party table. That's slower to pick up a new model and harder to get wrong.

## What to add, best first

1. **A fresh Claude reading from interactive sessions.** A statusline script that writes Claude Code's `rate_limits` to the `rate_limits.json` SSSF agents already fill. No credentials, no undocumented endpoint, no polling. [Claude Code's statusline docs](https://code.claude.com/docs/en/statusline) confirm the fields: `rate_limits.five_hour` and `rate_limits.seven_day`, each with `used_percentage` from 0 to 100 and `resets_at` in epoch seconds, for Pro, Max and Ultra logins after a session's first reply. Built as `scripts/statusline.py`.
2. **A burn rate and a projection.** When each window fills at the recent pace. Built into the meters.
3. **ChatGPT plan limits for the pi card.** See the next section.
4. **Per-model weekly windows and extra-usage spend.** Only the OAuth endpoint has them, at the cost of reading a login token. Skip unless item 1 carries them.
5. **Quality signals.** Tool error rates and interruptions, SSSF runs against interactive sessions. The traces hold the SSSF half already.
6. **History past `cleanupPeriodDays`.** Copy daily totals into sqlite.
7. **A warning near a limit**, as a browser notification.

Supporting many providers, as ccusage and CodexBar do, isn't worth copying. pi is the only route to ChatGPT that SSSF uses.

## ChatGPT visibility

[pi-usage-limits.md](pi-usage-limits.md) found that pi's ChatGPT sign-in sends its token to the public Responses API, and those responses carry no plan windows. Only a 429 says the plan is out. What the panel can still get, roughly in order of risk:

- **Interactive pi spend.** pi writes every session to `~/.pi/agent/sessions/**/*.jsonl`, with a list-price cost per message. ccusage and CodexBar both read those files. Counting them would split pi spend into SSSF agents and interactive use, as the Claude card already does. That needs no credentials. Built: SSSF's agents run pi with `--session-dir` inside the run, so they don't land there.
- **Codex CLI spend.** `~/.codex/sessions/` holds Codex's own sessions, if the engineer uses Codex directly.
- **Plan windows through `codex app-server`.** `account/rateLimits/read` returns the 5-hour and weekly windows with reset times. It needs Codex CLI installed and signed in to the same ChatGPT account. The method isn't documented by OpenAI, but Codex's own `/status` uses it, so it's the method least likely to break. Built as an opt-in poll, `SSSF_CODEX_LIMITS=1`. On Codex CLI 0.153.0, `codex app-server generate-json-schema` gives the reply: `rateLimits.primary` and `secondary`, each with `usedPercent`, `windowDurationMins` and `resetsAt` in epoch seconds, plus `planType`, `credits` and `rateLimitReachedType`. The first read on 2026-11-07 gave a Plus plan with a 300-minute window at 0% and a 10,080-minute window at 2%. An unused 5-hour window reports a reset a full window after the reading, since it starts on the next call. The same protocol has `account/rateLimitResetCredit/consume`, which spends a reset credit, so SSSF sends a fixed list of three read requests and a test holds it to that.

  pi draws on these windows. Run f0fe3deb in the sandbox repo, a ChatGPT-roster plan and build at 02:39 UTC on 2026-11-07, spent $0.065 at list price in 87 seconds. Before it, the 5-hour window was at 0% and unstarted, and the weekly window at 2%. After it, the 5-hour window was at 1%, with a reset 5 hours after the run's first call, and the weekly window at 3%. Codex reports whole percents, so one run says only that a full 5-hour window is somewhere around $4 to $13 at list price on Plus; the card's 100% figure waits for 10%.
- **Plan windows from `wham/usage` directly.** The same data, with SSSF holding the token from `~/.codex/auth.json`. Higher risk, since SSSF would handle a login token.
- **Codex's rollout files.** Codex logs a rate-limit snapshot in its session files on each turn ([pi-usage-limits.md](pi-usage-limits.md#3-how-codex-cli-reads-it)), so any direct Codex use leaves readings SSSF could read without a call.

Without a window reading, a 100% figure for ChatGPT can't be worked out the way the Claude card does it. Spend over time and the busiest 5-hour stretches still work, and the pi card already has both.

# Pi usage limits on a ChatGPT plan

Researched 2026-11-01. Can SSSF read a ChatGPT plan's usage windows for Pi agents, so a Pi roster can stop before the plan runs out, the way `rate_limit_stop` does for Claude Code?

## Short answer

Not today, and maybe not soon on the route SSSF uses. A cheap way to find out exists, though.

The research question assumed Pi calls the Codex backend (`chatgpt.com/backend-api/codex`). SSSF's ChatGPT roster doesn't. In Pi 1.4.0, `/login` then `openai` is OpenAI's new "Sign in with ChatGPT" token sharing. Pi sends that token straight to the public `https://api.openai.com/v1/responses` endpoint, the same one API keys use. The Codex-style login is a separate provider, `openai-codex`, which Pi now labels "legacy".

The two routes differ:

- **The Codex backend (Pi's legacy `openai-codex` provider, and Codex CLI).** It reports usage windows. Codex CLI reads them from `x-codex-*` response headers on HTTP streams and from a `codex.rate_limits` event on WebSocket streams. A separate `GET .../wham/usage` endpoint returns the same data. So the README's claim is wrong for this route.
- **The public Responses API with a ChatGPT token (what SSSF uses).** OpenAI's docs for this route describe no usage headers, no usage endpoint, and no way to read remaining plan usage. The only usage signal they document is an error once the limit is hit: `subscription_sharing_usage_limit_exceeded` (HTTP 429). Whether the responses carry undocumented `x-codex-*` headers anyway is unverified. Finding out needs one look at real response headers.

Pi exposes everything SSSF would need to read either one. An extension's `after_provider_response` handler gets every HTTP response's status and headers, and `provider_stream_event` gets every raw stream event. SSSF already passes extensions to Pi with `-e`. A small extension that logs headers during a normal run settles the open question, and it costs no extra request.

My recommendation is to ship the header-capture extension in observe-only mode first. If the `openai` route turns out to carry `x-codex-*` headers, wire them into `account.check`. If it doesn't, keep `max_run_cost` and `max_daily_cost` as the guard, and treat the 429 as a hard stop with no retry.

## 1. What the backend exposes

### Which endpoint Pi calls

SSSF's ChatGPT roster sets `model: openai/gpt-6.1-sol` (`.claude/skills/sssf/templates/sssf.config.chatgpt.yaml:11`). A real run in pi-agent-observability (`adws/adw_data/sessions/fce18c3c/scout/raw_output.jsonl`) records every assistant message as `provider: "openai"`, `api: "openai-responses"`. The local `~/.pi/agent/auth.json` holds an `oauth` entry for `openai` and no `openai-codex` entry (I checked key names and types only).

In Pi's bundled `pi-ai` 1.4.0 (`~/.nvm/versions/node/v24.11.1/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/`):

- `providers/openai.js:9-20` defines provider `openai` with `baseUrl: "https://api.openai.com/v1"`, an API-key login, and an OAuth login named "OpenAI (ChatGPT subscription)", "Sign in with ChatGPT".
- `auth/oauth/openai-chatgpt.js:1-5` says the flow "sends the resulting user access token directly to api.openai.com". Line 18 sets `RESOURCE = "https://api.openai.com/v1"` and line 23 requests scope `chatgpt.tokens.use.direct`.
- `api/openai-responses.js:26-30` (`isChatGPTSignIn`) tells a ChatGPT token from an API key by the missing `sk-` prefix.
- `providers/openai-codex.js:8-10` defines the other provider, `openai-codex`, named "OpenAI Codex (legacy)", at `https://chatgpt.com/backend-api`. `api/openai-codex-responses.js:465-472` appends `/codex/responses`. Its default transport is WebSocket (`docs/settings.md:124`, `transport` defaults to `"auto"`; `openai-codex-responses.js:186-200` tries WebSocket first and falls back to SSE).

### The public Responses API with a ChatGPT token

OpenAI's docs for this route:

- [Models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) says to send the token to `POST https://api.openai.com/v1/responses` and "do not point it at ChatGPT's `backend-api` endpoints." It says nothing about response headers, usage reporting, or reading remaining usage. A usage-limit error "can arrive as `response.failed`" with code `subscription_sharing_usage_limit_exceeded` or `subscription_sharing_usage_unavailable`.
- [Errors and recovery](https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery) documents `subscription_sharing_usage_limit_exceeded` (HTTP 429). It says to pause new requests on the plan and link to ChatGPT's usage settings. It also says not to "assume the entire plan is empty or infer a reset time from this code alone," because "an app-specific limit can also apply." `subscription_sharing_usage_unavailable` (HTTP 503) means the server couldn't check usage, so retry with backoff.
- The [overview](https://developers.openai.com/siwc/token-sharing-open-source) and [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) pages mention plan "limits" but don't describe them or any way to read them.

Third-party write-ups report that users set a weekly cap per app, separate from the plan's own windows ([dev.to](https://dev.to/hassann/sign-in-with-chatgpt-for-developers-the-oauth-flow-plan-usage-and-what-it-means-for-your-api-bill-48f7), [runtimewire](https://runtimewire.com/article/openai-launches-login-with-chatgpt-and-routes-plan-usage-into-third-party-ai-app)). I couldn't confirm that on an OpenAI page. Unverified.

What these responses carry in headers is unverified. The public API normally sends `x-ratelimit-*` headers, but those count requests and tokens per minute for an API organization, not plan windows. I made no authenticated request to check.

### The Codex backend

The `openai/codex` repo at `main` commit `67f0994b532b8740f14b5b738af7b7b2a915b1d6` (2026-10-31; latest release tag `rust-v0.166.0`) shows three places the backend reports usage. Paths below are under `codex-rs/`.

**Response headers.** `codex-api/src/rate_limits.rs:57-102` reads these on an SSE response:

| Header | Meaning |
|---|---|
| `x-codex-primary-used-percent` | float, 0 to 100 |
| `x-codex-primary-window-minutes` | integer |
| `x-codex-primary-reset-at` | integer, Unix seconds |
| `x-codex-secondary-used-percent`, `-window-minutes`, `-reset-at` | the same for the second window |
| `x-codex-limit-name` | optional label |
| `x-codex-credits-has-credits`, `x-codex-credits-unlimited`, `x-codex-credits-balance` | credits (lines 219-231) |
| `x-codex-rate-limit-reached-type` | set once a limit is hit (lines 188-193) |

`parse_all_rate_limits` (lines 28-51) also scans for other families, `x-<limit_id>-primary-used-percent` and so on, for metered limits besides `codex` (the code's example is `codex_other`). `codex-api/src/sse/responses.rs:45` calls it on every SSE response. There is no `reset-after-seconds` header. The reset is an absolute epoch in `-reset-at`.

**A WebSocket event.** On WebSocket streams the backend sends a JSON message with `type: "codex.rate_limits"` (`codex-api/src/endpoint/responses_websocket.rs:920-923`). Its shape (`rate_limits.rs:104-133`) is:

```
{ "type": "codex.rate_limits",
  "plan_type": ...,
  "rate_limits": { "primary":   { "used_percent": f64, "window_minutes": i64?, "reset_at": i64? },
                   "secondary": { ...same... } },
  "credits": { "has_credits": bool, "unlimited": bool, "balance": string? },
  "metered_limit_name": string?, "limit_name": string? }
```

**A usage endpoint.** `backend-client/src/client/rate_limit_resets.rs:124-129` builds `GET {base}/wham/usage` (ChatGPT-style base `https://chatgpt.com/backend-api`) or `GET {base}/api/codex/usage`. The response's windows (`codex-backend-openapi-models/src/models/rate_limit_window_snapshot.rs:14-23`) carry `used_percent` (integer), `limit_window_seconds`, `reset_after_seconds`, and `reset_at`. `backend-client/src/client.rs:770-783` converts seconds to minutes and keeps `reset_at`. This is a read, not a model call. Whether OpenAI counts it against the plan is unverified, though nothing in the client suggests it does.

**What `/status` shows.** The TUI asks the app server for `account/rateLimits/read` (`tui/src/app/background_requests.rs:138`). The app server answers by calling the usage endpoint (`app-server/src/request_processors/account_processor.rs:1200-1202`). The TUI labels each window by its length, not by whether it's primary or secondary. `tui/src/chatwidget/rate_limits.rs:103-131` maps about 300 minutes to "5h", 10080 to "weekly", and also knows daily, monthly, and annual. Only if the length is missing does it fall back to a primary or secondary label. Test fixtures use 300 and 10080 (`tui/src/status/tests.rs:301-306`), so on a typical plan primary is the 5-hour window and secondary the weekly one. SSSF should key on `window_minutes` the same way.

## 2. What Pi reads and surfaces

Pi doesn't parse any of this. A search of the Pi bundle and its `pi-ai` package for `x-codex`, `used_percent`, `codex.rate_limits`, and `wham/usage` found nothing. The only `rate_limits` hits are in vendored Anthropic and OpenAI admin SDK classes. Pi's usage handling is limited to errors:

- `api/openai-responses.js:185-189` adds a link to `https://chatgpt.com/settings/usage` when the error text contains `subscription_sharing_usage_limit_exceeded`.
- `api/openai-codex-responses.js:1256-1262` turns a Codex 429 with `usage_limit_reached` into "You have hit your ChatGPT usage limit", reading `plan_type` and `resets_at` from the error body.

Pi's JSON mode (`pi -p --mode json`, what `agent_pi.py:257` runs) emits session, agent, turn, message, and tool events. Assistant `message_end` events carry `usage` with token counts and a list-price `cost` and nothing about windows. I checked the event types and usage keys in two SSSF raw outputs. Pi's session files under `~/.pi/agent/sessions` contain no rate-limit fields either.

Pi does hand the raw data to extensions:

- `after_provider_response` gets `{ status, headers }` once per HTTP response, before the body is read (`pi-coding-agent/dist/core/extensions/types.d.ts:707-711`). `pi-coding-agent/dist/core/sdk.js:230-238` and `:274` wire it to the provider's `onResponse` callback. Both providers call `onResponse` on their HTTP path (`openai-responses.js:154`, `openai-codex-responses.js:286`).
- `provider_stream_event` gets every parsed stream event before Pi normalizes it (`types.d.ts:713-719`). Both providers pass every event through `onProviderStreamEvent` (`openai-responses.js:157`, `openai-codex-responses.js:542-545`). That includes WebSocket messages like `codex.rate_limits`, which `mapCodexEvents` passes to the callback before skipping types it doesn't know.
- `docs/extensions.md:125` warns that "provider hooks run only when that transport exposes the corresponding stage." On the legacy provider's WebSocket path Pi never calls `onResponse`, so headers aren't visible there. The `codex.rate_limits` event is visible, though.
- `docs/extensions.md:222` says `provider_stream_event` is notification-only and isn't persisted. `examples/extensions/provider-payload.ts:14-17` is a working example that logs status and headers from `after_provider_response` to a file.

## 3. How Codex CLI reads it

Codex CLI reads headers first, then events, then the endpoint:

1. On an SSE response, `parse_all_rate_limits` turns the headers into one `RateLimitSnapshot` per limit family (`codex-api/src/sse/responses.rs:45`, `:81`).
2. On a WebSocket stream, `parse_rate_limit_event` turns each `codex.rate_limits` message into a snapshot (`responses_websocket.rs:920-923`).
3. The core stores the newest snapshot and emits it inside a `TokenCount` event once token usage arrives (`core/src/session/turn.rs:3310-3315`; `core/src/session/mod.rs:5517-5542`).
4. `/status` and the app server's `account/rateLimits/read` call the usage endpoint instead.

The snapshot (`protocol/src/protocol.rs:2589-2603`) holds `limit_id`, `limit_name`, `primary`, `secondary`, `credits`, `individual_limit`, `spend_control_reached`, `plan_type`, and `rate_limit_reached_type`. Each window (`protocol.rs:2632-2641`) holds `used_percent` (0 to 100), `window_minutes`, and `resets_at` (Unix seconds).

Codex CLI's default ChatGPT base URL is `https://chatgpt.com/backend-api/codex` (`model-provider-info/src/lib.rs:86`). OpenAI's [Codex app-server page](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server) shows Codex can also run on a token-sharing token against `https://api.openai.com/v1`. Codex runs the same header parser on any SSE response, so if that route ever sends `x-codex-*` headers, Codex will read them. Whether it does is unverified.

**Rollout files.** Codex writes `TokenCount` events to its session rollouts (`rollout/src/policy.rs:134`), so each rollout holds rate-limit snapshots. A line is a `RolloutLine` (`history/src/lib.rs:375-381`): a `timestamp`, an optional `ordinal`, and the flattened item. Items are tagged by `type` in snake case (`history/src/rollout_payload.rs:31-33`; `EventMsg` at `protocol.rs:1515`). From the source, a rate-limit line looks like:

```
{ "timestamp": "...", "type": "event_msg",
  "payload": { "type": "token_count",
               "info": { ...token usage... },
               "rate_limits": { "limit_id", "limit_name", "primary": { "used_percent", "window_minutes", "resets_at" },
                                "secondary": {...}, "credits": {...}, "plan_type", ... } } }
```

Rollouts live under `~/.codex/sessions/` (`rollout/src/lib.rs:86`). Cold ones get compressed to `.jsonl.zst` (`rollout/src/compression.rs:26`, `:93`). This machine has no rollout files under `~/.codex/sessions`, so I couldn't check a real one. The shape above comes from source only. Reading rollouts would only help SSSF when the user also runs Codex on the same plan, and the sample would be as old as the last Codex turn.

## 4. Cheapest ways SSSF could read it

In order of cost:

1. **A Pi extension that captures headers and stream events. No extra request.** Register `after_provider_response` and keep any header that starts with `x-codex-` or `x-ratelimit-`, plus the status. Also register `provider_stream_event` and keep any event whose `type` is `codex.rate_limits`. Write the newest sample to `rate_limits.json` beside the project registry, with a `source` that says Pi and ChatGPT, the way `account.record` does for Claude. Then teach `account.check` to read a ChatGPT sample, by `window_minutes`, and call it for Pi agents in `agents.execute` (`templates/adws/adw_modules/agents.py:117-131`, which only checks `claude_code` today). SSSF already passes extensions with `-e` (`agent_pi.py:266-267`). The handler has to be fast, since Pi awaits it in stream order (`docs/extensions.md:224`). This works on SSE for headers and on either transport for the event. On the `openai` route it works only if the headers exist, which is the open question. Run it observe-only first: log every response header name, with no values, for one normal run. That answers the question at no cost.
2. **Read Pi's output.** This doesn't work. JSON mode and session files carry neither headers nor raw provider events.
3. **A separate usage request.** `GET https://chatgpt.com/backend-api/wham/usage` returns both windows without a model call. It's a read, so it probably doesn't count against the plan (unverified). It has two problems. OpenAI tells token-sharing apps not to call `backend-api` endpoints. And it needs a Codex-style ChatGPT token, while Pi's `openai` login token is issued for the `api.openai.com/v1` resource. Whether `wham/usage` accepts that token is unverified, and I'd expect not. Getting a Codex token means reading `~/.codex/auth.json` or Pi's legacy login, and SSSF shouldn't handle tokens. A cleaner version runs `codex app-server` and sends `account/rateLimits/read`, so Codex keeps its own token. That needs Codex installed and signed in to the same plan, and the app server is marked experimental.
4. **A probe request.** Sending a tiny model request just to read headers costs plan usage on every check. Not worth it.

## 5. Fallback if nothing is readable

- **Treat the 429 as a stop.** When a Pi message ends with `subscription_sharing_usage_limit_exceeded` in its error, stop the run with a `rate_limit_stop`-style trace event and no retry. Pi already declines to retry it on the codex path (`openai-codex-responses.js:315-318`), and `docs/settings.md:136` advises keeping provider retries at 0 so Pi can handle usage-limit errors. Per OpenAI's docs, don't guess a reset time from this code.
- **Dollar caps.** `max_run_cost` (default 5.0) and the repo's `max_daily_cost` in `repo.yaml` already cover Pi, using Pi's list-price `cost` (`budget.py:1-20`, `references/config.md:54`). The dashboard already sums Pi spend over rolling 5-hour and 7-day spans (`apps/visualizer/server/account.ts:185-202`). A per-window dollar cap for Pi would use the same sums, for example "stop when the last 5 hours of Pi spend passes $X". It's a proxy. It doesn't see the user's own ChatGPT or Codex use on the same plan, and nobody publishes how list-price dollars map to plan usage, so X has to be tuned by hand.

## Recommendation

1. Build the observe-only header logger (option 1) and run it on one normal ChatGPT-roster run. Log header names, not values. This settles whether the `openai` route sends `x-codex-*` headers.
2. If it does, finish option 1: record the sample, extend `account.check` to ChatGPT windows keyed by `window_minutes`, and call it for Pi agents.
3. If it doesn't, add the 429 hard stop and an optional rolling 5-hour Pi spend cap built from the dashboard's existing sums. Then correct the README and `references/config.md:239`. They say ChatGPT "doesn't send" usage windows. Accurate wording would be that the Codex backend sends them, but the token-sharing route SSSF uses documents none.
4. Don't switch the roster to Pi's legacy `openai-codex` provider just to get windows. Pi marks it legacy, and OpenAI points token-sharing apps at the public API.

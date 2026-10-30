# Config Reference

The full `sssf.config.yaml` spec: every field, how defaults merge, and how model / thinking / tools / extensions map onto the coding agent.

It lives at **`adws/adw_sssf_config/sssf.config.yaml`** — the default path every `adw_*.py` and the justfile resolve, and where `install.py` / `make_config.py` stamp it. Pass `--config <path>` to any ADW (or set `SSSF_CONFIG` for the justfile) to run against a different roster.

## Shape

```yaml
defaults:
  coding_agent: pi
  model: google/gemini-3.6-flash        # ALWAYS provider/model-id
  thinking: medium
  harness_engineering: []
  tools: [read, bash, edit, write, grep, find, ls]
  data_dir: adws/adw_data

observability:
  db: adws/adw_data/sssf.db
  poll_ms: 500

agents:
  - name: planner
    coding_agent: pi
    model: google/gemini-3.6-flash        # ALWAYS provider/model-id
    thinking: high
    color: "#a78bfa"
    purpose: Turn a request into a plan the builder can implement without asking questions.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/planner/system.md
      user: adws/adw_data/prompt_engineering/planner/user.md
    harness_engineering:
      - json-enforcer
    tools:
      - read
      - bash
```

## Fields

### `defaults`

| Field | Type | Meaning |
|---|---|---|
| `coding_agent` | `pi` \| `claude_code` | Which interface runs the agent: `agent_pi.py` or `agent_cc.py`. See [Claude Code](#claude-code-coding_agent-claude_code) for how the other fields map onto `claude`. |
| `model` | string | Model id. For Pi, any id registered in `~/.pi/agent/models.json`. Default `gemini-3.6-flash`. |
| `thinking` | enum | Reasoning effort — see below. Default `medium`. |
| `color` | hex string | Lane color for every agent that does not set its own. Default empty — the visualizer falls back to its own palette. |
| `harness_engineering` | list[string] | Pi extension paths. Rejected on a `claude_code` agent. |
| `tools` | list[string] | Roster-wide tool allowlist. Every agent that omits its own `tools` inherits this. Unset = all tools usable. |
| `protected_files` | list[string] | Paths **no** agent may modify unless it names them in its own `writes`: the same entry, or a path inside a protected directory. A broad glob such as `**/*.md` does not count. Default: `adws/adw_modules/`, `adws/adw_sssf_config/`, `adws/adw_*.py` — an agent must not be able to edit the machinery that decides whether its work passed — `specs/`, which is on record once `commit_plan` lands, and what Matt Pocock's skills write with the engineer: `.scratch/` (specs and tickets), `GLOSSARY.md`, `GLOSSARY-MAP.md`, and `docs/adr/`. |
| `data_dir` | path | Runtime home. Sessions land at `{data_dir}/sessions/{adw_id}/{agent_name}/`. Default `adws/adw_data`. |
| `rate_limit_stop` | fraction or `null` | Claude Code agents only. Before an agent starts, SSSF reads the account's last reported usage and stops the run if the 5-hour or 7-day window is at or over this fraction, with the time the window resets. Default `0.8`. `null` turns the check off. Each `claude` send records the report in `rate_limits.json` beside the project registry, so every repo sees the same account, and `just obs-all` shows it. Interactive sessions don't write a report, so the sample is as fresh as the last SSSF send. |
| `max_run_cost` | dollars or `null` | Before every send, retries and corrections included, SSSF compares what the run has spent with this cap and stops the run at or over it. Spend comes from the coding agent's own cost reports, at list prices, so on a subscription login it's an estimate of usage, not a bill. A run can go over by one call. The stop is recorded as `budget_stop`, and the message says how to look at, keep, or drop any uncommitted work. Default `5.0`. `null` turns it off. |
| `max_call_cost` | dollars or `null` | The roster-wide default for each agent's `max_call_cost`. Default `null`. |

### `observability`

| Field | Type | Meaning |
|---|---|---|
| `db` | path | SQLite trace db. `tracer.py` writes it directly; the visualizer polls it. Default `adws/adw_data/sssf.db`. |
| `poll_ms` | int | Visualizer live-poll cadence in ms. History uses the same queries, lazy-paged. Default `500`. |

### `agents[]`

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The identifier ADW scripts use. **ADWs name agents, never models.** |
| `purpose` | yes | One sentence: what this agent is for. Should match its `system.md` Purpose. |
| `prompt_engineering.system` | yes | Path to the system prompt — who the agent is, its single purpose, its output contract. |
| `prompt_engineering.user` | yes | Path to the default user prompt — the task template with `{{prompt}}`, `{{previous_envelope}}`, `{{context_handoff_dir}}`. |
| `color` | no | Hex swatch (`"#a78bfa"`) for this agent's lane in the visualizer. Travels config → `agent_sessions.color` → `/api/projects/:project/sessions/:adw_id`, and rides the `agent_start` event so a lane is colored while the agent is still running. Unset = the UI's fallback palette. |
| `coding_agent`, `model`, `thinking`, `color`, `harness_engineering` | no | Override the corresponding `defaults` key. |
| `tools` | no | Allowlist. **Omitting the key means all tools usable.** A capability list, not a boundary — see `writes`. |
| `writes` | no | What this agent may modify **in the repo**, enforced after every call. Omitted = unrestricted (still barred from `protected_files`). `[]` = no repo writes at all. A list = only those paths: a trailing `/` is a directory prefix, `*` matches within one path segment, `**` crosses segments, anything else is an exact path. Naming a `protected_files` path here is what unlocks it. **The session runtime under `data_dir` is always writable** — `writes: []` means read-only with respect to the repo, not unable to write its own report. |
| `max_call_cost` | no | Claude Code only. The most one call may spend, in estimated dollars, passed to `claude -p --max-budget-usd`. Claude Code counts it per call, even on a resumed session, and checks it after each API request. A call that hits it stops the run with no retry, recorded as `call_budget_stop`, and its spend still counts toward `max_run_cost`. A call that keeps hitting it is doing too much: split the task. |

Output types are deliberately absent: config defines who an agent *is*; the ADW call site defines how it's *used*. One agent serves many calls — same system prompt, different user prompt + output type per call.

## Defaults merging

`agents.py` merges each entry **over** `defaults`, key by key. An entry states only what differs; anything unset inherits. `agents.validate(cfg, REQUIRED_AGENTS)` then confirms every name an ADW declares exists, resolves to a usable coding agent + model, and has both prompt files present on disk. Any miss fails the run immediately — **no agent is ever spawned against a half-valid config.**

## Thinking levels

Pi's reasoning-effort ladder, lowest to highest:

```
off | minimal | low | medium | high | xhigh | max
```

Mapped to Pi's reasoning effort control and honored when the model is registered with `reasoning: true` in `~/.pi/agent/models.json`. On a non-reasoning model the setting is inert — no error, no effect. Rough guidance: `high`/`xhigh` for planners and reviewers, `medium` for builders, `low` for mechanical read-and-report agents. (On Claude Code the field maps to `--effort`; see [Claude Code](#claude-code-coding_agent-claude_code).)

## Model resolution

**Always write `model` as `provider/model-id`.** `agents.py` hands the string to the Pi interface, which resolves it against pi's merged catalog — `~/.pi/agent/models.json` plus pi's built-in providers. The same model is usually carried by more than one provider (`gemini-3.6-flash` lives under `google` *and* under `openrouter` as `google/gemini-3.6-flash`), and a bare id that matches several **raises at resolution**:

```
agent 'scout': model pattern 'gemini-3.6-flash' is ambiguous:
  [('google', 'gemini-3.6-flash'), ('openrouter', 'google/gemini-3.6-flash'), ...]
```

That is `agents.validate()` doing its job — it fails before anything spawns rather than silently billing the wrong provider — but it means every agent in the roster inheriting that default is grounded until the pattern is qualified. Qualifying is the whole fix: `google/gemini-3.6-flash`, `openai/gpt-5.6-terra`, `fireworks/accounts/fireworks/models/kimi-k3`. The leading segment is matched against the provider list first, so the rest of the string can contain slashes.

Other consequences worth knowing:

- **A subscription sign-in.** Pi can sign into some providers with a subscription instead of a key: `openai` with a ChatGPT login, `anthropic` with a Claude login. `pi auth check --provider openai --json` reports `"authType": "oauth"` when it has. Pi prefers that sign-in over a key in the environment, but falls back to the key when the sign-in lapses, and then bills the API. Set `SSSF_PI_AUTH=subscription` in `.env` and `agent_pi` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from Pi agents, so a lapsed sign-in fails the call instead. Keys for other providers still reach Pi. The cost Pi reports is list price, not a charge, the same as on Claude Code.
- A model must be in the catalog before any agent can name it. An unknown id fails at resolution, before spawn. `pi --list-models` is the catalog the resolver actually reads.
- **Ambiguity can appear without you touching the config.** Registering a new provider that carries a model you already use turns a formerly-fine bare pattern ambiguous. If a roster stops validating and nobody edited it, that is why.
- Provider credentials come from the environment, not the config — the key that matches the provider you named (`GEMINI_API_KEY` for `google/...`, `OPENROUTER_API_KEY` for `openrouter/...`).
- The resolved model is recorded per session in `agent_map.json` and mirrored into the `agent_sessions` table. **Changing an agent's model invalidates its session**: a joined run starts that agent fresh instead of resuming a context window built by a different model.

## Tools

`tools` maps to `pi --tools`. Pi's seven builtin tool names:

| Tool | Purpose | Pi's own default |
|---|---|---|
| `read` | read file contents | on |
| `bash` | execute bash commands | on |
| `edit` | find/replace edits | on |
| `write` | create/overwrite files | on |
| `grep` | search file contents | **off** |
| `find` | find files by glob | **off** |
| `ls` | list directory contents | **off** |

`grep`, `find`, and `ls` are off in bare Pi, so an agent that does not name them will shell out through `bash` to do the same work. The starter roster therefore sets `defaults.tools` to all seven and lets each agent narrow from there.

**Resolution order:** an agent's own `tools` list wins; an agent that omits the key inherits `defaults.tools`; if neither is set, `tools` stays `None` and all tools are usable. An empty list is not "all tools" — it is a tool-less agent, and it will stall.

## Write permissions — `writes` and `protected_files`

`tools` cannot express a safety boundary, because two of the tools are general
purpose. `bash` runs anything, including `git checkout`, which discards an
engineer's uncommitted work; `write` reaches any path, not only the one report
file an agent was granted it for. So "this agent changes nothing" is a claim a
tool list can state but never keep.

`adw_modules/permissions.py` keeps it, the same way every other claim in this
system is kept — after the fact, against the repo. Before an agent's first
prompt the working tree's change-set is fingerprinted; after its last send
(including JSON retries and gate corrections) it is fingerprinted again. Any
path that appeared, vanished, or changed is attributed to that agent.

Comparing change-sets rather than watching writes is deliberate: a path that was
modified before the agent ran and is clean afterwards has been **reverted**, and
a reversion is a modification. That is what catches `git checkout`.

A breach is not a gate violation. Gates are for work an agent can be asked to
redo; a write has already happened, so re-prompting fixes nothing. Instead:

1. every unauthorized change the agent **introduced** is rolled back — tracked
   files with `git checkout --`, untracked files by deletion;
2. a path that was **already dirty** before the agent ran is left untouched. The
   operator had uncommitted work there, and discarding it to tidy up would be
   the same harm this module exists to prevent;
3. the phase fails and names every path with what happened to it.

The run stops there, so the agent's permitted changes are never verified,
reviewed, or committed. The breach message lists them as left uncommitted, for
the engineer to finish or discard.

The run ends with that message in full and exit code 1, not a Python
traceback. Gate failures and budget and rate-limit stops end the same way.
A traceback means SSSF crashed, which is a bug to report.

To keep a breach from happening at all, the planner and builder prompts get the
`protected_files` list as `{{protected_files}}`. The planner puts any change the
request needs on a protected path under a `## For the engineer` heading, out of
the builder's steps, and the builder leaves those steps alone. When you write a
task that needs a protected edit, such as a new check in `quality.py`, say in the
task that the engineer makes that edit. The agents then build everything around
it, and you add it by hand after the run.

```yaml
defaults:
  protected_files: [adws/adw_modules/, adws/adw_sssf_config/, "adws/adw_*.py", specs/]

agents:
  - name: builder      # no `writes` key -> unrestricted, minus protected_files
  - name: scout
    writes: []         # no repo writes; its findings still land in context_handoff/
  - name: planner
    writes: [specs/]   # names the protected specs/, so it may write there
  - name: documenter
    writes: [app_docs/, docs/, "**/*.md", "*.md"]   # any Markdown, except specs/
```

**Only naming a protected path unlocks it.** The planner's `specs/` names the
protected `specs/`, so the planner may write there. The documenter's `**/*.md`
matches every spec too, but a broad glob does not name a protected path, so the
documenter may not change one. That keeps a spec as `commit_plan` recorded it:
in one real run, the builder's repo-wide `ruff format .` rewrote the Python
examples inside a committed spec, and `git add -A` carried the change into the
code commit.

**The session runtime under `data_dir` is always writable, for every agent.**
`context_handoff/` is how agents hand work to each other, and each agent's
prompts, `raw_output.jsonl`, and `envelope.json` sit beside it. That grant comes
from `data_dir` rather than from `.gitignore`: the runtime is normally ignored,
so it never even appears in a snapshot, but an agent's ability to record its own
work must not depend on a gitignore line someone can delete.

Narrow by role, not by reflex. Anything that must produce a `context_handoff/` artifact needs `write`, or it will resort to a `bash` heredoc. Withhold `edit`/`write` only where the restriction *is* the guarantee — a reviewer that cannot edit cannot quietly fix what it was asked to report.

### Extension tools must be named explicitly

`pi --tools` is an allowlist over **built-in, extension, and custom tools alike** — not just builtins. So the moment an agent has a `tools` list at all (its own, or one inherited from `defaults`), any tool registered by its `harness_engineering` extensions is **excluded unless it appears in that list by name**.

This fails quietly. The extension still loads, the run still succeeds, and the tool the extension exists to provide is simply never offered to the model — you find out by noticing the agent never called it.

```yaml
  - name: reviewer
    harness_engineering:
      - .pi/extensions/ast_query.ts     # registers tool: ast_query
    tools:
      - read
      - grep
      - find
      - ls
      - bash
      - ast_query                       # REQUIRED — the extension's tool, named or lost
```

Rule: **every entry in `harness_engineering` that registers a tool must have that tool name added to the agent's `tools` list.** Adding an extension is therefore a two-line change, never one. The alternative is dropping the `tools` key *and* leaving `defaults.tools` unset so the agent resolves to `None` (all tools) — but with a roster-wide `defaults.tools` in place, that escape hatch is closed; naming the tool is the only path.

## Harness engineering

`harness_engineering` entries are pi extension **file paths**, passed through as `pi -e <path>`, one flag per entry, scoped to that agent only. This is where per-agent harness changes live — e.g. an output-tightening extension for an agent that keeps wrapping its envelope in prose. The starter roster ships with none. Claude Code cannot load pi extensions, so validation rejects the field on a `claude_code` agent.

**If the extension registers a tool, name that tool in the agent's `tools` list too** — `--tools` filters extension tools exactly like builtins, so an unnamed extension tool is silently unavailable no matter that the extension loaded fine. See [Extension tools must be named explicitly](#extension-tools-must-be-named-explicitly) above. Extensions that only shape output or add flags (no tool registration) need no `tools` change.

## ChatGPT (Pi on a ChatGPT subscription)

`install.py --chatgpt` (or `make_config.py --chatgpt`) stamps `templates/sssf.config.chatgpt.yaml`. The harness stays Pi, so every field above applies unchanged. Only the models differ: `openai/gpt-6.1-sol` for the planner, builders, and reviewers, and `openai/gpt-6.1-terra` for the scout and documenter, as tested on 2026-10-29. The reviewers share the builder's model, so they don't get a second model's view.

Pi signs into ChatGPT with `pi`, then `/login`, then `openai`. Set `SSSF_PI_AUTH=subscription` in `.env`, so `agent_pi.py` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` out of each agent and a lapsed sign-in fails instead of billing the API. ChatGPT sends no usage windows with its responses, so `rate_limit_stop` doesn't apply, and the roster leaves it out. `max_run_cost` is the only cost limit.

`--chatgpt` can't be combined with `--coding-agent claude_code`. `install.py --update` keeps the ChatGPT roster, because `stamp.json` records which template each repo was stamped from.

## Claude Code (`coding_agent: claude_code`)

`install.py --coding-agent claude_code` (or `make_config.py --coding-agent claude_code`) stamps a ready roster from `templates/sssf.config.claude_code.yaml`. It keeps the Pi roster's prompts, purposes, and `writes`, and changes only the harness, the models, and the tool lists.

`adw_modules/agent_cc.py` runs the agent as `claude -p --output-format stream-json`. It has the same contract as `agent_pi.run()`, so prompts, envelopes, gates, `writes` enforcement, and the trace all work unchanged. Set it roster-wide in `defaults.coding_agent` or per agent. Pi and Claude Code agents can share one roster.

How the roster maps onto `claude`:

| Field | Becomes | Notes |
|---|---|---|
| `model` | `--model` | Write `anthropic/<id>`, e.g. `anthropic/claude-opus-5-5`. The prefix is stripped. Any other provider fails validation. A bare id or an alias like `opus` passes through. Claude has no offline catalog, so a misspelled id fails when that agent runs, not at validation. |
| `thinking` | `--effort` | `low` through `max` pass through. `off` and `minimal` become `low`, because `--effort` has no off switch. |
| `tools` | `--tools` | Pi names are translated: `read`→`Read`, `bash`→`Bash`, `edit`→`Edit`, `write`→`Write`, `grep`→`Grep`, `find` and `ls`→`Glob`. Claude has no LS tool and drops that name without an error, so `ls` lands on `Glob`. A capitalized name (`WebFetch`) is taken as a Claude tool name and passed through. Anything else fails validation. Unset means Claude's default set, which includes web, task, and scheduling tools. |
| `harness_engineering` | nothing | These are pi extensions. Validation rejects them on a `claude_code` agent. Remove them, and the `subagent_*` tools they register, before switching an agent over. |
| `system.md` | `--append-system-prompt` | Appended, not replaced. Claude Code keeps its own tool guidance and `system.md` adds the agent's identity. |

**Isolation.** Every run passes `--safe-mode` and `--disable-slash-commands`. The agent does not load the operator's `CLAUDE.md`, skills, plugins, hooks, or MCP servers. Without `--safe-mode`, `--setting-sources project` still let the global `CLAUDE.md` through. The agent sees its `system.md` and the repo, nothing else.

**Permissions.** Runs use `--permission-mode bypassPermissions`, since nobody is there to answer a prompt. The boundary is `writes` and `protected_files`, enforced by `permissions.py` after the call, the same as on Pi.

**Auth.** The default is the operator's subscription login. `agent_cc` removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the child's environment, because `.env` is loaded into every ADW and Claude prefers an API key when one is present. If the run's `init` event still reports an API key source (an `apiKeyHelper` in project settings, say), the run stops before the model is called. Set `SSSF_CLAUDE_AUTH=api_key` in `.env` to bill the API instead. `--bare` is never used, because it ignores OAuth.

**Sessions.** Claude needs a UUID session id, creates a session with `--session-id`, and continues one with `--resume`. `agent_cc` keeps a ledger file per session in `{agent}/claude_code_sessions/{uuid}.json`. If the file exists, the send resumes. That covers JSON retries, gate corrections, and a later phase rejoining the agent through `agent_map.json`. Claude stores the conversation itself under `~/.claude/projects/`, so factory sessions show up in `claude --resume` for that repo.

**Cost.** `total_cost_usd` in Claude's `result` event is a running total for the whole session, while its `usage` covers only the current send. The ledger keeps the last total, and each send is billed the difference. Only a total is reported, so the per-component cost columns in `agent_end` usage stay zero. On a subscription, cost is Claude's list-price figure for the tokens, not a charge.

**Context.** `context_tokens` is the last top-level assistant turn's `input + cache_creation + cache_read + output`. `context_window` comes from the `result` event's `modelUsage`.

`CLAUDE_CODE_PATH` in `.env` points at the binary when `claude` is not on PATH.

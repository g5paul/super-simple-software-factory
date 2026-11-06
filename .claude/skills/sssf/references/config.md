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
| `harness_engineering` | list[string] | Pi extension paths. Rejected on a `claude_code` agent. SSSF sets `OBS_NAME` to the agent's name for each Pi call, so an observability extension that reads it labels the session `builder` or `reviewer`. It also puts each run in its own pool, `OBS_POOL=sssf-<adw_id>`, unless you set `OBS_POOL` yourself, and adds the tags `sssf` and `adw-<adw_id>` to any `OBS_TAG` you set. Under `--sandbox vercel|e2b`, see [pi extensions in a VM](#pi-extensions-in-a-vm). |
| `tools` | list[string] | Roster-wide tool allowlist. Every agent that omits its own `tools` inherits this. Unset = all tools usable. |
| `protected_files` | list[string] | Paths **no** agent may modify unless it names them in its own `writes`: the same entry, or a path inside a protected directory. A broad glob such as `**/*.md` does not count. Default: `adws/adw_modules/`, `adws/adw_sssf_config/`, `adws/adw_*.py` — an agent must not be able to edit the machinery that decides whether its work passed — `specs/`, which is on record once `commit_plan` lands, and what Matt Pocock's skills write with the engineer: `.scratch/` (specs and tickets), `GLOSSARY.md`, `GLOSSARY-MAP.md`, and `docs/adr/`. |
| `data_dir` | path | Runtime home. Sessions land at `{data_dir}/sessions/{adw_id}/{agent_name}/`. Default `adws/adw_data`. |
| `rate_limit_stop` | fraction or `null` | Claude Code agents only. Before an agent starts, SSSF reads the account's last reported usage and stops the run if the 5-hour or 7-day window is at or over this fraction, with the time the window resets. A plan without a 5-hour window, such as a verified Ultra account, is checked on its 7-day window alone. It also stops a run when the account has moved onto usage credits, which bill at API rates once a Pro, Max or Ultra plan's limit is used up: before an agent starts, if the last report says so, and during a call, by killing the agent the moment Claude Code reports the switch. Default `0.8`. `null` turns off both checks, and lets runs spend credits. Each `claude` send records the report in `rate_limits.json` beside the project registry, so every repo sees the same account, and `just obs-all` shows it. Interactive sessions don't write a report, so the sample is as fresh as the last SSSF send. |
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

- **A subscription sign-in.** Pi can sign into some providers with a subscription instead of a key: `openai` with a ChatGPT login, `anthropic` with a Claude login. `pi auth check --provider openai --json` reports `"authType": "oauth"` when it has. Pi prefers that sign-in over a key in the environment, but falls back to the key when the sign-in lapses, and then bills the API. Set `SSSF_PI_AUTH=subscription` in `.env` and `agent_pi` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from Pi agents, so a lapsed sign-in fails the call instead. Keys for other providers still reach Pi. Pi agents get provider keys from `.env` only through SSSF's list of pi's provider keys (`PI_PROVIDER_KEYS`), plus `agent_env`. The cost Pi reports is list price, not a charge, the same as on Claude Code.
- A model must be in the catalog before any agent can name it. An unknown id fails at resolution, before spawn. `pi --list-models` is the catalog the resolver actually reads (in the operator container it reads it with the broker's token).
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

The git directory is outside every agent's `writes`, and outside the
change-set too, since `git status` never shows it. So it gets its own check.
Before each agent, SSSF saves `hooks/`, `info/`, `config`, `config.worktree`,
`commondir` and `sssf-private-names` in the repo's git directory, and in a worktree run the shared
one's too. It also saves every git config file git loads in the repo, any file
an `include` names, the directory `core.hooksPath` points at, and
`~/.gitconfig`, `~/.config/git/config` and the global ignore files even
before they exist. It never
saves the credential store. The copy is held in the SSSF process, not on disk,
so the agent can't edit it.

After the agent, and before any gate runs git, a change to any of them is put
back and stops the run with a breach naming the paths. A hook would run on the
chain's own commit, a `core.fsmonitor` or `filter` line runs on ordinary git
commands, `git config --global core.hooksPath` points every commit at the
agent's hooks, and a line in `info/exclude` would hide a new file from the
change-set. Emptying or deleting `sssf-private-names` would turn off the
pre-push hook's private-names check for your next push, and one the agent
creates is a change too. A breach here doesn't skip the other checks: an agent's own commit
is still undone and its other writes still rolled back, and the breach names
both. An agent that fails is put back the same way. SSSF also records
where every directory on the way to one of these paths resolves, walking the
path as git and the kernel do, `..` included. So it notices a symlink on the
way that now points somewhere else, such as a symlinked `~/.config`, `HOME`
or `.git`, or `lnk` in an include path `~/lnk/../x.inc`. It also notices a
real directory on the way that the agent swapped for a symlink, and a
directory a link points at that was swapped in turn. Nothing is written
through a symlink that changed or a directory swapped for one: SSSF reports
the change and stops the run, but never writes it back, since writing there
could land in a folder the agent chose. The breach says "NOT all put back"
and names the path, so check it by hand before your next git command in the
repo.

SSSF also notices a directory on the way that the agent removed, replaced
with a file, or made impossible to enter. Git would silently skip an include
through it, such as `~/d` in `~/d/../x.inc`. The run stops with "NOT all put
back" and names the directory. A watched file under it is still put back, and
if that remakes the directory, the breach says "put back as they were". No agent has a reason to write there: SSSF does the
committing.

SSSF reads the watched paths through their symlinks too, so a write through
a linked `~/.gitconfig` shows, and it reads at most 16 MB. If they come to
more, it can't check them, so the run stops before the first agent with a
breach saying they're too large to check. That happens when a link among
them, such as `.git/hooks`, `.git/sssf-private-names` or a linked git config
file, points at a large file or tree. Point it at something smaller, or
replace it with the file it stands for. If they pass 16 MB during an agent
call, that's a change, and the run stops with "NOT all put back".

An include path, or another git path SSSF watches, that has `..` in it and
a directory on the way that doesn't exist yet, that can't be entered, or
whose real path can't be read (a path longer than the system limit under a
directory you can't read), can't be checked. Once that directory is made or opened up, git resolves
`..` through the real directories, maybe through a symlink, and reads a
different file from the one SSSF watches. So the run stops before the next
agent with a breach naming the key, the config file and the path. That
includes a relative include such as `../team/x.inc` in `.git/config` while
`team/` doesn't exist. Write the path without `..`, or create the missing
directory first. A path with `..` whose directories all exist, and a path
with no `..` at all, are watched as before. If the agent makes a missing
directory in a path without `..` a symlink, the run stops with "NOT all put
back". A path without `..` under a directory whose real path can't be read is
watched as written. If the agent changes it, the run stops with "NOT all put
back" and SSSF doesn't write it back, since it can't check where the links on
the way lead.

SSSF's own git never runs an fsmonitor, and its undo and rollback run no
hooks. Two gaps it shares with a run without this check: an ignore file the
agent changed through a link can hide one of its own new files from the
rollback, and an agent that stops git ignoring your `.env` or `node_modules`
makes them look like its own new files, which the rollback deletes. That
includes a file whose name isn't valid UTF-8, which used to crash the rollback
before it deleted anything.

Two costs. An edit you make to your git config, or to
`.git/sssf-private-names`, while an agent runs is put back too; edit them
between runs. A git config or ignore file outside the repo, meaning outside both its git
directory and its working tree, is shared with everything you run, so the
breach marks it as maybe not the agent's change. A file in the working
tree, such as one an `include.path` or `core.excludesFile` names there, is
the agent's to edit, so it gets no mark, however its path is written. In a
worktree run that's the run's own worktree. For a config
file it lists the keys added (`+`), removed (`-`) or given a new value
(`~`), at most 10 per file and 30 in all, and never a value. For an ignore
or attributes file it says how many lines were added and removed. If the
change is yours, make it again with the command you used, such as
`gh auth setup-git`. The run stopped either way. Key names are shown as written, apart from the userinfo of a
`://` URL, so a secret in any other part of a key name is shown. And a tool that writes git config or hooks on install, like husky's
`prepare` script on `npm install` or `pre-commit install`, stops a run if an
agent runs it; run those in `worktree_setup`, which runs before the agents.

If git can't read the repo's git config at all, for example because an
include path runs into a symlink loop or a directory git can't enter, SSSF
can't ask git which files to watch. So the run stops before the next agent
with a breach that gives git's error. Fix the config file or path git names,
then run again. This applies whenever the repo root has a `.git`, and
whenever git fails for any reason other than "not a git repository", which
includes a broken `~/.gitconfig`. Before, SSSF treated such a repo as no
repo and ran the agent with no git-directory check. The breach quotes git's
error, and git quotes paths as written, so an include path the agent chose
can put text of its choosing there, even a false reason. Trust the stop, and
check the path git names by hand. The breach quotes at most 20 lines of
git's error, and cuts a line over 300 characters in the middle, keeping its
start and end. So a reason past line 20, in the middle of a long line, or
longer than about 148 characters can be lost; run the git command again
yourself to see it.

If git can't name the hooks directory, for example because `core.hooksPath`
leads through symlinks to a real path longer than the system limit, SSSF can't
watch the hooks git will run. So the run stops before the next agent with a
breach that gives git's error. Point `core.hooksPath` at a shorter path, then
run again. Before, SSSF ran the agent without watching any hooks directory.

The check catches mistakes and casual attempts. An agent runs as your user, so
a determined one could still get past it, for example by killing the chain
before the check runs, or by leaving a process behind that writes later. It
also doesn't watch a submodule's git directory, the index's
`--skip-worktree` flag, or the other name of a hard-linked `~/.gitconfig`.
It reads the config once before each agent, so a config that turns unreadable
partway through that read isn't caught.
It needs git 2.32 or later. To list a changed config's keys, it runs git with
`GIT_CONFIG_GLOBAL=/dev/null` so your `~/.gitconfig` isn't loaded. Older git
ignores that variable and loads it, `trace2` settings included, so an agent
that set `trace2.normalTarget` there could make that git write to a file of
its choosing.
It watches the config git loads on the current branch. A file an
`includeIf.onbranch:` for another branch names is watched, but an include or
a `core.hooksPath` set inside that file isn't. An agent can plant a hook
there, and it runs once someone switches to that branch.
If the agent moves one of your files onto a watched path that didn't exist
before the run, the put-back deletes it, as it deletes any new file there.
And it trusts `pwd -P`: one that fails but still prints a path gives SSSF
the wrong directory. dash and bash don't do that.
A new file with a non-ASCII name, under git's default `core.quotePath`, is
named in the breach but not deleted.
`--sandbox` and `--container` isolate the run.

The run stops there, so the agent's permitted changes are never verified,
reviewed, or committed. The breach message lists them as left uncommitted, for
the engineer to finish or discard.

The run ends with that message in full and exit code 1, not a Python
traceback. Gate failures, budget and rate-limit stops, and a change set
that cannot be taken (no git repo, no such `--base`, nothing changed) end
the same way.
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

## Agents that go quiet — `max_silence_minutes`

`max_silence_minutes` in `repo.yaml` stops an agent that writes nothing for that long, and fails the run with `SilentStop` and an `agent_silent` event. A hung agent spends nothing, so no cost cap catches it, and it holds its run and its worktree slot. It's on at 15 by default; `null` turns it off. Every line the agent writes resets the timer, on this machine and in a VM.

- **Claude Code** sends a heartbeat about every 30 seconds while a tool runs, and its Bash calls end within 10 minutes on their own. A stop there means the CLI itself stalled.
- **pi** streams only what a command prints, with no time limit on a command. A pi agent stuck on a command that prints nothing, such as a server started in the foreground, is the usual stop.
- **After the result.** An agent that has sent its result but stays alive gets 30 seconds, then the watchdog kills what's left and the call keeps its result, with a `leftovers_stopped` event. A Claude Code result sent while a background command still runs isn't final: the CLI waits for the command, then opens a new turn. So that wait gets the full limit, and a stop after it keeps the latest result too.
- **In a VM, a dead stream.** The agent's output comes over a connection that can drop with no error. After 2 minutes with no line, SSSF asks the VM whether the agent still runs, and asks again every 2 minutes after a yes. If the VM doesn't answer, or says the agent has exited while its stream never ended, the stream counts as dead: SSSF stops the agent once and fails the call with `SilentStop` and an `agent_stream_lost` event. With the VM gone, its SDK calls' own deadlines add a few minutes before the call fails. A check that can't tell counts as a yes, and the silence limit still applies. Nothing retries it; resume the run. A silence stop in a VM ends the read the same way. **Known limit:** the call is priced from the lines that arrived, so what the agent spent after the drop isn't counted, and an agent that finished while its stream was dead loses its result.
- **Raise it** when agents run quiet commands longer than 15 minutes, say a long build that prints nothing. Keep it above 10.

## Rules for every agent — `agent_rules`

`agent_rules` in `adws/adw_sssf_config/repo.yaml`, not in the roster, lists rules every agent gets, whatever its role or roster. SSSF appends them to each agent's system prompt under `## This repo's rules`, so the saved `prompts/system.md` shows what the agent read. Putting a rule here instead of in each prompt file keeps the prompts unchanged, so updates don't flag every one for review.

A rule is a plain string, which only asks, or a mapping with `forbid`, which is also enforced:

```yaml
agent_rules:
  - Keep commit subjects under 72 characters.
  - rule: Never run `git push`. Commit locally only.
    forbid: ['\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b']
  - rule: Never run the fh-* or stack recipes, or `pi` itself. They bill API keys from .env.
    forbid: ['\bjust\s+(fh-|fusion|plan\d)', '(^|[;&|]\s*)pi\b']
```

Each `forbid` entry is a Python regex, searched in the command of every shell call an agent makes: Claude Code's `Bash`, a subagent's included, and pi's `bash`. The check reads the stream when the call is announced. On a match, SSSF kills the agent and every process under it, records a `rule_breach` event with the agent, rule, pattern and command, and stops the run with `RuleBreach`. The dashboard shows "Stopped: an agent ran a command this repo forbids". The harness may have started the command before the kill, so the message says to check what it did. What the killed call spent still counts toward the run, the caps and the dashboard. pi prices every turn, so its spend is exact. Claude Code prices a call only in its final result, which a killed call never sends, so SSSF prices its tokens at list price from `adws/adw_modules/prices.json`. It reads each message's final output count from the `message_delta` that `--include-partial-messages` streams, because the usage on an `assistant` event is a snapshot from the message's start, and the `rule_breach` event says `cost_reported: false`. The dashboard's account panel prices transcripts from the same file. A model the table doesn't list adds its tokens and no cost. So SSSF warns once per run when a Claude call's result names a model the table doesn't list, subagents' models included, and records an `unlisted_model` log event. The account panel names such a model too. Add its price from claude.com/pricing to the skill's `prices.json`, then update the repos. To catch a changed price, run `uv run .claude/skills/sssf/scripts/check_prices.py` in the skill's source repo now and then. It compares the table with the page, field by field, and exits 1 on a difference; `--write` applies the page's prices. A pattern that isn't a valid regex fails the settings, and with them the run.

The search covers the whole command, heredoc bodies and quoted strings included, so `bash -c 'just test live'` and `cat <<EOF | bash` both match. A plan written through the shell with `cat > plan.md <<'EOF'` that says "never run `just test live`" matches too. The writing agents' prompts tell them to write files with their file tool instead, which no rule reads. Three attempts to skip heredoc bodies that only hold text each let real commands through, so the search no longer tries. The stream check and both refusers search the same text, line by line: `^` and `$` match at the start and end of every line, as a shell reads them, so `(^|[;&|]\s*)pi\b` above finds `pi` at the start of the second line of a command too. The search reads the text as written, not as the shell will run it, so quotes inside a word get past it: `j''ust test live` and `'just' test live` both run `just test live` and match neither check. A rule catches a mistake or a casual attempt, not an agent working around it.

`on_match: refuse` on a rule asks for the softer mode. On a pi agent, SSSF loads its own extension, `adws/adw_modules/sssf_rules.ts`, which blocks a matching `bash` call before it runs. The agent reads "SSSF refused this command" and the rule as the call's result, and the run goes on. Each refusal records a `rule_refused` log event and prints a line. The third refusal in one call stops the run with `rule_breach`, `refusals: 3`, so an agent can't loop on it. The extension reads the patterns as JavaScript regexes; one that doesn't compile there, or matches differently, isn't refused, and the stream check, reading it as Python, stops the run if that command runs.

Claude Code agents run with `--safe-mode`, which turns off every hook, including one passed with `--settings` (checked again 2026-11-03). Safe mode keeps environment variables, and Claude Code runs every `Bash` call through `CLAUDE_CODE_SHELL_PREFIX` when it is set. So for a repo with refuse rules, `agent_cc` sets it to SSSF's own script, `adws/adw_modules/sssf_refuse.py`, which reads the patterns as Python. A matching command is refused with exit code 126, and the agent reads `Exit code 126` and then the same refusal as pi's. The script searches the agent's own command, which Claude Code wraps in a shell line ending `eval '<command>' < /dev/null && pwd -P`. A line in another shape is searched whole, and so is each `eval '...'` argument in it, unquoted, so a pattern anchored to the start of a command, like `(^|[;&|]\s*)gh\s+`, still finds it. A project's session found that gap on 2026-11-03. An engineer's own `CLAUDE_CODE_SHELL_PREFIX` still runs, after SSSF's. In a VM, the runner copies the script in at the host path. Claude Code takes the prefix as one path, not a command, and fails every call if it can't run it, so `agent_cc` makes sure the script is executable before each call.

A repo's own scripts can refuse a command before it starts, which `forbid` can't promise. Every agent's environment, Claude Code or pi, carries `SSSF_ADW_ID`, the run's id, and `SSSF_AGENT`, its roster name, and so does every command it runs. A recipe that must never run under an agent can check first:

```just
fh-stack:
    @[ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: SSSF agents must not start pi. It bills .env's API keys."; exit 1; }
    pi --stack ...
```

The agent reads the refusal and carries on, and the run isn't stopped. An agent can unset the variable, so keep the `forbid` rule as the backstop.

It reads shell commands only. A command another tool runs, such as a pi extension's own tool, isn't checked. A pattern matches text, so a loose one catches ordinary commands: `\bgit\b.*\bpush\b` also stops `git stash push` and `git log --grep push`. The push pattern above allows only git's own options between `git` and `push`. Test a pattern with `re.search` against the commands agents really run before you add it. What an agent may change is enforced by `writes` and `protected_files`. For files agents must not read, see [Secret files](#secret-files--secret_files-and-agent_env). The three-refusal stop described above applies to the repo's own rules.

## Secret files — `secret_files` and `agent_env`

Agents must not read the repo's secret files. The protected set is `.env` and every `.env.*` except `.env.sample` and `.env.example`, anywhere in the repo or a run's worktree. `secret_files` in `repo.yaml` adds more. An entry with no `/` is a glob matched against a file name anywhere (`'*.pem'`). An entry with a `/` is a path or glob from the repo root (`config/credentials.json`). The data dir and `.git`, `node_modules`, `.venv`, `venv` and `__pycache__` are not searched.

SSSF finds the files when each agent starts, so a file created later isn't covered. Three routes keep them out:

- **Claude Code:** one `--disallowedTools "Read(//<abs>)"` per file, with `[ ] * ? ! \` and space in the path escaped by a backslash, since Claude Code reads the rule as a gitignore-style pattern. Read and Grep refuse it. Claude Code also emits a `permission_denied` system event for each refusal; SSSF ignores it, the agent reads the refusal and goes on, and the trace records the call as a failed tool call.
- **pi:** `sssf_rules.ts`, loaded into every pi agent, blocks `read`, `grep`, `find` and `ls` on the file's path.
- **Shell:** a built-in refuse rule refuses any command that names one of the files by name or path.

Refusals by the built-in rule never count toward the three-refusal stop, so a command that only mentions `.env` is refused and the run goes on. The cost caps bound a loop, and the stream check still backstops the rule: a matching call that wasn't refused stops the run.

`.env` keys don't reach agents. An agent gets only `SSSF_*`, `OBS_*` and `CLAUDE_CODE_OAUTH_TOKEN` from `.env`, plus pi's provider keys for pi agents. List any other key under `agent_env` to keep it. This includes an operator's `CLAUDE_CODE_SHELL_PREFIX` or any other Claude Code setting kept in `.env`. A key counts as from `.env` when `.env` names it, however it reached the environment, so a `just` recipe's `set dotenv-load` changes nothing. A shell export (direnv included) whose name `.env` doesn't set passes; one whose name `.env` also sets is held back too, since that name is a secret the repo keeps in `.env`. Quality checks, `worktree_setup` and git keep the full environment.

This stops accidental exposure, not an agent set on a secret. Agents run as the engineer's user, so one can build a path at runtime or read `/proc/<pid>/environ`. VMs are the boundary, since `.env` never enters them. The operator container mounts the repo and its `.env`, so it isn't one for `.env`. Also left open: obfuscated shell access, secrets exported in your shell that `.env` doesn't name, a grep over a whole folder, Edit and Write reading a file to edit it, and files under the pruned folders. The search also misses a secret file in a folder it can't read, one behind a symlinked folder, and a hard link to one. The names match case-sensitively, so on a case-insensitive filesystem, macOS's default, `.ENV` reads the protected `.env`. A deny path escapes `[ ] * ? ! \` and space; a path with other pattern characters, such as parentheses, `,` or `#`, may not be denied, which leaves that file as readable as before.

## Harness engineering

`harness_engineering` entries are pi extension **file paths**, passed through as `pi -e <path>`, one flag per entry, scoped to that agent only. This is where per-agent harness changes live — e.g. an output-tightening extension for an agent that keeps wrapping its envelope in prose. The starter roster's `planner` and `scout` load `adws/adw_data/harness_engineering/subagents.ts`. Claude Code cannot load pi extensions, so validation rejects the field on a `claude_code` agent.

**If the extension registers a tool, name that tool in the agent's `tools` list too** — `--tools` filters extension tools exactly like builtins, so an unnamed extension tool is silently unavailable no matter that the extension loaded fine. See [Extension tools must be named explicitly](#extension-tools-must-be-named-explicitly) above. Extensions that only shape output or add flags (no tool registration) need no `tools` change.

### pi extensions in a VM

Under `--sandbox vercel|e2b`, each `harness_engineering` path must be a file inside the repo, committed at the commit the run starts from. A file committed on the run's branch afterwards doesn't count. SSSF checks this before the VM opens, and if a path fails, it stops the run, naming the agent and the path.

In the VM, pi loads the file from the run's clone, at the worktree path, so files it imports beside it come along. Imports aren't checked. A missing one fails when pi loads the extension, and the run stops with pi's error.

`sssf_rules.ts` still loads last, after every listed extension.

In the VM, no command gets `OBS_AUTH_TOKEN`: not a check, git, setup, or an agent of either harness. A pi agent's obs events go through SSSF's relay to the host's obs server (`OBS_SERVER_URL`, or `http://127.0.0.1:43190`) when the agent loads at least one `harness_engineering` extension, the host has `OBS_AUTH_TOKEN`, and `OBS_DISABLE` isn't `true`. The relay listens on the VM's loopback, and the host posts the events with its own token, so the token never enters the VM. The host sets each event's pool, agent name and tags to the call's own. Otherwise obs is off: the agent gets `OBS_DISABLE=true`. If the relay won't start, obs is off for that call, with one warning in SSSF's trace. SSSF's trace records the run either way, with one `obs_relay` event per relayed call. Known limits: the extension flushes at most one batch of 50 as pi exits, and none if a post is already under way, so events still queued in pi then never reach the relay; a call's events past 20,000 or 20 MB aren't posted; and a warning from the live posts, such as obs being down, lands in the trace when the call ends. Every other `OBS_*` and `SSSF_*` variable crosses, and a host run still passes `OBS_AUTH_TOKEN` as before.

**Known limits on secrets in a VM.** Only that one name is held back, so a secret under another `OBS_` or `SSSF_` name crosses. The Claude token and pi's ChatGPT token reach agents in the VM by design, and nothing scans the commits or handoff folder that come back for secrets, so an agent that writes a token into the clone brings it to the host branch. A secret committed in the repo is in the clone too. The pre-push secret scan, when the repo has it, is what stops such a token leaving the machine.

**Known limit:** an agent can edit a committed extension in the VM, and the edited one loads for the next agent call. A host run has the same exposure, since an extension isn't a protected file. Nothing checks it.

### pi in the operator container

In the operator container (`container/run.sh`), `SSSF_PI_TOKEN_SOCKET` names a socket to the host's `pi_token_broker.py`. Before each pi agent call on a local tree, SSSF asks it for a one-hour token and sets `OPENAI_API_KEY` to it. The host keeps the sign-in. If the broker errors or doesn't answer, the call stops before pi starts. Config validation's model check (`pi --list-models`) and a trial's sign-in check for `openai` ask the broker the same way, so a broker error, or a stored `openai` login, stops validation with its message before any agent starts, instead of showing up as a missing model. A Claude Code roster never asks. `run.sh` removes a socket left at the broker's path, waits until the new broker answers, and exits 1 if it doesn't.

Each `harness_engineering` path must be inside the repo, committed or not, or the run stops before it starts. An `openai` entry in the `auth.json` pi reads in the container (`~/.pi/agent/auth.json`, or `$PI_CODING_AGENT_DIR/auth.json` when that is set) also stops the run, because pi would use it instead of the host's sign-in. SSSF finds that dir as pi 1.4.0 does: `~` or a leading `~/` is the home dir, a `file://` URL is read as a path, and `.` and `..` fold as text, so a symlink before a `..` doesn't move it. Any other value is used as written, `~user/x` included. So does anything at that path that isn't a regular file, a `PI_CODING_AGENT_DIR` that comes out relative (such as `~user/x`), a `file://` URL with a host, a `%`-escape, a query, an empty segment or any character beyond letters, digits and `._~!$&'()*+,;=@-`, and a value or `HOME` that isn't UTF-8. The check also reads `models.json` in that same dir: a `providers.openai` entry there with an `apiKey` (a literal, `$NAME` or `!command`) or any `headers` (at the provider, a model, or a model override) stops the run, because pi would use it ahead of the broker's token. A non-regular or unreadable `models.json` stops the run too. Models and settings without a key pass. obs works as on the host: no `OBS_DISABLE`, and `OBS_AUTH_TOKEN` is kept.

**Known limit:** any process in the container can get a one-hour token from the socket while the session is open, because everything there runs as one user.

**Known limit:** the check reads `auth.json` for an `openai` entry and `models.json` for an openai `apiKey` or `headers`. Other stored logins pi keeps are not checked.

**Known limit:** code in pi's `extensions/` dir in the agent dir, or a package that `settings.json` loads, can register an openai key in code and isn't checked.

**Known limit:** a `baseUrl`, `api` or `oauth` setting for openai in `models.json` passes. Each can send pi's calls, with the broker's token, to another endpoint, which can add a key of its own.

**Known limit:** SSSF reads key names only, so `"apiKey": "$OPENAI_API_KEY"` stops the run too.

**Known limit:** a `models.json` nested deeper than Python's JSON parser allows stops the run with a traceback instead of a message. It still stops before any pi agent starts.

**Known limit:** the check and pi read `auth.json` and `models.json` at different moments, so a file swapped between them can pass the check and still reach pi.

**Known limit:** an extension path inside the repo that is a symlink to a file outside it passes the launch check, and imports aren't followed.

**Known limit:** each connection to the socket starts one host `pi` process, with no cap, so a process in the container can load the host.

**Known limit:** the check reads `auth.json` and `models.json` only when each is a regular file, but a regular file whose read never ends, such as one on a FUSE mount, still holds the operator. It has no size limit either.

**Known limit:** `run.sh` removes an old socket before the broker binds its path, and another host process could bind it in between. Only the host user can write in `$XDG_RUNTIME_DIR`.

**Known limit:** `--sandbox vercel|e2b` launched from inside the container mints its VM tokens with the container's own pi, not through the broker.

**Known limit:** as in a VM, an agent can edit an extension in the repo, and the edited one loads for the next agent call.

## ChatGPT (Pi on a ChatGPT subscription)

`install.py --chatgpt` (or `make_config.py --chatgpt`) stamps `templates/sssf.config.chatgpt.yaml`. The harness stays Pi, so every field above applies unchanged. Only the models differ: `openai/gpt-6.1-sol` for the planner, builders, and reviewers, and `openai/gpt-6.1-terra` for the scout and documenter, as tested on 2026-10-29. The reviewers share the builder's model, so they don't get a second model's view.

Pi signs into ChatGPT with `pi`, then `/login`, then `openai`. Set `SSSF_PI_AUTH=subscription` in `.env`, so `agent_pi.py` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` out of each agent and a lapsed sign-in fails instead of billing the API. That sign-in calls OpenAI's public API through token sharing, which sends no usage windows with its responses (checked on real responses, 2026-11-01; only the Codex backend sends them). So `rate_limit_stop` doesn't apply, and the roster leaves it out. `max_run_cost`, and `max_live_cost` and `max_daily_cost` in `repo.yaml`, are its cost limits. At the plan's limit, OpenAI refuses the call with `subscription_sharing_usage_limit_exceeded`, and the run stops with a `rate_limit_stop` event whose `provider` is `openai`. OpenAI gives no reset time, so SSSF guesses none and blocks nothing ahead: the next run asks again. Leave pi's `retry.provider.maxRetries` setting at its default, 0. Above 0, pi retries every HTTP 429, this one too, before the run can stop.

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

**Auth.** The default is the operator's subscription login. `agent_cc` removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the child's environment, because `.env` is loaded into every ADW and Claude prefers an API key when one is present. If the run's `init` event still reports an API key source (an `apiKeyHelper` in project settings, say), the run stops before the model is called. Set `SSSF_CLAUDE_AUTH=api_key` to bill the API instead. `.env` keys no longer reach Claude Code agents, so with `SSSF_CLAUDE_AUTH=api_key`, export `ANTHROPIC_API_KEY` in your shell and keep it out of `.env`, or list it under `agent_env`. `--bare` is never used, because it ignores OAuth.

**Sessions.** Claude needs a UUID session id, creates a session with `--session-id`, and continues one with `--resume`. `agent_cc` keeps a ledger file per session in `{agent}/claude_code_sessions/{uuid}.json`. If the file exists, the send resumes. That covers JSON retries, gate corrections, and a later phase rejoining the agent through `agent_map.json`. Claude stores the conversation itself under `~/.claude/projects/`, so factory sessions show up in `claude --resume` for that repo.

**Cost.** `total_cost_usd` in Claude's `result` event is a running total for the whole session, while its `usage` covers only the current send. The ledger keeps the last total, and each send is billed the difference. Only a total is reported, so the per-component cost columns in `agent_end` usage stay zero. On a subscription, cost is Claude's list-price figure for the tokens, not a charge.

**Context.** `context_tokens` is the last top-level assistant turn's `input + cache_creation + cache_read + output`. `context_window` comes from the `result` event's `modelUsage`.

`CLAUDE_CODE_PATH` in `.env` points at the binary when `claude` is not on PATH.

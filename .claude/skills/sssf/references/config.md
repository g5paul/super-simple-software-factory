# Config reference

The full spec of the roster, `sssf.config.yaml`: every field, how defaults merge, and how the model, thinking level, tools and extensions map onto the coding agent. It also covers the repo's own settings in `repo.yaml`, and the guards they control.

The roster lives at `adws/adw_sssf_config/sssf.config.yaml`. That's the default path every ADW (AI developer workflow, an `adw_*.py` script) and the justfile resolve, and where `install.py` and `make_config.py` stamp it. To run against a different roster, pass `--config <path>` to any ADW, or set `SSSF_CONFIG` for the justfile.

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
| `coding_agent` | `pi` \| `claude_code` | Which coding agent runs the agent: pi through `agent_pi.py`, or Claude Code through `agent_cc.py`. See [Claude Code](#claude-code-coding_agent-claude_code) for how the other fields map onto `claude`. |
| `model` | string | Model id. For pi, any id registered in `~/.pi/agent/models.json`. Default `google/gemini-3.6-flash`. |
| `thinking` | string | Reasoning effort, see [Thinking levels](#thinking-levels). SSSF does not check the value. Pi gets it as written, and Claude Code maps a value it doesn't know to `medium`. Default `medium`. |
| `color` | hex string | Lane color for every agent that does not set its own. Default empty, and the dashboard then uses its own palette. |
| `harness_engineering` | list[string] | Paths of pi extensions. Rejected on a `claude_code` agent. SSSF sets `OBS_NAME` to the agent's name for each pi call, so an observability extension that reads it labels the session `builder` or `reviewer`. It also puts each run in its own pool, `OBS_POOL=sssf-<adw_id>`, unless you set `OBS_POOL` yourself, and adds the tags `sssf` and `adw-<adw_id>` to any `OBS_TAG` you set. Under `--sandbox vercel|e2b|openshell`, see [pi extensions in a VM](#pi-extensions-in-a-vm). |
| `tools` | list[string] | Roster-wide tool allowlist. Every agent that omits its own `tools` inherits this. Unset means every tool is usable. |
| `protected_files` | list[string] | Paths no agent may modify unless it names them in its own `writes`: the same entry, or a path inside a protected directory. A broad glob such as `**/*.md` does not count. Default in code: `adws/adw_modules/`, `adws/adw_sssf_config/` and `adws/adw_*.py`, so an agent can't edit the machinery that decides whether its work passed, and `specs/`, which is on record once `commit_plan` lands. The starter `sssf.config.yaml` lists those four and adds what Matt Pocock's skills write with the engineer: `.scratch/` for specs and tickets, `GLOSSARY.md`, `GLOSSARY-MAP.md` and `docs/adr/`. A roster that sets the key replaces the code default, so list all of them. |
| `writes` | list[string] | The roster-wide default for each agent's `writes`. `load_config` copies it into every agent that doesn't set its own. Unset means each agent without `writes` is unrestricted, except for `protected_files`. |
| `data_dir` | path | Runtime home. Sessions land at `{data_dir}/sessions/{adw_id}/{agent_name}/`. Default `adws/adw_data`. |
| `rate_limit_stop` | fraction or `null` | Claude Code agents only. Before an agent starts, SSSF reads the account's last reported usage and stops the run if the 5-hour or 7-day window is at or over this fraction, with the time the window resets. A subscription without a 5-hour window, such as a verified Ultra account, is checked on its 7-day window alone. It also stops a run when the account has moved onto usage credits, which bill at API rates once a Pro, Max or Ultra subscription's limit is used up. It checks for credits before an agent starts, if the last report says so, and during a call, where it kills the agent the moment Claude Code reports the switch. Default `0.8`. `null` turns off both checks, and lets runs spend credits. Each `claude` send records the report in `rate_limits.json` beside the project registry, so every repo sees the same account, and `just obs-all` shows it. Interactive sessions add readings too once the engineer sets SSSF's statusline, `scripts/statusline.py` in the skill. Claude Code hands it the same windows after every reply, and it merges them into `rate_limits.json`. Without it, the sample is as fresh as the last SSSF send. |
| `max_run_cost` | dollars or `null` | Before every send, retries and corrections included, SSSF compares what the run has spent with this cap and stops the run at or over it. Spend comes from the coding agent's own cost reports, at list prices, so on a subscription it's an estimate of usage, not a bill. A run can go over by one call. The stop is recorded as `budget_stop`, and the message says how to look at, keep, or drop any uncommitted work. Default `5.0`. `null` turns it off. |
| `max_call_cost` | dollars or `null` | The roster-wide default for each agent's `max_call_cost`. Default `null`. |

### `observability`

| Field | Type | Meaning |
|---|---|---|
| `db` | path | The trace, a SQLite database. `tracer.py` writes it directly, and the dashboard polls it. Default `adws/adw_data/sssf.db`. |
| `poll_ms` | int | Has no effect. Nothing reads it, and the dashboard polls every 500 ms. The field stays so older rosters still load. Default `500`. |

### `agents[]`

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | The identifier ADW scripts use. ADWs name agents, never models. |
| `purpose` | yes | One sentence that says what the agent is for. It should match the Purpose in its `system.md`. |
| `prompt_engineering.system` | yes | Path to the system prompt, which says who the agent is, its one purpose and its output contract. |
| `prompt_engineering.user` | yes | Path to the default user prompt, the task template with `{{prompt}}`, `{{previous_envelope}}`, `{{context_handoff_dir}}`. |
| `color` | no | Hex color, such as `"#a78bfa"`, for this agent's lane in the dashboard. It goes from the roster to `agent_sessions.color` to `/api/projects/:project/sessions/:adw_id`. The `agent_start` event carries it too, so a lane has its color while the agent is still running. Unset means the dashboard's fallback palette. |
| `coding_agent`, `model`, `thinking`, `color`, `harness_engineering` | no | Override the corresponding `defaults` key. |
| `tools` | no | Allowlist. Omitting the key makes every tool usable. The list grants capabilities and sets no boundary. `writes` sets the boundary. |
| `writes` | no | What this agent may modify in the repo, enforced after every call. Omitted means unrestricted, but still barred from `protected_files`. `[]` means no repo writes at all. A list means only those paths: a trailing `/` is a directory prefix, `*` matches within one path segment, `**` crosses segments, and anything else is an exact path. Naming a `protected_files` path here is what unlocks it. The session runtime under `data_dir` is always writable, so `writes: []` makes the agent read-only in the repo but still lets it write its own report. |
| `max_call_cost` | no | Claude Code only. The most one call may spend, in estimated dollars, passed to `claude -p --max-budget-usd`. Claude Code counts it per call, even on a resumed session, and checks it after each API request. A call that hits it stops the run with no retry, recorded as `call_budget_stop`, and its spend still counts toward `max_run_cost`. A call that keeps hitting it is doing too much, so split the task. |

The roster has no output types. The roster says who an agent is, and the ADW's call site says how it's used. One agent serves many calls, each with the same system prompt and its own user prompt and output type.

## Defaults merging

`agents.py` merges each entry over `defaults`, key by key. An entry states only what differs, and anything unset is inherited. `agents.validate(cfg, REQUIRED_AGENTS)` then confirms that every agent name an ADW declares exists, resolves to a usable coding agent and model, and has both prompt files on disk. Any miss fails the run at once, so no agent ever starts against a half-valid roster.

## Thinking levels

Pi's reasoning-effort levels, lowest to highest:

```
off | minimal | low | medium | high | xhigh | max
```

SSSF passes the level to pi's reasoning-effort control. Pi honors it when the model is registered with `reasoning: true` in `~/.pi/agent/models.json`. On a model without reasoning, the setting has no effect and raises no error. As a rough guide, use `high` or `xhigh` for planners and reviewers, `medium` for builders, and `low` for agents that only read and report. On Claude Code the field maps to `--effort`, as [Claude Code](#claude-code-coding_agent-claude_code) describes.

## Model resolution

Always write `model` as `provider/model-id`. `agents.py` hands the string to `agent_pi.py`, which resolves it against pi's merged catalog, the models in `~/.pi/agent/models.json` plus pi's built-in providers. The same model is usually carried by more than one provider. For example, `gemini-3.6-flash` is under `google`, and also under `openrouter` as `google/gemini-3.6-flash`. A bare id that matches several raises an error at resolution:

```
agent 'scout': model pattern 'gemini-3.6-flash' is ambiguous:
  [('google', 'gemini-3.6-flash'), ('openrouter', 'google/gemini-3.6-flash'), ...]
```

That's `agents.validate()` doing its job. It fails before anything starts, rather than quietly billing the wrong provider. But every agent in the roster that inherits that default can't run until the id names its provider. Naming the provider is the whole fix, for example `google/gemini-3.6-flash`, `openai/gpt-5.6-terra` or `fireworks/accounts/fireworks/models/kimi-k3`. The resolver matches the first segment against the provider list first, so the rest of the string can contain slashes.

Other things to know:

- **A subscription sign-in.** Pi can sign into some providers with a subscription instead of a key: `openai` with a ChatGPT sign-in, `anthropic` with a Claude sign-in. `pi auth check --provider openai --json` reports `"authType": "oauth"` when it has. Pi prefers that sign-in over a key in the environment, but falls back to the key when the sign-in lapses, and then bills the API. If you set `SSSF_PI_AUTH=subscription` in `.env`, `agent_pi` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from pi agents, so a lapsed sign-in fails the call instead. Keys for other providers still reach pi. Pi agents get provider keys from `.env` only through SSSF's list of pi's provider keys (`PI_PROVIDER_KEYS`), plus `agent_env`. The cost pi reports is list price, not a charge, the same as on Claude Code.
- A model must be in the catalog before any agent can name it. An unknown id fails at resolution, before the agent starts. `pi --list-models` prints the catalog the resolver reads. In the operator container, it reads the catalog with the broker's token.
- **Ambiguity can appear without a roster edit.** Registering a new provider that carries a model you already use makes a bare id that worked before ambiguous. If a roster stops validating and nobody edited it, that's why.
- Provider keys come from the environment, not the roster. Each provider takes its own key, for example `GEMINI_API_KEY` for `google/...` and `OPENROUTER_API_KEY` for `openrouter/...`.
- SSSF records the resolved model per session in `agent_map.json`, and copies it into the `agent_sessions` table. **Changing an agent's model invalidates its session.** A joined run starts that agent fresh instead of resuming a context window built by a different model.

## Tools

`tools` maps to `pi --tools`. Pi has seven built-in tools:

| Tool | Purpose | Pi's own default |
|---|---|---|
| `read` | read file contents | on |
| `bash` | run bash commands | on |
| `edit` | find/replace edits | on |
| `write` | create/overwrite files | on |
| `grep` | search file contents | off |
| `find` | find files by glob | off |
| `ls` | list directory contents | off |

`grep`, `find` and `ls` are off in bare pi, so an agent that doesn't name them shells out through `bash` for the same work. The starter roster sets `defaults.tools` to all seven, and each agent narrows the list from there.

An agent's own `tools` list wins. An agent that omits the key inherits `defaults.tools`. If neither is set, `tools` stays `None` and every tool is usable. An empty list isn't "all tools". It makes an agent with no tools, and that agent stalls.

## Write permissions, `writes` and `protected_files`

`tools` cannot express a safety boundary, because two of the tools are general
purpose. `bash` runs anything, including `git checkout`, which discards an
engineer's uncommitted work. `write` reaches any path, not only the one report
file an agent was granted it for. So "this agent changes nothing" is a claim a
tool list can state but never keep.

`adw_modules/permissions.py` keeps that claim the way SSSF keeps every other
one, after the fact and against the repo. Before an agent's first prompt, it
fingerprints the working tree's change-set. After the agent's last send, JSON
retries and gate corrections included, it fingerprints the change-set again. It
attributes any path that appeared, vanished or changed to that agent.

It compares change-sets instead of watching writes on purpose. A path that was
modified before the agent ran and is clean afterwards has been reverted, and a
reversion is a modification. That's what catches `git checkout`.

A breach isn't a failed gate. Gates are for work an agent can be asked to redo.
A write has already happened, so prompting again fixes nothing. Instead, SSSF
does three things:

1. It rolls back every unauthorized change the agent introduced. It restores
   tracked files with `git checkout --` and deletes untracked ones.
2. It leaves alone a path that was already dirty before the agent ran. The
   engineer had uncommitted work there, and discarding it to tidy up would be
   the same harm this module exists to prevent.
3. It fails the phase and names every path with what happened to it.

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
ADW's own commit, a `core.fsmonitor` or `filter` line runs on ordinary git
commands, `git config --global core.hooksPath` points every commit at the
agent's hooks, and a line in `info/exclude` would hide a new file from the
change-set. Emptying or deleting `sssf-private-names` would turn off the
pre-push hook's private-names check for your next push, and one the agent
creates is a change too. A breach here doesn't skip the other checks. An agent's own commit
is still undone and its other writes still rolled back, and the breach names
both. An agent that fails is put back the same way. SSSF also records
where every directory on the way to one of these paths resolves, walking the
path as git and the kernel do, `..` included. So it notices a symlink on the
way that now points somewhere else, such as a symlinked `~/.config`, `HOME`
or `.git`, or `lnk` in an include path `~/lnk/../x.inc`. It also notices a
real directory on the way that the agent swapped for a symlink, and a
directory a link points at that was swapped in turn. SSSF writes nothing
through a symlink that changed, or through a directory swapped for one. It
reports the change and stops the run, but never writes it back, since writing
there could land in a folder the agent chose. The breach says "NOT all put back"
and names the path, so check it by hand before your next git command in the
repo.

SSSF also notices a directory on the way that the agent removed, replaced
with a file, or made impossible to enter. Git would silently skip an include
through it, such as `~/d` in `~/d/../x.inc`. The run stops with "NOT all put
back" and names the directory. A watched file under it is still put back, and
if that remakes the directory, the breach says "put back as they were". No agent has a reason to write there, since SSSF does the
committing.

SSSF reads the watched paths through their symlinks too, so a write through
a linked `~/.gitconfig` shows, and it reads at most 16 MB. If they come to
more, it can't check them, so the run stops before the first agent with a
breach saying they're too large to check. That happens when a link among
them, such as `.git/hooks`, `.git/sssf-private-names` or a linked git config
file, points at a large file or tree. Point it at something smaller, or
replace it with the file it stands for. If they pass 16 MB during an agent
call, that's a change, and the run stops with "NOT all put back".

SSSF can't check an include path, or another git path it watches, that has
`..` in it and a directory on the way that doesn't exist yet, can't be
entered, or whose real path can't be read. For example, SSSF can't read the real path
of a path longer than the system limit under a directory you can't read. Once that directory is made or opened up, git resolves
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

The check has costs. An edit you make to your git config, or to
`.git/sssf-private-names`, while an agent runs is put back too, so edit them
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
`://` URL, so a secret in any other part of a key name is shown. A tool that writes git config or hooks on install, such as husky's
`prepare` script on `npm install` or `pre-commit install`, stops a run if an
agent runs it. Run those in `worktree_setup`, which runs before the agents.

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
longer than about 148 characters can be lost. Run the git command again
yourself to see it.

If git can't name the hooks directory, for example because `core.hooksPath`
leads through symlinks to a real path longer than the system limit, SSSF can't
watch the hooks git will run. So the run stops before the next agent with a
breach that gives git's error. Point `core.hooksPath` at a shorter path, then
run again. Before, SSSF ran the agent without watching any hooks directory.

The check catches mistakes and casual attempts. An agent runs as your user, so
a determined one could still get past it, for example by killing the run
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
It also trusts `pwd -P`. A `pwd -P` that fails but still prints a path gives
SSSF the wrong directory. dash and bash don't do that.
A new file with a non-ASCII name, under git's default `core.quotePath`, is
named in the breach but not deleted.
`--sandbox` and `--container` isolate the run.

The run stops there, so the agent's permitted changes are never verified,
reviewed or committed. The breach message lists them as left uncommitted, for
the engineer to finish or discard.

The run ends with that message in full and exit code 1, not a Python
traceback. Gate failures, cost-cap and rate-limit stops, and a change set
that can't be taken end the same way. A change set can't be taken when there's
no git repo, no such `--base`, or nothing changed.
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

Only naming a protected path unlocks it. The planner's `specs/` names the
protected `specs/`, so the planner may write there. The documenter's `**/*.md`
matches every spec too, but a broad glob does not name a protected path, so the
documenter may not change one. That keeps a spec as `commit_plan` recorded it.
In one real run, the builder's repo-wide `ruff format .` rewrote the Python
examples inside a committed spec, and `git add -A` carried the change into the
code commit.

The session runtime under `data_dir` is always writable, for every agent.
`context_handoff/` is how agents hand work to each other, and each agent's
prompts, `raw_output.jsonl` and `envelope.json` sit beside it. That grant comes
from `data_dir`, not from `.gitignore`. The runtime is normally ignored, so it
never appears in a snapshot. But an agent's ability to record its own work must
not depend on a gitignore line someone can delete.

Narrow `tools` by role, not by reflex. An agent that must produce a `context_handoff/` file needs `write`, or it falls back on a `bash` heredoc. Withhold `edit` and `write` only where the restriction is the guarantee. For example, a reviewer that can't edit can't quietly fix what it was asked to report.

### Extension tools must be named explicitly

`pi --tools` is an allowlist over built-in, extension and custom tools alike, not only the built-in ones. So once an agent has a `tools` list at all, its own or one inherited from `defaults`, pi excludes any tool its `harness_engineering` extensions register unless the list names that tool.

This fails quietly. The extension still loads, the run still succeeds, and pi never offers the model the tool the extension exists to provide. You find out only by noticing the agent never called it.

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
      - ast_query                       # REQUIRED: the extension's tool, named or lost
```

Every entry in `harness_engineering` that registers a tool must have that tool's name in the agent's `tools` list. So adding an extension is a two-line change, never one. The other way is to drop the `tools` key and also leave `defaults.tools` unset, so the agent resolves to `None`, meaning all tools. With a roster-wide `defaults.tools` in place, that way is closed, and naming the tool is the only path.

## A local sandbox: `--sandbox openshell`

`--sandbox openshell` runs the agents, checks and git in a local microVM made with NVIDIA OpenShell. It is free and needs no API key. A roster's pi agents must be on `openai/` models, and a roster with no Claude Code agent needs no token file. Run `just openshell-setup` once, or `just sssf openshell-setup` where SSSF's recipes are a module. It checks `/dev/kvm` access, runs the OpenShell gateway as a systemd user service with telemetry off, and builds the sandbox image with Podman. The image holds the repo's Python, meaning the major.minor version uv picks in the repo's main checkout. Every sandbox command gets that version as `UV_PYTHON`. uv installs it under `/usr/local` when the image is built, since the sandbox can't download one. When that version changes, the run stops before it starts and names `just openshell-setup`, which builds the image for it. It never runs `sudo`. It rewrites its own unit file, `~/.config/systemd/user/openshell-gateway.service`, on each run, so keep your own unit under another name. It creates TLS keys and `gateway.toml` only when they're missing. It refuses inside a run. When `SSSF_ADW_ID` is set, as it is for every agent, the recipe prints one line and exits non-zero before it does anything else. `scripts/openshell_image.py` does the same unless `--print` is given. That stops an agent calling them directly. It doesn't stop one that clears the variable, runs the steps by hand, or starts the recipe through something that gives it a fresh environment without the variable, such as `ssh localhost`, `sudo`, `systemd-run --user` or a `tmux` server started before the run. A session outside a run isn't marked at all. A repo still on an older copy of the recipe gets only the script's refusal, after the recipe has already written its files and started the gateway.

**Installing OpenShell.** `just openshell-setup` needs `openshell`, `openshell-gateway` and `podman` on `PATH`, and installs none of them. OpenShell's release ships plain tarballs for the CLI, the gateway, the supervisor and the VM driver. Check each against the SHA-256 NVIDIA publishes for the release, then unpack them all into one folder on `PATH`, such as `~/.local/opt/openshell/bin`. That needs no root. The release's `install.sh` runs `sudo apt-get install` instead.

The sandbox's network is closed except for default rules written per coding agent. `claude.exe` may reach `api.anthropic.com:443` for a roster with a Claude Code agent, and `/usr/local/bin/node` may reach `api.openai.com:443` for a roster with a pi agent. Each rule covers every command that agent runs, since OpenShell matches a process's parents. Read-only requests, meaning `GET`, `HEAD` and `OPTIONS`, may reach `pypi.org`, `files.pythonhosted.org` and `registry.npmjs.org`. GitHub is closed. A repo opens more with `sandbox_allow` in `repo.yaml`. Each attempt a rule refuses is a `sandbox_denied` event in the trace, and a run is never stopped for one. Env values go in on stdin and never on a command line, above all the Claude token and a pi agent's ChatGPT token, `OPENAI_API_KEY`. Here a pi agent also gets `PI_OFFLINE=1`.

## Repo settings in `repo.yaml`

`adws/adw_sssf_config/repo.yaml` holds the rules a repo keeps whatever roster runs, and every clone follows it. `repo_settings.py` rejects an unknown key, so a misspelled one fails the run.

| Key | Default | Meaning |
|---|---|---|
| `merge` | `any` | How `just merge` lands a `--worktree` run. `ff_only` refuses anything but a fast-forward. `SSSF_MERGE_FF_ONLY` in `.env` overrides it on one machine. |
| `max_worktree_runs` | `2` | How many `--worktree` runs may be alive at once. Set `1` if the checks bind a fixed port or share a database. |
| `max_live_cost` | off | Dollars every live run in the repo may spend together, checked at launch and before each agent call. |
| `max_daily_cost` | off | Dollars the repo's runs may spend in a rolling 24 hours, finished runs included. |
| `sandbox` | `none` | Where an ADW that takes `--sandbox` runs new work when the flag is left out: `none`, `vercel`, `e2b` or `openshell`. A resume stays where its run started. |
| `sandbox_allow` | `[]` | The hosts `--sandbox openshell` opens beyond its defaults, each entry one network rule: `binary` (a real path in the sandbox, or `*`), `host`, `port` (default `443`) and `access` (`read-only` or `full`). The key only matters under `--sandbox openshell`. See the example below. |
| `max_silence_minutes` | `15` | See [below](#agents-that-go-quiet-max_silence_minutes). |
| `worktree_setup` | `[]` | Shell commands run in order from a worktree's root before its first agent, such as `bun install --frozen-lockfile` (under agentsh, `bun install --frozen-lockfile --backend=copyfile`). |
| `worktree_setup_timeout` | `600` | Seconds each setup command may take before the run stops. |
| `agent_rules` | `[]` | See [Rules for every agent](#rules-for-every-agent-agent_rules). |
| `secret_files`, `agent_env` | `[]` | See [Secret files](#secret-files-secret_files-and-agent_env). |
| `local_guard` | `text` | `text` keeps SSSF's base guards alone. `agentsh` keeps them and also runs each local agent under the agentsh service with a generated default-deny policy. It needs `sandbox: none` and a git checkout. See [Running local agents under agentsh](#running-local-agents-under-agentsh-local_guard). |
| `deny_exec` | `[]` | Under `local_guard: agentsh`, the commands the policy refuses to exec. Each has `name` (a slug), `command` (a bare name), optional `args` (a Go regex run over all of argv joined by spaces, `argv[0]` included) and a `reason`. |
| `network_allow` | `[]` | Under `local_guard: agentsh`, the bare host names agents may reach on port 443, such as `api.example.com` or `*.example.com`. |

Example `sandbox_allow`:

```yaml
sandbox_allow:
  - binary: "*"
    host: api.example.com
    access: read-only
```

## Agents that go quiet, `max_silence_minutes`

`max_silence_minutes` in `repo.yaml` stops an agent that writes nothing for that long, and fails the run with `SilentStop` and an `agent_silent` event. A hung agent spends nothing, so no cost cap catches it, and it holds its run and its worktree slot. It's on at 15 by default, and `null` turns it off. Every line the agent writes resets the timer, on the host and in a VM.

- **Claude Code** sends a heartbeat about every 30 seconds while a tool runs, and its Bash calls end within 10 minutes on their own. A stop there means the CLI itself stalled.
- **pi** streams only what a command prints, with no time limit on a command. A pi agent stuck on a command that prints nothing, such as a server started in the foreground, is the usual stop.
- **After the result.** An agent that has sent its result but stays alive gets 30 seconds, then the watchdog kills what's left and the call keeps its result, with a `leftovers_stopped` event. A Claude Code result sent while a background command still runs isn't final: the CLI waits for the command, then opens a new turn. So that wait gets the full limit, and a stop after it keeps the latest result too.
- **In a VM, a dead stream.** The agent's output comes over a connection that can drop with no error. After 2 minutes with no line, SSSF asks the VM whether the agent still runs, and asks again every 2 minutes after a yes. If the VM doesn't answer, or says the agent has exited while its stream never ended, the stream counts as dead. SSSF stops the agent once and fails the call with `SilentStop` and an `agent_stream_lost` event. With the VM gone, its SDK calls' own deadlines add a few minutes before the call fails. A check that can't tell counts as a yes, and the silence limit still applies. Nothing retries it, so resume the run. A silence stop in a VM ends the read the same way. One limit is known. SSSF prices the call from the lines that arrived, so what the agent spent after the drop isn't counted, and an agent that finished while its stream was dead loses its result.
- **Raise it** when agents run quiet commands longer than 15 minutes, such as a long build that prints nothing. Keep it above 10.

## Rules for every agent, `agent_rules`

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

Each `forbid` entry is a Python regex, searched in the command of every shell call an agent makes: Claude Code's `Bash`, a subagent's included, and pi's `bash`. The check reads the stream when the call is announced. On a match, SSSF kills the agent and every process under it, records a `rule_breach` event with the agent, rule, pattern and command, and stops the run with `RuleBreach`. The dashboard shows "Stopped: an agent ran a command this repo forbids". The coding agent may have started the command before the kill, so the message says to check what it did. What the killed call spent still counts toward the run, the caps and the dashboard. pi prices every turn, so its spend is exact. Claude Code prices a call only in its final result, which a killed call never sends, so SSSF prices its tokens at list price from `adws/adw_modules/prices.json`. It reads each message's final output count from the `message_delta` that `--include-partial-messages` streams, because the usage on an `assistant` event is a snapshot from the message's start, and the `rule_breach` event says `cost_reported: false`. The dashboard's account panel prices transcripts from the same file. A model the table doesn't list adds its tokens and no cost. So SSSF warns once per run when a Claude call's result names a model the table doesn't list, subagents' models included, and records an `unlisted_model` log event. The account panel names such a model too. Add its price from claude.com/pricing to the skill's `prices.json`, then update the repos. To catch a changed price, run `uv run .claude/skills/sssf/scripts/check_prices.py` in the hub, the skill's source repo, now and then. It compares the table with the page, field by field, and exits 1 on a difference. `--write` applies the page's prices. A model the page doesn't list goes under `elsewhere` in `prices.json`, with the page it came from, and the check skips it. A pattern that isn't a valid regex fails the settings, and with them the run.

The search covers the whole command, heredoc bodies and quoted strings included, so `bash -c 'just test live'` and `cat <<EOF | bash` both match. A plan written through the shell with `cat > plan.md <<'EOF'` that says "never run `just test live`" matches too. The writing agents' prompts tell them to write files with their file tool instead, which no rule reads. Three attempts to skip heredoc bodies that only hold text each let real commands through, so the search no longer tries. The stream check and both refusers search the same text, line by line. `^` and `$` match at the start and end of every line, as a shell reads them, so `(^|[;&|]\s*)pi\b` above finds `pi` at the start of the second line of a command too. The search reads the text as written, and again with every backslash-newline removed, along with any backslashes right before it, so `ju\` then a newline then `st test live` and `just test \` then a newline then `live` both match. It reads the text a third time with the tabs right after each backslash-newline removed too, since a `<<-` heredoc strips a body line's leading tabs before the inner shell joins the lines: `bash <<-'EOF'` with `ju\` and `st test live` on tab-indented lines runs `just test live`. The second text keeps those tabs because outside a `<<-` body the shell reads one as a word break: `just test\`, a newline, a tab, then `live` runs `just test live`. A match in any of the three counts, and `^` and `$` match at every line in all three. The third text can over-match: `cat .e\`, a newline, a tab, then `nv` outside a heredoc runs `cat .e nv` and is still refused. Beyond that it doesn't read the command as the shell will run it, so these known limits get past it. The example `(^|[;&|]\s*)pi\b` misses pi after a wrapper word, such as `env pi`, `timeout 60 pi` or `bash -c 'pi'`. Under `local_guard: agentsh`, a `deny_exec` entry with `command: pi` refuses the exec whatever comes before it. Quotes inside a word: `j''ust test live` and `'just' test live` run `just test live` and match no check. A backslash before an ordinary character inside a word: `j\ust test live` does the same. A split command after a line the shell doesn't continue: the removal also joins a backslash-newline the shell keeps, as at the end of a comment or after an escaped backslash, so that line swallows the command's start and a `^` or `\b` anchor misses it. `# note\`, a newline, `ju\`, a newline, then `st test live` runs `just test live` and matches no check. Closing that would mean searching from each line after a continuation, which is quadratic. A `^` anchor on an indented line: a `<<-` body's leading tabs stay in the search, so `(^|[;&|]\s*)pi\b` misses `pi -p hi` on a tab-indented line of a `<<-` body, split or not, and a pattern anchored with `^` has to allow for them. Two shells reading one command: in an unquoted `<<-EOF` body the outer shell joins a single backslash-newline and keeps the tab, while a doubled backslash leaves a continuation the inner shell joins after it strips the tab. A body mixing the two, such as `ju\\` on one line and `st test\` on the next, runs `just test`, a tab, then `live`, and matches none of the three texts. Guard run cefd2f59 found it, and the engineer accepted it on 2026-11-08. A rule catches a mistake or a casual attempt, not an agent working around it.

`on_match: refuse` on a rule asks for the softer mode. On a pi agent, SSSF loads its own extension, `adws/adw_modules/sssf_rules.ts`, which refuses a matching `bash` call before it runs. The agent reads "SSSF refused this command" and the rule as the call's result, and the run goes on. Each refusal records a `rule_refused` log event and prints a line. The third refusal in one call stops the run with `rule_breach`, `refusals: 3`, so an agent can't loop on it. The extension reads the patterns as JavaScript regexes, so a pattern must read the same there as in Python. The settings fail to load, whatever the rule's `on_match`, on Python syntax JavaScript can't compile or reads differently: `\A`, `\Z`, `\z`, `\a`, `\U` and `\N{...}` escapes, named groups `(?P<name>...)` and backreferences `(?P=name)`, comment groups `(?#...)`, inline flag groups such as `(?i)` or `(?i:...)`, conditional `(?(1)...)` and atomic `(?>...)` groups, possessive quantifiers such as `++`, a quantifier with no minimum such as `{,3}`, and a character class opening with `]`, such as `[]x]`. The message names the construct and a portable spelling, such as `^` for `\A` or a plain group for a named one. Some differences no check catches. `\w`, `\d` and `\b` see non-ASCII letters and digits in Python but not in JavaScript. `\s` and `\S` split differently: Python's `\S` matches U+FEFF, which JavaScript counts as space, and Python's `\s` matches `\x1c` to `\x1f` and `\x85`, which JavaScript's doesn't. JavaScript's `.` doesn't match `\r`, U+2028 or U+2029. JavaScript's `^` and `$` also treat `\r` as a line end, and Python's don't, so `x=y`, `\r`, then `pi` is refused on pi but not by the stream check or Claude Code's prefix. Pi is the stricter side. JavaScript reads a character above U+FFFF, such as an emoji, as two, so `.`, `\S` or a negated class matches only half of it, and a quantifier after one applies to half of it. And a quantified lookbehind, such as `(?<=g)*`, loads but doesn't compile in JavaScript, so pi skips that pattern. A command that slips past pi through any of these runs, and the stream check stops the run on it. On pi it counts a refusal only when pi's result shows it never ran the call, and pi times every call it runs. So the command's output can't pass for a refusal. A refuse-rule call still waiting for its result when the agent's process ends, as when a command kills pi, stops the run with `rule_breach` too, on both coding agents. So keep a pattern to ASCII, spell whitespace as `[ \t]+` and a non-space as `[^ \t\n]`, use `[^\n]` rather than `.`, and keep a pattern meant to see past a character above U+FFFF from relying on one of those three.

Claude Code agents run with `--safe-mode`, which turns off every hook, including one passed with `--settings`. That was checked again on 2026-11-03. Safe mode keeps environment variables, and Claude Code runs every `Bash` call through `CLAUDE_CODE_SHELL_PREFIX` when it is set. So for a repo with refuse rules, `agent_cc` sets it to SSSF's own script, `adws/adw_modules/sssf_refuse.py`, which reads the patterns as Python. A matching command is refused with exit code 126, and the agent reads `Exit code 126` and then the same refusal as pi's. The script searches the agent's own command, which Claude Code wraps in a shell line ending `eval '<command>' < /dev/null && pwd -P`. A line in another shape is searched whole, and so is each `eval '...'` argument in it, unquoted, so a pattern anchored to the start of a command, like `(^|[;&|]\s*)gh\s+`, still finds it. A project's session found that gap on 2026-11-03. An engineer's own `CLAUDE_CODE_SHELL_PREFIX` still runs, after SSSF's. In a VM, the runner copies the script in at the host path. Claude Code takes the prefix as one path, not a command, and fails every call if it can't run it, so `agent_cc` makes sure the script is executable before each call. A matching `Bash` call that Claude Code denies itself, by its own permission rules, shows as its `permission_denied` event. It counts as refused the same way, three-refusal stop included. SSSF counts the prefix's refusal by its text, because Claude Code's stream gives it the same result as a command that printed it and exited 126. Forging one needs the prefix to miss a command the stream check matches. Both read the same command with Python `re`, so that takes a change in how Claude Code wraps a command, the shape above. `docs/open.md` in the SSSF hub watches for it. A `forbid` rule without `on_match: refuse` still stops the run on a match, denied or not.

A repo's own scripts can refuse a command before it starts, which `forbid` can't promise. Every agent's environment, Claude Code or pi, carries `SSSF_ADW_ID`, the run's id, and `SSSF_AGENT`, its roster name, and so does every command it runs. A recipe that must never run under an agent can check first:

```just
fh-stack:
    @[ -z "${SSSF_ADW_ID:-}" ] || { echo "Refusing: SSSF agents must not start pi. It bills .env's API keys."; exit 1; }
    pi --stack ...
```

The agent reads the refusal and carries on, and the run isn't stopped. An agent can unset the variable, so keep the `forbid` rule as the backstop.

These rules stop an agent's mistakes, not an agent set on getting past them. Agents run as the engineer's user, and the stream check reads what the coding agent writes to its stdout, which a command can write to as well: `printf '<event>' > /proc/$PPID/fd/1` adds a forged event to the stream, pi's or Claude Code's. SSSF reads only the running coding agent's own event types, so a forgery has to use those. A forged result can make a command that ran look refused, and a forged usage-limit error can end a run as `rate_limit_stop` instead of `rule_breach`. A VM, `--sandbox`, is the boundary for an agent you don't trust.

The check reads shell commands only. A command another tool runs, such as a pi extension's own tool, isn't checked. A pattern matches text, so a loose one catches ordinary commands: `\bgit\b.*\bpush\b` also stops `git stash push` and `git log --grep push`. The push pattern above allows only git's own options between `git` and `push`. Test a pattern with `re.search` against the commands agents really run before you add it. What an agent may change is enforced by `writes` and `protected_files`. For files agents must not read, see [Secret files](#secret-files-secret_files-and-agent_env). The three-refusal stop described above applies to the repo's own rules.

## Running local agents under agentsh, `local_guard`

With `local_guard: agentsh` in `repo.yaml`, every agent on the host runs as `agentsh wrap` under a policy SSSF generates from the repo's `secret_files`, `deny_exec` and `network_allow` settings. The base guards stay on, and agentsh is added around them, not swapped in. SSSF reads the setting once, when the run starts. A later edit reaches the next run only.

It's opt-in, and tested so far only on Python repos with a Claude Code roster. The checkout's path, a worktree's path and your home folder may not hold any of `* ? [ ] { } \ $`. agentsh puts `${PROJECT_ROOT}` and `${HOME}` into its rules as raw text, where these are glob syntax, so a run refuses before any agent starts. uv, npm, bun and Corepack can read their config in the folders above the repo, for these names only: `uv.toml`, `pyproject.toml`, `.python-version`, `.python-versions`, `package.json` and `pnpm-workspace.yaml`, plus `~/.bunfig.toml`. The folders themselves can be opened and checked but not listed. `uv venv` works on a system Python in `/usr/bin`, and an existing venv on one of your uv-managed Pythons works too. Agents' caches live in `adws/adw_data/agent-cache/`, one per repo, used only by wrapped agents. SSSF sets `UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `UV_PYTHON_BIN_DIR`, `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `npm_config_cache`, `npm_config_userconfig`, `npm_config_init_module`, `BUN_INSTALL_CACHE_DIR`, `BUN_RUNTIME_TRANSPILER_CACHE_PATH`, `COREPACK_HOME`, `npm_config_store_dir`, `npm_config_cache_dir` and `CARGO_HOME` to point there, so a first install in a repo downloads again. Agents can't write `~/.cache/uv`, `~/.local/share/uv`, `~/.npm` or `~/.cargo` directly, and a hardlink from a cache refuses the next launch (see below). They read only the uv-managed Pythons and tools, and may link to a managed interpreter. npm reads its user config and `npm init`'s module from the agent cache, not `~/.npmrc` and `~/.npm-init.js`, so a registry setting or token in your `~/.npmrc` doesn't reach agents. It was denied before, too. Put a repo's registry settings in its own `.npmrc`, take tokens from the environment, and add the host to `network_allow`. It needs agentsh `0.20.6-dev.fork+05b3777f`, the fork's commit `05b3777f` on `sssf/links-listing`, which is the build SSSF pins. Earlier builds fail `tar -x` on files in subfolders, let an agent read any file it could read outside the wrap through a namespace of its own, let it move a file it may not read into its workspace with `mv` and read it there, let it kill any process your user owns, the SSSF runner among them, let a DNS query carry data to a server the agent picked, let an agent list any folder it could open, and couldn't allow a venv on a uv-managed Python. Install the build and reinstall with `just agentsh-install` together, since a run refuses while either differs. The new build refuses every `/usr/bin` link under the old policies, and the old policies would let an agent link to `~/.claude/settings.json` or `.git/config` under the new build. After an SSSF update, reinstall with `just agentsh-install` and run the sudo lines it prints. Until you do, a run refuses, because the installed config and policies differ from what the checkout renders. A pi roster has no policy yet. Keep `local_guard: text` for a repo that needs any of these.

Install step, once per repo and again after those settings change:

1. Run `uv run --directory <hub> .claude/skills/sssf/scripts/agentsh_install.py --repo <project main checkout>`. The hub must be a clean checkout of the SSSF source on the host, at the skill version in the project's `stamp.json`. The script reads only `repo.yaml`, `sssf.config.yaml` and `stamp.json` from the project and runs nothing from it. It writes the policies and a merged server config into a fresh temp folder, shows a diff against `/etc/agentsh/`, or the whole file when nothing is installed, then prints the `sudo install` lines and `sudo systemctl restart agentsh`. Read the diff. A new host or a missing `deny_exec` there is a change to look into. It always prints the restart line, "Nothing to install" included. It never runs sudo.
2. Stop every agent, then run those lines. `sudo install` follows a link, so an agent running during the install could swap a temp file for a link to a root-only file and get it copied to `/etc/agentsh/sssf.yaml` at mode 0644. The script prints the same warning.

**Claude Code agents under agentsh.** They don't use your Claude Code files:

- **Their own folder.** Each run's Claude Code agents run from their own config folder, `adws/adw_data/sessions/<adw_id>/claude-config/`, one per run, and a retry's `--resume` reuses it.
- **A token.** They sign in with a token from `claude setup-token`, read from `CLAUDE_CODE_OAUTH_TOKEN` in the launching shell, or else from `~/.config/sssf-sandbox/token.env` (`CLAUDE_CODE_OAUTH_TOKEN=<token>`, mode 600), the file `--sandbox` reads. A run with neither refuses before it starts. The token can only run inference, and it lasts about a year. Agents never get your sign-in's refresh token, and their Bash calls don't see the token.
- **What they can't touch.** They can't read or write `~/.claude` or `~/.claude.json`. They can read Claude Code's program in `~/.local/share/claude` but not change it, and SSSF sets `DISABLE_AUTOUPDATER=1` for them. `~/.local/state/claude`, for locks, and `~/.cache/claude-cli-nodejs`, for logs, stay writable.
- **Who can change the folder.** A run's agents, and those of the repo's other runs, can change that folder. Agents load it under `--safe-mode`, where a planted hook doesn't fire.
- **Runs under `local_guard: text`.** With the base guards alone, agents keep your sign-in and `~/.claude`.
- **Reinstall.** After this update, reinstall with `just agentsh-install` and run the sudo lines it prints. The pin line in the rendered config now names `BINARY`, `CONFIG`, `POLICY_DIR`, `SERVER`, `API` and the pinned hash, so until you reinstall, a run refuses as "differs".

A run refuses to start, with a message, when:

- the service isn't active;
- the binary isn't the pinned build (the pin line in the rendered config names `BINARY`, `CONFIG`, `POLICY_DIR`, `SERVER`, `API` and `PINNED_SHA256`, so an edit to any of them in the checkout refuses as "differs");
- `BINARY` or `CONFIG` is a link. The install writes root-owned files there, never links;
- the installed config differs from what the checkout renders, apart from `policies.allowed`, where other repos' names may sit;
- an installed policy differs from the generated one;
- the service started before the binary or the config last changed, or its start time can't be read. The message gives `sudo systemctl restart agentsh`. The check counts whole seconds, so a restart in the same second as the install refuses once, and a second restart clears it;
- the checkout's or worktree's path, or your home folder, holds a glob character (see above);
- a link or a file is at the agent cache's path;
- a regular file in the workspace or the git common dir has a hard link outside them (see below), or a folder there can't be read, has a path of PATH_MAX or more, or stops being a folder during the check;
- SSSF can't render a config template; or
- the directory isn't a git checkout.

The two "differs" refusals name `git status adws/` and `git diff adws/` first, then the hub install. A denied exec counts toward the three-refusal stop like a text refusal. Denied file and network access goes to the trace with its path and never counts. The wrap sets `JUST_NO_DOTENV=true`, so `just` recipes under `set dotenv-load` don't load a `.env` the policy denies and fail on it. Three denials end the call as a breach even when it was leaving by another stop, such as a rate-limit stop. Below three, the other stop stands.

Limits that remain:

- a copied binary under a new name;
- a process that detaches from the run;
- quality checks, which run outside the wrap;
- the main checkout's `.claude/` secrets on a `--worktree` run;
- an `argv[0]` holding a space;
- a session a signal leaves open;
- the window between the printed lines and your sudo, when a running agent can swap the temp files;
- a diff that only helps if it's read;
- a surviving edit to other `adws/` code, which can change what the next run does. The `writes` check puts such an edit back after each phase;
- a link made by a process outside the wrap while an agent runs, which the next launch refuses;
- bun has no environment setting for its install backend, so a `bun install` without `--backend=copyfile` in `worktree_setup` or a check still links to `~/.bun/install/cache`, and the next launch refuses;
- a path of PATH_MAX (4,096 bytes on Linux) or more in the workspace or the git common dir. The walk can't read what's in it, so the launch refuses, names the path's first 200 characters and says to remove it;
- a process that unlinks a file during the walk can hide that file's outside link, and one that swaps a folder for a link while the walk reads it can do the same. Only a process acting during the walk can, such as an agent of another run in a worktree under the checkout;
- temp folders aren't walked, and neither are `sessions/` and `agent-cache/` under an absolute `data_dir` outside the repo on a plain run, or under the main checkout on a `--worktree` run. A link there to a cache, made outside the wrap, isn't refused;
- a clone made with `--shared` or `--reference` reads another repo's objects through `objects/info/alternates`. That isn't a hardlink, so the walk can't see it. Make the install clone with `git clone --no-local` and neither flag.

**Hard links outside the workspace.** agentsh judges a file operation by its path, so a write to a workspace file that has a second link in `~/.cache/uv`, `~/.bun/install/cache` or another clone's `.git` changes that copy too, and it then runs, outside the wrap, in your other projects. At every launch SSSF walks the workspace and the git common dir, follows no link and skips nothing, and refuses while a regular file there has more links than the walk found inside those two trees. Cost: the walk's time grows with the number of entries in the workspace and the git common dir, old session folders included, at one `lstat` per entry and about 0.5 to 1.2 s per 100,000 entries. On 2026-11-11 the hub held 725,510 entries, 709,880 of them under `adws/adw_data/sessions/`, which is several seconds per launch; archiving old session folders, by moving them out of the workspace, cuts it. To rebuild: `UV_LINK_MODE=copy uv sync --reinstall` for a uv venv; `rm -rf node_modules && bun install --backend=copyfile` for `node_modules`; `git clone --no-local` for a clone that shares git objects. One-time steps for the hub and sssf-sandbox: first remove the folders that earlier bypass reviews left in `adws/adw_data/sessions/` and that the walk refuses, meaning mode-000 folders and paths of PATH_MAX or more (the hub, as of 2026-11-11, has 8 mode-000 folders under runs `6bbc5ca1` and `f689a4dd`, and a 4,522-character chain under run `06ac79f1`). From the repo root, find mode-000 folders with `find adws/adw_data -type d -perm 000`, and paths too long for the check with ``find "$PWD/adws/adw_data" 2>/dev/null | awk 'length >= 4096 { print substr($0, 1, 200) }' | sort -u`` (GNU `find` walks paths past PATH_MAX; the absolute path is what counts, because that is what the walk passes). Look before you remove, then remove each with `chmod -R u+rwx <folder> && rm -rf <folder>`, picking the session folder that holds it. Then rebuild sssf-sandbox's `.venv`, rebuild both repos' `.claude/skills/sssf/apps/visualizer/node_modules`, and make `~/github/sssf-install` a `git clone --no-local`. Under agentsh, `worktree_setup`, quality checks, plan checks and `just rebase`'s setup get `UV_LINK_MODE=copy` and `npm_config_package_import_method=copy`; the cache folders stay yours. bun has no such setting, so write `bun install --backend=copyfile` in `worktree_setup` and checks. Wrapped agents get the same two settings with their own cache. Agents may still hardlink between workspace files; the check allows those. If you'd rather not, set `local_guard: text`.

The install refuses to run from a copy of the skill inside the project it installs for, so the SSSF hub installs its own policy from a separate clone of itself. See `docs/designs/agentsh-local-sandbox.md` in the SSSF hub.

More known limits, each with what still applies:

- `GIT_CONFIG_COUNT` with an alias, and `git send-pack`, reach exec without the word `push` where the rule looks. The network rules refuse GitHub.
- Any alias value naming `push`, in any case, is denied, such as `git -c 'alias.p=!f(){ git push; }; f' p`. The alias pattern's `.*push` also reaches past the alias value into later arguments. So an alias whose value only mentions push, such as `git -c alias.ci='commit -m push' ci`, is denied. So is a command after an alias option that names push anywhere, such as `git -c alias.st=status log --grep=push`. It fails closed, and each denial counts toward the three-refusal stop.
- The alias pattern reads the text, so it misses a quoted or escaped alias value that git joins before running it, such as `git -c 'alias.p=pu"sh"' p`. It also misses a verb git autocorrects with `help.autocorrect`, such as `git -c help.autocorrect=immediate puhs`, and an alias in a repo-local config the agent owns, such as `git -C /tmp/r p`. The network rules refuse GitHub.
- `mv .git .git2`, an edit and a move back, on a run without `--worktree`. The after-call guard undoes it.
- A `git -C` path, or any option value, holding `push` as a separate word after a space is denied. It fails closed.
- A stop with the run out of file descriptors, where neither `ps` nor `/proc` can be read, kills only the wrap. It has no parent-death signal, so the agent can run on until it exits.
- `git -c include.path=<file> p`, `git --config-env=alias.p=VAR p`, `GIT_CONFIG_GLOBAL=<file> git p` and `/usr/lib/git-core/git-push` also reach exec without the word where the rule looks. The network rules refuse GitHub.
- An `argv[0]` holding a newline or a tab gets past `sssf-no-push`, as one holding a space does. The network rules refuse GitHub.
- A multi-line commit message or `--grep` value naming `push` as its own word, after a leading option such as `git -C /x commit -m`, is denied. It fails closed, but each denial counts toward the three-refusal stop, so an agent retrying that message can end its run.
- A link's target is judged as the file it leads to, resolved from the link's folder through any links on the way, so a relative link in an allowed place works, as do `uv venv` and the links in npm's `node_modules/.bin`. A target that passes through `/proc` or `/dev/fd`, or holds `..` after a folder that doesn't exist, is denied. It fails closed. `worktree_setup` runs outside the wrap and isn't affected. Each denial counts as a file denial, not a refusal.
- A link is judged when it's made. A link moved later, or a folder on its path swapped for a link, can lead somewhere else. That matters only to a tool that follows it outside the wrap, such as your own editor or host git, since every read inside the wrap is judged at the file it reaches.
- On a folder that ignores case, such as `/mnt/c` under WSL, `.SSH` reaches `.ssh`, and a rule naming `.ssh` misses it.
- Under the wrap, a tool that needs a namespace gets `EPERM`: `unshare` and `clone` that ask for one, `setns`, `mount` and the new mount API, `chroot` and `pivot_root` are refused in every traced process. That stops bubblewrap, Chrome's and Electron's sandboxes and rootless podman. `clone3` gets `ENOSYS`, which glibc answers by falling back to `clone`, so threads and forks work as before. Claude Code and pi don't use a namespace in SSSF's runs.
- An agent may signal only processes in its own session. agentsh checks a signal before the kernel delivers it, so if the target exits and its pid is reused in between, the new process can be reached when your user may signal it. agentsh can't name signal 64 in a rule, so a program's signal 64, even to its own processes, is refused. A refused signal goes to the trace as a denial and doesn't count toward the three-refusal stop.
- The listed config files above the workspace are readable, so an index URL token in `uv.toml` or `pyproject.toml`, or a registry token in `~/.bunfig.toml`, reaches the agent. On a `--worktree` run that includes the main checkout's own `pyproject.toml` and `package.json`.
- A fresh venv needs a Python in `/usr/bin` that fits the repo's `requires-python`. Under the wrap, uv can't download a Python. An existing venv on one of your managed Pythons works. An existing `.venv`, or one `worktree_setup` makes, still works. Otherwise install the version into the repo's agent cache yourself, outside the wrap, from the main checkout: `F="$(git rev-parse --show-toplevel)/adws/adw_data/agent-cache"`, then `UV_PYTHON_INSTALL_DIR="$F/uv-python" UV_PYTHON_BIN_DIR="$F/bin" uv python install --no-bin 3.13`. `--no-bin` and `UV_PYTHON_BIN_DIR` keep uv from adding a `python3.x` to `~/.local/bin`. Agents may edit the Python this installs, so only wrapped agents' venvs and the workspace venvs quality checks run may use it. Never put `$F/bin` on your PATH, point your own uv at `$F/uv-python`, or base a venv of your own on it.
- An absolute link may point into a system folder only in `/usr/bin` or `/bin`, or at a uv-managed interpreter, `~/.local/share/uv/python/*/bin/python3.*`, so a Python under `/usr/local` or `/opt` can't be a venv's base. agentsh judges a link's own path and its target apart, so no agent can make a link inside your uv tree.
- Under agentsh an agent can list `/`, `/home`, its workspace, and on a plain run `adws/adw_data`, which is in the workspace. It can't list your home folder or `~/github`. Your other repos' names and files still reach it other ways. `/proc/<pid>/cwd` and `cmdline` show the folders your other processes run in. `stat` still says whether a name exists, and `inotify` and `fanotify` events can show names that change in a watched folder.
- One repo's wrapped runs share one agent cache, so one run's agent can plant a package that another run of the same repo installs.
- An agent can replace its workspace root with an absolute link to a folder it made. Host git, run in the workspace, would follow it. The after-call guard's swapped-directory check is expected to stop the run, but this hasn't been run under the service.
- On a `--worktree` run, agents may read and write the main checkout's `<data_dir>/sessions/`, which holds every run's handoff folder, and Claude Code agents may read `adws/adw_modules/sssf_refuse.py`. Secret files and git config stay denied inside the sessions folder. SSSF's own writes there never follow a link. It writes each file to a temp file in the same folder and renames it over the target, opens logs so a link, hard link or FIFO at the path is replaced, and replaces a link in place of a folder. This holds on plain runs too. On a plain run the data folder is in the workspace, so an agent that swaps `adws/adw_data` or a folder above it for a link still moves where SSSF writes, as before.
- Before each wrapped agent starts, SSSF makes `adws/adw_data/agent-cache` with a `mkdir` that doesn't follow a link at its path. If a link or a file is there, the launch refuses and SSSF leaves it in place for you to look at. On a plain run, a swap of `adws/adw_data` or a folder above it still moves the cache, as the line above says. SSSF makes the session folder's subfolders by path, so a folder link an agent put there is followed, and an empty folder can appear at its target. No file is written or deleted there. SSSF's reads under the session folder, such as `agent_map.json`, `plan.md` and the Claude Code cost ledger, follow links too. Both hold on plain runs and on `main` before the agentsh wrap.
- On a remote VM run, handoff files come back with the host's default mode, not their mode in the VM, and links in the handoff folder aren't copied back. A handoff script loses its execute bit, so run it with `sh x.sh`.
- An agent can write `.env.example` and `.env.sample` anywhere, outside its workspace too, because their allow sits ahead of every deny, as before.
- A denied exec names its command from argv when the event has no `command`, shortened to about 160 characters.
- An agent's edit to the project's `agentsh_sssf.yaml`, `agentsh_guard.py`'s pin constants or other `adws/` code never reaches the install, which renders from the hub. The next run refuses with the "differs" message. Discard the change, and don't reinstall. The `writes` check puts an edit to `adws/adw_modules/` back after each phase, and the running process doesn't reread it. A surviving edit to `adws/` code other than the pinned constants can change what the next run does.
- The install's version check reads the project's `stamp.json`, which an agent can edit, so it catches only an honest mismatch. A one-character `skill` value matches any hub version that starts with it. The diff before the sudo lines is the check that counts, so read it.
- `install.py --update` removes SSSF files a newer skill retired, going by `stamp.json`. An agent that forges a record there can have it delete a real `adws/adw_*.py` or `adws/adw_modules/` file, which it could delete directly anyway. Never anything outside those, a link's target, or a path with `..`. A forged record whose `sha256` matches a file the engineer customized, such as `quality.py`, makes `--update` overwrite that file with the template, as if it were untouched. A forged key under `container/` makes `--update` add the container templates the repo never asked for. A file already there that differs goes to review, marked `!`, and isn't overwritten. Run `git status` after an update.
- `install.py --update` checks every `stamp.json` record before it writes anything. A record with a null byte or non-UTF-8 key, a missing or non-hex `sha256`, a missing `source`, or a roster record that names no starter roster, makes it refuse with nothing written and name the record. So does a `stamp.json` that can't be read, isn't JSON, is nested too deeply to save, or has no `files` object. Restore it with `git checkout -- adws/adw_sssf_config/stamp.json`, check `git log` on it, and run the update again.
- A small `stamp.json` nested a few thousand levels deep can still stop `install.py --update`. Saving with `indent=2` writes about 2n² spaces for a value n deep, so the check can run out of memory before any write, or a 20 KB stamp can save as 200 MB. Restore it with `git checkout` as above.
- On a terminal that isn't UTF-8, a non-ASCII record key makes `--update` stop with a traceback after it saves the stamp, before its summary. The summary also prints record keys as they are, so a forged key's terminal escapes can hide lines of it. Run `git status` after an update.
- A FIFO or a very large file at `repo.yaml` or `sssf.config.yaml` makes the hub install hang or crawl. Stop it, and check those files with `git status`.
- A template that isn't valid UTF-8 gives a traceback when a run starts. It stops before any agent.
- A signal landing inside the walk can cut it short, and `_stopped` then blocks a second attempt. `rules.stop_tree` doesn't run again for that process, so what the walk missed can run on.

## Secret files, `secret_files` and `agent_env`

Agents must not read the repo's secret files. The protected set is `.env` and every `.env.*` except `.env.sample` and `.env.example`, anywhere in the repo or a run's worktree. `secret_files` in `repo.yaml` adds more. An entry with no `/` is a glob matched against a file name anywhere, such as `'*.pem'`. An entry with a `/` is a path or glob from the repo root, such as `config/credentials.json`. The data dir and `.git`, `node_modules`, `.venv`, `venv` and `__pycache__` are not searched.

SSSF finds the files when each agent starts, so a file created later isn't covered. Three routes keep them out:

- **Claude Code.** One `--disallowedTools "Read(//<abs>)"` per file, with `[ ] * ? ! \` and space in the path escaped by a backslash, since Claude Code reads the rule as a gitignore-style pattern. Claude Code may deny a protected-file read itself, in any tool: Read, Grep, and a Bash `cat .env` too, before SSSF's shell prefix sees it. Each denial arrives as a `permission_denied` system event naming the call. SSSF counts that call as refused, as if its own refusal were the result, matching by the call's id and never the message. The agent reads the denial and goes on. A Bash call a refuse rule matched records `rule_refused`, and the trace records the call as a failed tool call.
- **pi.** `sssf_rules.ts`, loaded into every pi agent, refuses `read`, `grep`, `find` and `ls` on the file's path. The agent reads "SSSF refused this read call: .env is one of this repo's secret files…", with grep, find or ls in place of read for those tools, and nothing is read.
- **Shell.** A built-in refuse rule refuses any command that names one of the files by name or path. Its refusal reads "SSSF refused this command, by SSSF's secret-file rule: …", so the agent can tell it from a repo's own rule.

Refusals by the built-in rule never count toward the three-refusal stop, so a command that only mentions `.env` is refused and the run goes on. The cost caps bound a loop, and the stream check still backstops the rule. A matching call that neither SSSF nor Claude Code refused stops the run.

`.env` keys don't reach agents. An agent gets only `SSSF_*`, `OBS_*` and `CLAUDE_CODE_OAUTH_TOKEN` from `.env`, plus pi's provider keys for pi agents. List any other key under `agent_env` to keep it. This includes the engineer's own `CLAUDE_CODE_SHELL_PREFIX` or any other Claude Code setting kept in `.env`. A key counts as from `.env` when `.env` names it, however it reached the environment, so a `just` recipe's `set dotenv-load` changes nothing. A shell export, direnv's included, whose name `.env` doesn't set passes. One whose name `.env` also sets is held back too, since that name is a secret the repo keeps in `.env`. Quality checks, `worktree_setup` and git keep the full environment.

This stops accidental exposure, not an agent set on a secret. Agents run as the engineer's user, so one can build a path at runtime or read `/proc/<pid>/environ`. VMs are the boundary, since `.env` never enters them. The operator container mounts the repo and its `.env`, so it isn't one for `.env`. Also left open: obfuscated shell access, secrets exported in your shell that `.env` doesn't name, a grep over a whole folder, Edit and Write reading a file to edit it, and files under the pruned folders. The search also misses a secret file in a folder it can't read, one behind a symlinked folder, and a hard link to one. The names match case-sensitively, so on a case-insensitive filesystem, macOS's default, `.ENV` reads the protected `.env`. A deny path escapes `[ ] * ? ! \` and space. A path with other pattern characters, such as parentheses, `,` or `#`, may not be denied, which leaves that file as readable as before.

## Harness engineering

`harness_engineering` entries are file paths of pi extensions, passed through as `pi -e <path>`, one flag per entry, scoped to that agent only. Per-agent changes to the coding agent go here, for example an extension that tightens the output of an agent that keeps wrapping its report in prose. The starter roster's `planner` and `scout` load `adws/adw_data/harness_engineering/subagents.ts`. Claude Code cannot load pi extensions, so validation rejects the field on a `claude_code` agent.

If the extension registers a tool, name that tool in the agent's `tools` list too. `--tools` filters extension tools just like built-in ones, so an extension tool the list doesn't name is unavailable, with no warning, even though the extension loaded. See [Extension tools must be named explicitly](#extension-tools-must-be-named-explicitly) above. Extensions that only shape output or add flags, and register no tool, need no `tools` change.

`subagents.ts` imports its helper `themeMap.ts`, from the same folder, and calls `applyExtensionDefaults` when a session starts. In an interactive pi session, that sets the color theme named for the extension in `THEME_MAP`, from `.pi/themes/`, falling back to `synthwave`, and the terminal title. SSSF runs its agents without a UI, so in a run it changes nothing. An extension of your own can call it the same way.

### pi extensions in a VM

Under `--sandbox vercel|e2b|openshell`, each `harness_engineering` path must be a file inside the repo, committed at the commit the run starts from. A file committed on the run's branch afterwards doesn't count. SSSF checks this before the VM opens, and if a path fails, it stops the run, naming the agent and the path.

In the VM, pi loads the file from the run's clone, at the worktree path, so files it imports beside it come along. Imports aren't checked. A missing one fails when pi loads the extension, and the run stops with pi's error.

`sssf_rules.ts` still loads last, after every listed extension.

In the VM, no command gets `OBS_AUTH_TOKEN`, whether a check, git, setup, or an agent on either coding agent. A pi agent's obs events go through SSSF's relay to the host's obs server, at `OBS_SERVER_URL` or else `http://127.0.0.1:43190`, when the agent loads at least one `harness_engineering` extension, the host has `OBS_AUTH_TOKEN`, and `OBS_DISABLE` isn't `true`. The relay listens on the VM's loopback, and the host posts the events with its own token, so the token never enters the VM. The host sets each event's pool, agent name and tags to the call's own. Otherwise obs is off, and the agent gets `OBS_DISABLE=true`. If the relay won't start, obs is off for that call, with one warning in SSSF's trace. SSSF's trace records the run either way, with one `obs_relay` event per relayed call. The relay has known limits. The extension flushes at most one batch of 50 as pi exits, and none if a post is already under way, so events still queued in pi then never reach the relay. A call's events past 20,000 or 20 MB aren't posted. A warning from the live posts, such as obs being down, lands in the trace when the call ends. Every other `OBS_*` and `SSSF_*` variable crosses, and a host run still passes `OBS_AUTH_TOKEN` as before.

**Known limits on secrets in a VM.** They hold for `--sandbox openshell` too. Only that one name is held back, so a secret under another `OBS_` or `SSSF_` name crosses. The Claude token reaches Claude Code agents and pi's ChatGPT token reaches pi agents, by design, and nothing scans the commits or handoff folder that come back for secrets, so an agent that writes a token into the clone brings it to the host branch. A secret committed in the repo is in the clone too. The pre-push secret scan, when the repo has it, is what stops such a token leaving the machine.

**Known limit.** An agent can edit a committed extension in the VM, and the edited one loads for the next agent call. A host run has the same exposure, since an extension isn't a protected file. Nothing checks it.

### pi in the operator container

In the operator container, which `container/run.sh` starts, `SSSF_PI_TOKEN_SOCKET` names a socket to the host's `pi_token_broker.py`. Before each pi agent call on a local tree, SSSF asks it for a one-hour token and sets `OPENAI_API_KEY` to it. The host keeps the sign-in. If the broker errors or doesn't answer, the call stops before pi starts. Roster validation's model check, `pi --list-models`, and a trial's sign-in check for `openai` ask the broker the same way. So a broker error, or a stored `openai` sign-in, stops validation with its message before any agent starts, instead of showing up as a missing model. A Claude Code roster never asks. `run.sh` removes a socket left at the broker's path, waits until the new broker answers, and exits 1 if it doesn't.

Each `harness_engineering` path must be inside the repo, committed or not, or the run stops before it starts. An `openai` entry in the `auth.json` pi reads in the container also stops the run, because pi would use it instead of the host's sign-in. That file is `~/.pi/agent/auth.json`, or `$PI_CODING_AGENT_DIR/auth.json` when that's set. SSSF finds that dir as pi 1.4.0 does: `~` or a leading `~/` is the home dir, a `file://` URL is read as a path, and `.` and `..` fold as text, so a symlink before a `..` doesn't move it. Any other value is used as written, `~user/x` included. So does anything at that path that isn't a regular file, a `PI_CODING_AGENT_DIR` that comes out relative, such as `~user/x`, a `file://` URL with a host, a `%`-escape, a query, an empty segment or any character beyond letters, digits and `._~!$&'()*+,;=@-`, and a value or `HOME` that isn't UTF-8. The check also reads `models.json` in that same dir. A `providers.openai` entry there with an `apiKey`, whether a literal, `$NAME` or `!command`, or with any `headers`, at the provider, a model or a model override, stops the run, because pi would use it ahead of the broker's token. A non-regular or unreadable `models.json` stops the run too. Models and settings without a key pass. obs works as on the host, with no `OBS_DISABLE`, and `OBS_AUTH_TOKEN` is kept.

**Known limit.** Any process in the container can get a one-hour token from the socket while the session is open, because everything there runs as one user.

**Known limit.** The check reads `auth.json` for an `openai` entry and `models.json` for an openai `apiKey` or `headers`. Other stored sign-ins pi keeps aren't checked.

**Known limit.** Code in pi's `extensions/` dir in the agent dir, or a package that `settings.json` loads, can register an openai key in code and isn't checked.

**Known limit.** A `baseUrl`, `api` or `oauth` setting for openai in `models.json` passes. Each can send pi's calls, with the broker's token, to another endpoint, which can add a key of its own.

**Known limit.** SSSF reads key names only, so `"apiKey": "$OPENAI_API_KEY"` stops the run too.

**Known limit.** A `models.json` nested deeper than Python's JSON parser allows stops the run with a traceback instead of a message. It still stops before any pi agent starts.

**Known limit.** The check and pi read `auth.json` and `models.json` at different moments, so a file swapped between them can pass the check and still reach pi.

**Known limit.** An extension path inside the repo that is a symlink to a file outside it passes the launch check, and imports aren't followed.

**Known limit.** Each connection to the socket starts one host `pi` process, with no cap, so a process in the container can load the host.

**Known limit.** The check reads `auth.json` and `models.json` only when each is a regular file, but a regular file whose read never ends, such as one on a FUSE mount, still holds the operator. It has no size limit either.

**Known limit.** `run.sh` removes an old socket before the broker binds its path, and another host process could bind it in between. Only the host user can write in `$XDG_RUNTIME_DIR`.

**Known limit.** `--sandbox vercel|e2b|openshell` launched from inside the container mints its VM tokens with the container's own pi, not through the broker.

**Known limit.** As in a VM, an agent can edit an extension in the repo, and the edited one loads for the next agent call.

## ChatGPT, pi on a ChatGPT subscription

`install.py --chatgpt`, or `make_config.py --chatgpt`, stamps `templates/sssf.config.chatgpt.yaml`. The coding agent stays pi, so every field above applies unchanged. Only the models differ: `openai/gpt-6.1-sol` for the planner, builders, and reviewers, and `openai/gpt-6.1-terra` for the scout and documenter, as tested on 2026-10-29. The reviewers share the builder's model, so they don't get a second model's view.

Pi signs into ChatGPT with `pi`, then `/login`, then `openai`. Set `SSSF_PI_AUTH=subscription` in `.env`, so `agent_pi.py` keeps `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` out of each agent and a lapsed sign-in fails instead of billing the API. That sign-in calls OpenAI's public API through token sharing, which sends no usage windows with its responses. That was checked on real responses on 2026-11-01. Only the Codex backend sends them. So `rate_limit_stop` doesn't apply, and the roster leaves it out. `max_run_cost`, and `max_live_cost` and `max_daily_cost` in `repo.yaml`, are its cost caps. At the subscription's limit, OpenAI refuses the call with `subscription_sharing_usage_limit_exceeded`, and the run stops with a `rate_limit_stop` event whose `provider` is `openai`. OpenAI gives no reset time, so SSSF guesses none and stops no run ahead of time. The next run asks again. Leave pi's `retry.provider.maxRetries` setting at its default, 0. Above 0, pi retries every HTTP 429, this one too, before the run can stop.

`--chatgpt` can't be combined with `--coding-agent claude_code`. `install.py --update` keeps the ChatGPT roster, because `stamp.json` records which template each repo was stamped from.

## Claude Code (`coding_agent: claude_code`)

`install.py --coding-agent claude_code`, or `make_config.py --coding-agent claude_code`, stamps a ready roster from `templates/sssf.config.claude_code.yaml`. It keeps the pi roster's prompts, purposes and `writes`, and changes only the coding agent, the models and the tool lists.

`adw_modules/agent_cc.py` runs the agent as `claude -p --output-format stream-json`. It has the same contract as `agent_pi.run()`, so prompts, envelopes, gates, `writes` enforcement, and the trace all work unchanged. Set it roster-wide in `defaults.coding_agent` or per agent. Pi and Claude Code agents can share one roster.

How the roster maps onto `claude`:

| Field | Becomes | Notes |
|---|---|---|
| `model` | `--model` | Write `anthropic/<id>`, for example `anthropic/claude-opus-5-5`. The prefix is stripped. Any other provider fails validation. A bare id or an alias like `opus` passes through. Claude has no offline catalog, so a misspelled id fails when that agent runs, not at validation. |
| `thinking` | `--effort` | `low` through `max` pass through. `off` and `minimal` become `low`, because `--effort` has no off switch. |
| `tools` | `--tools` | Pi names are translated: `read`→`Read`, `bash`→`Bash`, `edit`→`Edit`, `write`→`Write`, `grep`→`Grep`, `find` and `ls`→`Glob`. Claude has no LS tool and drops that name without an error, so `ls` lands on `Glob`. A capitalized name, such as `WebFetch`, is taken as a Claude tool name and passed through. Anything else fails validation. Unset means Claude's default set, which includes web, task, and scheduling tools. |
| `harness_engineering` | nothing | These are pi extensions. Validation rejects them on a `claude_code` agent. Remove them, and the `subagent_*` tools they register, before switching an agent over. |
| `system.md` | `--append-system-prompt` | Appended, not replaced. Claude Code keeps its own tool guidance and `system.md` adds the agent's identity. |

**Isolation.** Every run passes `--safe-mode` and `--disable-slash-commands`. The agent doesn't load the engineer's `CLAUDE.md`, skills, plugins, hooks, or MCP servers. Without `--safe-mode`, `--setting-sources project` still let the global `CLAUDE.md` through. The agent sees its `system.md` and the repo, nothing else.

**Permissions.** Runs use `--permission-mode bypassPermissions`, since nobody is there to answer a prompt. The boundary is `writes` and `protected_files`, enforced by `permissions.py` after the call, the same as on pi.

**Auth.** The default is the engineer's subscription sign-in. `agent_cc` removes `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` from the child's environment, because `.env` is loaded into every ADW and Claude prefers an API key when one is present. If the run's `init` event still reports an API key source, such as an `apiKeyHelper` in project settings, the run stops before the model is called. Set `SSSF_CLAUDE_AUTH=api_key` to bill the API instead. `.env` keys no longer reach Claude Code agents, so with `SSSF_CLAUDE_AUTH=api_key`, export `ANTHROPIC_API_KEY` in your shell and keep it out of `.env`, or list it under `agent_env`. `--bare` is never used, because it ignores OAuth.

**Sessions.** Claude needs a UUID session id, creates a session with `--session-id`, and continues one with `--resume`. `agent_cc` keeps a ledger file per session in `{agent}/claude_code_sessions/{uuid}.json`. If the file exists, the send resumes. That covers JSON retries, gate corrections, and a later phase rejoining the agent through `agent_map.json`. Claude stores the conversation itself under `~/.claude/projects/`, so factory sessions show up in `claude --resume` for that repo.

**Cost.** `total_cost_usd` in Claude's `result` event is a running total for the whole session, while its `usage` covers only the current send. The ledger keeps the last total, and each send is billed the difference. Only a total is reported, so the per-component cost columns in `agent_end` usage stay zero. On a subscription, cost is Claude's list-price figure for the tokens, not a charge.

**Context.** `context_tokens` is the last top-level assistant turn's `input + cache_creation + cache_read + output`. `context_window` comes from the `result` event's `modelUsage`.

`CLAUDE_CODE_PATH` in `.env` points at the binary when `claude` is not on PATH.

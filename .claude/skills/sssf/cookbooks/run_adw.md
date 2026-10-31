# Run ADW

Run a workflow and report on it. **You run and observe — you never step into the process or do the work yourself.**

## Step 0 — translate the request

**Read [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md) before you launch anything.** The prompt you pass is read by every agent in the chain, so it gets written deliberately: same intent, sharper words, verified paths, and a stated "done means". That cookbook is the whole procedure; this one starts once you have the prompt.

## The orchestrator's posture

The ADW is the worker. Your job is to launch it, watch the trace, and tell the engineer what happened. Do not read the agent's target files and "help", do not fix the code an agent was supposed to fix, do not edit an envelope. If a run fails, report the failing phase and its violations — the fix is a config, prompt, or ADW change, made deliberately, and then a re-run.

## Launch

**Check how the repo loads SSSF's recipes first.** If its own justfile has a `mod sssf 'sssf.just'` line, every recipe in this cookbook takes the `sssf` prefix: `just sssf rosters`, `just sssf phases <adw_id>`, `just sssf procs <adw_id>`, `just sssf kill <adw_id>`, `just sssf merge <adw_id>`. Otherwise run them as written.

Which chain to launch is decided in `how_to_prompt_for_the_eng.md`, and the short version is: **the ADW the engineer named, or else the most complete composed chain the work justifies — never a single-agent one.** Read `ls adws/adw_*.py` and the `Phases:` line in each docstring to see what this repo has; the names below are shape, not a menu.

```bash
uv run adws/<end-to-end-chain>.py "add a /health endpoint"
uv run adws/<plan-build-verify-chain>.py requests/health.md
uv run adws/<build-first-chain>.py "implement the plan" --adw-id a1b2c3d4
uv run adws/<recon-chain>.py "where is auth handled" --config path/to/other.config.yaml
```

The prompt is inline text or a file path. Launch in the background so you can poll while it works; the `adw_id` is printed on startup — capture it, everything else keys off it.

Redirect its output to a file (`> run.log 2>&1`), never through a pipe like `| head` or `| tail`. A pipe closes when that command exits, and the chain's output has nowhere to go. The trace has every line the chain prints, so read the trace, not the pipe.

### Listen for the roster

The chain says *what runs*; the config says *who runs it*. **If the engineer references a roster, a config, or a model tier, pass it — do not fall through to the default.**

```bash
just rosters                            # every roster on disk, and the model each agent runs
```

That prints the path to pass and who is in it, in one read:

```
adws/adw_sssf_config/sssf.config.yaml
    planner     fireworks/accounts/fireworks/models/kimi-k3
    builder     google/gemini-3.6-flash (inherited)
adws/adw_sssf_config/sssf.frontier.config.yaml
    planner     anthropic/claude-opus-5
```

Read those from disk every time. Rosters are the engineer's to add, rename, and retune, so a name you remember from a doc is a guess.

They will rarely say `--config`. Treat any of these as naming a roster, then resolve it to a file:

| What they say | What it means |
|---|---|
| "run it on the frontier config", "use the frontier roster" | the roster file whose name matches |
| "run this with the big models", "use the sota roster" | the non-default roster — confirm which if there is more than one. Each config's header comment lists the names it answers to, so `head -3` on the file settles it |
| "have opus plan this one" | a roster whose planner is that model; if none exists, say so rather than editing the config mid-request |
| nothing about models at all | the default, `adws/adw_sssf_config/sssf.config.yaml` |

`--config` takes the path directly; the justfile recipes read `SSSF_CONFIG` instead:

```bash
uv run adws/<chain>.py "<prompt>" --config adws/adw_sssf_config/sssf.frontier.config.yaml
SSSF_CONFIG=adws/adw_sssf_config/sssf.frontier.config.yaml just <recipe> "<prompt>"
```

Two things that bite:

- **Never swap rosters on your own.** A different roster is a different cost and a different result. If the default's model looks wrong for the work, say so and let the engineer choose.
- **Switching rosters mid-session breaks resumption.** `agent_map.json` records the model each coding-agent session was created with, so a joined run (`--adw-id`) whose config now names a different model starts that agent **fresh** instead of resuming its context window. That is deliberate — a bad resume is worse — but it means "plan on the frontier roster, then build on the default" costs the builder its accumulated context. Say so when you report it.

`--adw-id` is optional on **every** ADW. Given one, the run joins that session if it exists or creates it pinned to exactly that id: same `sessions/{adw_id}/` dirs, same `context_handoff/`, envelopes appended, and each agent resumes its existing coding-agent context window via `agent_map.json`. That is how you chain ADWs — plan under one id, then build under the same id.

### On its own branch: `--worktree`

Without it, a chain works and commits on the branch that is checked out, in the engineer's own checkout. With `--worktree`, the run gets branch `sssf/<adw_id>`, cut from the tip of the checked-out branch, in its own folder at `adws/adw_data/worktrees/<adw_id>`. The engineer can keep working while it runs, and nothing reaches their branch until it's merged. It applies to `adw_simple_sdlc`, `adw_plan_build_test`, `adw_plan_build_test_quality`, and `adw_build_test`. `adw_tickets` keeps its own rule: it runs on the feature branch the engineer made.

- **Add it only when the engineer asks** for a separate branch, a branch per run, or to keep working while it runs. Never add it on your own judgement.
- The engineer's uncommitted changes are not part of the run. The run says so on startup.
- Write a prompt file outside the checkout, in your scratchpad. A file left in the checkout makes `just merge` refuse until it's gone.
- Up to `max_worktree_runs` in `adws/adw_sssf_config/repo.yaml` go at once, 2 by default. A launch over the cap refuses and names the runs alive. See "Beside another run" below.
- `--adw-id` resumes a worktree run inside its own folder, with or without the flag, and refuses once it has been merged or discarded.
- The trace stays in the main checkout, so watching it works the same.
- If `repo.yaml` has `worktree_setup`, the run's first phase is `setup`, which installs what a fresh checkout lacks. When it fails, no agent has started: report its message, and the log it names, to the engineer. A resume skips it, and `just rebase` runs it again.

```bash
uv run adws/<chain>.py "<prompt>" --worktree
just merge <adw_id>      # only a successful run: fast-forward, or a merge commit if the branch moved on
just discard <adw_id>    # drop the run's branch and folder, whatever state it is in
just rebase <adw_id>     # ff_only repos, only on the engineer's word: onto the launch branch, then recheck
```

Report the branch with the result. **Run `just merge` only when the engineer says to**: the merge is the gate. It refuses unless the run succeeded, the launch branch is checked out, and the checkout is clean. In a repo that only takes fast-forwards, `merge: ff_only` in `adws/adw_sssf_config/repo.yaml` (or `--ff-only` for one merge) refuses a launch branch that moved on, with nothing changed. The rebase is then the engineer's call, never yours. When they say to, `just rebase <adw_id>` moves the branch onto the launch branch and reruns `adw_quality` in the worktree. A conflict or a failed check puts the branch back as it was. Then `just merge` again. A conflict aborts, leaves the checkout as it was, and keeps the branch; tell the engineer and show the commands it printed. Neither recipe pushes.

### Beside another run

A repo can have more than one worktree run going, up to its `max_worktree_runs`. Each is its own launch with its own adw_id, merged on its own.

- **Start a second only when the engineer asks for one.** If a request arrives while a run is going, say which run is going and ask: start this one beside it, or wait. Never decide that yourself.
- Both need `--worktree`. A plain run works in the engineer's checkout, which a worktree run's merge needs clean.
- Watch each by its adw_id. Report each one when it finishes, with its own adw_id, status, cost, and branch, without waiting for the other.
- Merge neither without the engineer's word, and merge one at a time. The first fast-forwards. The second makes a merge commit, or, in an `ff_only` repo, refuses until the engineer has you run `just rebase`.
- Each run's `max_run_cost` holds for that run alone, so two runs can spend up to twice it between them. The usage check before each agent's first call doesn't see the other run's calls either, so two runs can pass `rate_limit_stop` together and go a little over it.
- In the container, each worktree run gets its own project environment, built on its first `uv run`, so its first check is slower. Merge and discard remove it.

## Observe

The trace db is `adws/adw_data/sssf.db`. It is WAL, so reads never block the running writers — poll it as often as you like.

```bash
# where the run stands
sqlite3 adws/adw_data/sssf.db \
  "select seq, name, kind, owner, status, attempt from phases where adw_id='a1b2c3d4' order by seq;"

# the live tail — cursor on rowid, same query the visualizer polls
sqlite3 adws/adw_data/sssf.db \
  "select rowid, type, name, started_at from events where adw_id='a1b2c3d4' and rowid > 0 order by rowid limit 50;"

# why a phase failed
sqlite3 adws/adw_data/sssf.db \
  "select attempt, gate, passed, checks_json from gate_results where adw_id='a1b2c3d4';"

# session-level status
sqlite3 adws/adw_data/sssf.db \
  "select adw_id, request, status, total_tokens from sessions order by started_at desc limit 5;"

# what an agent actually did, slowest tool calls first
sqlite3 adws/adw_data/sssf.db \
  "select name, tokens, started_at, ended_at from events
   where adw_id='a1b2c3d4' and type='tool_call' order by ended_at desc limit 20;"
```

Poll on a cursor: keep the highest `rowid` you have seen and query `where rowid > ?`. Don't re-read the whole table each pass.

`tool_call` rows carry a real span, so durations come off the columns — see `references/observability.md` for which fields each event type populates.

The ADW also narrates to stdout, and every line it prints is written to the db as a `log` event — terminal and swim lane tell the same story by construction, so tailing the background process is a valid second view rather than a competing source of truth.

Files are the raw record if you need more than the db shows: `adws/adw_data/sessions/{adw_id}/{agent}/raw_output.jsonl` (full coding-agent stream), `envelope.json` (the parsed final response), `prompts/` (exactly what was sent), and `context_handoff/` (what agents wrote for each other).

## When a run is stuck

A hung coding agent produces no events at all, so the trace goes quiet rather than red. Read it in this order:

```bash
just phases <adw_id>     # which phase is still `running`
just procs <adw_id>      # what that phase is actually running, with pids
just kill <adw_id>       # stop it: the chain first, then its agents
```

`processes` rows with `ended_at IS NULL` are the live ones. If `procs` shows a pi child but the phase has produced no `tool_call` events and its `raw_output.jsonl` is empty, the agent never got started properly — check the model resolves and that nothing is blocking the subprocess, rather than waiting it out. `just kill` stops the chain first, so it can't retry the phase with a new agent, and then the agents. Pids get reused, so before signalling one it checks the live process still runs the recorded script or agent binary, and started no later than the trace recorded it. Any pid that fails the check is left alone and reported as `NOT stopped`.

A killed run ends `fail` and its process rows close, so the trace never claims work is in flight that is already dead. A killed worktree run keeps its branch and folder; `just discard <adw_id>` removes them.

## Report

Tell the engineer, in order: which chain and which roster you launched (name the config whenever it was not the default), which phase is running now (or which failed), phase statuses in sequence, and for a failure the gate violations or the error verbatim. A `verify_N` phase that shows `success` means the checks ran, not that they passed: a failing check hands the output to the builder, and its phase still succeeds. Read each verify phase's `checks` tally, or the `quality:*` events' `passed`, and report how many rounds failed and why. Remember **every phase defaults to `fail`** — a phase showing `fail` may simply never have completed; `queued` means it never started. Don't dress up a partial run as a success.

For a visual live view, the visualizer app in the skill (`just obs`, which prints its address: the first free port from 4600, or `SSSF_OBS_PORT`) polls this same db — sessions as cards, runs as swim lanes, phases and tool calls drill-in. `just obs-all` shows every repo SSSF is installed in, in one dashboard, under a panel with the Claude account's usage limits and where its spend went. The sqlite queries above remain the headless equivalent.

# Make SSSF your own

Everything the installer stamps is yours to edit, in your repo, never inside the skill. `install.py --update` keeps your edits when the skill changes, and flags a file only when the skill changed it too. Start at the top of this list, since each step pays off sooner than the next:

| Change | File |
|---|---|
| [your checks](#your-checks) | `adws/adw_modules/quality.py` |
| [your prompts](#your-prompts) | `adws/adw_data/prompt_engineering/<agent>/` |
| [your roster](#your-roster) | `adws/adw_sssf_config/sssf.config.yaml` |
| [the repo's rules](#the-repos-rules) | `adws/adw_sssf_config/repo.yaml` |
| [your chains](#your-chains) | `adws/adw_*.py` |
| [your gates](#your-gates) | `adws/adw_modules/gates.py` |
| [pi extensions](#pi-extensions) | `adws/adw_data/harness_engineering/` |

## Your checks

`quality.py` holds five blocks: `test`, `lint`, `format`, `typecheck` and `build`. Each ships as an `echo` that exits 0 and says it's a placeholder, so until you replace them, every test phase passes. Put in the commands your CI runs. sssf-sandbox's test block:

```python
def test(run) -> QualityCheckResult:
    return _run(QualityCheckSpec(
        name="test",
        area="backend",
        operation="build",
        argv=["uv", "run", "pytest", "-q"],
        timeout_seconds=600,
    ), run)
```

Write `argv` as a list, never a shell string, and call each tool by its bare name, such as `bun` or `uv`. The checks run with your own shell's environment, so the tools resolve as they do in your terminal. Delete the blocks you don't need from `run_quality()`'s list.

Check your wiring with no agent at all:

```bash
just quality "check the quality blocks"
```

A block still on its placeholder reports `not configured`.

## Your prompts

Each agent has a `system.md` and a `user.md` in `adws/adw_data/prompt_engineering/<agent>/`. This is where your standards go: what a good plan looks like, what a review must catch, how commit messages read.

An agent's report format is in its `user.md`, under `## Report`. It must match the output type in `adws/adw_modules/data_types.py` and the call site's `output_type=`. If you change one, change all three in the same edit, or every call spends rounds on corrections.

## Your roster

`adws/adw_sssf_config/sssf.config.yaml` says who each agent is: its model, thinking level, prompts, tools, and where it may write.

```yaml
agents:
  - name: reviewer
    model: anthropic/claude-opus-5-5   # a different model from the builder, so it does not share its blind spots
    max_call_cost: 1.00                # one call stops itself here
    thinking: high
    purpose: Confirm that what was built is what was asked for; change nothing.
    prompt_engineering:
      system: adws/adw_data/prompt_engineering/reviewer/system.md
      user: adws/adw_data/prompt_engineering/reviewer/user.md
    writes: []                         # it may change nothing in the repo
    tools: [read, grep, find, bash, write]
```

That's the starter Claude roster's reviewer. It has `bash` and `write` but `writes: []`, so it can run commands and write its report outside the repo, and any change it makes inside the repo is rolled back.

- **Models.** Write every model as `provider/model-id`. A bare id can match several providers, and the run refuses to start.
- **Cheap where it's safe.** Put a frontier model on the planner and the reviewers, and a cheaper one on the scout and documenter.
- **`writes`.** An agent that changes a file outside its `writes` has the change rolled back, and its phase fails. Widen it when the change was legitimate.
- **Caps.** `defaults.max_run_cost` caps a run, $5 by default. `max_call_cost` caps one Claude Code call. `defaults.rate_limit_stop` stops a run before a Claude agent starts once a usage window is that full, 0.8 by default.

`just rosters` lists each roster and the model each agent runs. `references/config.md` in the skill documents every key.

SSSF prices each call from `adws/adw_modules/prices.json`. A run warns about a model the file doesn't list. `uv run .claude/skills/sssf/scripts/check_prices.py` compares it with Claude's published prices.

## The repo's rules

`adws/adw_sssf_config/repo.yaml` holds what every clone of the repo follows. Commit it.

| Setting | Default | What it does |
|---|---|---|
| `merge` | `any` | `ff_only` makes `just merge` refuse anything but a fast-forward |
| `max_worktree_runs` | 2 | how many worktree runs go at once. Set 1 if your checks bind a fixed port or share a test database |
| `worktree_setup` | `[]` | commands that install what a fresh checkout lacks, such as `bun install --frozen-lockfile`, run before the first agent |
| `worktree_setup_timeout` | 600 | seconds each `worktree_setup` command may take before the run stops |
| `sandbox` | `none` | `vercel` or `e2b` sends new runs to a VM by default |
| `max_live_cost` | off | dollars every live run may spend together |
| `max_daily_cost` | off | dollars the repo's runs may spend in a rolling 24 hours |
| `max_silence_minutes` | 15 | how long an agent may write nothing before it and the run are stopped |
| `agent_rules` | `[]` | rules every agent gets |
| `secret_files` | `[]` | files agents must not read, on top of `.env` and `.env.*` (but `.env.sample`, `.env.example`) |
| `agent_env` | `[]` | `.env` keys agents still get, besides `SSSF_*`, `OBS_*`, `CLAUDE_CODE_OAUTH_TOKEN` and pi's provider keys |

To cap every repo on the machine together, write `max_daily_cost: 40.00`, with your own number, in `~/.config/sssf/limits.yaml`.

### Rules for every agent

A plain string only asks. A rule with `forbid` patterns is enforced: SSSF reads each shell command an agent runs, and a match stops the agent and the run. With `on_match: refuse`, the command is refused before it runs and the run goes on.

```yaml
agent_rules:
  - Keep commit subjects under 72 characters.
  - rule: Never run `just deploy`. It spends real money outside SSSF's caps.
    forbid: ['\bjust\s+deploy\b']
  - rule: Never run `git push`. Commit locally only.
    forbid: ['\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b']
    on_match: refuse
```

A stop rule may let the command start before the stop, so say what must not happen in the rule's text too. Every agent's environment also carries `SSSF_ADW_ID`, so your own scripts and hooks can refuse when an agent runs them:

```bash
[ -n "$SSSF_ADW_ID" ] && { echo "not from an SSSF agent" >&2; exit 1; }
```

## Your recipes

The justfile is the skill's, and an update replaces it. Put recipes of your own in `local.just` beside it, which the justfile loads when it's there and an update never touches. [run.md](run.md#every-recipe) lists the shipped ones.

## Your chains

An ADW is a short Python script in `adws/`. Copy the closest one and edit its phase list. To start from agents in your roster:

```bash
uv run .claude/skills/sssf/scripts/make_adw.py --name review_docs --agents scout,builder
```

Each named agent becomes one phase, chained by its report. A phase needs a `description` that says what it does and why, and SSSF refuses one that only repeats the name. [create_adw.md](../../.claude/skills/sssf/cookbooks/create_adw.md) covers the rest, and [concepts.md](../concepts.md#phases) explains phases.

## Your gates

A gate is one function in `adws/adw_modules/gates.py`:

```python
def gate(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for path in envelope.artifacts:
        report.check(path, path.endswith(".md"), "a Markdown file")
    return report
```

It checks what the agent's report claims, after the agent finishes, with one `check` per item. A failed check goes back to the same agent session as a correction. Pass a list of gates to `ph.call(AgentCall(..., gates=[...]))`.

## pi extensions

pi agents can load TypeScript extensions from `adws/adw_data/harness_engineering/`, named per agent in the roster's `harness_engineering:`. The skill ships `subagents.ts`, which lets the planner and scout start subagents. Claude Code agents can't load them. In a VM, an extension loads from the run's own clone, so commit it first.

## Strip what you don't need

This fork is built to be cut down. The parts that serve only this hub, and are safe to delete in your fork:

- the guard chain, which reviews changes to SSSF's own guard code: `adws/adw_guard_change.py`, `.claude/agents/bypass-reviewer.md`, `adws/tests/test_guard_change.py`, `docs/designs/hub-guard-chain.md`, the guard section of `CLAUDE.md`, and the run specs it left in `specs/`;
- the hub's read-only reports on its projects: `adws/check_projects.py`, `adws/levers.py`, `adws/features.py`, and their tests `adws/tests/test_check_projects.py`, `adws/tests/test_levers.py` and `adws/tests/test_features.py`;
- `local.just`, the hub's recipes for those reports and the guard chain;
- `docs/projects.md`, `docs/sssf.md` and `docs/open.md`: the repos this hub runs, how it runs SSSF on itself, and its list of open work.

Keep the `agent_rules` in this hub's `repo.yaml` unless you want agents to push. They refuse `git push` and the `gh` commands their pattern lists, such as `gh pr create` and `gh api`.

The stamped chains you never run can go too, in an installed repo. `install.py --update` adds back any stamped file that's missing, so a deleted chain returns with each update. Delete it again in the update's commit.

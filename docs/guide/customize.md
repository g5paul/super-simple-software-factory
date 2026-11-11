# Make SSSF your own

The installer stamps its files into your repo, and every one of them is yours to edit. Edit them in your repo, never inside the skill. `install.py --update` keeps your edits when the skill changes, and flags a file only when the skill changed it too.

Work down this table from the top. Each change pays off sooner than the one below it:

| Change | File |
|---|---|
| [your checks](#your-checks) | `adws/adw_modules/quality.py` |
| [your prompts](#your-prompts) | `adws/adw_data/prompt_engineering/<agent>/` |
| [your roster](#your-roster) | `adws/adw_sssf_config/sssf.config.yaml` |
| [the repo's rules](#the-repos-rules) | `adws/adw_sssf_config/repo.yaml` |
| [your ADWs](#your-adws) | `adws/adw_*.py` |
| [your gates](#your-gates) | `adws/adw_modules/gates.py` |
| [pi extensions](#pi-extensions) | `adws/adw_data/harness_engineering/` |

## Your checks

`quality.py` holds five blocks: `test`, `lint`, `format`, `typecheck` and `build`. Each block ships as an `echo` that exits 0 and says it's a placeholder. Until you replace them, every test phase passes.

1. Put in the commands your CI runs. For example, sssf-sandbox's test block:

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

   Write `argv` as a list, never a shell string. Call each tool by its bare name, such as `bun` or `uv`. The checks run with your own shell's environment, so the tools resolve as they do in your terminal.
2. Delete the blocks you don't need from `run_quality()`'s list.
3. Check your wiring. This runs no agent:

   ```bash
   just quality "check the quality blocks"
   ```

   A block still on its placeholder reports `not configured`.

## Your prompts

Each agent has a `system.md` and a `user.md` in `adws/adw_data/prompt_engineering/<agent>/`. Your standards go there, for example what a good plan looks like, what a review must catch and how commit messages read.

An agent's report format is in its `user.md`, under `## Report`. Three places must match:

- the `## Report` section in `user.md`;
- the output type in `adws/adw_modules/data_types.py`;
- the call site's `output_type=`.

If you change one, change all three in the same edit. If they differ, every call spends rounds on corrections.

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

That's the reviewer from the starter Claude roster. It has `bash` and `write` but `writes: []`. So it can run commands and write its report outside the repo, and SSSF rolls back any change it makes inside the repo.

- **Models.** Write every model as `provider/model-id`. On a pi agent, a bare id can match several providers, and then the run refuses to start. A Claude Code agent passes a bare id through to `claude`.
- **Cheap where it's safe.** Put a frontier model on the planner and the reviewers, and a cheaper one on the scout and documenter.
- **`writes`.** If an agent changes a file outside its `writes`, SSSF rolls the change back and its phase fails. Widen `writes` when the change was legitimate.
- **Caps.** The roster caps what one run and one call may spend. [The caps](../concepts.md#the-caps) lists every cap and its default.

`just rosters` lists each roster and the model each agent runs. The skill's [config reference](../../.claude/skills/sssf/references/config.md) documents every key.

SSSF prices each call from `adws/adw_modules/prices.json`, and a run warns about a model that file doesn't list. To compare `prices.json` with Claude's published prices, run `uv run .claude/skills/sssf/scripts/check_prices.py`.

## The repo's rules

`adws/adw_sssf_config/repo.yaml` holds the rules every clone of the repo follows. Commit it.

Most repos set a few keys: `merge`, `max_worktree_runs`, `worktree_setup`, and the two cost caps `max_live_cost` and `max_daily_cost`. The config reference's [table of `repo.yaml` settings](../../.claude/skills/sssf/references/config.md#repo-settings-in-repoyaml) gives every key, its default and what it does.

To cap every repo on the host together, write `max_daily_cost: 40.00`, with your own number, in `~/.config/sssf/limits.yaml`.

### Rules for every agent

A plain string only asks. A rule with `forbid` patterns is enforced. SSSF reads each shell command an agent runs, and a match stops the agent and the run. With `on_match: refuse`, SSSF refuses the command before it runs, and the run goes on.

```yaml
agent_rules:
  - Keep commit subjects under 72 characters.
  - rule: Never run `just deploy`. It spends real money outside SSSF's caps.
    forbid: ['\bjust\s+deploy\b']
  - rule: Never run `git push`. Commit locally only.
    forbid: ['\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b']
    on_match: refuse
```

A stop rule may let the command start before the stop, so say what must not happen in the rule's text too.

Every agent's environment also carries `SSSF_ADW_ID`. Your own scripts and hooks can check it and refuse when an agent runs them:

```bash
[ -n "$SSSF_ADW_ID" ] && { echo "not from an SSSF agent" >&2; exit 1; }
```

## Your recipes

The justfile is the skill's, and an update replaces it. Put your own recipes in `local.just` beside it. The justfile loads `local.just` when it's there, and an update never touches it. [Every recipe](run.md#every-recipe) lists the shipped ones.

## Your ADWs

An ADW (AI developer workflow) is a short Python script in `adws/`. To make one, copy the closest ADW and edit its phase list. To start from agents in your roster instead, run:

```bash
uv run .claude/skills/sssf/scripts/make_adw.py --name review_docs --agents scout,builder
```

Each named agent becomes one phase, and each phase's report feeds the next. A phase needs a `description` that says what it does and why. SSSF refuses a description that only repeats the name. The [create_adw cookbook](../../.claude/skills/sssf/cookbooks/create_adw.md) covers the rest, and [Phases](../concepts.md#phases) explains how phases work.

## Your gates

A gate is one function in `adws/adw_modules/gates.py`:

```python
def gate(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for path in envelope.artifacts:
        report.check(path, path.endswith(".md"), "a Markdown file")
    return report
```

After the agent finishes, the gate checks what the agent's report claims, with one `check` per item. A failed check goes back to the same agent session as a correction. Pass a list of gates to `ph.call(AgentCall(..., gates=[...]))`.

## pi extensions

Pi agents can load TypeScript extensions from `adws/adw_data/harness_engineering/`. The roster names each agent's extensions under `harness_engineering:`. The skill ships `subagents.ts`, which lets the planner and scout start subagents. Claude Code agents can't load extensions. In a VM, an extension loads from the run's own clone, so commit it first. [pi extensions in a VM](../../.claude/skills/sssf/references/config.md#pi-extensions-in-a-vm) has the details.

## Strip what you don't need

The hub is built to be cut down. These parts serve only the hub, and you can delete them in your fork:

- the guard-change chain, which reviews changes to SSSF's own guard code: `adws/adw_guard_change.py`, `.claude/agents/bypass-reviewer.md`, `adws/tests/test_guard_change.py`, `docs/designs/hub-guard-chain.md`, the guard section of `CLAUDE.md`, and the run specs it left in `specs/`;
- the hub's read-only reports on its projects: `adws/check_projects.py`, `adws/levers.py`, `adws/features.py`, and their tests `adws/tests/test_check_projects.py`, `adws/tests/test_levers.py` and `adws/tests/test_features.py`;
- `local.just`, the hub's recipes for those reports and the guard-change chain;
- `docs/projects.md`, the repos this hub runs;
- `docs/sssf.md`, how the hub runs SSSF on itself;
- `docs/open.md`, the hub's list of open work.

The hub's own `repo.yaml` sets `local_guard: agentsh`, which needs a build of agentsh that isn't published yet. In your fork, set it to `text`, or every run there refuses to start.

Keep the `agent_rules` in the hub's `repo.yaml` unless you want agents to push. They refuse `git push` and the `gh` commands their pattern lists, such as `gh pr create` and `gh api`.

In an installed repo, you can also delete the stamped ADWs you never run. `install.py --update` adds back any stamped file that's missing, so a deleted ADW returns with each update. Delete it again in the update's commit.

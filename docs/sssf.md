# SSSF in the hub

The hub runs SSSF on itself, so guard changes can go through a chain
(`docs/designs/hub-guard-chain.md`). The install sits on `main`: `adws/`,
`justfile`, `.env.sample` and the config under `adws/adw_sssf_config/` were
stamped from the skill in `.claude/skills/sssf/`.

## What's here

Most of `adws/` is the install, the same files every repo gets. A few
parts exist only in the hub; `sssf-update` never copies them.

| Part | Hub only | What it is |
|---|---|---|
| `adws/adw_*.py`, `adws/adw_modules/` | no | the chains and their modules, stamped from `templates/adws/` ([concepts.md](concepts.md)) |
| `adws/adw_data/prompt_engineering/` | no, except two | each agent's prompts; `guard_spec_reviewer/` and `bypass_reviewer/` are the guard chain's ([Guard changes](#guard-changes)) |
| `adws/adw_data/harness_engineering/` | no | pi extensions: `subagents.ts` and its helper `themeMap.ts` |
| `adws/adw_guard_change.py` | yes | the chain every change to guard code goes through ([Guard changes](#guard-changes)) |
| `adws/check_projects.py` | yes | checks each installed repo after an update ([Check the projects' updates](#check-the-projects-updates)) |
| `adws/levers.py` | yes | the four levers, week by week ([The four levers](#the-four-levers)) |
| `adws/features.py` | yes | what each feature cost ([What each feature cost](#what-each-feature-cost)) |
| `adws/tests/` | yes | tests for the four hub scripts above |
| `.claude/agents/bypass-reviewer.md` | yes | a bypass review outside a chain run ([Guard changes](#guard-changes)) |
| `.githooks/pre-push` | yes | the secret scan and private-name check on every push ([secret-scan.md](secret-scan.md)) |

## Ground rules

- `adws/` is main's guard. A run changes the skill in `.claude/skills/sssf/`,
  never `adws/`, and `adws/` is a protected path. The two meet only when an
  update lands on `main`.
- Runs cut their own branch from `main`, and commit locally. The engineer
  runs every push. A `forbid` rule with `on_match: refuse` refuses `git push`
  and the `gh` commands that write to GitHub, and the pre-push hook refuses
  a push while `SSSF_ADW_ID` is set.
- Agents bill to the Claude subscription login. `.env` holds no API key.

## Install

```bash
uv run .claude/skills/sssf/scripts/install.py --coding-agent claude_code
```

The Claude Code roster as shipped: planner, reviewer, standards_reviewer and
spec_reviewer on Opus; builder and ticket_builder on the default, Sonnet;
scout and documenter on Haiku.

`protected_files` adds the hub's own rules and records to the shipped list:
`.claude/agents/`, `CLAUDE.md`, `CLAUDE.local.md`, `docs/projects.md` and
`adws/tests/`.

`repo.yaml` sets `max_worktree_runs: 1`, since the suite's process-kill tests
are timing-sensitive, and `worktree_setup: []`, since the suite needs only
`uv`.

## Quality

Two blocks. The hub has no lint, format, typecheck or build step.

| Block | argv |
|---|---|
| test | `uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider` |
| chain_test | `uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest adws/tests -q -p no:cacheprovider` |

The skill's suite takes about 75 seconds, the chain's tests about one. The hub has no CI, so this is the whole check.

## Check the billing

Every agent's init event must show `apiKeySource` as `none`:

```bash
grep -h '"subtype":"init"' adws/adw_data/sessions/*/*/raw_output.jsonl \
  | python3 -c "import json,sys; print({json.loads(l)['apiKeySource'] for l in sys.stdin})"
```

## Run and watch

`just simple-sdlc path/to/task.md` runs the usual chain. `just sessions`,
`just phases <adw_id>` and `just obs` read the trace.

## Guard changes

A change to guard code runs through `adws/adw_guard_change.py`, on its own
branch cut from `main`:

```bash
git checkout -b guard-<slug> main
uv run adws/adw_guard_change.py specs/tasks/<task>.md
```

The planner writes a spec with a threat model, a baseline, a bar and what's
out of scope. `guard_spec_reviewer` passes it before any code. The builder
changes the guard, and verify runs the hub suite and the chain's own tests.
Then `bypass_reviewer` judges a snapshot commit of the tree against the
baseline, for at most three rounds. Bypasses, harms and new false positives
block; `--allow-false-positives` turns the last off for one run. `--base`
names the baseline ref, `main` by default, and `--worktree` runs on branch
`sssf/<adw_id>` in its own worktree instead of the current branch. A fix that
adds module-level state or a `global` to a guard module stops the run, and
so does the reviewer's `adds_mechanism`.

Every finding lands in `context_handoff/guard_report.md`. A run that
passes commits the code; one that stops leaves it in the tree for the
engineer. Nothing merges to `main` on its own.

The chain, its prompts, its two roster agents and its tests are hub-only:
`adws/adw_guard_change.py`, `adws/adw_data/prompt_engineering/` under
`bypass_reviewer/` and `guard_spec_reviewer/`, and `adws/tests/`. An
update never touches them. `max_run_cost` is $20 here, since a run can make
three Opus bypass reviews at up to $5 each.

## Update SSSF

`just sssf-update` refuses here, because the skill's source is this repo's
own copy. After a skill change merges to `main`, update on a short branch:

```bash
uv run .claude/skills/sssf/scripts/install.py --update
```

Then `--take` or `--keep` each flagged file, as in the install cookbook, and
fast-forward `main`. The stamp records no `skill` commit here, for the same
reason. `git log -1 -- adws/` names the update that stamped `adws/`.

## The dashboard

Run the all-projects dashboard from here:

```bash
just obs-all
```

It reads the machine-wide registry, so it shows every project from any
repo. From here it's built from the skill's source, always the newest, and
nothing runs in a project's repo: it only reads their traces. `just obs` in
a project still shows that one repo.

## Check the projects' updates

After the hub asks each project session to update, check their reports with

```bash
uv run adws/check_projects.py
```

It reads every repo in the install registry except this one, and fails a
repo when its `stamp.json` names a different skill, its last stamp commit
isn't "Update SSSF to skill <h>", its tree isn't clean, its committed
`.claude/skills/sssf` differs from this repo's at that commit, or no
`adw_quality` run has passed since the update. A stray or leftover skill
file shows as `extra`. It only reads git and each trace, so it is safe on a
repo whose session owns it. `--skill <h>` expects an older commit, and
`--repo <name>` checks one repo. `--listed` leaves out the repos
`.git/sssf-private-names` lists and says only how many, so its output is
safe to paste into this public repo. The script and its tests are hub-only,
like the guard chain.

## The four levers

`uv run adws/levers.py` prints one row per ISO week, across every repo in
the install registry, for the levers `docs/research/thinking-in-threads.md`
describes: runs and the most at once, minutes and agent tool calls per agent
run, agents per run, and touches with agent tool calls per touch. A touch is
an agent run's engineer phase or a merge commit, so it measures sessions'
launches as much as the engineer's attention; the script's docstring says
what each column counts. `--weeks N` sets the window, `--by-repo` adds a
table per repo, and `--listed` leaves out private repos as `check_projects.py`
does. Accepted, $/accepted and longest show yield: runs whose status is
`success`, the week's spend per one, and the longest agent run. `--real`
leaves out quality-only runs and the repos `.git/sssf-test-beds` names, one
per line; the file lives in `.git`, so no name is committed. It only reads,
and it is hub-only.

## What each feature cost

`uv run adws/features.py` joins each repo's agent runs to the commits that
reached its main line, the current branch unless `--branch REPO=BRANCH`
says otherwise. Runs that landed at one point are one feature, with its
cost, run count and lead time; a rebased commit is matched by patch-id, then
subject. Runs that never landed are pending when a branch still holds them,
gone otherwise, and runs with no commits are counted apart. `--repo NAME`
picks one repo and can repeat. `--weeks`, `--listed` and `--real` work as in
`levers.py`, and `levers.py --by-type` gives one row per chain. Both only
read, and both are hub-only.

# Run SSSF

Every example on this page is a real run, made on 2026-11-05 in [sssf-sandbox](../projects.md), a small Python text library, on skill 7decd41. Output is trimmed to the lines that matter, and long lines are cut short. Costs are list price; on a subscription you aren't charged them. All eight runs together came to $2.65.

You can ask for any of these in plain words in a Claude Code session in the repo, starting with `/sssf`. The session writes the prompt, launches the chain and reports back. The commands below are what it runs, and you can run them yourself.

## Pick a chain

| You want | Run |
|---|---|
| one answer from one agent | `just prompt "summarize this repo"` |
| read-only recon | `just scout "where is auth handled"` |
| a plan, no code | `just plan "add a /health endpoint"` |
| plan, build, commit, no tests | `just plan-build "..."` |
| plan, build, test, commit | `just sdlc "..."` |
| the same, plus every quality check, a review and docs | `just simple-sdlc "..."` |
| a feature with a spec and tickets | `just tickets .scratch/<feature>/` |
| two rosters on one request | `just trial "..." main chatgpt` |

A prompt can be a string or a path to a Markdown file. For a bug fix, a rename, or a change that follows an existing pattern, use `just simple-sdlc`. For a new mode, a new file format, or work that splits into slices you can check one at a time, use tickets. [choose_a_flow.md](../../.claude/skills/sssf/cookbooks/choose_a_flow.md) has the two questions that decide it.

## A plain build

```console
$ just sdlc "Add textkit.snake_case(text), which lowercases the text and joins its words with underscores, so 'Hello World' becomes 'hello_world'. Export it from textkit and test it."
adw_id: d36de4d2   engineer George Paul
▶ 01 request  engineer · George Paul  Capture the incoming ask
▶ 02 plan  agent · planner  Turn the request into an implementable plan
  ▸ planner anthropic/claude-opus-5-5
  ✓ gate artifacts_exist 2 checked
  ✓ gate files_non_empty 2 checked
  ✓ gate plan_well_formed 4 checked
  ✓ plan 40.6s
▶ 03 build  agent · builder  Implement the plan exactly
  ✓ build 12.9s
▶ 04 test_1  code · quality  Run the suite
  ✓ test_1 0.2s
▶ 05 commit  code · git  Land the code only after the suite came back green
  · sha: b0b2638, message: Add textkit.snake_case helper
│  status   ✓ success
│  cost     $0.3687
│  next     just phases d36de4d2
```

This ran on the default Claude roster. The run worked on the checked-out branch and committed there. Had the suite failed, the builder would have got the failures and up to two fixes, each rechecked by the suite, and the run would have failed without a commit.

Next: `git show` to read the change, or `just phases d36de4d2` for each phase's status.

## On its own branch

Add `--worktree`, and the run gets its own branch and folder. You can keep working in your checkout while it runs.

```console
$ SSSF_CONFIG=adws/adw_sssf_config/sssf.config.chatgpt.yaml just sdlc "Add textkit.kebab_case(text), ..." --worktree
  · working in adws/adw_data/worktrees/8b8c596c on sssf/8b8c596c, from master at b0b2638; `just merge 8b8c596c` lands it
▶ 01 setup  code · worktree  Run repo.yaml's worktree_setup in the new worktree
...
│  status   ✓ success
│  cost     $0.0840

$ just merge 8b8c596c
merged sssf/8b8c596c into master (fast-forward), now at 085a13e; removed its worktree and branch
```

`SSSF_CONFIG` ran this one on the ChatGPT roster. `just merge` lands a successful run: a fast-forward when it can, or a merge commit when your branch moved on. It never rebases and never pushes.

`just merge` refuses, with nothing changed, unless the run succeeded, you have its launch branch checked out, your checkout is clean, and the run's worktree holds nothing uncommitted. In this run, a stray file in the worktree stopped the merge:

```console
$ just merge c95d4481
merge c95d4481: run c95d4481's worktree (adws/adw_data/worktrees/c95d4481) has uncommitted changes: STRAY.txt. The merge takes only sssf/c95d4481's commits and then removes the worktree, so they'd be lost. Commit them there (`git -C adws/adw_data/worktrees/c95d4481 add -A && git -C adws/adw_data/worktrees/c95d4481 commit`) and merge again, or `just discard c95d4481` to drop the run.
```

The other recipes for a worktree run:

```bash
just discard <adw_id>    # drop the branch and folder, whatever state they're in
just rebase <adw_id>     # for a repo that only takes fast-forwards: move the branch onto yours, then recheck
```

`--worktree` works with `adw_build_test`, `adw_plan_build_test`, `adw_plan_build_test_quality`, `adw_simple_sdlc` and `adw_tickets`. A worktree is a fresh checkout without your untracked dependencies, such as `node_modules`. List the commands that install them under `worktree_setup` in `repo.yaml`; they run before the first agent.

## Two runs at once

Worktree runs can go side by side, up to `max_worktree_runs` in `repo.yaml`, 2 by default. Each is its own launch, merged on its own.

```console
$ export SSSF_CONFIG=adws/adw_sssf_config/sssf.config.chatgpt.yaml
$ just sdlc "Add textkit.kebab_case(text), ..." --worktree &
$ just sdlc "Add textkit.camel_case(text), ..." --worktree &
$ wait
│  adw_id   8b8c596c   status ✓ success   cost $0.0840
│  adw_id   84fddff0   status ✓ success   cost $0.0934

$ just merge 8b8c596c
merged sssf/8b8c596c into master (fast-forward), now at 085a13e; removed its worktree and branch
$ just merge 84fddff0
CONFLICT (content): Merge conflict in CHANGELOG.md
CONFLICT (content): Merge conflict in README.md
CONFLICT (content): Merge conflict in textkit/__init__.py
The branch and its worktree (adws/adw_data/worktrees/84fddff0) are kept. To merge by hand: `git merge sssf/84fddff0`, resolve, commit, then `just discard 84fddff0`.
```

Both runs added an export and a changelog line in the same places, so the second merge conflicted. SSSF aborted it and left your checkout as it was. Here both sides were kept:

```bash
git merge sssf/84fddff0      # resolve each file, keeping both changes
uv run pytest -q             # 220 passed
git add -A && git commit --no-edit
just discard 84fddff0        # the commits are on master now; this drops the branch and folder
```

Two runs that touch the same files will conflict like this. Run them side by side when they work in different places, or merge the first before starting the second.

Each run's `max_run_cost` holds for that run alone, so two runs can spend twice it. `max_live_cost` in `repo.yaml` caps what live runs spend together. Set `max_worktree_runs: 1` if your checks bind a fixed port or share a test database.

## A feature from tickets

`just tickets` builds a feature one ticket at a time, from a folder you write with the engineer's skills ([Matt Pocock's](https://github.com/mattpocock/skills) `/to-spec` and `/to-tickets`) or by hand:

```
.scratch/case-convert/
├── spec.md                          # needs a "## Testing Decisions" section naming the test seams
└── issues/
    ├── 01-split-identifier.md       # "# 01: Title", "**Blocked by:** None", "**Status:** ready-for-agent"
    └── 02-pascal-case.md            # "**Blocked by:** 01"
```

Commit the folder, then build it. On `main` or `master` it needs `--worktree`:

```console
$ git add .scratch && git commit -m "Spec and tickets for case-convert"
$ SSSF_CONFIG=adws/adw_sssf_config/sssf.config.chatgpt.yaml just tickets .scratch/case-convert/ --worktree
▶ 03 read  code · tickets  Check the feature folder and order its tickets, before any agent spends money
  · feature: case-convert, tickets: 2, to_build: 01 02, spent_so_far: $0.00, estimate: about $0.45
▶ 04 scout  agent · scout  Explore once for the whole feature
▶ 05 t01_build  agent · ticket_builder  Build ticket 01, test-first: Split an identifier into words
▶ 06 t01_verify_1  code · quality  Run the suite and every quality check
▶ 07 t01_commit  code · git  Mark ticket 01 done and land it on its own
▶ 08 t02_build  agent · ticket_builder  Build ticket 02, test-first: PascalCase
...
▶ 11 review_standards  agent · standards_reviewer  Does the feature follow this repo's standards?
  ✓ AxisReviewOutput 0 findings
▶ 12 review_spec  agent · spec_reviewer  Does the feature do what its spec and tickets ask?
▶ 14 document  agent · documenter  Write up the finished feature
│  status   ✓ success
│  cost     $0.3843

$ just merge 5e00926c
merged sssf/5e00926c into master (fast-forward), now at 4f14fa9; removed its worktree and branch
$ git log --oneline -3
4f14fa9 Document identifier splitting and PascalCase
8912aff 02: PascalCase
f8b4e7b 01: Split an identifier into words
```

Each ticket gets a fresh builder and lands as its own commit once every check passes. The read step refuses, before any agent spends, on a spec with no test seams, a dirty tree, an uncommitted folder, or `main` or `master` without `--worktree`. A rerun skips finished tickets and resumes at the first unfinished one. [tickets.md](../../.claude/skills/sssf/cookbooks/tickets.md) covers each stop and what to do.

## Two rosters on one request

A trial runs one request on several rosters at once, each as a worktree run from the same commit, then a panel of referees compares the diffs blind. `main` is `sssf.config.yaml`; any other name is `sssf.config.<name>.yaml`.

```console
$ just trial "Add textkit.swap_case(text), which flips the case of every letter, ..." main chatgpt
...
trial 04c40f37: Add textkit.swap_case(text), ...

attempt     roster              run      checks  panel  rank  rank*  cost   time   diff
70c716ca    main (Claude Code)  success  pass    pass   2     2      $0.58  2m10s  +96 -1
ae1894b7 <  chatgpt (pi)        success  pass    pass   1     1      $0.15  3m19s  +75 -1

rank is the average place across the panel, 1 is best; rank* leaves out the vote of the reviewer from the attempt's own family
suggested: ae1894b7, chatgpt (pi), the cheapest attempt to pass every gate
total: $1.01, every attempt plus the panel ($0.28)

$ just trial-merge 04c40f37 ae1894b7
merged sssf/ae1894b7 into master (fast-forward), now at 009b634; removed its worktree and branch
discarded sssf/70c716ca and its worktree
```

Each attempt is a full `adw_simple_sdlc` run, so a trial costs one run per roster plus the panel. The panel has one referee per model family, and each sees the attempts in a different order. `rank*` shows whether a referee favors its own family's attempt. Nothing merges until you pick. `just trial-show <trial>` prints the scorecard at any point, `just trial-review <trial>` runs the panel again if `just trial` died or its panel failed, and `just trial-discard <trial>` drops every attempt. A trial needs a free worktree slot per roster.

## In the container

With `container/run.sh`, the operator session runs in Podman and launches chains without asking you to approve each one. Here it ran headless, with a request in plain words:

```console
$ container/run.sh -p 'Use the sssf skill (/sssf) for this: add textkit.count_uppercase(text), which counts the uppercase letters in a text, so Hello World gives 2. Export it from textkit and test it. Build it with the plan-build-test chain on the main roster, then report the adw_id, status, cost and commit.'
...
The run succeeded: all 5 phases passed and the tests came back green.

- adw_id: b167d554
- Status: success (request, plan, build, test and commit all passed)
- Cost: $0.2788 for 186,050 tokens
- Commit: 0457272 "Export count_uppercase from textkit and test it" (on master)
```

The first start built the image. The session wrote its own prompt for the chain, with what's in and out of scope, and reported what the builder found: the helper already existed, so it only exported and tested it. Run without `-p` for an interactive session.

## In a remote VM

`--sandbox vercel` or `--sandbox e2b` runs the agents, checks and git in a VM made for the run. It implies `--worktree`, so you merge as usual. These two ran at once, Vercel on the Claude roster and E2B on ChatGPT:

```console
$ just sdlc "Add textkit.count_lowercase(text), ..." --sandbox vercel
  · working in adws/adw_data/worktrees/3cf4cea1 on sssf/3cf4cea1, from master at 0457272; `just merge 3cf4cea1` lands it
▶ 01 sandbox  code · sandbox  Make the run's vercel VM and clone its branch in
  · working in vercel VM orange-worrying-partridge-fYXJ8G; commits come back to sssf/3cf4cea1
...
│  status   ✓ success
│  cost     $0.3375

$ SSSF_CONFIG=adws/adw_sssf_config/sssf.config.chatgpt.yaml just sdlc "Add textkit.count_spaces(text), ..." --sandbox e2b
  · working in e2b VM iggke1u4jbkw5ah5gfpzv; commits come back to sssf/c8f40a3c
│  status   ✓ success
│  cost     $0.0938

$ just merge 3cf4cea1
merged sssf/3cf4cea1 into master (fast-forward), now at a63d4bc; removed its worktree and branch
```

The VMs lived 85 and 117 seconds, and both were deleted at the end of their runs. The second merge conflicted on `CHANGELOG.md` and was merged by hand, as in [Two runs at once](#two-runs-at-once).

If a run stops, its uncommitted work in the VM is saved to `adws/adw_data/sessions/<adw_id>/sandbox-uncommitted.patch`, not applied. `just sandbox-usage` shows what VM runs have used of each provider's free allowance:

```console
$ just sandbox-usage
e2b, all time: 20 runs, 0.37 VM hours, CPU 0.022 h
  credit       $0.0617   0.1% of 100 dollars, Hobby's one-time credit
vercel, last 30 days: 22 runs, 0.81 VM hours
  active CPU   0.037 h     0.7% of 5 h
```

`--sandbox openshell` does the same in a local microVM, free and with no API key, for Claude Code rosters only. Its network is closed except `api.anthropic.com` for Claude Code and read-only package registries; denials show as `sandbox_denied` events. Run `just openshell-setup` once first.

`sandbox: vercel`, `sandbox: e2b` or `sandbox: openshell` in `repo.yaml` makes a VM the repo's default, and `--sandbox none` keeps one run local. [install.md](install.md#set-up-remote-vms) has the setup.

## Chain two runs by hand

`--adw-id` joins an existing run, and each agent continues its own session:

```bash
just plan "add a /health endpoint"                     # prints adw_id a1b2c3d4
uv run adws/adw_build_test.py "implement the plan" --adw-id a1b2c3d4
```

## Stop a run

```bash
just procs <adw_id>    # what the run has alive, with pids
just kill <adw_id>     # stop the chain first, so it can't retry, then its agents
```

A killed run marks itself `fail` in the trace. [watch.md](watch.md) shows how to follow a run while it goes.

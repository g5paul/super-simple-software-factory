# Docs for people who use this fork

Status: decided on 2026-11-05. The worktree bug is reproduced and fixed; the rest is not started. Work goes in the order under "The plan".

## The gap

An audit of the user-facing docs on 2026-11-05, against main at 92220f5, found 15 wrong claims, 15 stale ones, 16 gaps and 10 style problems, plus wrong content in six of the eight diagrams. The findings are under "Audit" below.

The README tries to do five jobs at once. It's 52 KB and holds upstream's pitch, the install, the concepts, a reference, and the fork's open list. Install is one dense block with most of its steps in comments, and nothing shows how to run SSSF day to day. The diagrams are upstream's, unchanged since de31374. Most of them draw a "tester" agent that no longer exists, and their counts are wrong.

The audit also found a code bug that the docs repeat, so it comes first.

## Decisions

1. **Fix the worktree bug first, apart from the docs.** `adw_build_test --worktree` followed by `just merge` can delete the run's work. Reproduce it in sssf-sandbox first. Then `just merge` refuses a run whose worktree has uncommitted changes, and `adw_build_test` gets a commit phase, so `--worktree` and `--sandbox` runs keep their work. `just discard` still drops everything, and says how many uncommitted files went with the run. The engineer chose that over a refusing discard, since discard is documented as dropping a run in any state and trials call it to drop their losing attempts.
2. **Audience: both newcomers and the engineer.** A guided path for someone who forks this fork, and a reference section the rest can skip to.
3. **Upstream's voice and links.** Keep the credit to disler and the links to his repo and video, in a section of their own. Drop the "IndyDevDan" sign-off, since it makes the fork's docs read as his. Rewrite "Why this exists" and the three principles once, in the engineer's voice.
4. **Images.** Fix all eight before drawing any new ones:
   - the current agents in place of "tester";
   - the right counts;
   - a dark background on 01 to 06, like 00 and 08 already have, so all eight look the same in GitHub's light and dark themes;
   - each element starts fully visible and fades in only where the viewer allows motion, so a renderer that doesn't animate shows the whole picture;
   - renumbered, so 07 isn't skipped.

   New diagrams come later, and only where a guide page needs one.
5. **Structure.**
   - **README:** what SSSF is, one diagram, a 5-minute quickstart, then links.
   - **`docs/guide/`:**
     - `install.md`: a Claude subscription, ChatGPT through pi, API keys, and the container, each with a check that it worked;
     - `run.md`: one worked example per feature;
     - `watch.md`, `customize.md` and `troubleshooting.md`.
   - **`docs/concepts.md`:** phases, envelopes, gates, the trace and the caps, with the diagrams.
   - **`docs/fork.md`:** a changelog of what this fork changed from upstream, and nothing more.
   - **`docs/README.md`:** an index that keeps user docs apart from the hub's own (`sssf.md`, `projects.md`, `designs/`, `research/`, `postmortems/`).
6. **The opening.** The README opens with this paragraph from upstream's "Built to be Observed, Customized, and Reused", quoted word for word, credited and linked:

   > So take it. Fork it, strip the parts you do not need, rename the agents, throw out half the workflows, and roll what is left into the factory your product actually needs. The specific chains in here matter far less than the shape: code owns the loop, agents own the phases, and every run leaves a trace you can go read.

   One line follows it: this fork is what came of doing that. Then two short lists:
   - **kept from upstream:** code owns the loop, agents own the phases, and every run leaves a trace;
   - **changed or added:** subscriptions instead of API keys, caps and usage stops, a branch per run and parallel runs, the container and the VMs, rules the repo owns, and the guard chain.

   The copy doesn't claim the fork threw out half the workflows. It kept all 13 and grew by about 54,000 lines, keeping the shape and building what a real repo needed around it. "Built to be Observed, Customized, and Reused" becomes the closing section. It invites readers to strip this fork in turn, and names the parts that are the hub's own and safe to delete: the guard chain, `projects.md` and the push rules.
7. **Order and branches.** Three branches off main, each merged before the next starts:
   1. the bug fix;
   2. the diagrams;
   3. the docs, one commit per page.
8. **Examples from fresh runs.** `run.md`'s examples come from fresh sssf-sandbox runs on the current skill, one per feature:
   - a plain build;
   - a worktree run and merge;
   - tickets;
   - a trial;
   - a race;
   - `--container`;
   - `--sandbox` on Vercel and on E2B.

   Each is trimmed to the command, the key output lines and the next step. The set is capped at $5.
9. **The skill's own docs.** Fix them in the same pass, as their own commit. That covers `install.md`'s counts and its checklist's API keys, and `env.sample`'s missing sandbox keys. Then send the projects the update.
10. **Review before the docs merge.** A fresh reviewer checks every command, recipe, count and file path in the new pages against the code, and tries each install path on a scratch repo.

## The plan

1. **The bug fix branch.**
   1. Reproduce the data loss in sssf-sandbox.
   2. Fix it test-first: `_merge` refuses a run with a dirty worktree, `_discard` says what it dropped, and `adw_build_test` commits a green worktree run.
   3. Merge, then run a proof.
2. **The images branch.** Decision 4.
3. **The docs branch.**
   1. Fresh sssf-sandbox runs for the examples.
   2. The pages in decision 5, one commit each.
   3. The skill's own docs, in a commit of their own.
4. **The review.** Fix what it finds, merge, then send the update to the projects.

## Audit

Checked against main at 92220f5. Line numbers are as of that commit.

### The worktree bug

Reproduced on 2026-11-05 in sssf-sandbox: run b03647f9 (`adw_build_test --worktree`, $0.010) succeeded with `WORKTREE_PROBE.md` uncommitted in its worktree. `just merge b03647f9` printed "merged sssf/b03647f9 into master (fast-forward), now at c52bbae; removed its worktree and branch", and the file was gone from both.


`adw_build_test` has no commit phase, so a `--worktree` run leaves its work uncommitted in the worktree. `_merge` checks for uncommitted changes in the launch checkout, never in the run's worktree (`worktree.py:378`). The merge of an unchanged branch succeeds, then `_remove` runs `git worktree remove --force` (`worktree.py:532`). README:427 and `run_adw.md:75,112` show this chain for `--worktree` and `--sandbox`.

### README.md, wrong

- **:263 "Five starter agents".** All three rosters have eight: ticket_builder, standards_reviewer and spec_reviewer as well. The trial panel also has its `referee` prompts.
- **:510, a hung agent "goes quiet".** `max_silence_minutes`, 15 by default, now stops it.
- **:550, "There is no cloud".** The list of opt-in features there leaves out `--sandbox`.
- **:526.** The obs relay feeds the engineer's pi obs server, not `just obs`. The README uses "obs" for both, and never explains the pi obs extension.
- **:269.** It says only that a `forbid` match kills the run. It never mentions `on_match: refuse`.
- **:92, the install block.**
  - `git init && git commit --allow-empty -m init` runs every time, so an existing repo gets a junk empty commit.
  - The install is never committed, though worktrees are cut from HEAD and VMs get a bundle of the branch.
- **:427 and :463.** `--worktree` on `adw_build_test` loses the work, as above.
- **:66-67.** The pi link returns 404.
- **:343 and :278, alt text.** The text at :343 says seven tables, but image 06 draws six. The text at :278 names a reviewer lane, but image 04 has a tester lane.
- **:506, "Three ADWs".** Five run `quality.py`, plus `adw_quality`.
- **:59.** The agentic install doesn't say to restart Claude Code, though `/sssf` registers only at session start.
- **:302, "three swim lanes".** Images 00 and 04 draw five owner lanes.
- **:21 and :391-413.** These say the repo is the skill plus `docs/designs`. The root also holds the hub's own install: `adws/`, `adw_guard_change.py`, `adws/tests`, `justfile`, `specs/`, `.claude/agents/` and `docs/sssf.md`.

### README.md, stale

- **:211 and :399.** They say twelve cookbooks; there are 13.
- **:216 and :411.** The helper list leaves out `adw_sandbox_usage`.
- **:401.** The `scripts/` list leaves out `check_prices.py` and `e2b_template.py`, though :108 says to run `e2b_template.py`.
- **:546.** It says chains are 40 to 300 lines. `adw_tickets.py` is 366 and `adw_kill.py` is 36.
- **:507, "~200 SSSF files".** The skill alone has 212 tracked files, and `adws/` about 60 more.
- **:180-182.** The key table leaves out standards_reviewer, spec_reviewer and ticket_builder.
- **:346.** It names only `agent_pi.py` as tailing stdout; `agent_cc.py` does too.
- **:334.** The `--session-id` note is pi only. Claude Code continues a session with `--resume`.
- **:522-527, "Still open".** It's hub status, and it goes stale within days.
- **:150.** The model-trial history is run history, not install guidance.

### README.md, gaps

- **`--sandbox`:**
  - no key names: `VERCEL_TOKEN`, `VERCEL_TEAM_ID`, `VERCEL_PROJECT_ID`, `E2B_API_KEY`;
  - no `token.env` format: `CLAUDE_CODE_OAUTH_TOKEN=`;
  - no example;
  - `env.sample` has none of these keys either.
- **`--container`:** nothing says how to start it with `container/run.sh`. Podman and `XDG_RUNTIME_DIR` aren't in the prerequisites.
- **Prerequisites:** they leave out `just`, and gitleaks 8.30.1 or later. Once the hook is on, a push is refused without gitleaks.
- **Private repos:** `.git/sssf-private-names`, and keeping a private repo off VMs, appear only in fork.md and secret-scan.md.
- **`sssf-update`:** nothing says when it refuses: an unmerged source, a dirty tree, or a live chain. `SSSF_ALLOW_UNMERGED`, `--take` and `--keep` aren't documented.
- **A plain `cp` install:** `stamp.json` gets "skill: unknown" unless `SSSF_SOURCE` is set.
- **Recipes the README never mentions:**
  - `login-check`, `trial-review`, `prompt`, `scout`, `plan`, `plan-build`, `sdlc`, `simple-sdlc` and `obs-dev`;
  - the `SSSF_CONFIG=` roster swap;
  - `mod sssf 'sssf.just'`, for a repo with its own justfile.
- **No runnable example for:**
  - building a tickets folder;
  - `--add-roster`. At :478 the trial comes before :486 says to add the roster;
  - `limits.yaml`;
  - `adw_document --base`.
- **No billing check:** nothing says how to confirm Claude agents won't bill the API, with `just login-check`.
- **No usage-credits stop:** nothing mentions the stop when the account moves onto usage credits.
- **Prices:** `prices.json`, `check_prices.py` and the warning for an unlisted model are missing.
- **`SSSF_ADW_ID`:** nothing says agents get it in their environment.
- **Cloning this repo:** nothing warns that a clone carries the hub's guard-change factory and its refuse rules.
- **The post-install checklist:** only the failure table links to it.
- **Trials:** the `referee` panel and `trial-review` are missing, and so is the fact that a trial is always simple-sdlc.

### README.md, style

- **Duplication:**
  - the cost caps are explained in six places;
  - `rate_limit_stop`, `max_call_cost`, the usage panel and `/usage` in two each;
  - ChatGPT having no usage windows in three.
- **The spend caps** sit under "The trace". They'd read better as their own section.
- **:306.** The line about a red suite needs `adw_build_test.py`'s explanation.
- **:6 and :556-580.** Upstream's video and the "IndyDevDan" sign-off.
- **:537.** It says the prompts describe a demo app, but that app exists only on upstream's `example` branch.
- **Repeated principles.** :191-201 and :533-552 say the three principles twice.
- **:449.** This is the only pointer to `choose_a_flow.md`.

### docs/fork.md

- **Wrong, :35.** It says the installer asks whether a repo is private. `install.py` asks nothing; the operator agent asks.
- **Stale, :3.** The counts are from 2026-11-02. Today the fork has 395 commits, 338 files, +54,384/−513 lines, 1059 skill tests, 35 hub tests and about 70 dashboard tests.
- **Stale, "Upstream has added nothing since".** This holds for `upstream/main`. `upstream/example` is newer.
- **Stale, the log.** It stops at b72d057 and has no row for the obs relay.
- **Stale, :46, "free Hobby tier".** That's Vercel's tier. E2B runs on credit.
- **Gap.** It doesn't list `adw_kill`, `adw_worktree`, `adw_sandbox_usage` or `adw_guard_change` as new.
- **Style.** It files the plan gate, quiet agents and private repos under "Spend and usage limits".

### Other docs

- **`docs/sssf.md:129`.** It says the suite takes about 75 seconds; it takes about 4 minutes now.
- **`docs/secret-scan.md`.** It's accurate. Its "On this machine" and "Who pushes" sections describe one workstation, and should be labelled as the hub's own setup.
- **`install.md` in the skill:**
  - it says "twelve starter ADWs";
  - it lists 5 of the 9 prompt folders;
  - its checklist names only `OPENROUTER_API_KEY`, though the pi roster's planner and reviewers also need `FIREWORKS_API_KEY` and `OPENAI_API_KEY`.

### Images

None has changed since upstream's de31374.

- **00:** plausible.
- **01 to 04:** each draws a "tester" agent.
- **01 and 06:** six tables; there are seven.
- **03:** 8 playbooks, 6 ADWs, 13 modules and 4 agents, and "/install". The real numbers are 13 cookbooks, 17 ADW scripts, 34 module files and 8 agents.
- **05:** it puts `## Report` in `builder/system.md`; it's in `user.md`.
- **08:** fine.
- **07:** missing. It was an image of upstream's demo app, on `upstream/example` only, and nothing on main refers to it.
- **Backgrounds:** 01 to 06 are transparent, with light text and a dark glow, so they're hard to read on GitHub's light theme.
- **Animation:** all eight start at opacity 0 and fade in, so a renderer that doesn't animate shows a blank image.

### What a new user hits first

1. The install block adds an empty commit and never commits the install.
2. The pi link is dead.
3. `/sssf` is missing until Claude Code restarts.
4. The pi starter roster needs three keys; the docs name one.
5. `adw_build_test --worktree` with `just merge` can delete the work.
6. `--sandbox` has no key names, no token format and no example.
7. `--container` has no start instructions.
8. The post-install checklist is buried, so CI fails on SSSF's own files.
9. The counts in the README and the images disagree.

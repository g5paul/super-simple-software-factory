# Design: trials

Status: building. Settled in one grilling session on 2026-11-01. Where the text and a decision disagree, the decision wins.

A trial runs one request under several rosters at once, each as its own worktree run, then has a panel of reviewers judge what each one built. The engineer picks the winner from a scorecard, and `just trial-merge` lands it. Every attempt's scorecard stays in the trace, so over many trials a repo builds up a leaderboard of which roster does its work best and at what cost.

[Parallel worktree runs](parallel-worktree-runs.md), decision 1, left room for this: "One task tried several ways, with the best one merged, is left room for, not built."

## Why

A roster is a bet: which harness, which models, which thinking levels. The bet changes as models change, and the only fair way to compare two rosters is on the same task, from the same commit, judged the same way. Today an engineer can run a request twice with `--config`, but nothing ties the two runs together, judges them side by side, or remembers which one won.

## What was found

A read-only survey of the skill before the decisions:

- **Rosters are files already.** Every chain takes `--config`. The skill ships three starter rosters: `sssf.config.yaml` (Pi), `sssf.config.claude_code.yaml`, and `sssf.config.chatgpt.yaml`. A repo is stamped with one, as `adws/adw_sssf_config/sssf.config.yaml`. `just rosters` lists every `sssf*.yaml` in that folder.
- **Worktree runs keep attempts apart.** Each has its own branch, folder, adw_id, trace, and cost. `just merge` lands one and `just discard` drops one. `max_worktree_runs` defaults to 2.
- **Only `adw_simple_sdlc` has every gate.** It plans, builds, runs all of `quality.py` in a bounded fix loop, has the reviewer approve or ask for revisions, and commits the code only when both pass. A successful run is one whose checks passed and whose own reviewer approved.
- **Each roster brings its own reviewer.** A roster's reviewer passing its own builder's work is a weak gate for comparing rosters.
- **Cost is a list-price estimate on both harnesses,** so a Claude Code run and a ChatGPT run compare fairly with each other. Neither is what a subscription bills.
- **Claude Code and ChatGPT are separate plans.** Two Claude Code attempts share one usage window; a Claude Code attempt and a ChatGPT attempt do not.
- **Pi reports its sign-in.** `pi auth check --provider openai --json` answers `{"status": "ready", "authType": "oauth"}` without printing a credential. `claude auth status` answers the same question for Claude Code.
- **fusion-harness's `/fh-rank`** has every model answer, then every model rank all the answers with the authors hidden and the order shuffled per reviewer, and reports average positions with and without each model's vote for itself. It is a pi extension driven from pi's interactive session.

## Decisions

### Goal and shape

1. **Both goals, one mechanism.** A trial lands one winner and records a scorecard for every attempt. Which roster is best for a repo is answered from the scorecards of many trials, not from one.
2. **What varies is the roster file.** Attempts differ only in their roster: harness, models, and thinking levels together. The prompts, the chain, and the request stay the same. A repo keeps its rosters side by side in `adws/adw_sssf_config/` as `sssf.config.<name>.yaml`, and a trial names the ones to race. The repo's own `sssf.config.yaml` is called `main`.
3. **Each attempt runs the whole chain,** from the same commit, with the same request, and plans for itself. Planning is part of what a roster is good or bad at.
4. **One launch, separate runs.** `just trial "<request>" main chatgpt` starts one ordinary worktree run per roster, each with its own adw_id, tagged with a shared trial id. The trace, `just kill`, the dashboard, and every cost cap keep working per run. This bends [parallel worktree runs decision 2](parallel-worktree-runs.md) only in that one command starts several runs.
5. **The engineer picks the winner, from a scorecard.** There is no judge that merges on its own.
6. **Gates first, then the cheapest.** An attempt passes when its run succeeded, its last full `quality.py` check passed, and no panel reviewer blocked it. Among those that pass, the suggestion is the cheapest. The scorecard also shows wall time, findings, diff size, and tokens, without weighting them into one score. When none pass, there is no suggestion, and nothing merges unless the engineer chooses to.
7. **Trials run `adw_simple_sdlc`,** the only chain with all the gates.

### The panel

8. **A panel, one reviewer per family.** Once every attempt has ended, a panel reviews them. It has one reviewer from each model family in the trial: Claude (a `claude_code` agent, or a Pi agent on `anthropic/*`), or the Pi provider's name otherwise, such as `openai`. Each panel reviewer is that family's first roster's `reviewer` agent, with its model, thinking, and harness, and the panel's own prompts. A roster's own reviewer still runs inside its attempt, as part of the work. This replaces the single referee from the main roster that the grilling first settled.
9. **The panel sees the request and the diff, nothing else.** For each attempt: the request, and the diff from the trial's base commit to the attempt's branch, without `specs/` and the data dir. No plan, so a good plan can't excuse a bad diff. No roster name, so a reviewer can't favour its own family. Attempts are labelled A, B, and so on, in a fresh random order for each reviewer.
10. **Each reviewer gives a verdict on every attempt and ranks them all.** A verdict is approve or block, with the requirements it checked and what blocks it.
11. **How the verdicts combine.** An attempt passes the review gate only when no reviewer blocks it. The scorecard shows each attempt's average rank, with and without the vote of the reviewer from its own family, as `/fh-rank` does. The suggestion stays the cheapest passing attempt, with the ranks beside the costs, so the engineer can see when the cheapest and the best ranked differ.
12. **The panel is its own run.** `just trial` launches it after the attempts end: a plain run in the main checkout, tagged with the trial id, under every cost cap, so its cost never lands on an attempt and each attempt's cost stays comparable. Only attempts whose run succeeded are reviewed; a failed run has already failed the gates, and reviewing it would cost money for nothing. If the panel fails, the review gate reads unknown and there is no suggestion.
13. **Borrow the method, not the code.** SSSF implements the panel itself, with credit to fusion-harness's `/fh-rank` and karpathy's llm-council. There is no dependency on the fusion-harness repo, which is a pi extension built for pi's interactive session and whose stacks bill API keys.
14. **The same roster twice is allowed.** `just trial "<request>" main main` races a roster against itself, which shows how much its results vary from run to run. Each attempt is its own entry in the scorecard and the leaderboard. With one family, the panel has one reviewer, and the rank without self-votes is empty; the scorecard says so.

### Launching and landing

15. **In parallel, or not at all.** A trial needs one free worktree slot per attempt. Short of that, it refuses the whole trial, naming the runs holding the slots. It never runs half a trial and never queues. Before launching, it checks the repo's and the machine's cost caps. If two attempts share a family's plan, it warns that they share one usage window.
16. **Every roster must be ready before anything starts.** Each roster must load and validate for `adw_simple_sdlc`. A roster with a Claude Code agent needs `claude auth status` logged in. A Pi agent's provider must report ready with `pi auth check`; on `openai`, that means the ChatGPT sign-in (`authType: oauth`) and `SSSF_PI_AUTH=subscription`, so it can't fall back to billing the API. One failure refuses the trial.
17. **An attempt refused at launch ends the trial.** Each attempt still takes the launch lock on its own, so another run can take a slot between the trial's check and an attempt's launch. If any attempt exits without starting, the trial stops the attempts that did start and discards their branches.
18. **`just trial` drives the whole trial.** It launches the attempts as detached processes, waits for all of them to end, runs the panel, and prints the scorecard. If it dies, the attempts carry on, since they are ordinary runs; `just trial-review <trial>` then runs the panel and prints the scorecard. The same command retries a panel that failed. An attempt that fails or is killed doesn't stop the others; it just doesn't pass the gates.
19. **Landing the winner.** `just trial-merge <trial> <adw_id>` merges the winner the way `just merge` does, then discards the other attempts. If the merge fails, nothing is discarded. `just trial-discard <trial>` drops every attempt. The traces and scorecards stay.

### What gets recorded and shown

20. **Each attempt records a `trial` event** at its start: the trial id, the roster name, the roster file's sha256, and the base commit. The request comes from the run's own `sessions.request`, so the tag stays small. The hash keeps a roster edited halfway through a series of trials apart from its old self. `just trial` passes it to each attempt in `SSSF_TRIAL`, so no chain changes.
21. **The panel run records the scorecard.** A `trial_diffs` event holds each reviewed attempt's diff size, recorded before any panel agent runs, so a failed panel still leaves the sizes. A `trial_scorecard` event holds every attempt's row, the ranks, the verdicts, and the suggestion. Python computes the scorecard once; the dashboard reads it rather than repeating the logic. The winner's run records `trial_merged`.
22. **What the numbers mean.** Wall time is the run's start to its end. Diff size is lines added plus lines removed from the base to the branch, without `specs/` and the data dir. Findings are the panel's: blocking reasons and unmet requirements, per reviewer. Cost and tokens are the run's own totals. The scorecard's last line is the trial's whole cost, every attempt plus the panel.
23. **No cap of its own.** Each run is held to `max_run_cost` as usual, and the daily and machine caps cover a trial that grows too large.
24. **A trial page on the dashboard,** at `#/<project>/trial/<trial>`: the attempts side by side, with gates, cost, wall time, findings, diff size, tokens, ranks, and the suggestion, each attempt linking to its run. A run that belongs to a trial links to its trial page. `just trial-show <trial>` prints the same table in a terminal.
25. **A roster leaderboard per repo,** on the project page under the gauges. One row per roster name and file hash: trials entered, attempts that passed the gates, wins, and the median cost and wall time of the attempts that passed. The row whose hash matches the roster file on disk is marked current. It is per repo, because a roster's strengths depend on the codebase.

### Rosters, operation, and rollout

26. **Named roster files, added on request.** `install.py --add-roster chatgpt` (or `claude_code`, or `pi`) stamps the skill's starter roster as `adws/adw_sssf_config/sssf.config.<name>.yaml` and records it in the stamp, so `--update` keeps it current like any stamped file. An engineer can also write a roster by hand under any name.
27. **Only when the engineer asks.** The skill gets a trial cookbook. The operator runs a trial only when the engineer asks for one by name, such as "try this with main and chatgpt". It never turns an ordinary request into a trial. After a trial, it reports the scorecard and waits for the engineer's pick.
28. **Rollout is opt-in.** An update brings the trial code, the cookbook, and the dashboard page, but no roster files. The update note says how to add one, and that three attempts need `max_worktree_runs` raised to 3.
29. **Proof in sssf-sandbox.** A real trial of `main` (Claude Code) against `chatgpt`, on a small request: the panel, the scorecard checked against each run's trace, `just trial-merge`, and the leaderboard. Before that, tests cover the refusals: too few slots, and a roster that isn't signed in. About $1 to $2 of Claude estimate, plus some ChatGPT plan usage.

## Open

- A judge that picks the winner on its own, once enough scorecards show whether its picks match the engineer's.

## Log

| Date | Where | What | Result |
|---|---|---|---|
| 2026-11-01 | hub | grilling session: decisions 1 to 29 | settled |
| 2026-11-01 | sssf-sandbox | `just trial ... main chatgpt` without `SSSF_PI_AUTH=subscription` in `.env` | refused before any attempt started; 85 sessions before and after, no `sssf/` branch |
| 2026-11-01 | sssf-sandbox | trial c258e6b9: add `textkit/word_count.py`, `main` (Claude Code) against `chatgpt` (Pi on GPT) | both attempts succeeded and passed. Panel b33eaf13, one Claude and one GPT reviewer, each drew its own order; both ranked the GPT attempt first, so rank and rank* agree. `main` 1fd1afe2 $0.57 in 2m21s; `chatgpt` c96c5204 $0.12 in 3m57s, suggested as the cheapest to pass. Panel $0.21, trial $0.90. Scorecard checked against each session's cost, timestamps, and phases |
| 2026-11-01 | sssf-sandbox | `just trial-merge c258e6b9 c96c5204` | fast-forwarded master to a2d8088 and discarded 1fd1afe2. 133 tests pass. The leaderboard shows one row per roster, with hashes that match the files on disk and 1 win for `chatgpt` |
| 2026-11-01 | hub | the first trial's log held only the launch line until the end | Python buffered stdout to the file. `adw_trial.py` now line-buffers it |

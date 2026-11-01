# Design: parallel worktree runs

Status: done. Settled in two grilling sessions on 2026-10-30, built, proven in sssf-sandbox (all seven cases of decision 12, the three setup cases of decision 22, the three spend-cap cases of decision 48, the three daily-cap cases of decision 57, and the gauge's checks of decision 65), proven on a real worktree run in pi-agent-observability (70f9e62e), and rolled out to yt-summarizer and pi-agent-observability. What is still open is under "Open", below the decisions. Where the text and a decision disagree, the decision wins.

[Branch per run](branch-per-run.md) gave each run its own branch and folder, but only one at a time: its decision 12 refused a second worktree run while one was alive, because in the container every run shared one project environment, and that was untested. This design lifts that limit, so a repo can have more than one `--worktree` run going at once.

## Why

A run takes minutes. While one works on a bug fix, an engineer with a second, unrelated request waits for it or runs the second on their own branch by hand. The worktree already keeps each run apart in git. What stops two at once is the state they still share: the container's project environment, the trace db's write lock, the merge in the main checkout, and the count of live runs, which can't see across the host and the container.

## What was found

A read-only survey of the skill, before the decisions:

- **Container environment: a real collision.** `run.sh` sets `UV_PROJECT_ENVIRONMENT` to one path per repo, so every worktree's `uv run` syncs the same environment, and its editable install points at whichever worktree synced last. On the host each worktree builds its own `.venv`, so there is nothing to fix there.
- **Trace db: mostly safe.** Writes are small, autocommit, WAL, keyed by adw_id. The write lock waits 5s, then the phase fails. Two runs opening an older db at the same moment could race on the same column migration.
- **Caps.** `max_run_cost` is per run, in memory. `rate_limit_stop` reads one account-wide sample once per agent, so N runs can all pass at the threshold and then all send.
- **Keyed by adw_id, so safe:** the session folders, `agent_map.json`, `context_handoff/`, the coding agents' session ids, and `just kill`.
- **Live-run count.** `live_runs()` checks each pid with `kill(pid, 0)`, which can't see a host pid from the container or the other way round, though both share the trace.
- **Merge.** Two runs cut from the same tip: the first fast-forwards, the second needs a merge commit, so under `merge: ff_only` it is always refused. Two `just merge` at the same moment would race on git's index lock.
- **Quality checks.** SSSF's own use no fixed ports or temp paths. A repo's own commands might.
- **The dashboard** reads everything per adw_id and assumes nothing about one live run.

## Decisions

### Goal and shape

1. **Independent tasks at once.** Each run takes a different request. One task tried several ways, with the best one merged, is [trials](trials.md), its own design.
2. **Separate launches.** Each run is its own `uv run adws/adw_<chain>.py ... --worktree`, with its own adw_id, merged on its own with `just merge`. There is no batch ADW. The trace, the dashboard, `just kill`, and `just merge` keep working per adw_id.
3. **A cap per repo.** `max_worktree_runs` in `adws/adw_sssf_config/repo.yaml`, default 2. A launch over the cap refuses, naming the runs alive. 1 is today's behaviour. This supersedes [branch-per-run decision 12](branch-per-run.md).
4. **Only when the engineer asks.** If a request arrives while a run is going, the operator says so and asks whether to start it beside the other one or wait. It never starts a second run on its own judgement.

### Making it safe

5. **An environment per run in the container.** A worktree run in the container uses `UV_PROJECT_ENVIRONMENT` with `-<adw_id>` appended, so two runs never sync the same one. `just merge` and `just discard` delete it. A run without `--worktree` keeps the shared one. The cost is a fresh environment per run.
6. **Caps stay per run.** `max_run_cost` and the per-agent `rate_limit_stop` check are unchanged. A repo can spend up to its run cap times its live runs, and the docs say so. The cap of 2 bounds the overshoot.
7. **`just rebase <adw_id>`, only on the engineer's word.** For a repo with `merge: ff_only`, where the second of two runs can never fast-forward. It rebases the run's branch onto the launch branch's tip, aborts on a conflict and leaves everything as it was, reruns `quality.py` in the worktree, and logs `worktree_rebased` with the old and new tips. `just merge` itself still never rebases ([branch-per-run decision 16](branch-per-run.md)).
8. **A lock around merge, rebase, and discard.** The second of two waits up to 30s, then refuses with a clear message.
9. **Live runs counted across host and container.** Each process row records the hostname. A run started on the other side counts as live while the trace says it is running. Pids are checked only on the side that started them. `just kill` refuses a run started on the other side and names that side.
10. **Trace writes under load.** The write lock waits up to 30s, and the column migration skips a column another process just added.
11. **The repo's own checks.** The onboard and install checklists ask whether `quality.py` is safe to run twice at once: no fixed port, temp path, or shared database. If it isn't, the repo keeps `max_worktree_runs: 1` until it is. SSSF doesn't try to detect it.

### Proof, rollout, and docs

12. **Proof in sssf-sandbox, then a normal update for each project.** Every case is a real run:
    1. Two worktree runs at once on the host. Both succeed; the first merge fast-forwards, the second makes a merge commit.
    2. A third run while two are going refuses, naming both.
    3. Two at once in the container, each in its own environment, removed by merge and discard.
    4. One from the host and one from the container at once. The cap counts both, and `just kill` from the wrong side refuses and names the right one.
    5. Under `merge: ff_only`, the second merge refuses, `just rebase` reruns the checks, and `just merge` fast-forwards.
    6. A rebase with a conflict aborts, and the branch and checkout are as they were.
    7. Two `just merge` at the same moment: one waits for the other's lock.
13. **Existing repos choose their cap.** When an update brings `max_worktree_runs` into a repo, the project session checks its `quality.py` against decision 11 and writes 1 or 2 into `repo.yaml`, with a one-line reason.
14. **The operator watches each run.** `run_adw.md` gets a section on running beside another run: ask first (decision 4), watch both, report each by adw_id as it finishes, and never merge either without the engineer's word.
15. **Scope.** `adw_tickets` stays out, as it was for branch per run. Superseded by decisions 26 to 38.

### Setting up a worktree

The pi-agent-observability update found the gap: a worktree is a fresh checkout, so it has none of the untracked dependencies the main checkout has. That repo has five `node_modules` folders and four bun lockfiles, and its checks call `tsc`, `vue-tsc`, and `vite` through `npm exec`. A Python repo never hit this, because uv builds each worktree its own `.venv` on its first `uv run`. These decisions hold for any worktree run, one at a time or not.

16. **`worktree_setup` in repo.yaml.** A list of shell commands, run in order with `sh -c` from the worktree's root, stopping at the first that fails. Empty by default, so a repo without it runs as before. They are the repo's own lines, written the way its README writes them, such as `cd extension && bun install --frozen-lockfile`.
17. **A traced first phase.** `setup`, kind `code`, after the launch lock is released and before any agent starts. Each command's output goes to the run's `context_handoff/setup/`, the way quality checks keep theirs.
18. **A failure ends the run there.** Before any agent spends anything. The worktree and branch stay for a look, and `just discard` removes them. No retry.
19. **A resume skips setup; a rebase reruns it.** A resumed run's worktree is already set up. A rebase can bring in a changed lockfile, so `just rebase` runs setup again before its checks, and a failed setup puts the branch back, like a failed check.
20. **The quality checks' environment.** The engineer's own environment, plus the run's own `UV_PROJECT_ENVIRONMENT` in the container.
21. **Rollout.** The onboard and post-install checklists ask what a fresh checkout needs before the checks run, and the answer goes into `worktree_setup`. An update that brings the key in has the project session work out its repo's commands, the way it chose `max_worktree_runs`.
22. **Proof.** In sssf-sandbox: a setup that writes a file the tests need, so the run passes only if setup ran; a setup that fails and stops the run before its first agent; and a rebase that reruns setup. Then one worktree run in pi-agent-observability, by that session, on the engineer's word: the only proof of `node_modules`.
23. **Worktree runs only.** A plain run works in the engineer's checkout, which has its dependencies already.
24. **600s per command.** `worktree_setup_timeout` in repo.yaml raises it. A timeout is a failed setup.
25. **Recorded here,** not in a separate doc.

### A ticket run on its own branch

Settled in a third grilling session on 2026-10-30. A ticket run is the longest work SSSF does, about $3 for six tickets, so it gains the most from being kept apart. Two things made it more than a flag. A ticket run resumes by git history, finding its feature's "NN:" commits, while a worktree run resumes by adw_id. And a ticket run's guard refuses `main`, which a worktree makes needless.

26. **One branch for the whole feature.** A `--worktree` ticket run builds every ticket in order on `sssf/<adw_id>`, in one worktree, and counts as one run against `max_worktree_runs`. Parallel tickets stay deferred.
27. **It may launch from `main`.** With `--worktree`, a ticket run's commits land on its own branch, and only `just merge` moves them, so the `main`/`master` refusal is lifted. Without the flag it stays.
28. **The feature folder must be committed.** A worktree checks out the launch branch's last commit, so an uncommitted edit to the spec or a ticket would not reach the agents. A new `--worktree` ticket run refuses while the feature folder has uncommitted changes in the launch checkout, and names the files. Other uncommitted changes get the usual warning.
29. **Opt-in,** as on the other chains.
30. **Only a finished run merges.** A run that gives up partway is `fail`, and `just merge` lands only a successful run. A half-built feature waits on its branch for a rejoin, or for a merge by hand.
31. **A rerun rejoins its run.** A stopped `--worktree` ticket run resumes with `--adw-id <id>`, in the same worktree and on the same branch. Its stop message prints the command. A new adw_id never adopts another run's branch: one adw_id owns one worktree, and `just merge`, `rebase`, and `discard` rely on that.
32. **One branch per feature at a time.** While an unlanded worktree run holds a feature folder, any other ticket run on that folder refuses, with `--worktree` or without, before any worktree or agent. The refusal names the holding run and how to resume or discard it.
33. **The cap stays per attempt.** Each attempt gets its own `max_run_cost`, as each rerun does today. The stop message gives the feature's total across every attempt, as it already does.
34. **Leftover work stays in the worktree.** A stop's look, keep, and drop commands start with `cd <worktree>` when the run worked in one, for every chain. A rejoin on a dirty worktree refuses, as a rerun on a dirty tree does.
35. **Every agent starts fresh on a rejoin.** A rejoin would resume each agent's context window from `agent_map.json`. A ticket run starts every attempt with an empty map, so the unfinished ticket's builder doesn't carry its failed attempt, as a rerun with a new adw_id never did.
36. **A rejoin reuses the scout's notes.** They cover the whole feature and sit in the run's `context_handoff/`. A rejoin that finds them skips the scout and says so in the read step.
37. **Done marks live on the branch until the merge.** The feature folder in the engineer's checkout shows no ticket done until then. The finish and stop messages say so in one line, and SSSF writes nothing to the checkout. The dashboard's ticket table reads the trace, so it shows the real progress.
38. **Proof.** In sssf-sandbox, a two-ticket feature launched from `master` with `--worktree`: stopped after ticket 1 by a low `max_run_cost`, rejoined with `--adw-id`, which skips the scout and ticket 1, finished, and merged. Then three refusals: a second run on the held feature, an uncommitted edit to the feature folder, and a plain run on `master`. Then the cookbooks, and a rollout on the engineer's word.

### A cap on what live runs spend together

Settled in a fourth grilling session on 2026-10-30. Decision 6 kept every cap per run, so two runs at once can spend up to twice `max_run_cost`. `rate_limit_stop` already reads one account-wide sample, so it needs no change. Dollars were the open part.

39. **What it caps.** What the live runs spend together, against one cap. A budget over time, across finished runs too, is a different feature and goes on the open list.
40. **`max_live_cost` in repo.yaml.** The roster is chosen per run, so two runs on different rosters would disagree about a shared limit. repo.yaml holds the repo's own rules, beside `max_worktree_runs`.
41. **Finished calls count.** The trace learns a call's cost when it ends, so each live run can overshoot by one call, at most its `max_call_cost`. The docs say so. Nothing is reserved when a call starts.
42. **Every live run counts.** Plain or worktree, on either host, by the liveness test `max_worktree_runs` uses: a live process on this host, or a running session on the other.
43. **Off by default.** An update changes nothing until someone sets it. Onboarding asks the engineer, and suggests `max_run_cost` times 1.5. An update only mentions it.
44. **A budget stop at the next send.** The run about to send stops before it, as a `BudgetStop`, with a `live_budget_stop` event, the dashboard's stop reason, and the usual leftover-work help. The message names each live run and what it has spent. The others stop at their own next send.
45. **Checked before every send, and at launch.** Beside `check_run`, one trace query each. At launch, under the launch lock and before any worktree or setup, a new run refuses while the live runs are already at the cap.
46. **Each run counts this attempt only.** A live run's spend is what its calls have cost since its current process started, as decision 33 made `max_run_cost` per attempt.
47. **On the dashboard.** `run_caps` records `max_live_cost`, and the stop reason has its own label. No repo-wide spend chart.
48. **Proof.** In sssf-sandbox with `max_live_cost` at about $0.15: two worktree runs at once, where one stops with `live_budget_stop` naming the other; a third launch refused before any worktree while they are at the cap; and with the setting removed, the same two runs as today. Then the cookbooks, and a rollout on the engineer's word, in which the repos set nothing.

### A cap on what a repo spends in a day

Settled in a fifth grilling session on 2026-10-31. `max_live_cost` stops runs that spend together, but once they finish, the next ones start from zero. Nothing held what a repo spends over a day. yt-summarizer's busiest day so far cost $34.20 over 58 runs; its others, and pi-agent-observability's, cost a few dollars.

49. **One repo, in repo.yaml.** `max_daily_cost` caps one repo's trace, beside `max_live_cost`. A cap across every repo on the machine would need a setting outside any repo and a read of every trace; it goes on the open list.
50. **The last 24 hours, rolling.** No time zone, and no midnight reset that lets a run spend the cap at 23:50 and again at 00:10.
51. **What counts.** Every `agent_end` cost that ended inside the window: any chain, any status, Claude Code or pi. Each live run adds its in-process count where that is higher, as decision 46's build found the trace lags a call in correction. A run that began yesterday counts only its calls inside the window.
52. **Checked before every send, and at launch.** Beside `check_run` and `check_live`, and at launch under the launch lock, before any worktree. Its own `DailyBudgetStop` and `daily_budget_stop` event. A run can still overshoot by one call.
53. **The message says when it clears.** The last 24 hours' spend, the cap, and the UTC time the spend drops under it if nothing else runs, found by walking the window's calls from the oldest. Then how to raise it, and, on a stop, the usual leftover-work help.
54. **repo.yaml only.** Every check reads it fresh from the main checkout, so a raise takes effect at the next send. No `.env` override: a cap one line can quietly lift guards less. A stopped run resumes with `--adw-id`.
55. **Off by default.** Onboarding and the post-install checklist ask the engineer, suggesting 4 × `max_run_cost`. An update only mentions it.
56. **On the dashboard.** `run_caps` records `max_daily_cost`, and the stop has its own label. A gauge of the last 24 hours goes on the open list.
57. **Proof.** In sssf-sandbox: a launch refused because the trace's last 24 hours are already at the cap, naming when it clears, at $0; a `simple_sdlc` run with the cap just above the last 24 hours, stopped before its second call; and a run with the setting off, as today. About $0.50. Then a rollout on the engineer's word, in which the repos set nothing.

### A gauge of the last 24 hours

Settled in a sixth grilling session on 2026-10-31. Decision 56 showed `max_daily_cost` only in `run_caps` and on a stop, so the first sign of the cap was a run stopping.

58. **Where.** One gauge component, on each repo's card on the projects page and at the top of its runs list, the page `just obs` opens.
59. **What it shows.** The last 24 hours' spend, always. With a cap, a bar: "$X of $C in the last 24 hours". Without one, "no daily cap". At or over the cap, the UTC time it drops under.
60. **The cap from repo.yaml.** The server reads `max_daily_cost` from the repo's own `adws/adw_sssf_config/repo.yaml` on each request, the value the checks read; `run_caps` is stale until the next run. A missing or unreadable file reads as no cap.
61. **The check's number.** The sum of `agent_end` costs that ended in the last 24 hours, and the same clear-time walk. The server can't see a live run's in-process count, so a call in flight isn't counted until it ends; a tooltip says so.
62. **Daily only.** A gauge of `max_live_cost` goes on the open list.
63. **The account panel's colors.** Plain under 80% of the cap, amber from 80%, red at or over it, when runs stop.
64. **Its own endpoint.** `/api/projects/:project/day` returns the spend, the cap, and the clear time. Each page polls it every 10 seconds, so the runs list's half-second poll doesn't scan 24 hours of events.
65. **Proof, at $0.** bun tests for the window, the clear time, and a missing or broken `repo.yaml`; in sssf-sandbox with a cap set, the endpoint's total and clear time against the Python check's message; and Chromium screenshots of a card and a runs list header, under the cap and over it.

### A gauge of what live runs spend together

Settled in a seventh grilling session on 2026-11-01. The dashboard drew `max_daily_cost` against the last 24 hours, but `max_live_cost` showed only in `run_caps` and on a stop.

66. **The check's number.** What `check_live` sees: for each live run, the cost of the calls that ended since its `adw` process started, so a resumed run counts its current attempt only. The server tests each PID with `process.kill(pid, 0)`, since it runs on the ADWs' machine; a row from another host falls back to its session reading `running`. Session status alone would count a run killed with `kill -9` forever. A call in flight counts once it ends, and a tooltip says so.
67. **Where, and when.** On each project card and atop its runs list, under the 24-hour line. Only when `max_live_cost` is set or a run is live: an idle repo with no cap gets no line.
68. **What it says.** With a cap, a bar and "$X of $C across N live runs"; without one, "$X across N live runs, no live cap". Under it, each run as the stop message lists them, "adw_id $cost", largest first, each linking to its run on the runs list. The project card is itself a link, so there the ids are plain text. At or over the cap, "new runs and sends stop until a run finishes". There is no clear time to give.
69. **Its own endpoint and component.** `/api/projects/:project/live` returns the cap, the total, and each run's id and cost. `LiveGauge.vue` polls it every 10 seconds. The server reads the cap from `repo.yaml` on each request, as decision 60 does.
70. **The same colours.** Plain under 80% of the cap, amber from 80%, red at or over it.
71. **Proof.** bun tests on a temporary trace holding a live run, a run whose PID is dead, and a resumed run whose earlier attempt must not count, all at $0. In sssf-sandbox, one short run under a low cap, with the endpoint checked against Python's `live_spend` while it runs, for a few cents. Chromium screenshots of a card and a runs list, with a cap and without.
72. **One repo.** A cap across every repo stays open. The account panel doesn't change.

### One cap across every repo

Settled in an eighth grilling session on 2026-11-01. `max_daily_cost` holds one repo's trace (decision 49), so two repos running unattended on one machine could each spend up to their own.

73. **Daily only.** One cap on what every repo's runs spent in the last 24 hours. No machine-wide live cap: each repo's `max_live_cost`, `max_run_cost`, and `max_worktree_runs` already bound what runs at once.
74. **`~/.config/sssf/limits.yaml`, as `max_daily_cost`.** Beside the registry, in the folder `$SSSF_REGISTRY` moves, as `rate_limits.json` is. Container runs see it through the `~/.config/sssf` mount. Not in any repo: one repo shouldn't cap the others. A missing file is no cap.
75. **The registry's repos, and the run's own.** Each one's trace, read for the last 24 hours. The launching repo counts even if the registry lacks it.
76. **Checked where the repo's daily cap is.** Before every send and at launch, after `check_daily`, with the same walk and clear time, and the same count of the run's own spend the trace doesn't hold yet.
77. **No machine launch lock.** Two repos launching at once can both pass; each stops at its next send. The overshoot is one call per run, as with every daily cap.
78. **Its own stop.** `MachineBudgetStop`, a `BudgetStop`, and a `machine_budget_stop` event with its own dashboard label. The message gives the total, each repo's share, the cap and the file it's in, and the UTC time it drops under. `run_caps` records it as `machine_daily_cost`.
79. **On the account panel.** "$X of $C across every repo in the last 24 hours", a bar, the same colours, and the clear time at the cap. Only when the file sets a cap.
80. **Set by hand.** Onboarding a repo doesn't ask, and `install.py` doesn't write the file. The README says how; the install checklist's daily-spend step and the update note say so in a line.
81. **A repo that can't be read counts $0.** A moved or deleted repo, or a trace that won't open, doesn't block every launch on the machine. The message lists the repos it read.
82. **Proof.** Under a temporary `$SSSF_REGISTRY` for sandbox runs only, so the real repos are never capped: a launch refused at the machine's real total, at $0; a cheap two-agent run stopped before its builder; a run with no file, as today; and the account panel's line against the Python message, with screenshots. About $0.25.

## Open

Known, and not built. None of them blocks a run.

- **Setup is all or nothing.** Every worktree run runs all of `worktree_setup`, even when it touches one package. In pi-agent-observability the four installs take about a second from bun's cache, so this costs nothing there yet.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-10-30 | hub | grilling session, this design | settled |
| 2026-10-30 | hub | built decisions 3, 5 to 11, 13, 14: cap, locks, hosts, per-run environment, `just rebase`, cookbooks | 237 Python tests pass, 9 new |
| 2026-10-30 | sssf-sandbox | first launch of the proof | failed before any run started: `live_runs` read the new `host` column before anything migrated the existing trace. Fixed in 9b7f256, with a test on an old trace |
| 2026-10-30 | sssf-sandbox | cases 1, 2, 7: pr1vowel and pr1palin at once on the host | both success over the same 45s, $0.50. A third launch refused naming both. With the land lock held 6s by `flock`, both merges started together, waited, and landed 0.5s apart: fast-forward, then merge commit f638e56 |
| 2026-10-30 | sssf-sandbox | case 5: pr5initl and pr5rever, `--ff-only` | the second merge refused and pointed at `just rebase`. The rebase reran the checks as run d6d2bfe4, 89 tests in the rebased worktree, logged `worktree_rebased`, and the merge fast-forwarded. $0.51 |
| 2026-10-30 | sssf-sandbox | case 6: pr6sixty and pr6eight edit the same line | the rebase hit the conflict and aborted. Branch at the same commit, worktree and checkout clean, no event logged. git's `--continue` hints left the message (a6a8967). $0.35 |
| 2026-10-30 | sssf-sandbox | case 3: pr3conso and pr3title at once in the container | both success, each in `sssf-sandbox-<adw_id>` beside the shared environment. Merge and discard in the container removed each one's environment. $0.54 |
| 2026-10-30 | sssf-sandbox | case 4: pr4hostr on the host, pr4contr in the container | both sides counted both, a third launch from the container refused, and `just kill` from each side refused the other side's run, naming both hostnames. A discard from the host printed the command to remove the container run's environment. $0.75 |
| 2026-10-30 | hub | grilling session: setting up a worktree, decisions 16 to 25 | settled |
| 2026-10-30 | hub | built decisions 16 to 24 (bcd9eef) | 241 Python tests pass, 3 new |
| 2026-10-30 | sssf-sandbox | setup case 1: ws1acron and ws1squas, with a gitignored `.setup-marker` standing in for `node_modules` and a test that needs it | both success, `setup` phase 1 in each, the marker written in each worktree, 104 tests passed in the run. $0.40 |
| 2026-10-30 | sssf-sandbox | setup case 3: rebase ws1squas after ws1acron merged | the marker deleted from the worktree first; the rebase reran setup, which wrote it back, and the checks passed (run f78c2494). Merge fast-forwarded |
| 2026-10-30 | sssf-sandbox | setup case 2: ws2fail with a setup that exits 4 | `setup` failed, session `fail` at $0.00, no agent sessions or processes. The message named the command, its exit code, the tail of its output, and its log. Worktree kept until `just discard` |
| 2026-10-30 | yt-summarizer, pi-agent-observability | rollout of e8b822d: each session chose `max_worktree_runs` from its own `quality.py` | both 2, with a reason in `repo.yaml`. pi-agent-observability's update found the `node_modules` gap that decisions 16 to 25 answer |
| 2026-10-30 | yt-summarizer, pi-agent-observability | rollout of 88532f5: each session chose `worktree_setup` | yt-summarizer `[]`, checked in a fresh worktree. pi-agent-observability four `bun install --frozen-lockfile` commands; in a fresh worktree, quality passed 5/5 with them (2784c9c0) and 3 checks failed without (83a44605) |
| 2026-10-30 | pi-agent-observability | first worktree run, 70f9e62e: "Remove all from swimlane" | success, 11/11 phases, $0.50. `setup` phase 1 ran the four installs, and all six checks passed in the worktree, the three that failed without setup among them. Merged as a fast-forward under `ff_only` (4ad56bd), on the engineer's word |
| 2026-10-30 | hub, both projects | the run banner said `just merge` where pi-agent-observability needs `just sssf merge` | every message that names a recipe now goes through `utils.just()` (8431eed). pi-agent-observability's banner reads `just sssf phases 39027a65` |
| 2026-10-30 | hub | the dashboard showed a rebased run like any other | the run summary says how the latest `just rebase` went: kept, put back after failed checks or setup, or aborted on a conflict, which now logs `worktree_rebased` too. The card tags a kept rebase. The `worktree` event records `just` or `just sssf`, and the card's merge hint uses it. Read back right from sandbox runs pr5rever and ws1squas |
| 2026-10-30 | scratch repo | does each per-run environment's editable install point at its own worktree? Case 3's check printed nothing because sssf-sandbox has no `[build-system]`, so it never installs editable | a package with two worktrees, one environment each: each imports its own worktree's code, on the host (uv 0.10.3) and in the sandbox's container image (uv 0.13.6). One shared environment, synced from both, gave the first run the second's code, which is why decision 9 gives each run its own |
| 2026-10-30 | hub | grilling session: a ticket run on its own branch, decisions 26 to 38 | settled |
| 2026-10-30 | hub | built decisions 26 to 37 (c3f93d7) | 246 Python tests pass, 4 new |
| 2026-10-30 | sssf-sandbox `master` | ticket case 1: tw1words, two-ticket word-stats feature with `--worktree`, launched from `master` with `max_run_cost` $0.04 | `setup` ran, then the scout and ticket 1 (a35c20d); the cap stopped it before ticket 2's builder, at $0.11. The stop message printed the rejoin command and the done-marks line. The checkout's tickets still said `ready-for-agent` |
| 2026-10-30 | sssf-sandbox | ticket case 2: two more runs on word-stats while tw1words held it, one with `--worktree` and one without | both refused before any worktree or session, naming tw1words and its resume command |
| 2026-10-30 | sssf-sandbox | ticket case 3: rejoin tw1words with `--adw-id` and the $5 cap | no setup, scout skipped with a note, ticket 1 skipped; ticket 2 built in a new builder session, both reviewers approved with one non-blocking finding each, docs committed. Success, $0.53 for both attempts. `just merge tw1words` fast-forwarded `master` to c71e396, and the done marks then showed in the checkout |
| 2026-10-30 | sssf-sandbox | ticket case 4: an uncommitted edit to word-stats' spec, then `--worktree` | refused, naming the file, before any worktree or session |
| 2026-10-30 | sssf-sandbox | ticket case 5: a plain run on `master` | refused in the read step, $0.00, no agent |
| 2026-10-30 | hub | grilling session: a cap on what live runs spend together, decisions 39 to 48 | settled |
| 2026-10-30 | hub | built decisions 39 to 47 (4823686) | 253 Python tests pass, 7 new. One change: a run's own spend is the larger of the trace's and its in-process count, since `agent_end` is written only when a call ends, corrections and all |
| 2026-10-30 | sssf-sandbox `master` | live-cost case 1: lc1paus (`adw_build_test`, held 90s in its checks by a `PROOF_PAUSE` test) and lc1stop (`adw_simple_sdlc`) at once, `max_live_cost` $0.02 | the design said about $0.15, but one builder call costs more, so $0.02 put a single call over it. lc1stop's plan cost $0.25 and its build send stopped with `live_budget_stop`, naming lc1stop $0.25 and lc1paus $0.00, whose build call was still in flight. lc1paus finished, success, $0.07, needing no second send |
| 2026-10-30 | sssf-sandbox | live-cost case 2: lc1third launched while lc1paus sat in its checks at $0.07 | refused with `LiveBudgetStop` before any worktree or session |
| 2026-10-30 | sssf-sandbox | live-cost case 3: the same pair with `max_live_cost` off, lc2paus and lc2stop | both succeeded, $0.06 and $0.45, no `live_budget_stop`; `run_caps` recorded the cap as off. All four runs discarded, the pause test removed |
| 2026-10-30 | yt-summarizer, pi-agent-observability | rollout of 223043b, with `max_live_cost` | yt-summarizer 9488ed4 on `sssf-run`, quality 71f9e481; pi-agent-observability 31d1e90 on `main`, quality bcc276ad. Each took the commented `max_live_cost` block into its own `repo.yaml` and left the cap off: the value is the engineer's choice, $7.50 suggested for both |
| 2026-10-31 | hub | grilling session: a cap on what a repo spends in a day, decisions 49 to 57 | settled |
| 2026-10-31 | hub | built decisions 49 to 56 (1016ca4) | 260 Python tests pass, 7 new |
| 2026-10-31 | sssf-sandbox `master` | daily case 1: dc1refus launched with `max_daily_cost` $7.00, the last 24 hours at $7.56 from the earlier proofs | refused with `DailyBudgetStop` before any worktree or session, clearing at 15:41 UTC. Recomputed by hand from the trace: the same total and time. $0 |
| 2026-10-31 | sssf-sandbox | daily case 2: dc2stop (`adw_simple_sdlc`, `--worktree`) with the cap $0.05 over the last 24 hours, $7.61 | the plan call cost $0.29, and the build send stopped with `daily_budget_stop`: $7.85 against $7.61, clearing at 15:21 UTC. `run_caps` recorded 7.61. Discarded |
| 2026-10-31 | sssf-sandbox | daily case 3: dc3off (`adw_prompt`) with the setting off, the last 24 hours at $7.85 | success, $0.004, no `daily_budget_stop`, `run_caps` recorded the cap as off. The sandbox is clean at a2950ba, with the cap off |
| 2026-10-31 | hub | grilling session: a gauge of the last 24 hours, decisions 58 to 65 | settled |
| 2026-11-01 | hub | built decisions 58 to 64 | 34 server tests pass, 6 new: the window, the clear time, and a missing, commented-out, or broken `repo.yaml` |
| 2026-11-01 | sssf-sandbox | gauge case 2: the endpoint against `budget._daily_over`, at caps $5.00 and $1.50 | both $1.66; under $5.00 no clear time, over $1.50 both 02:20 UTC. The endpoint read each edit to `repo.yaml` without a restart. $0 |
| 2026-11-01 | sssf-sandbox | gauge case 3: Chromium screenshots of the card and the runs list, from a server on a registry holding only the sandbox | green at $5.00, amber at $2.00 (83%), red with "runs stop until 2026-11-01 02:20 UTC" at $1.50, and "no daily cap" with the setting off. Sandbox clean at cb5c479, cap off |
| 2026-11-01 | hub | grilling session: a gauge of what live runs spend together, decisions 66 to 72 | settled |
| 2026-11-01 | hub | built decisions 66 to 70 | 41 server tests pass, 7 new: a live run's current attempt only, a dead pid, another host's run by status, a row with no host, no processes table, the pid test, and the cap read |
| 2026-11-01 | sssf-sandbox | live case 1: lg2proof (`adw_plan_build`) under a $5.00 `max_live_cost`, read after the planner ended and before the builder did | the endpoint and `worktree.live_spend` both $0.1646052 for lg2proof; after the run, $0 and no runs. $0.18. lg1proof (`adw_build_test`, $0.017) had one agent, so it was never live with spend, and was discarded |
| 2026-11-01 | hub | live case 2: a made-up trace whose two runs were `sleep` processes, from a server on a registry holding only the sandbox and it | red at $3.75 of $3.50 with the stop note, "no live cap" with the cap off, amber at $1.35 of $1.60 (84%); killing one `sleep` dropped its run though its session read `running`. $0 |
| 2026-11-01 | sssf-sandbox | live case 3: Chromium screenshots of the sandbox's card and runs list during lg2proof | $0.16 of $5.00 across 1 live run, green, the run linked on the runs list and plain on the card |
| 2026-11-01 | hub | grilling session: one cap across every repo, decisions 73 to 82 | settled |
| 2026-11-01 | hub | built decisions 73 to 81 | 270 Python tests pass, 7 new: another registered repo's day stops a run, the launching repo counts unregistered, an unreadable repo counts nothing and is not created, off without the file, a launch refused, `run_caps`, and a broken file stops runs. 45 server tests, 4 new |
| 2026-11-01 | sssf-sandbox | machine case 1: mc1refus2 (`adw_prompt`) under a temporary `$SSSF_REGISTRY` holding the three repos and a made-up fourth with $1.40 in its trace, cap $1.50 | refused at launch, $1.62 across four repos, clears 2026-11-02 00:25 UTC, checked by hand against the oldest call. $0. An earlier try, mc1refus, ran on the sandbox's old code and succeeded, $0.008, before the sandbox was updated from the branch |
| 2026-11-01 | sssf-sandbox | machine case 2: mc2stop (`adw_plan_build`) with the cap at $1.70, just above the machine's total | the planner ran, $0.081; stopped before the builder at $1.70, `run_caps` recorded $1.70 |
| 2026-11-01 | sssf-sandbox | machine case 3: mc3off (`adw_prompt`) with no `limits.yaml` | success, $0.008, no stop, `run_caps` recorded the cap as off |
| 2026-11-01 | hub | machine case 4: `/api/machine/day` from a server on the temporary registry, against `budget._machine_over` | both $1.71 with the same shares and clear time; screenshots red at $1.70, amber at $2.00, green at $5.00, and the broken-file message |
| 2026-11-01 | hub | the open item "a pi run at the ChatGPT plan's limit" | designed and built in [pi-usage-stop.md](pi-usage-stop.md): the run stops by name at the 429, with nothing to read before it |

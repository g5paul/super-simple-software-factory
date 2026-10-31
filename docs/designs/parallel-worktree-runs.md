# Design: parallel worktree runs

Status: done. Settled in two grilling sessions on 2026-10-30, built, proven in sssf-sandbox (all seven cases of decision 12, and the three setup cases of decision 22), proven on a real worktree run in pi-agent-observability (70f9e62e), and rolled out to yt-summarizer and pi-agent-observability. What is still open is under "Open", below the decisions. Where the text and a decision disagree, the decision wins.

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

1. **Independent tasks at once.** Each run takes a different request. One task tried several ways, with the best one merged, is left room for, not built.
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

## Open

Known, and not built. None of them blocks a run.

- **Spend across runs.** Two runs can spend up to twice `max_run_cost`, and can go a little past `rate_limit_stop` (decision 6). That is documented, not guarded. A guard across a repo's runs would need shared accounting between processes.
- **One task, several attempts.** Decision 1 leaves room for running one request several ways and merging the best. Nothing builds it.
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

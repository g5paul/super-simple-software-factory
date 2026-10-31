# Design: parallel worktree runs

Status: settled in a grilling session on 2026-10-30. The decisions below are the record. Not built yet. Where the text and a decision disagree, the decision wins.

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
15. **Scope.** `adw_tickets` stays out, as it was for branch per run.

## Log

| Date | Repo | What | Result |
|---|---|---|---|
| 2026-10-30 | hub | grilling session, this design | settled |

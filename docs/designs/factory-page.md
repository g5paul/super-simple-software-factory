# Design: a factory page on the dashboard

Status: proposed 2026-11-08, decided 2026-11-11 in a grilling session with the engineer (20 questions, every recommended answer accepted). The open items are in [open.md](../open.md).

A page on the dashboard, started from the hub with `just factory`, that shows, across every repo SSSF is installed in, what today only the hub session sees after asking each project session and checking its claims: which skill each repo runs, whether its tree and checks are healthy, what its runs cost, and whether the machine itself is healthy.

## Why

On 2026-11-08, 360 relay processes leaked by four skill tests held about 9 GB of the machine's 15.9 GB before a project session noticed, a day and a half after the first. Every test passed. Nothing in SSSF looks at the host: `check_projects.py`, the quality runs and the trace all check git and run results. The symptoms were a slower machine and flaky tests, which were put down to load.

The same day, rolling one skill update out to five repos took a round of messages per repo, and each report was checked by hand against `check_projects.py` and the trace. The three hub scripts that answer "is everything current, and what does it cost" print to a terminal only when someone runs them.

## What the first release is

A **health board**: is anything wrong right now, across every registered repo and the machine. Red rows first, each saying what's wrong, the number that tripped it, and the next step, a command or which session to ask. The page only reads: no button changes anything. The tab title carries the red count. Nothing is pushed anywhere else, no desktop notification and no terminal message.

It shows only from the hub. `just factory`, a hub-only recipe in `local.just`, starts the skill's dashboard with `--factory <hub path>`. The factory view's code ships in the skill's `apps/visualizer` to every repo, and stays hidden unless the server gets that flag. Under it, the landing page (`#/`) is the board, and the existing repo list sits below it unchanged. Repo names and runs link into the existing sessions and trace views.

It stays local. The server binds `127.0.0.1` only. The page shows every repo by its real name, and a "listed only" switch hides the repos in the hub's `.git/sssf-private-names` before a screenshot or an export.

### Rows and colours

**Projects**, from `check_projects.py --json`, plus each repo's default `sandbox` from `repo.yaml` and, for `openshell`, whether its image is built.

- Red: the tree is dirty on its SSSF branch, the committed skill doesn't match the hub's at its stamp, or its last quality run failed.
- Amber: its skill is behind the hub's latest, or no quality run has passed since its last update. Behind is amber, since an update takes a round of messages and isn't urgent.

**Machine**, read by the server from files, with no provider token:

- Memory, from `/proc/meminfo`: amber under 3 GB free, red under 1.5 GB.
- Leftover test processes, from `/proc`: any process of the engineer's whose command line or working folder is under `$TMPDIR/pytest-of-$USER/` or `/tmp/pytest-of-$USER/` while no pytest process runs. Red at any. The page lists PID, age and command, and never kills them. This catches all 360 relays of 2026-11-08.
- Live VMs, from the traces: a `sandbox_start` with no `sandbox_end`. Amber when one has no running SSSF run. A VM a crash leaked before its start event is missed; see Deferred.
- agentsh's store and audit log, from `/var/lib/sssf-agentsh-prune/status.json`: amber at 15 GB together, red at 25 GB. The prune: amber when the last is over 3 hours old, red over 6.
- Polls and drops, from each trace's `agentsh_denials` events in the last day: amber at a slowest poll over 1 second or a dropped `ptrace_execve`, red at a poll over 3 seconds. Drops come from a structured `drops` field the guard adds (piece 3); a call from before the field shows "drops: not recorded".

**Runs**, in progress and the last 24 hours, from each trace and `phase_times.py --json`:

- Red: a run that failed or stopped (a refusal stop, a rule breach, a crash), a guard run with a blocking finding or a bypass, a phase past three times its usual top.
- Amber: a phase past twice its usual top, any rule refusal, a spec review that blocked, a run past twice its usual cost.

Guard finding counts come from each `bypass_review_N` phase's log event, refusals from `rule_refused` events, and a spec review's approval from its envelope. All are in the trace already.

### How it reads

The server runs the hub's scripts with a new `--json` flag and caches each result for 30 seconds, so each script stays the one source of its logic. It is the first time the server runs a command, and it does so only under `--factory`. Machine figures refresh every 10 seconds; projects and runs every 30, with a "refresh now" button that reruns the scripts. On 2026-11-11 `check_projects.py` took 0.16 s and `phase_times.py` 0.07 s.

### Build order

Each piece merges before the next.

1. **Done in 502e91d.** The dashboard binds `127.0.0.1`. On 2026-11-11 `Bun.serve` had no `hostname` (`server/index.ts`), so every repo's `just obs` and `obs-all` listened on every interface. By hand, with a test, shipped to every repo.
2. **Done.** `--json` for `check_projects.py` and `phase_times.py`, by hand. Hub-only. `check_projects.py --json` adds `stamped`, `behind` and `at_stamp` per repo, which the Projects row's amber and red need: the text checks compare only against the hub's latest skill.
3. A guard run that adds `drops: {type: count}` to the `agentsh_denials` event. Today the counts are only sentences in its notes, whose wording changed twice in one week.
4. The page itself, by an SSSF chain run on a hub branch (`adw_plan_build_test`) from a brief that points here. It is the hub's first non-guard feature run under agentsh, so it also tests Bun and Vite under the policy. The hub session steps in by hand only if the chain gets stuck.
5. This doc and the open items, updated as each piece lands.

### Done

Tests against seeded traces and a fake `/proc` for every colour rule above. Then one real check from the hub, read from the page's JSON API rather than a screenshot: every registered repo, the switch hiding the unlisted ones, the night's prune status, and the day's guard runs with their refusals.

## Deferred

Left out of the first release on purpose, to consider later:

- **A trends tab**: `levers.py`'s weekly rows, `features.py`'s cost per feature (4 seconds a run today, so it needs its own cache), and `phase_times.py`'s usual-times table, as against its slow list, which the board uses. They mean more after a few more weeks of data.
- **VM allowance use**, from the sums `server/sandbox.ts` already makes over every repo (`/api/machine/sandbox`). The board shows live VMs only.
- **The page in project repos.** That means moving the four hub scripts into the skill's `scripts/`, with the version comparison shown only where a hub checkout is found. Reopen if the engineer finds they want the board from a project's dashboard.
- **Live VMs from the providers' APIs**, which would catch a VM leaked before its `sandbox_start` event, at the price of a provider token on the page.
- **Alerts outside the page**: a desktop notification or a terminal line when a row turns red.
- **Actions**: a button that kills a leftover process, starts a prune, or archives a run. The board only reads.
- **Thresholds in config.** The first release keeps them as constants in the factory module, since only the hub runs it.

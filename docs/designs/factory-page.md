# Design: a factory page on the dashboard

Status: proposed 2026-11-08, not decided. The open item is in [open.md](../open.md).

A page in `just obs-all` that shows, across every repo SSSF is installed in, what today only the hub session sees after asking each project session and checking its claims: which skill each repo runs, whether its tree and checks are healthy, what its runs cost, and whether the machine itself is healthy.

## Why

On 2026-11-08, 360 relay processes leaked by four skill tests held about 9 GB of the machine's 15.9 GB before a project session noticed, a day and a half after the first. Every test passed. Nothing in SSSF looks at the host: `check_projects.py`, the quality runs and the trace all check git and run results. The symptoms were a slower machine and flaky tests, which were put down to load.

The same day, rolling one skill update out to five repos took a round of messages per repo, and each report was checked by hand against `check_projects.py` and the trace. The three hub scripts that answer "is everything current, and what does it cost" print to a terminal only when someone runs them.

## What it shows

`just obs-all` already reads `~/.config/sssf/projects.json`, the registry the three scripts read, so the page needs no new source of truth.

- **Per project**, from `adws/check_projects.py`: the skill version against the hub's latest, the branch, whether the tree is clean, and the last quality run's status. Add the default `sandbox` from `repo.yaml` and, for `openshell`, whether the image for the repo's current Python is built (`openshell_image.py --print` against `podman images`).
- **The host:** free memory, live VMs per provider, and any process whose command line names a pytest temp folder whose run has ended. The skill's `no_processes_left` check catches a leak in the run that made it; this catches what a crashed run left behind, which that check can't.
- **Levers and features**, from `adws/levers.py` and `adws/features.py`: the weekly rows and the cost per feature.

- **Phase times**, from `adws/phase_times.py` (2026-11-11): each chain's usual phase times and cost, and the phases, runs and costs in the last 14 days past twice their 90th percentile. Hub-only, like the three above.
- **VM allowances**, from `adw_sandbox_usage.py`: what VM runs used of Vercel's and E2B's free allowances. This one already ships in the skill, but reads one trace unless others are named, and an allowance is shared by every repo on the same team.
- **agentsh's machine state**, for repos on `local_guard: agentsh`: the prune's `/var/lib/sssf-agentsh-prune/status.json` (store and audit sizes against the 15 GB note, the last prune, the last skip and why), the notes `check_machine` gives, and each recent call's slowest poll and drop count from its `agentsh_denials` event. On 2026-11-11 a store at 32 GB stopped runs before any note showed it, and the store's growth that day was read by hand from the status file.
- **Run reports**, from each run's `context_handoff/`: a guard run's `guard_report.md` finding counts (bypasses, harms, false positives, broken promises, old holes), a spec review that blocked, and the rule refusals in the trace by rule and phase. The dashboard shows a run's phases and events today, but not what its reports found. On 2026-11-11 the refusals by shape, 11 in 8 guard runs, came from a query typed by hand.

Build the first two first. They are read-only and cheap, and they answer "is everything current and healthy" at a glance. The levers and features rows mean more after a few more weeks of data. Of the rows added since the proposal, agentsh's machine state and run reports come next: both answer questions the hub session asked by hand on 2026-11-11.

## To decide

1. **Where the code lives.** The four scripts (`check_projects.py`, `levers.py`, `features.py`, `phase_times.py`) are hub-only (`adws/`), and the dashboard ships in the skill to every repo. Recommended: move them into the skill's `scripts/`, and have the page compare skill versions only where it finds a hub checkout. The other way, a page that shows only from the hub, is simpler, but the hub's dashboard is the one least likely to be open.
2. **Privacy.** The page names every repo in the registry, unlisted ones included. It stays local: never served beyond `localhost`, never in a screenshot or log that goes anywhere public. `check_projects.py --listed` already filters to listed repos; the page needs the same switch for anything it exports.

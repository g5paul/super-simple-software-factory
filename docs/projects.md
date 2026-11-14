# Projects running SSSF

These are the projects, the repos this hub has set SSSF up in. Each project's own Claude session owns its repo. The hub sends updates and checks reports, and changes nothing there. An open item on this page is for the engineer to decide with that project's session. It isn't an SSSF change.

In the Skill column, the first commit is the skill commit the project is on, and the commit in parentheses is the project's own commit that updated to it.

| Project | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| pi-agent-observability | `main` | 4d14947 (241fb1c). Has the operator container, with bun added for its `quality.py`. Its pi roster ran there with obs on, in run a97a2d4b. In a VM, its obs events reach the engineer's obs through the relay, as in run 0939000a on Vercel | Leave the obs server alone. The rule is a plain string. Since 2fb103c, `just obs`, `just all` and `scripts/smoke-server.sh` refuse when `SSSF_ADW_ID` is set |
| fusion-harness | `sssf-run`. `main` feeds the patches and stays clean | 4d14947 (53d402d) | Never run the stack recipes or start `pi`, and never read `.env`. Two things enforce the recipe rule: `forbid`, and an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack. Since 41c3432, a second `forbid` rule with `on_match: refuse` refuses `pi` run as a command, by name, path, npx or bunx, or inside `bash -c`, and allows reads under `.pi/`. It misses pi started from a script, `xargs pi` and `eval "pi"` |
| sssf-sandbox | `agentsh-rollout`, from `master`. `master` is on 1908177 (eb17851) | 1058fcd (03b8bbc). Its default roster, `main`, is the Claude Code starter. Five more rosters sit beside it in `adws/adw_sssf_config/`: `fable` (3049796) is `main` with Fable 5.2 at high as planner and reviewers; `fable-medium` (3eac28e) is `fable` at medium; `chatgpt` (d154852) is the ChatGPT starter on pi; `vmext` (ed99569) and `obsrelay` (cd483e2) are `chatgpt` plus a proof extension, for remote-sandboxes decisions 27 and 49. `local_guard: agentsh` with the policy `sssf-sssf-sandbox-claude`, installed from the hub's install clone. Has the operator container. Its default has been `sandbox: none` since c3e1c93 turned agentsh on, because agentsh needs it. From 7c292ee until then it was `openshell`. OpenShell proved itself there with pi in d1c04d1f and with Claude Code in 57ed2b49 | The hub's own test bed. A `forbid` rule with `on_match: refuse` on `SSSF-REFUSE-PROBE`, the refusal test |
| agentsh | `main`, local only: `sssf/links-listing` (05b3777f) plus the install. Pushes of `main` go to the remote `no-push` (decision 27 in [upstream-patches.md](designs/upstream-patches.md)); exports go to `origin` | 1ea669c (b165f676). Claude roster, `local_guard: text`, `max_worktree_runs: 1`. `quality.py` and `export_checks` run the `CGO_ENABLED=0` Go build, tests and `go vet` (84e0c440). The tests skip two packages, and eleven tests that fail here whatever the code does, ten in `internal/api` (back since f1c6ae90) and one in the postgres proxy package (back since a973718c, run on its own after the parallel suite since 10ca9dcd, because its tests race under full-suite load); `internal/ocsf` is back since 77d3a9e0. `agent_rules` tells agents where Go is (6b95be7f). Commits use the noreply email. Scout run 9ab6e3ba passed. Chain run cda7b802 fixed the `ocsf` test (merged as d5e14cb1), c948cb31 fixed `go vet` (merged as 62128cc0, exported to `export/c948cb31`), and 3c724f0e made bind follow the network policy (merged as 872fabd0, exported `--onto sssf/links-listing` to `export/3c724f0e`; the binary isn't installed). Nothing pushed | No agent rules |
| sssf-export-fork | `main`, a clone of the bare `~/github/sssf-export-upstream.git`, its only remote, `upstream` | 180ded1 (750b0c4). A tiny Python package on a Claude roster with every agent on Haiku. `local_guard: text`. Proves `just export` with a real chain, as [upstream-patches.md](designs/upstream-patches.md) decision 22 says | The export's test bed. No agent rules |
| super-simple-software-factory | `main` | c9bbdf2 (9f8b1b5, stamp fixed in the next commit). 9f8b1b5 ran `install.py --update` from the hub's own checkout, not `just hub-update`, so the stamp kept `skill: 1ea669c`. `just hub-update` from the install clone `~/github/sssf-install` then changed only the stamp, so the files match the clone's. From 2026-11-13 to 2026-11-20 13:00 UTC its planner and reviewers run on Fable 5.2 (efc25f9), a week's test of what Fable does to usage. `local_guard: agentsh` with the policy `sssf-super-simple-software-factory-claude` since dd07d8f | The hub itself, for the guard-change chain. A `forbid` rule with `on_match: refuse` refuses `git push` and the `gh` commands that write to GitHub |

## Unlisted projects

This page lists only the projects the engineer chose to name in public. Unlisted is the default for a new install, for any repo the engineer would rather not have named, whether it's public or private on GitHub.

- **Records.** A separate private repo, `sssf-private`, records an unlisted project's install. This repo never does.
- **Names.** In this repo's files and commit messages, an unlisted project is "an unlisted project". That covers the fixes it turns up. Their tests and comments describe the bug without naming the project.
- **The push check.** Each unlisted name goes in the hub clone's `.git/sssf-private-names`. The pre-push hook refuses a push whose messages or added lines name one ([secret-scan.md](secret-scan.md)).
- **VMs.** An install that's private on GitHub also keeps `sandbox: none`, so its code goes to a remote VM only when the engineer asks.

## Open SSSF issues by project

Which of [open.md](open.md#sssf)'s SSSF rows reach a project depends on its setup: the skill commit, the roster, the guard and the sandbox. The first table maps each setup to the rows it brings in, and the second gives each project's setup. When a row opens or closes in open.md, change it here too. None of these rows stops a project's runs today.

| Setup | Open rows it brings in |
|---|---|
| Every install | Three gaps in the after-call check: gitignored files, `git replace`, and a background write that lands after it. An agent may write any run's worktree. An agent's command can write to its harness's stdout. The push pattern backtracks exponentially |
| Skill before bed6436 | A resumed run commits the failed attempt's leftover code with the new spec. Fixed in bed6436 |
| Skill before c9bbdf2 | In a `just trial` attempt, agents get the trial's Python as `python3`. Fixed in c9bbdf2 |
| Claude roster | A command that prints SSSF's refusal and exits 126 counts as a real refusal |
| pi roster | Regex differences between Python and pi's extension that the load check can't reject |
| `local_guard: agentsh` | Loopback connections are denied, `timeout` leaves the wrapped processes running, bind gets past the policy (fixed in the fork, not installed), dropped events, a leftover session stops the prune, the tracer stall, and npm's warnings about pnpm settings. Installing a new agentsh binary before the hub changes its pin would stop every run here |
| `sandbox: openshell` | An agent can overwrite the VM's refuse script. Two setup gaps: `just openshell-setup`, and the advice when `openshell` is off `PATH`. Two rows on how SSSF picks the VM's Python. Claude Code in the image is unpinned |
| The hub only | The builder may edit the guard code SSSF ships. Three skill tests flaked |

| Project | Skill | Roster | Guard | Sandbox | Rows that reach it |
|---|---|---|---|---|---|
| pi-agent-observability | 4d14947 | pi | text | none | Every install, pi roster |
| fusion-harness | 4d14947 | Claude | text | none | Every install, Claude roster |
| sssf-sandbox | 1058fcd | Claude, and pi and Fable in trials | agentsh | none | Every install, Claude roster, pi roster, `local_guard: agentsh` |
| agentsh | 1ea669c | Claude | text | none | Every install, skill before bed6436, skill before c9bbdf2, Claude roster |
| sssf-export-fork | 180ded1 | Claude | text | none | Every install, skill before bed6436, skill before c9bbdf2, Claude roster |
| super-simple-software-factory | c9bbdf2 | Claude, Fable planner and reviewers to 2026-11-20 | agentsh | none | Every install, Claude roster, `local_guard: agentsh`, the hub only |

The unlisted projects' setups are in the private record.

## Open items

[open.md](open.md#projects) lists a listed project's open items.

## Pre-push secret scan

Each project has the hub's gitleaks hook in `.githooks/pre-push` ([secret-scan.md](secret-scan.md)). Each clone also needs `git config core.hooksPath .githooks`. Only the engineer can set it, because the project sessions' permission checks refuse it.

pi-agent-observability and fusion-harness each added the hook by hand before the installer shipped it, and set `core.hooksPath` in their clones. On its update to skill 96f1130, pi-agent-observability took the template's hook.

| Project | Hook commit | Switched on |
|---|---|---|
| pi-agent-observability | d5d958a on `main`. The skill's template since a6f1e3f | yes |
| fusion-harness | 54ac5d8 on `main`, merged into `sssf-run` as beaca9b. It kept its own hook. `sssf-run`'s copy gained the agent refusal in 1cc5d81 and the template's wording in 97385d6, and `main`'s copy didn't | yes |
| sssf-sandbox | dfc09a0, stamped by the installer. It has no remote | no |
| agentsh | b165f676, stamped by the installer | no |
| sssf-export-fork | 750b0c4, stamped by the installer. Its only remote is the local bare upstream | no |

## Checks on the next real runs

The hub makes these checks read-only, from the trace and the raw logs, as each run happens.

- **The first hub run on Fable, after efc25f9.** The planner and reviewers should show `claude-fable-5-2` in `agent_sessions`, and their calls `--effort high`. No call should stop at its doubled cap, and the run's cost should include the Fable calls, which the hub's price table has had since 9f8b1b5. Look at `rate_limits.json` for a window besides `seven_day`, such as a separate one for Fable's cap of half the weekly limit.
- **The Fable week, on 2026-11-20 13:00 UTC.** Compare its peak reading and its spend by model and seat with the week before, 41% and $946 at list price. The method and the expected 54% to 75% are in the private record.

- **The output count, on the next Claude Code run in a project.** It's the first such run since 102217c. Each agent's `raw_output.jsonl` should hold one `message_delta` per message and no text deltas. Each finished call, priced from the raw log the way a killed call is priced, should match its `total_cost_usd`. sssf-sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** It can happen in fusion-harness, whose recipe rule refuses on Claude Code and pi agents alike. It can also happen in pi-agent-observability, if its engineer adopts a refuse rule. Three things should hold:
  - The trace shows a `rule_refused` event.
  - The call's result starts "SSSF refused this command".
  - Nothing the command named has run.

  sssf-sandbox runs 6f9929be and f893de05 showed the event and the result on pi. On Claude Code, sssf-sandbox runs 2c3b149b on the host and 12c1f748 in a Vercel VM did too, with the result starting `Exit code 126` first. In 12c1f748, the planner's own Bash calls ran through the same prefix as normal.

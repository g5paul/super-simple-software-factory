# Projects running SSSF

These are the projects, the repos this hub has set SSSF up in. Each project's own Claude session owns its repo. The hub sends updates and checks reports, and changes nothing there. An open item on this page is for the engineer to decide with that project's session. It isn't an SSSF change.

In the Skill column, the first commit is the skill commit the project is on, and the commit in parentheses is the project's own commit that updated to it.

| Project | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| pi-agent-observability | `main` | 4d7e6bc (aec6772). Has the operator container, with bun added for its `quality.py`. Its pi roster ran there with obs on, in run a97a2d4b. In a VM, its obs events reach the engineer's obs through the relay, as in run 0939000a on Vercel | Leave the obs server alone. The rule is a plain string. Since 2fb103c, `just obs`, `just all` and `scripts/smoke-server.sh` refuse when `SSSF_ADW_ID` is set |
| fusion-harness | `sssf-run`. `main` feeds the patches and stays clean | 4d7e6bc (63977a8) | Never run the stack recipes or start `pi`, and never read `.env`. Two things enforce the recipe rule: `forbid`, and an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack. Since 41c3432, a second `forbid` rule with `on_match: refuse` refuses `pi` run as a command, by name, path, npx or bunx, or inside `bash -c`, and allows reads under `.pi/`. It misses pi started from a script, `xargs pi` and `eval "pi"` |
| sssf-sandbox | `agentsh-rollout`, from `master`. `master` is on 4d7e6bc (4189260) | 45e6cc8 (05f8f3c). `local_guard: agentsh` with the policy `sssf-sssf-sandbox-claude`, installed from the hub's install clone. Has the operator container, and `sandbox: openshell` as its default since 7c292ee. OpenShell proved itself there with pi in d1c04d1f and with Claude Code in 57ed2b49 | The hub's own test bed. A `forbid` rule with `on_match: refuse` on `SSSF-REFUSE-PROBE`, the refusal test |
| super-simple-software-factory | `main` | 45e6cc8 (f0a3ae5), from the install clone `~/github/sssf-install`, as [sssf.md](sssf.md#the-install-clone) describes. `local_guard: agentsh` with the policy `sssf-super-simple-software-factory-claude` since dd07d8f | The hub itself, for the guard-change chain. A `forbid` rule with `on_match: refuse` refuses `git push` and the `gh` commands that write to GitHub |

## Unlisted projects

This page lists only the projects the engineer chose to name in public. Unlisted is the default for a new install, for any repo the engineer would rather not have named, whether it's public or private on GitHub.

- **Records.** A separate private repo, `sssf-private`, records an unlisted project's install. This repo never does.
- **Names.** In this repo's files and commit messages, an unlisted project is "an unlisted project". That covers the fixes it turns up. Their tests and comments describe the bug without naming the project.
- **The push check.** Each unlisted name goes in the hub clone's `.git/sssf-private-names`. The pre-push hook refuses a push whose messages or added lines name one ([secret-scan.md](secret-scan.md)).
- **VMs.** An install that's private on GitHub also keeps `sandbox: none`, so its code goes to a remote VM only when the engineer asks.

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

## Checks on the next real runs

The hub makes these checks read-only, from the trace and the raw logs, as each run happens.

- **The output count, on the next Claude Code run in a project.** It's the first such run since 102217c. Each agent's `raw_output.jsonl` should hold one `message_delta` per message and no text deltas. Each finished call, priced from the raw log the way a killed call is priced, should match its `total_cost_usd`. sssf-sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** It can happen in fusion-harness, whose recipe rule refuses on Claude Code and pi agents alike. It can also happen in pi-agent-observability, if its engineer adopts a refuse rule. Three things should hold:
  - The trace shows a `rule_refused` event.
  - The call's result starts "SSSF refused this command".
  - Nothing the command named has run.

  sssf-sandbox runs 6f9929be and f893de05 showed the event and the result on pi. On Claude Code, sssf-sandbox runs 2c3b149b on the host and 12c1f748 in a Vercel VM did too, with the result starting `Exit code 126` first. In 12c1f748, the planner's own Bash calls ran through the same prefix as normal.

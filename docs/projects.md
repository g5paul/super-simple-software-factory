# Projects running SSSF

The repos this hub has set SSSF up in. Each one's own Claude session owns its repo: the hub sends updates and checks reports, and changes nothing there. An open item here is for the engineer to decide with that repo's session, not an SSSF change.

| Repo | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| pi-agent-observability | `main` | 33a1ed2 (f5c4b44). Has the operator container, with bun added for its `quality.py`; its pi roster ran there with obs on, in run a97a2d4b. In a VM its obs events reach the engineer's obs through the relay, as in run 0939000a on Vercel | Leave the obs server alone. The rule is a plain string; since 2fb103c, `just obs`, `just all` and `scripts/smoke-server.sh` refuse when `SSSF_ADW_ID` is set |
| fusion-harness | `sssf-run` (`main` feeds the patches and stays clean) | 33a1ed2 (7f239be) | Never run the stack recipes or start `pi`, never read `.env`. The recipe rule is enforced by `forbid` and by an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack. Since 41c3432 a second `forbid` rule with `on_match: refuse` refuses `pi` run as a command, by name, path, npx or bunx, or inside `bash -c`, and allows reads under `.pi/`. It misses pi started from a script, `xargs pi` and `eval "pi"` |
| sssf-sandbox | `master` | 33a1ed2 (580d568). Has the operator container | The hub's own test bed. None |
| super-simple-software-factory | `main` | Stamped from the skill on `main`, see `docs/sssf.md` | The hub itself, for the guard-change chain. A `forbid` rule with `on_match: refuse` refuses `git push` and the `gh` commands that write to GitHub |

## Unlisted projects

This page lists only the repos the engineer chose to name in public. Unlisted is the default for a new install, for any repo the engineer would rather not have named, public or private on GitHub. An unlisted repo's install is recorded in a separate private repo, `sssf-private`, never here. In this repo's files and commit messages it's "an unlisted project", fixes it turns up included: their tests and comments describe the bug without naming it. Each unlisted name goes in the hub clone's `.git/sssf-private-names`, and the pre-push hook refuses a push whose messages or added lines name one ([secret-scan.md](secret-scan.md)). An install that's private on GitHub also keeps `sandbox: none`, so its code goes to a remote VM only when the engineer asks.

## Open items

A listed project's open items are in [open.md](open.md#projects).

## If the hub session goes to the background

On 2026-11-02 the hub session became a background job without the engineer meaning it to. The next day, `claude --continue` refused to open it ("Your most recent conversation moved to a background session ... registered from another machine or container"), and `claude --resume 8236c0c7-361e-485d-966a-a6f30eaa813f` brought it back. The likely cause is pressing the left arrow on an empty prompt, for example twice after clearing the line with Backspace: Claude Code's agent view takes that as "background this session". Nothing records which key it was.

- **Getting it back:** `claude --resume <session id>`, `claude attach <id>`, or `claude agents` and Enter on its row. Don't use `--fork-session`: it makes a second hub session with the same history, two writers in one repo.
- **Side effect:** the failed `--continue` left a stale socket in `/run/user/1000/cc-socks/` with no process behind it, and the restarted session registered as "super-simple-software-factory (2)". The engineer removed the socket. Replies from project sessions still arrived.
- **If it happens again:** turn off `leftArrowOpensAgents` in `/config`. The engineer left it on for now, on 2026-11-03. It's stored in `~/.claude.json`, so change it through `/config`, not by hand.

## Pre-push secret scan

Each repo has the hub's gitleaks hook in `.githooks/pre-push` ([secret-scan.md](secret-scan.md)), and each clone needs `git config core.hooksPath .githooks`, which only the engineer can set: the project sessions' permission checks refuse it.

| Repo | Hook commit | Switched on |
|---|---|---|
| pi-agent-observability | d5d958a on `main`; the skill's template since a6f1e3f | yes |
| fusion-harness | 54ac5d8 on `main`, merged into `sssf-run` as beaca9b. Its own, kept: `sssf-run`'s copy gained the agent refusal in 1cc5d81 and the template's wording in 97385d6, `main`'s didn't | yes |
| sssf-sandbox | dfc09a0, stamped by the installer; it has no remote | no |

## Checks on the next real runs

The hub checks these read-only, from the trace and the raw logs, when each run happens.

- **The next Claude Code run in a project: the output count.** The first since 102217c. Each agent's `raw_output.jsonl` should hold a `message_delta` per message and no text deltas. Priced from the raw log the way a killed call is priced, each finished call should match its `total_cost_usd`. Sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** That can happen in fusion-harness, whose recipe rule refuses on Claude Code and pi agents alike, or in pi-agent-observability, if its engineer adopts a refuse rule. The trace should show a `rule_refused` event, the call's result should start "SSSF refused this command", and nothing it named should have run. Sandbox runs 6f9929be and f893de05 showed both on pi. On Claude Code, 2c3b149b on this machine and 12c1f748 in a Vercel VM did too, the result starting `Exit code 126` first; in 12c1f748 the planner's own Bash calls ran through the same prefix as normal.

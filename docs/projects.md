# Projects running SSSF

The repos this hub has set SSSF up in. Each one's own Claude session owns its repo: the hub sends updates and checks reports, and changes nothing there. An open item here is for the engineer to decide with that repo's session, not an SSSF change.

| Repo | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| yt-summarizer | `sssf-run` (`main` follows it on the engineer's word; at f911645, two commits behind) | 47b7621 (e9cf348) | No push and no GitHub writes. A `forbid` rule with `on_match: refuse` since cd9d356, and the tracked `pre-push` hook, which refuses when `SSSF_ADW_ID` is set |
| pi-agent-observability | `main` | 47b7621 (6a19381). Its roster loads the obs extension, so `--sandbox` still refuses it | Leave the obs server alone. The rule is a plain string; since 2fb103c, `just obs`, `just all` and `scripts/smoke-server.sh` refuse when `SSSF_ADW_ID` is set |
| fusion-harness | `sssf-run` (`main` feeds the patches and stays clean) | 47b7621 (97385d6) | Never run the stack recipes or start `pi`, never read `.env`. The recipe rule is enforced by `forbid` and by an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack. Since 41c3432 a second `forbid` rule with `on_match: refuse` refuses `pi` run as a command, by name, path, npx or bunx, or inside `bash -c`, and allows reads under `.pi/`. It misses pi started from a script, `xargs pi` and `eval "pi"` |
| sssf-sandbox | `master` | 47b7621 (aa70c9b) | The hub's own test bed. None |

## Open items

None as of 2026-11-03.

## If the hub session goes to the background

On 2026-11-02 the hub session became a background job without the engineer meaning it to. The next day, `claude --continue` refused to open it ("Your most recent conversation moved to a background session ... registered from another machine or container"), and `claude --resume 8236c0c7-361e-485d-966a-a6f30eaa813f` brought it back. The likely cause is pressing the left arrow on an empty prompt, for example twice after clearing the line with Backspace: Claude Code's agent view takes that as "background this session". Nothing records which key it was.

- **Getting it back:** `claude --resume <session id>`, `claude attach <id>`, or `claude agents` and Enter on its row. Don't use `--fork-session`: it makes a second hub session with the same history, two writers in one repo.
- **Side effect:** the failed `--continue` left a stale socket in `/run/user/1000/cc-socks/` with no process behind it, and the restarted session registered as "super-simple-software-factory (2)". The engineer removed the socket. Replies from project sessions still arrived.
- **If it happens again:** turn off `leftArrowOpensAgents` in `/config`. The engineer left it on for now, on 2026-11-03. It's stored in `~/.claude.json`, so change it through `/config`, not by hand.

## Pre-push secret scan

Each repo has the hub's gitleaks hook in `.githooks/pre-push` ([secret-scan.md](secret-scan.md)), and each clone needs `git config core.hooksPath .githooks`, which only the engineer can set: the project sessions' permission checks refuse it.

| Repo | Hook commit | Switched on |
|---|---|---|
| yt-summarizer | f911645, on `main` too; the skill's template since dec0d13 | yes |
| pi-agent-observability | d5d958a on `main`; the skill's template since a6f1e3f | yes |
| fusion-harness | 54ac5d8 on `main`, merged into `sssf-run` as beaca9b. Its own, kept: `sssf-run`'s copy gained the agent refusal in 1cc5d81 and the template's wording in 97385d6, `main`'s didn't | yes |
| sssf-sandbox | dfc09a0, stamped by the installer; it has no remote | no |

## Checks on the next real runs

The hub checks these read-only, from the trace and the raw logs, when each run happens.

- **yt-summarizer's next Claude Code run: the output count.** It's the first project run since 102217c. Each agent's `raw_output.jsonl` should hold a `message_delta` per message and no text deltas. Priced from the raw log the way a killed call is priced, each finished call should match its `total_cost_usd`. Sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** That can happen in fusion-harness, whose recipe rule refuses on Claude Code and pi agents alike, or in pi-agent-observability, if its engineer adopts a refuse rule. The trace should show a `rule_refused` event, the call's result should start "SSSF refused this command", and nothing it named should have run. Sandbox runs 6f9929be and f893de05 showed both on pi. On Claude Code, 2c3b149b on this machine and 12c1f748 in a Vercel VM did too, the result starting `Exit code 126` first; in 12c1f748 the planner's own Bash calls ran through the same prefix as normal.

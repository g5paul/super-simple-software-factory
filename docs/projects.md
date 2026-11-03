# Projects running SSSF

The repos this hub has set SSSF up in. Each one's own Claude session owns its repo: the hub sends updates and checks reports, and changes nothing there. An open item here is for the engineer to decide with that repo's session, not an SSSF change.

| Repo | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| yt-summarizer | `sssf-run` (`main` follows it on the engineer's word; at a97f7dd, one commit behind, without the pre-push hook) | 647c7a5 (a97f7dd) | No push and no GitHub writes. A `forbid` rule with `on_match: refuse` since cd9d356, and an untracked `pre-push` hook that refuses when `SSSF_ADW_ID` is set |
| pi-agent-observability | `main` | 673a70d (6a46888), one skill commit behind: 647c7a5 changed only the cookbooks. Its roster loads the obs extension, so `--sandbox` still refuses it | Leave the obs server alone. The rule is a plain string; since 2fb103c, `just obs`, `just all` and `scripts/smoke-server.sh` refuse when `SSSF_ADW_ID` is set |
| fusion-harness | `sssf-run` (`main` feeds the patches and stays clean) | 647c7a5 (ae49dae) | Never run the stack recipes or start `pi`, never read `.env`. The recipe rule is enforced by `forbid` and by an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack. Since 41c3432 a second `forbid` rule with `on_match: refuse` refuses `pi` run as a command, by name, path, npx or bunx, or inside `bash -c`, and allows reads under `.pi/`. It misses pi started from a script, `xargs pi` and `eval "pi"` |
| sssf-sandbox | `master` | fd5a964 | The hub's own test bed. None |

## Open items

None as of 2026-11-03.

## Pre-push secret scan

Each repo has the hub's gitleaks hook in `.githooks/pre-push` ([secret-scan.md](secret-scan.md)), and each clone needs `git config core.hooksPath .githooks`, which only the engineer can set: the project sessions' permission checks refuse it.

| Repo | Hook commit | Switched on |
|---|---|---|
| yt-summarizer | f911645 on `sssf-run`; also refuses pushes from SSSF agents | yes |
| pi-agent-observability | d5d958a on `main` | yes |
| fusion-harness | 54ac5d8 on `main`, merged into `sssf-run` as beaca9b | yes |
| sssf-sandbox | none: it has no remote | n/a |

## Checks on the next real runs

The hub checks these read-only, from the trace and the raw logs, when each run happens.

- **yt-summarizer's next Claude Code run: the output count.** It's the first project run since 102217c. Each agent's `raw_output.jsonl` should hold a `message_delta` per message and no text deltas. Priced from the raw log the way a killed call is priced, each finished call should match its `total_cost_usd`. Sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** That can happen in fusion-harness, whose recipe rule refuses on Claude Code and pi agents alike, or in pi-agent-observability, if its engineer adopts a refuse rule. The trace should show a `rule_refused` event, the call's result should start "SSSF refused this command", and nothing it named should have run. Sandbox runs 6f9929be and f893de05 showed both on pi. On Claude Code, 2c3b149b on this machine and 12c1f748 in a Vercel VM did too, the result starting `Exit code 126` first; in 12c1f748 the planner's own Bash calls ran through the same prefix as normal.

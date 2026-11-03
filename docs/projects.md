# Projects running SSSF

The repos this hub has set SSSF up in. Each one's own Claude session owns its repo: the hub sends updates and checks reports, and changes nothing there. An open item here is for the engineer to decide with that repo's session, not an SSSF change.

| Repo | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| yt-summarizer | `sssf-run` (`main` follows it, one update behind) | ed9be4a | No push. Enforced by a `forbid` rule, and by an untracked `pre-push` hook that refuses when `SSSF_ADW_ID` is set |
| pi-agent-observability | `main` | ed9be4a | Leave the obs server alone. A plain string: it asks, nothing enforces it |
| fusion-harness | `sssf-run` (`main` feeds the patches and stays clean) | ed9be4a | Never run the stack recipes or start `pi`, never read `.env`. The recipe rule is enforced by `forbid` and by an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack |
| sssf-sandbox | `master` | ed9be4a | The hub's own test bed. None |

## Open items

- **pi-agent-observability: the obs rule only asks.** Its session offered `SSSF_ADW_ID` guards in `just obs`, `just all` and `scripts/smoke-server.sh`. Those would refuse before the server starts, without the false matches a `forbid` regex has: `\bjust\s+obs\b` also stops `grep "just obs" README.md`. Since 7f8724c, a `forbid` rule with `on_match: refuse` changes that trade-off here, because this roster is all pi: a false match is refused and the run goes on, instead of killing it. Its session has told the engineer. It's waiting on the engineer.
- **fusion-harness: nothing stops a direct `pi` call.** The rule says never start `pi`, but the `forbid` pattern and the guards cover only the recipes. A plain `\bpi\b` pattern also matches `.pi/fusion-harness/...` paths, so an agent that only reads the stack YAMLs would be killed. A narrow pattern for `pi` as a command needs testing against the commands agents really run. `on_match: refuse` wouldn't soften a false match here: the roster is all Claude Code, where a refuse rule still stops the run. Its session has asked the engineer about switching the recipe rule anyway.

## Checks on the next real runs

The hub checks these read-only, from the trace and the raw logs, when each run happens.

- **yt-summarizer's next Claude Code run: the output count.** It's the first project run since 102217c. Each agent's `raw_output.jsonl` should hold a `message_delta` per message and no text deltas. Priced from the raw log the way a killed call is priced, each finished call should match its `total_cost_usd`. Sandbox runs 28f436d5 and b2367eda matched to the cent.
- **The first refusal in a project.** That can happen in fusion-harness, whose recipe rule refuses on its ChatGPT roster's pi agents, or in pi-agent-observability, if its engineer adopts a refuse rule. The trace should show a `rule_refused` event, the call's result should start "SSSF refused this command", and nothing it named should have run. Sandbox runs 6f9929be and f893de05 showed both.

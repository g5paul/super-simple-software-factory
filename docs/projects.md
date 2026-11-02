# Projects running SSSF

The repos this hub has set SSSF up in. Each one's own Claude session owns its repo: the hub sends updates and checks reports, and changes nothing there. An open item here is for the engineer to decide with that repo's session, not an SSSF change.

| Repo | SSSF branch | Skill | Agent rules |
|---|---|---|---|
| yt-summarizer | `sssf-run` (`main` follows it) | c76b079 | No push. Enforced by a `forbid` rule, and by an untracked `pre-push` hook that refuses when `SSSF_ADW_ID` is set |
| pi-agent-observability | `main` | c76b079 | Leave the obs server alone. A plain string: it asks, nothing enforces it |
| fusion-harness | `sssf-run` (`main` feeds the patches and stays clean) | c76b079 | Never run the stack recipes or start `pi`, never read `.env`. The recipe rule is enforced by `forbid` and by an `SSSF_ADW_ID` guard in fh-workhorse, fh-sota and fh-stack |
| sssf-sandbox | `master` | 56b73ea | The hub's own test bed. None |

## Open items

- **pi-agent-observability: the obs rule only asks.** Its session offered `SSSF_ADW_ID` guards in `just obs`, `just all` and `scripts/smoke-server.sh`. Those would refuse before the server starts, without the false matches a `forbid` regex has: `\bjust\s+obs\b` also stops `grep "just obs" README.md`. It's waiting on the engineer.
- **fusion-harness: nothing stops a direct `pi` call.** The rule says never start `pi`, but the `forbid` pattern and the guards cover only the recipes. A plain `\bpi\b` pattern also matches `.pi/fusion-harness/...` paths, so an agent that only reads the stack YAMLs would be killed. A narrow pattern for `pi` as a command needs testing against the commands agents really run.

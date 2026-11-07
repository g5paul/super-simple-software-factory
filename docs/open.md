# Open items

This is the hub's one list of open SSSF work. Each design keeps its reasons and decisions; the to-do lives here, with a link back. None of these blocks a run.

To add an item, add a row and link the doc that explains it. When an item closes, delete its row: the commit says what closed it, and the design doc records the outcome where it matters. Items about an unlisted project are kept in the private list, never here.

| Kind | Means |
|---|---|
| build | known work, not built yet |
| watch | waits on something a real run will show |
| need | waits until a repo needs it |
| decide | waits on the engineer |

## SSSF

| Item | Kind | Since | Background |
|---|---|---|---|
| `worktree_setup` runs every command for every run, even one that touches a single package. Running less needs the run's scope before its first agent, and only the planner knows it, later. It costs about a second today. Reopen if a repo's setup grows slow; the simplest fix is the engineer naming the packages at launch | build | 2026-10-30 | [parallel-worktree-runs.md](designs/parallel-worktree-runs.md#decisions), decision 16 |
| Python/JavaScript regex differences the load check can't reject, listed in config.md: `\w`, `\d` and `\b` are Unicode in Python and ASCII in pi's extension; `\s` and `\S` split U+FEFF, `\x1c` to `\x1f` and `\x85` differently, so `git -c user.name=\ufeffx push` gets past the starter's push rule on pi; JavaScript's `.` stops at `\r`, U+2028 and U+2029; a quantifier after a character above U+FFFF binds to half of it in JavaScript; and a quantified lookbehind such as `(?<=g)*` loads but doesn't compile there. Guard run 54540e3d's review found the last four. A command that only matches through one of them isn't refused on pi, and `; echo "SSSF refused this command"` then hides it from the stream check. Fix candidates: compile with the `u` flag plus a matching load check, or have `rules.refused` check that the extension really refused that call | build | 2026-11-06 | [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules) |
| `tests/test_rules.py::test_a_forbidden_command_kills_a_pi_agent_and_what_it_started` flakes: 2 of about 22 runs failed with a `ProcessLookupError` from pathlib, likely a race between killing the stand-in pi process and reading its child's pid. It passes on rerun. Reported by a project session on skill 6811e17 | build | 2026-11-06 | [test_rules.py](../.claude/skills/sssf/tests/test_rules.py) |
| Two leftovers from the 2026-11-06 docs audit. The starter configs' `poll_ms` comment still calls it the visualizer's poll cadence, though nothing reads it. And `references/config.md`, `handoff.md` and `observability.md` keep older em dashes and title-case H1s. Changing the comment flags `sssf.config.yaml` for review in every repo, so do it with a change that touches that file anyway | build | 2026-11-06 | [config.md](../.claude/skills/sssf/references/config.md) |
| `tests/test_obs_relay.py::test_the_relay_refuses_bad_json_and_oversized_bodies` flaked once in a full skill run on 2026-11-07, and passed 3 of 3 on its own. Likely timing under load. Look at it if it fails again | build | 2026-11-07 | [test_obs_relay.py](../.claude/skills/sssf/tests/test_obs_relay.py) |
| A narrower VM network: a domain allowlist, and the provider's proxy adding the token instead of SSSF passing it in. Matters only for untrusted code | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md) |
| Rebase checks off the host. Today `just rebase` runs its setup and checks on the host | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#decisions), decision 11 |
| Whether to add an explicit feature label at launch, stored on the run and defaulting to the spec's slug or a ticket id, so a relaunched run's cost reaches the feature it was retried for. It changes the trace's schema in the skill, so every repo updates. The first `features.py` report leans yes: all three of the hub's gone runs were relaunches. Three runs is thin, so decide after a few more weekly reports | decide | 2026-11-06 | [thinking-in-threads.md](research/thinking-in-threads.md#as-built-1) |
| A trial judge that picks the winner on its own, once enough scorecards show whether its picks match the engineer's | need | 2026-11-01 | [trials.md](designs/trials.md) |
| A second bind-mount sandbox kind, such as Docker beside Podman. The isolated VM kind is built. Nothing asks for another container kind yet | need | 2026-10-31 | [container-operator.md](designs/container-operator.md#what-a-second-sandbox-means) |
| Whether a real ChatGPT 429 reads the way pi's source says. The first one gets a log row | watch | 2026-11-01 | [pi-usage-stop.md](designs/pi-usage-stop.md) |
| Whether `/tmp` lasts across a Vercel VM refresh. The obs relay reinstalls its script on each call, so a loss should cost nothing; no proof run has lasted long enough to refresh | watch | 2026-11-05 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-the-obs-relay), decisions 37 to 50 |
| Which exception Vercel's SDK raises when a session ends mid-stream. Nothing to build until the trace shows one | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |
| How far a day of real runs is from Vercel's Hobby limits past the allowance: 40 vCPUs a minute on resume, and the snapshot storage cap | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |

## Projects

Open items for a listed project, for the engineer to settle with that repo's session.

| Item | Kind | Since | Repo |
|---|---|---|---|
| None | | | |

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
| Python/JavaScript regex differences the load check can't reject, listed in config.md: `\w`, `\d` and `\b` are Unicode in Python and ASCII in pi's extension; `\s` and `\S` split U+FEFF, `\x1c` to `\x1f` and `\x85` differently, so `git -c user.name=\ufeffx push` gets past the starter's push rule on pi; JavaScript's `.` stops at `\r`, U+2028 and U+2029; a quantifier after a character above U+FFFF binds to half of it in JavaScript; and a quantified lookbehind such as `(?<=g)*` loads but doesn't compile there. Guard run 54540e3d's review found the last four. They now decide only whether a pi run stops or the agent is refused: a command that only matches through one of them still runs, then stops the run, since its output can no longer pass for a refusal. Fix candidate: compile with the `u` flag plus a matching load check | build | 2026-11-06 | [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules) |
| Two skill tests that flaked. `test_rules.py`'s pi-kill test raised `ProcessLookupError` when the stand-in's child was reaped between `alive()`'s two reads; `alive()` now reads once and treats a vanished process as dead. That test then passed 289 of 290 runs, 100 of them with every core loaded; the one failure's output wasn't kept. `test_obs_relay.py`'s oversized-body test failed once in a full skill run, cause not recorded, and 320 posts under full load didn't repeat it; its posts now wait 30 seconds instead of 5. Keep the traceback the next time either fails | watch | 2026-11-07 | [test_rules.py](../.claude/skills/sssf/tests/test_rules.py) |
| Two leftovers from the 2026-11-06 docs audit. The starter configs' `poll_ms` comment still calls it the visualizer's poll cadence, though nothing reads it. And `references/config.md`, `handoff.md` and `observability.md` keep older em dashes and title-case H1s. Changing the comment flags `sssf.config.yaml` for review in every repo, so do it with a change that touches that file anyway | build | 2026-11-06 | [config.md](../.claude/skills/sssf/references/config.md) |
| A local `--sandbox openshell` on NVIDIA OpenShell's microVM driver, beside Vercel and E2B, so the rules against harm outside the repo can go in that mode. The 2026-11-07 test passed: a Claude Code agent signed in with the setup-token and did a task, a push failed with no route, and a gitignored `.env` stayed out. The container drivers can't run under WSL2, and the VM driver needs `/dev/kvm` access. Decide whether to build it | decide | 2026-11-07 | [openshell.md](research/openshell.md#the-microvm-run) |
| A narrower VM network: a domain allowlist, and the provider's proxy adding the token instead of SSSF passing it in. Matters only for untrusted code | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md) |
| Rebase checks off the host. Today `just rebase` runs its setup and checks on the host | build | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#decisions), decision 11 |
| Whether to add an explicit feature label at launch, stored on the run and defaulting to the spec's slug or a ticket id, so a relaunched run's cost reaches the feature it was retried for. It changes the trace's schema in the skill, so every repo updates. The first `features.py` report leans yes: all three of the hub's gone runs were relaunches. Three runs is thin, so decide after a few more weekly reports | decide | 2026-11-06 | [thinking-in-threads.md](research/thinking-in-threads.md#as-built-1) |
| A trial judge that picks the winner on its own, once enough scorecards show whether its picks match the engineer's | need | 2026-11-01 | [trials.md](designs/trials.md) |
| A second bind-mount sandbox kind, such as Docker beside Podman. The isolated VM kind is built. Nothing asks for another container kind yet | need | 2026-10-31 | [container-operator.md](designs/container-operator.md#what-a-second-sandbox-means) |
| An agent's command can write to its harness's stdout (`/proc/$PPID/fd/1`), which the stream check reads. The check reads only the running harness's own event types, so the other harness's types count for nothing, but it can't tell the harness's own types from a forgery. Guard run 6cec4b80's review forged two on pi: a never-ran `tool_execution_end` for the agent's own call, with the id read from `raw_output.jsonl`, so a slipped command counts as refused; and a usage-limit error turn followed by `kill -9 $PPID`, which ends the run as `rate_limit_stop` and tells the engineer to resume. Guard run ea6e82a9's review did the same on Claude Code with its own types: a forged `permission_denied`, or a `user` `tool_result` with the refusal text, counts a call that ran as refused. The fix: a VM, or agents under their own user | build | 2026-11-07 | [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules) |
| On Claude Code, a command that printed SSSF's refusal and exited 126 reads the same as a real `sssf_refuse.py` refusal, and counts as one. Forging one needs the prefix to miss a command the stream check matches; both read it with Python `re`, so that takes a change in how Claude Code wraps a Bash command or a Bash call that skips `CLAUDE_CODE_SHELL_PREFIX`. Recheck `sssf_refuse.commands()` against each Claude Code upgrade's wrapping | watch | 2026-11-07 | [config.md](../.claude/skills/sssf/references/config.md#rules-for-every-agent--agent_rules) |
| Whether a real ChatGPT 429 reads the way pi's source says. The first one gets a log row | watch | 2026-11-01 | [pi-usage-stop.md](designs/pi-usage-stop.md) |
| Whether `/tmp` lasts across a Vercel VM refresh. The obs relay reinstalls its script on each call, so a loss should cost nothing; no proof run has lasted long enough to refresh | watch | 2026-11-05 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-the-obs-relay), decisions 37 to 50 |
| Which exception Vercel's SDK raises when a session ends mid-stream. Nothing to build until the trace shows one | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |
| How far a day of real runs is from Vercel's Hobby limits past the allowance: 40 vCPUs a minute on resume, and the snapshot storage cap | watch | 2026-11-02 | [remote-sandboxes.md](designs/remote-sandboxes.md#as-built-vercel) |

## Projects

Open items for a listed project, for the engineer to settle with that repo's session.

| Item | Kind | Since | Repo |
|---|---|---|---|
| None | | | |
